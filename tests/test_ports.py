from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from gcl_oss.contracts import Constraint, EvidenceEnvelope, ObjectiveSpec, PolicyResult, Scope
from gcl_oss.ports import (
    ArtifactVerificationReceipt,
    ArtifactVerificationRequest,
    ArtifactVerifier,
    ConstraintClassifier,
    Plan,
    Planner,
    VerifiedArtifactContent,
)


class ExampleClassifier:
    @property
    def has_deterministic_fallback(self) -> bool:
        return True

    async def classify(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Constraint]:
        return []


class ExamplePlanner:
    @property
    def deterministic(self) -> bool:
        return True

    async def propose(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        constraints: Sequence[Constraint],
        objective: ObjectiveSpec,
        scope: Scope,
    ) -> Plan | None:
        return None


class ExampleArtifactVerifier:
    async def verify(
        self,
        request: ArtifactVerificationRequest,
    ) -> ArtifactVerificationReceipt:
        return ArtifactVerificationReceipt(
            verifier="https://verifier.example/v1",
            artifact_uri=request.artifact_uri,
            artifact_digest=request.expected_digest,
            manifest_media_type="application/vnd.oci.image.manifest.v1+json",
            manifest_size_bytes=2,
            verified_at=datetime.now(timezone.utc),
            content=[
                VerifiedArtifactContent(
                    role="config",
                    digest="sha256:" + "a" * 64,
                    media_type="application/vnd.oci.image.config.v1+json",
                    size_bytes=2,
                )
            ],
        )


def test_protocols_support_third_party_adapters_without_inheritance() -> None:
    assert isinstance(ExampleClassifier(), ConstraintClassifier)
    assert isinstance(ExamplePlanner(), Planner)
    assert isinstance(ExampleArtifactVerifier(), ArtifactVerifier)


def test_artifact_receipt_only_represents_positive_time_aware_verification() -> None:
    common = {
        "verifier": "https://verifier.example/v1",
        "artifact_uri": "registry.example/team/item@sha256:" + "b" * 64,
        "artifact_digest": "sha256:" + "b" * 64,
        "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
        "manifest_size_bytes": 2,
        "content": [],
    }

    with pytest.raises(ValidationError, match="Input should be True"):
        ArtifactVerificationReceipt(
            **common,
            verified=False,
            verified_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValidationError, match="timezone"):
        ArtifactVerificationReceipt(**common, verified_at=datetime.now())
