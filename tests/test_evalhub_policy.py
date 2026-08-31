from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from importlib import resources

from gcl_oss.adapters.evalhub import normalize_evalhub_job
from gcl_oss.contracts import Scope
from gcl_oss.policy_packs.evalhub import (
    EVALHUB_PROMOTION_CONSTRAINT,
    EvalHubEvidencePolicy,
    EvalHubPromotionConstraintClassifier,
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


def test_policy_accepts_pinned_terminal_result_with_complete_oci_provenance() -> None:
    item = evidence()
    policy = EvalHubEvidencePolicy(expected_producer_prefix=SOURCE_BASE_URL)
    result = asyncio.run(policy.evaluate([item], SCOPE))

    assert result.allowed is True
    assert result.evidence_refs == [item.assurance.digest]


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
