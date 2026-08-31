from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from importlib import resources

import pytest

from gcl_oss.adapters.evalhub import EvalHubAdapterError, normalize_evalhub_job
from gcl_oss.adapters.evalhub_oci import verify_evalhub_oci_artifacts
from gcl_oss.adapters.oci import OCI_DISTRIBUTION_VERIFIER
from gcl_oss.contracts import Scope
from gcl_oss.policy_packs.evalhub import (
    EVALHUB_PROMOTION_CONSTRAINT,
    EvalHubEvidencePolicy,
    EvalHubPromotionConstraintClassifier,
)
from gcl_oss.ports import (
    ArtifactVerificationReceipt,
    ArtifactVerificationRequest,
    VerifiedArtifactContent,
)

SCOPE = Scope(tenant="team-a", namespace="models", environment="staging")
SOURCE_BASE_URL = "https://evalhub.example"
SOURCE_URL = (
    SOURCE_BASE_URL
    + "/api/v1/evaluations/jobs/a1b2c3d4-5678-9abc-def0-1234567890ab"
)


def fixture() -> dict:
    path = resources.files("gcl_oss.data").joinpath(
        "evalhub-job-failed-safety.json"
    )
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def evidence(raw: dict | None = None):
    return normalize_evalhub_job(
        raw or fixture(),
        source_url=SOURCE_URL,
        scope=SCOPE,
        model_version="v7",
    )


class FakeArtifactVerifier:
    def __init__(self, item, *, wrong_digest: bool = False) -> None:
        self.item = item
        self.wrong_digest = wrong_digest
        self.requests: list[ArtifactVerificationRequest] = []

    async def verify(
        self,
        request: ArtifactVerificationRequest,
    ) -> ArtifactVerificationReceipt:
        self.requests.append(request)
        digest = "sha256:" + "d" * 64 if self.wrong_digest else request.expected_digest
        return ArtifactVerificationReceipt(
            verifier=OCI_DISTRIBUTION_VERIFIER,
            artifact_uri=request.artifact_uri,
            artifact_digest=digest,
            manifest_media_type="application/vnd.oci.image.manifest.v1+json",
            manifest_size_bytes=512,
            registry_digest=digest,
            verified_at=self.item.metadata.observed_at + timedelta(seconds=1),
            content=[
                VerifiedArtifactContent(
                    role="layer",
                    digest="sha256:" + "e" * 64,
                    media_type="application/vnd.eval-hub.evaluation-card.v1+json",
                    size_bytes=128,
                )
            ],
        )


def test_policy_accepts_pinned_terminal_result_with_complete_oci_provenance() -> None:
    item = evidence()
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([item], SCOPE))

    assert result.allowed is True
    assert result.evidence_refs == [item.assurance.digest]


def test_policy_can_require_registry_verified_oci_content() -> None:
    item = evidence()
    strict_policy = EvalHubEvidencePolicy(
        expected_producer_prefix=SOURCE_BASE_URL,
        require_verified_oci_artifacts=True,
    )
    missing = asyncio.run(strict_policy.evaluate([item], SCOPE))
    assert missing.allowed is False
    assert "verification receipts are incomplete" in missing.reason

    verifier = FakeArtifactVerifier(item)
    verified_item = asyncio.run(verify_evalhub_oci_artifacts(item, verifier))
    admitted = asyncio.run(strict_policy.evaluate([verified_item], SCOPE))
    assert admitted.allowed is True
    assert "registry-verified OCI content" in admitted.reason
    assert verifier.requests[0].expected_digest == item.assurance.digest


def test_verification_helper_rejects_a_receipt_for_different_content() -> None:
    item = evidence()
    with pytest.raises(EvalHubAdapterError, match="different content"):
        asyncio.run(
            verify_evalhub_oci_artifacts(
                item,
                FakeArtifactVerifier(item, wrong_digest=True),
            )
        )


def test_strict_policy_rejects_an_untrusted_verifier_receipt() -> None:
    item = evidence()
    verified_item = asyncio.run(
        verify_evalhub_oci_artifacts(item, FakeArtifactVerifier(item))
    )
    extensions = deepcopy(verified_item.extensions)
    extensions["io.github.eval-hub/oci-verifications"][0]["receipt"][
        "verifier"
    ] = "https://attacker.invalid/verifier"
    tampered = verified_item.model_copy(update={"extensions": extensions})
    policy = EvalHubEvidencePolicy(
        expected_producer_prefix=SOURCE_BASE_URL,
        require_verified_oci_artifacts=True,
    )
    result = asyncio.run(policy.evaluate([tampered], SCOPE))
    assert result.allowed is False
    assert "untrusted verifier" in result.reason


def test_policy_fails_closed_when_oci_provenance_is_missing() -> None:
    raw = deepcopy(fixture())
    raw["results"]["benchmarks"][0]["artifacts"] = {}
    item = evidence(raw)
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([item], SCOPE))

    assert result.allowed is False
    assert "OCI provenance" in result.reason


def test_policy_rejects_a_lookalike_producer_origin() -> None:
    item = normalize_evalhub_job(
        fixture(),
        source_url=(
            "https://evalhub.example.attacker.invalid/api/v1/evaluations/jobs/42"
        ),
        scope=SCOPE,
    )
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([item], SCOPE))
    assert result.allowed is False
    assert "unexpected producer" in result.reason


def test_policy_revalidates_oci_manifest_against_assurance() -> None:
    item = evidence()
    extensions = deepcopy(item.extensions)
    extensions["io.github.eval-hub/oci-artifacts"][0]["digest"] = (
        "sha256:" + "c" * 64
    )
    tampered = item.model_copy(update={"extensions": extensions})
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([tampered], SCOPE))
    assert result.allowed is False
    assert "mismatched reference" in result.reason


def test_policy_accepts_a_bound_multi_benchmark_oci_manifest() -> None:
    raw = fixture()
    second = deepcopy(raw["results"]["benchmarks"][0])
    second["id"] = "toxicity"
    second["provider_id"] = "lm_evaluation_harness"
    second["benchmark_index"] = 1
    second["artifacts"] = {
        "oci_reference": (
            "quay.io/example/evalhub-results@sha256:" + "c" * 64
        ),
        "oci_digest": "sha256:" + "c" * 64,
    }
    raw["results"]["benchmarks"].append(second)
    item = evidence(raw)
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([item], SCOPE))
    assert result.allowed is True


def test_failed_collection_derives_hard_promotion_review_constraint() -> None:
    item = evidence()
    classifier = EvalHubPromotionConstraintClassifier()
    constraints = asyncio.run(classifier.classify([item], [], SCOPE))

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.name == EVALHUB_PROMOTION_CONSTRAINT
    assert constraint.hard is True
    assert constraint.expression["effect"] == "block-promotion-pending-review"
    assert constraint.evidence_refs == [item.assurance.digest]


def test_passing_collection_derives_no_constraint() -> None:
    raw = fixture()
    raw["results"]["test"]["pass"] = True
    raw["results"]["test"]["score"] = 0.95
    raw["results"]["benchmarks"][0]["test"]["pass"] = True
    raw["results"]["benchmarks"][0]["test"]["primary_score"] = 0.95
    item = evidence(raw)
    classifier = EvalHubPromotionConstraintClassifier()
    constraints = asyncio.run(classifier.classify([item], [], SCOPE))
    assert constraints == []
