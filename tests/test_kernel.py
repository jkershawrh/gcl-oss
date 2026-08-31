from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pytest

from gcl_oss.builtin import (
    EvidenceFreshnessCheck,
    FailedMeasurementConstraintClassifier,
    MemoryProofRecorder,
    MinimumConfidencePolicy,
    NoOpProposalSink,
    ReviewFailedMeasurementsPlanner,
    RiskReductionObjectiveInterpreter,
    StaticSigner,
    standalone_action_registry,
)
from gcl_oss.contracts import (
    Assurance,
    Candidate,
    Consequence,
    Constraint,
    EvidenceEnvelope,
    EvidenceMetadata,
    Measurement,
    MeasurementStatus,
    ObjectiveMode,
    ObjectiveSpec,
    ObjectiveTerm,
    PolicyResult,
    ProposerIdentity,
    Scope,
    Subject,
)
from gcl_oss.kernel import GovernanceKernel, KernelStatus, cycle_key_for
from gcl_oss.ports import Plan, ProposalReceipt, ProposalStatus

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
SCOPE = Scope(tenant="team-a", namespace="models", environment="test")
SEED = bytes.fromhex("22" * 32)
PROPOSER = ProposerIdentity(
    id="gcl-oss-test",
    workload_identity="spiffe://example.org/ns/test/sa/kernel",
    trust_domain="example.org",
)


def evidence(
    *,
    scope: Scope = SCOPE,
    confidence: float = 0.98,
    expires_at: datetime | None = None,
    status: MeasurementStatus = MeasurementStatus.FAILED,
    evidence_id: str = "evaluation-42",
    digest_character: str = "a",
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        metadata=EvidenceMetadata(
            id=evidence_id,
            correlation_id="corr-42",
            observed_at=NOW - timedelta(minutes=1),
            expires_at=expires_at or NOW + timedelta(minutes=10),
            producer="https://evalhub.example/jobs/42",
            schema_uri="https://evalhub.example/schemas/result/v1",
        ),
        scope=scope,
        subject=Subject(type="model", id="fraud-detector", version="v7"),
        measurement=Measurement(
            name="safety-collection",
            value=0.62,
            threshold=0.90,
            unit="score",
            status=status,
        ),
        assurance=Assurance(
            confidence=confidence,
            digest="sha256:" + digest_character * 64,
        ),
        extensions={"evalhub.io/job-id": "42"},
    )


def components() -> tuple[
    GovernanceKernel,
    NoOpProposalSink,
    MemoryProofRecorder,
    StaticSigner,
]:
    sink = NoOpProposalSink()
    proof = MemoryProofRecorder()
    signer = StaticSigner("test-key-v1", SEED)
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=signer,
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=sink,
        policy_checks=[MinimumConfidencePolicy(0.8)],
        proof_recorders=[proof],
        clock=lambda: NOW,
    )
    return kernel, sink, proof, signer


def test_kernel_produces_a_signed_proposal_without_execution_claim() -> None:
    kernel, sink, proof, signer = components()
    result = asyncio.run(kernel.run([evidence()], scope=SCOPE))

    assert result.status == KernelStatus.PROPOSED
    assert result.signed_package is not None
    assert result.proposal_receipt is not None
    assert result.proposal_receipt.execution_verified is False
    assert (
        result.signed_package.package.extensions[
            "io.github.jkershawrh.gcl/cycle-key"
        ]
        == result.cycle_key
    )
    assert result.signed_package.package.constraints
    assert result.signed_package.package.objective.constraint_ids == [
        result.signed_package.package.constraints[0].id
    ]
    assert result.signed_package.package.policy_results[0].allowed is True
    selected = next(
        candidate
        for candidate in result.signed_package.package.candidates
        if candidate.id == result.signed_package.package.selected_candidate_id
    )
    assert selected.action == "io.github.jkershawrh.gcl.governance/request_review"
    assert len(sink.packages) == 1
    assert len(proof.entries) == 6
    assert proof.entries[-1]["content"]["proposal_receipt"]["status"] == "deferred"
    public_key = asyncio.run(signer.verification_key("test-key-v1"))
    assert result.signed_package.verify(public_key, at=NOW + timedelta(minutes=1))


