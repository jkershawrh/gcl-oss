from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from gcl_oss.contracts import (
    Assurance,
    Candidate,
    Consequence,
    DecisionPackage,
    EvidenceEnvelope,
    EvidenceMetadata,
    FalsificationResult,
    FalsificationVerdict,
    Measurement,
    MeasurementStatus,
    ProposerIdentity,
    Scope,
    SignedDecisionPackage,
    Subject,
)
from gcl_oss.ports import ProposalReceipt, ProposalStatus

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
SEED = bytes.fromhex("11" * 32)


def evidence() -> EvidenceEnvelope:
    return EvidenceEnvelope(
        metadata=EvidenceMetadata(
            id="evalhub-job-42",
            correlation_id="corr-42",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
            producer="https://evalhub.example/api/v1/jobs/42",
            schema_uri="https://evalhub.example/schemas/job-result/v1",
        ),
        scope=Scope(tenant="team-a", namespace="models"),
        subject=Subject(type="model", id="fraud-detector", version="v7"),
        measurement=Measurement(
            name="safety-collection",
            value=0.62,
            threshold=0.90,
            unit="score",
            status=MeasurementStatus.FAILED,
        ),
        assurance=Assurance(confidence=0.98, digest="sha256:" + "a" * 64),
        extensions={"evalhub.io/job-id": "42"},
    )


def package() -> DecisionPackage:
    item = evidence()
    candidate = Candidate(
        action="governance.gcl.io/request_review",
        parameters={"queue": "model-risk"},
        consequence=Consequence.MEDIUM,
        rationale="The required safety collection failed.",
        evidence_refs=[item.assurance.digest],
    )
    result = FalsificationResult(
        candidate_id=candidate.id,
        check_id="evidence-is-fresh",
        verdict=FalsificationVerdict.SURVIVES,
        reasoning="The evidence is inside its validity window.",
        evidence_refs=[item.assurance.digest],
    )
    return DecisionPackage(
        created_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=6),
        correlation_id="corr-42",
        scope=item.scope,
        proposer=ProposerIdentity(
            id="gcl-oss",
            workload_identity="spiffe://example.org/ns/gcl/sa/kernel",
            trust_domain="example.org",
        ),
        evidence_refs=[item.assurance.digest],
        candidates=[candidate],
        selected_candidate_id=candidate.id,
        falsification_results=[result],
    )


def test_evidence_requires_a_valid_freshness_window() -> None:
    item = evidence()
    assert item.is_fresh(NOW + timedelta(minutes=1))
    assert not item.is_fresh(NOW + timedelta(minutes=16))


def test_extensions_must_be_namespaced() -> None:
    payload = evidence().model_dump()
    payload["extensions"] = {"job-id": "42"}
    with pytest.raises(ValidationError, match="namespaced"):
        EvidenceEnvelope.model_validate(payload)


def test_selected_candidate_must_survive_falsification() -> None:
    payload = package().model_dump()
    payload["falsification_results"][0]["verdict"] = "fails"
    with pytest.raises(ValidationError, match="must survive"):
        DecisionPackage.model_validate(payload)


def test_nested_evidence_must_be_declared_by_package() -> None:
    payload = package().model_dump()
    payload["candidates"][0]["evidence_refs"] = ["sha256:" + "b" * 64]
    with pytest.raises(ValidationError, match="absent from evidence_refs"):
        DecisionPackage.model_validate(payload)


def test_ed25519_signature_binds_package_identity_and_expiry() -> None:
    unsigned = package()
    signed = SignedDecisionPackage.sign(unsigned, SEED, "test-key-v1")
    public_key = Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()

    assert signed.verify(
        public_key,
        expected_key_id="test-key-v1",
        at=NOW + timedelta(minutes=2),
    )
    assert not signed.verify(
        public_key,
        expected_key_id="wrong-key",
        at=NOW + timedelta(minutes=2),
    )
    assert not signed.verify(public_key, at=NOW + timedelta(minutes=7))


def test_proposal_receipt_cannot_claim_execution() -> None:
    with pytest.raises(ValidationError, match="cannot verify execution"):
        ProposalReceipt(
            status=ProposalStatus.ACCEPTED,
            consumer="https://workflow.example/proposals",
            package_digest="sha256:" + "a" * 64,
            execution_verified=True,
        )
