from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from gcl_oss.contracts import (
    Assurance,
    Candidate,
    Consequence,
    Constraint,
    ConstraintSource,
    DecisionPackage,
    EvidenceEnvelope,
    EvidenceMetadata,
    EvidenceReference,
    FalsificationResult,
    FalsificationVerdict,
    Measurement,
    MeasurementStatus,
    ObjectiveMode,
    ObjectiveSpec,
    ObjectiveTerm,
    PolicyResult,
    ProposerIdentity,
    RejectedAlternative,
    Scope,
    SignedDecisionPackage,
    Subject,
    sha256_digest,
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
    constraint = Constraint(
        name="io.github.jkershawrh.gcl.governance/review-required",
        hard=True,
        expression={"measurement": "safety-collection", "status": "failed"},
        confidence=0.98,
        source=ConstraintSource.DETERMINISTIC,
        rationale="The required safety collection failed.",
        evidence_refs=[item.assurance.digest],
    )
    candidate = Candidate(
        action="io.github.jkershawrh.gcl.governance/request_review",
        parameters={"queue": "model-risk"},
        consequence=Consequence.MEDIUM,
        rationale="The required safety collection failed.",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.4},
        constraint_refs=[constraint.id],
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
        evidence=[
            EvidenceReference(
                id=item.metadata.id,
                producer=item.metadata.producer,
                schema_uri=item.metadata.schema_uri,
                artifact_digest=item.assurance.digest,
                envelope_digest=sha256_digest(item),
                artifact_uri=item.assurance.artifact_uri,
            )
        ],
        evidence_refs=[item.assurance.digest],
        policy_results=[
            PolicyResult(
                check_id="minimum-confidence",
                allowed=True,
                reason="confidence floor satisfied",
                evidence_refs=[item.assurance.digest],
            )
        ],
        constraints=[constraint],
        objective=ObjectiveSpec(
            interpreter="https://jkershawrh.github.io/gcl-oss/tests/objective",
            mode=ObjectiveMode.DETERMINISTIC,
            terms=[
                ObjectiveTerm(
                    name="io.github.jkershawrh.gcl.governance/risk", weight=1.0
                )
            ],
            rationale="Reduce governance risk.",
            constraint_ids=[constraint.id],
            evidence_refs=[item.assurance.digest],
        ),
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
    with pytest.raises(ValidationError, match="should match pattern"):
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


def test_evidence_manifest_must_match_package_digest_references() -> None:
    payload = package().model_dump()
    payload["evidence"][0]["artifact_digest"] = "sha256:" + "b" * 64
    with pytest.raises(ValidationError, match="manifest digests"):
        DecisionPackage.model_validate(payload)


def test_decision_package_cannot_embed_a_denied_policy() -> None:
    payload = package().model_dump()
    payload["policy_results"][0]["allowed"] = False
    with pytest.raises(ValidationError, match="denied policy"):
        DecisionPackage.model_validate(payload)


def test_ed25519_signature_binds_package_identity_and_expiry() -> None:
    unsigned = package()
    signed = SignedDecisionPackage.sign(unsigned, SEED, "test-key-v1")
    public_key = Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()

    assert signed.verify(
        public_key,
        expected_key_id="test-key-v1",
        expected_scope=unsigned.scope,
        expected_trust_domain="example.org",
        at=NOW + timedelta(minutes=2),
    )
    assert not signed.verify(
        public_key,
        expected_key_id="wrong-key",
        at=NOW + timedelta(minutes=2),
    )
    assert not signed.verify(
        public_key,
        expected_scope=Scope(tenant="another-team"),
        at=NOW + timedelta(minutes=2),
    )
    assert not signed.verify(public_key, at=NOW + timedelta(minutes=7))


def test_ed25519_signature_detects_a_tampered_package() -> None:
    unsigned = package()
    signed = SignedDecisionPackage.sign(unsigned, SEED, "test-key-v1")
    public_key = Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()
    payload = unsigned.model_dump()
    payload["candidates"][0]["parameters"] = {"queue": "different-queue"}
    tampered_package = DecisionPackage.model_validate(payload)
    tampered = SignedDecisionPackage(
        package=tampered_package,
        digest=sha256_digest(tampered_package),
        signature=signed.signature,
        key_id=signed.key_id,
    )
    assert not tampered.verify(public_key, at=NOW + timedelta(minutes=2))


def test_proposal_receipt_cannot_claim_execution() -> None:
    with pytest.raises(ValidationError, match="cannot verify execution"):
        ProposalReceipt(
            status=ProposalStatus.ACCEPTED,
            consumer="https://workflow.example/proposals",
            package_digest="sha256:" + "a" * 64,
            execution_verified=True,
        )


def test_objective_is_action_free_and_requires_a_positive_weight() -> None:
    objective = ObjectiveSpec(
        interpreter="https://jkershawrh.github.io/gcl-oss/tests/objective",
        mode=ObjectiveMode.DETERMINISTIC,
        terms=[
            ObjectiveTerm(
                name="io.github.jkershawrh.gcl.governance/risk", weight=1.0
            )
        ],
        rationale="reduce measured governance risk",
        constraint_ids=[package().constraints[0].id],
        evidence_refs=["sha256:" + "a" * 64],
    )
    assert "action" not in objective.model_dump()
    payload = objective.model_dump()
    payload["action"] = "io.github.jkershawrh.gcl.governance/hold"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ObjectiveSpec.model_validate(payload)

    with pytest.raises(ValidationError, match="positive weight"):
        ObjectiveSpec(
            interpreter="https://jkershawrh.github.io/gcl-oss/tests/objective",
            mode=ObjectiveMode.DETERMINISTIC,
            terms=[
                ObjectiveTerm(
                    name="io.github.jkershawrh.gcl.governance/risk", weight=0.0
                )
            ],
            rationale="invalid zero-weight objective",
            constraint_ids=[package().constraints[0].id],
            evidence_refs=["sha256:" + "a" * 64],
        )
    with pytest.raises(ValidationError, match="sum to 1.0"):
        ObjectiveSpec(
            interpreter="https://jkershawrh.github.io/gcl-oss/tests/objective",
            mode=ObjectiveMode.DETERMINISTIC,
            terms=[
                ObjectiveTerm(
                    name="io.github.jkershawrh.gcl.governance/risk", weight=0.5
                )
            ],
            rationale="invalid non-normalized objective",
            constraint_ids=[package().constraints[0].id],
            evidence_refs=["sha256:" + "a" * 64],
        )


def test_selected_candidate_must_cover_every_hard_constraint() -> None:
    unsigned = package()
    additional = Constraint(
        name="io.github.jkershawrh.gcl.governance/approval-required",
        hard=True,
        expression={"approval": "required"},
        confidence=1.0,
        source=ConstraintSource.DETERMINISTIC,
        rationale="Independent approval is required.",
        evidence_refs=unsigned.evidence_refs,
    )
    payload = unsigned.model_dump()
    payload["constraints"].append(additional.model_dump())
    payload["objective"]["constraint_ids"].append(additional.id)
    with pytest.raises(ValidationError, match="cover every hard constraint"):
        DecisionPackage.model_validate(payload)


def test_rejected_alternative_must_match_the_candidate_record() -> None:
    unsigned = package()
    alternative = Candidate(
        action="io.github.jkershawrh.gcl.governance/hold",
        parameters={"subject_ids": ["fraud-detector"]},
        consequence=Consequence.HIGH,
        rationale="A hold is the more restrictive option.",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.9},
        constraint_refs=[unsigned.constraints[0].id],
        evidence_refs=unsigned.evidence_refs,
    )
    mismatched = alternative.model_copy(update={"rationale": "different rationale"})
    payload = unsigned.model_dump()
    payload["candidates"].append(alternative.model_dump())
    payload["rejected_alternatives"] = [
        RejectedAlternative(candidate=mismatched, reason="review is sufficient").model_dump()
    ]
    with pytest.raises(ValidationError, match="must match its plan candidate"):
        DecisionPackage.model_validate(payload)