def test_exact_replay_returns_cached_result_without_second_delivery() -> None:
    kernel, sink, proof, _ = components()
    item = evidence()
    first = asyncio.run(kernel.run([item], scope=SCOPE))
    second = asyncio.run(kernel.run([item], scope=SCOPE))

    assert first.replayed is False
    assert second.replayed is True
    assert second.cycle_key == first.cycle_key
    assert len(sink.packages) == 1
    assert len(proof.entries) == 6


def test_concurrent_replay_delivers_only_once() -> None:
    kernel, sink, proof, _ = components()
    item = evidence()

    async def run_both():
        return await asyncio.gather(
            kernel.run([item], scope=SCOPE),
            kernel.run([item], scope=SCOPE),
        )

    first, second = asyncio.run(run_both())
    assert {first.replayed, second.replayed} == {False, True}
    assert len(sink.packages) == 1
    assert len(proof.entries) == 6


def test_duplicate_evidence_identity_is_rejected() -> None:
    kernel, _, _, _ = components()
    item = evidence()
    with pytest.raises(ValueError, match="duplicate producer"):
        asyncio.run(kernel.run([item, item], scope=SCOPE))


def test_cycle_key_binds_the_normalized_evidence_content() -> None:
    original = evidence()
    payload = original.model_dump()
    payload["measurement"]["value"] = 0.7
    changed = EvidenceEnvelope.model_validate(payload)
    assert cycle_key_for([original], SCOPE) != cycle_key_for([changed], SCOPE)


def test_kernel_canonicalizes_evidence_order_before_planning() -> None:
    kernel, _, _, _ = components()
    first = evidence(evidence_id="evaluation-1", digest_character="a")
    second = evidence(evidence_id="evaluation-2", digest_character="b")
    result = asyncio.run(kernel.run([second, first], scope=SCOPE))
    assert result.signed_package is not None
    assert result.signed_package.package.evidence_refs == [
        first.assurance.digest,
        second.assurance.digest,
    ]


def test_cross_tenant_evidence_is_rejected_before_planning() -> None:
    kernel, sink, proof, _ = components()
    other_scope = Scope(tenant="team-b", namespace="models", environment="test")
    result = asyncio.run(kernel.run([evidence(scope=other_scope)], scope=SCOPE))

    assert result.status == KernelStatus.REJECTED
    assert "scope" in result.reasons[0]
    assert not sink.packages
    assert (
        proof.entries[0]["event_type"]
        == "io.github.jkershawrh.gcl.evidence.rejected.v1alpha1"
    )
    assert (
        proof.entries[-1]["event_type"]
        == "io.github.jkershawrh.gcl.decision.rejected.v1alpha1"
    )


def test_stale_evidence_is_rejected_before_planning() -> None:
    kernel, sink, _, _ = components()
    result = asyncio.run(
        kernel.run([evidence(expires_at=NOW - timedelta(seconds=1))], scope=SCOPE)
    )
    assert result.status == KernelStatus.REJECTED
    assert "validity window" in result.reasons[0]
    assert not sink.packages


def test_package_expiry_cannot_outlive_its_evidence() -> None:
    kernel, _, _, _ = components()
    evidence_expiry = NOW + timedelta(minutes=1)
    result = asyncio.run(
        kernel.run([evidence(expires_at=evidence_expiry)], scope=SCOPE)
    )
    assert result.signed_package is not None
    assert result.signed_package.package.expires_at == evidence_expiry


def test_policy_denial_prevents_signing_and_delivery() -> None:
    kernel, sink, _, _ = components()
    result = asyncio.run(kernel.run([evidence(confidence=0.2)], scope=SCOPE))
    assert result.status == KernelStatus.REJECTED
    assert "minimum-confidence" in result.reasons[0]
    assert result.signed_package is None
    assert not sink.packages


def test_no_constraints_produces_no_candidate() -> None:
    kernel, sink, _, _ = components()
    result = asyncio.run(
        kernel.run([evidence(status=MeasurementStatus.PASSED)], scope=SCOPE)
    )
    assert result.status == KernelStatus.NO_CANDIDATE
    assert "no evidence-derived constraints" in result.reasons[0]
    assert not sink.packages


class FailingFinalProofRecorder(MemoryProofRecorder):
    async def record(self, event_type, payload, correlation_id):
        if event_type == "io.github.jkershawrh.gcl.decision.proposed.v1alpha1":
            raise RuntimeError("proof store unavailable")
        return await super().record(event_type, payload, correlation_id)


def test_final_proof_failure_is_explicit_and_does_not_duplicate_delivery() -> None:
    sink = NoOpProposalSink()
    proof = FailingFinalProofRecorder()
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=sink,
        policy_checks=[MinimumConfidencePolicy(0.8)],
        proof_recorders=[proof],
        clock=lambda: NOW,
    )
    item = evidence()
    first = asyncio.run(kernel.run([item], scope=SCOPE))
    second = asyncio.run(kernel.run([item], scope=SCOPE))

    assert first.status == KernelStatus.PROPOSED
    assert "proof recording failed" in first.reasons[0]
    assert second.replayed is True
    assert len(sink.packages) == 1


class FailingProposalSink:
    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, package):
        self.calls += 1
        raise TimeoutError("consumer outcome unavailable")


def test_unknown_delivery_is_not_automatically_retried() -> None:
    sink = FailingProposalSink()
    proof = MemoryProofRecorder()
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=sink,
        proof_recorders=[proof],
        clock=lambda: NOW,
    )
    item = evidence()
    first = asyncio.run(kernel.run([item], scope=SCOPE))
    second = asyncio.run(kernel.run([item], scope=SCOPE))

    assert first.status == KernelStatus.DELIVERY_UNKNOWN
    assert first.signed_package is not None
    assert second.replayed is True
    assert sink.calls == 1
    assert proof.entries[-1]["event_type"].endswith("decision.delivery_unknown.v1alpha1")
    assert "proposal_receipt" not in proof.entries[-1]["content"]


class WrongDigestProposalSink:
    async def propose(self, package):
        return ProposalReceipt(
            status=ProposalStatus.ACCEPTED,
            consumer="https://consumer.example/proposals",
            package_digest="sha256:" + "f" * 64,
        )


class MutatingProposalSink:
    async def propose(self, package):
        package.package.candidates[0].parameters["queue"] = "tampered"
        return ProposalReceipt(
            status=ProposalStatus.ACCEPTED,
            consumer="https://consumer.example/proposals",
            package_digest=package.digest,
        )


def test_proposal_sink_cannot_mutate_the_kernel_copy() -> None:
    signer = StaticSigner("test-key-v1", SEED)
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=signer,
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=MutatingProposalSink(),
        clock=lambda: NOW,
    )
    result = asyncio.run(kernel.run([evidence()], scope=SCOPE))
    assert result.signed_package is not None
    assert result.signed_package.package.candidates[0].parameters["queue"] == "model-risk"
    public_key = asyncio.run(signer.verification_key("test-key-v1"))
    assert result.signed_package.verify(public_key, at=NOW + timedelta(minutes=1))


def test_mismatched_receipt_digest_is_delivery_unknown() -> None:
    proof = MemoryProofRecorder()
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=WrongDigestProposalSink(),
        proof_recorders=[proof],
        clock=lambda: NOW,
    )
    result = asyncio.run(kernel.run([evidence()], scope=SCOPE))
    assert result.status == KernelStatus.DELIVERY_UNKNOWN
    assert "does not match" in result.reasons[0]
    assert proof.entries[-1]["content"]["proposal_receipt"]["status"] == "accepted"


class NondeterministicPlanner:
    @property
    def deterministic(self) -> bool:
        return False

    async def propose(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        constraints: Sequence[Constraint],
        objective: ObjectiveSpec,
        scope: Scope,
    ) -> Plan | None:
        return None


def test_kernel_refuses_nondeterministic_planner() -> None:
    with pytest.raises(ValueError, match="deterministic"):
        GovernanceKernel(
            planner=NondeterministicPlanner(),
            objective_interpreter=RiskReductionObjectiveInterpreter(),
            constraint_classifiers=[FailedMeasurementConstraintClassifier()],
            registry=standalone_action_registry(),
            falsification_checks=[EvidenceFreshnessCheck()],
            signer=StaticSigner("test-key-v1", SEED),
            key_id="test-key-v1",
            proposer=PROPOSER,
            proposal_sink=NoOpProposalSink(),
            clock=lambda: NOW,
        )


class UnknownActionPlanner:
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
        candidate = Candidate(
            action="unknown.example/act",
            parameters={},
            consequence=Consequence.LOW,
            rationale="unknown action",
            objective_values={
                term.name: 0.5 for term in objective.terms
            },
            constraint_refs=[constraint.id for constraint in constraints],
            evidence_refs=[evidence[0].assurance.digest],
        )
        return Plan(candidates=[candidate], selected_candidate_id=candidate.id)