def test_evidence_references_are_sha256_digests() -> None:
    payload = package().model_dump()
    payload["objective"]["evidence_refs"] = ["artifact-42"]
    with pytest.raises(ValidationError, match="should match pattern"):
        DecisionPackage.model_validate(payload)


def test_selected_candidate_must_minimize_the_signed_objective() -> None:
    unsigned = package()
    lower_cost = Candidate(
        action="io.github.jkershawrh.gcl.governance/hold",
        parameters={"subject_ids": ["fraud-detector"]},
        consequence=Consequence.HIGH,
        rationale="Lower signed cost for contract validation.",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.1},
        constraint_refs=[unsigned.constraints[0].id],
        evidence_refs=unsigned.evidence_refs,
    )
    payload = unsigned.model_dump()
    payload["candidates"].append(lower_cost.model_dump())
    payload["rejected_alternatives"] = [
        RejectedAlternative(candidate=lower_cost, reason="incorrectly rejected").model_dump()
    ]
    with pytest.raises(ValidationError, match="minimize the weighted objective"):
        DecisionPackage.model_validate(payload)


def test_objective_values_must_be_finite() -> None:
    payload = package().model_dump()
    payload["candidates"][0]["objective_values"][
        "io.github.jkershawrh.gcl.governance/risk"
    ] = float("inf")
    with pytest.raises(ValidationError, match="finite number"):
        DecisionPackage.model_validate(payload)