def test_unregistered_action_fails_before_signing() -> None:
    kernel = GovernanceKernel(
        planner=UnknownActionPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=NoOpProposalSink(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="not registered"):
        asyncio.run(kernel.run([evidence()], scope=SCOPE))


def test_missing_required_falsification_check_fails_before_signing() -> None:
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=NoOpProposalSink(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="not configured"):
        asyncio.run(kernel.run([evidence()], scope=SCOPE))


class NoFallbackObjectiveInterpreter:
    @property
    def has_deterministic_fallback(self) -> bool:
        return False


class NoFallbackConstraintClassifier:
    @property
    def has_deterministic_fallback(self) -> bool:
        return False


class NondeterministicPolicy:
    @property
    def deterministic(self) -> bool:
        return False


class NondeterministicFalsificationCheck:
    @property
    def check_id(self) -> str:
        return "nondeterministic-check"

    @property
    def deterministic(self) -> bool:
        return False


def test_kernel_requires_determinism_at_governance_boundaries() -> None:
    common = {
        "planner": ReviewFailedMeasurementsPlanner(),
        "registry": standalone_action_registry(),
        "falsification_checks": [EvidenceFreshnessCheck()],
        "signer": StaticSigner("test-key-v1", SEED),
        "key_id": "test-key-v1",
        "proposer": PROPOSER,
        "proposal_sink": NoOpProposalSink(),
        "clock": lambda: NOW,
    }
    with pytest.raises(ValueError, match="objective interpreter"):
        GovernanceKernel(
            objective_interpreter=NoFallbackObjectiveInterpreter(),
            constraint_classifiers=[FailedMeasurementConstraintClassifier()],
            **common,
        )
    with pytest.raises(ValueError, match="constraint classifiers"):
        GovernanceKernel(
            objective_interpreter=RiskReductionObjectiveInterpreter(),
            constraint_classifiers=[NoFallbackConstraintClassifier()],
            **common,
        )
    with pytest.raises(ValueError, match="policy checks"):
        GovernanceKernel(
            objective_interpreter=RiskReductionObjectiveInterpreter(),
            constraint_classifiers=[FailedMeasurementConstraintClassifier()],
            policy_checks=[NondeterministicPolicy()],
            **common,
        )
    with pytest.raises(ValueError, match="falsification checks"):
        GovernanceKernel(
            objective_interpreter=RiskReductionObjectiveInterpreter(),
            constraint_classifiers=[FailedMeasurementConstraintClassifier()],
            falsification_checks=[NondeterministicFalsificationCheck()],
            **{key: value for key, value in common.items() if key != "falsification_checks"},
        )


class OutsideEvidenceObjectiveInterpreter:
    @property
    def has_deterministic_fallback(self) -> bool:
        return True

    async def interpret(self, constraints, policy_results, scope):
        return ObjectiveSpec(
            interpreter="https://jkershawrh.github.io/gcl-oss/tests/outside-evidence",
            mode=ObjectiveMode.DETERMINISTIC,
            terms=[
                ObjectiveTerm(
                    name="io.github.jkershawrh.gcl.governance/risk", weight=1.0
                )
            ],
            rationale="invalid objective for boundary testing",
            constraint_ids=[constraint.id for constraint in constraints],
            evidence_refs=["sha256:" + "b" * 64],
        )


def test_objective_cannot_reference_evidence_outside_the_cycle() -> None:
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=OutsideEvidenceObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=NoOpProposalSink(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="objective references evidence"):
        asyncio.run(kernel.run([evidence()], scope=SCOPE))


class WrongCheckId(EvidenceFreshnessCheck):
    async def challenge(self, candidate, evidence, scope, at):
        result = await super().challenge(candidate, evidence, scope, at)
        return result.model_copy(update={"check_id": "different-check"})


def test_falsification_result_must_match_the_registered_check() -> None:
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[WrongCheckId()],
        signer=StaticSigner("test-key-v1", SEED),
        key_id="test-key-v1",
        proposer=PROPOSER,
        proposal_sink=NoOpProposalSink(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="wrong check id"):
        asyncio.run(kernel.run([evidence()], scope=SCOPE))
