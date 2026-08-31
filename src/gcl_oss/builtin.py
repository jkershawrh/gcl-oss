from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

from gcl_oss.contracts import (
    Candidate,
    Consequence,
    Constraint,
    ConstraintSource,
    EvidenceEnvelope,
    FalsificationResult,
    FalsificationVerdict,
    MeasurementStatus,
    ObjectiveMode,
    ObjectiveSpec,
    ObjectiveTerm,
    PolicyResult,
    RejectedAlternative,
    Scope,
    SignedDecisionPackage,
    objective_cost,
)
from gcl_oss.ports import Plan, ProposalReceipt, ProposalStatus
from gcl_oss.registry import ActionDefinition, ActionRegistry

GOVERNANCE_NAMESPACE = "io.github.jkershawrh.gcl.governance"
REVIEW_REQUIRED_CONSTRAINT = f"{GOVERNANCE_NAMESPACE}/review-required"
REQUEST_REVIEW_ACTION = f"{GOVERNANCE_NAMESPACE}/request_review"
HOLD_ACTION = f"{GOVERNANCE_NAMESPACE}/hold"
RISK_OBJECTIVE = f"{GOVERNANCE_NAMESPACE}/risk"
INTERVENTION_OBJECTIVE = f"{GOVERNANCE_NAMESPACE}/intervention"


class MemoryEvidenceSource:
    def __init__(self, evidence: Sequence[EvidenceEnvelope]) -> None:
        self._evidence = tuple(evidence)

    async def receive(self) -> AsyncIterator[EvidenceEnvelope]:
        for item in self._evidence:
            yield item


class MemoryProofRecorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def record(
        self,
        event_type: str,
        payload: BaseModel | dict[str, Any],
        correlation_id: str,
    ) -> str:
        receipt_id = f"memory:{uuid4()}"
        content = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        self.entries.append(
            {
                "receipt_id": receipt_id,
                "event_type": event_type,
                "correlation_id": correlation_id,
                "content": content,
            }
        )
        return receipt_id


class NoOpProposalSink:
    def __init__(self) -> None:
        self.packages: list[SignedDecisionPackage] = []

    async def propose(self, package: SignedDecisionPackage) -> ProposalReceipt:
        self.packages.append(package)
        return ProposalReceipt(
            status=ProposalStatus.DEFERRED,
            consumer="noop://standalone",
            package_digest=package.digest,
            reason="standalone no-op sink retained the proposal without executing it",
        )


class StaticSigner:
    def __init__(self, key_id: str, private_seed: bytes) -> None:
        if len(private_seed) != 32:
            raise ValueError("Ed25519 private key seed must be exactly 32 bytes")
        self._key_id = key_id
        self._private_seed = private_seed

    async def sign(self, payload: bytes, key_id: str) -> bytes:
        if key_id != self._key_id:
            raise KeyError(key_id)
        return Ed25519PrivateKey.from_private_bytes(self._private_seed).sign(payload)

    async def verification_key(self, key_id: str) -> bytes:
        if key_id != self._key_id:
            raise KeyError(key_id)
        return (
            Ed25519PrivateKey.from_private_bytes(self._private_seed)
            .public_key()
            .public_bytes_raw()
        )


class MinimumConfidencePolicy:
    def __init__(self, minimum: float = 0.5) -> None:
        if not 0.0 <= minimum <= 1.0:
            raise ValueError("minimum confidence must be between 0 and 1")
        self._minimum = minimum

    @property
    def deterministic(self) -> bool:
        return True

    async def evaluate(
        self,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
    ) -> PolicyResult:
        below = [item for item in evidence if item.assurance.confidence < self._minimum]
        return PolicyResult(
            check_id="minimum-confidence",
            allowed=not below,
            reason=(
                f"all evidence meets confidence floor {self._minimum}"
                if not below
                else f"{len(below)} evidence item(s) are below confidence floor {self._minimum}"
            ),
            evidence_refs=[item.assurance.digest for item in evidence],
        )


class EvidenceFreshnessCheck:
    @property
    def check_id(self) -> str:
        return "evidence-freshness"

    @property
    def deterministic(self) -> bool:
        return True

    async def challenge(
        self,
        candidate: Candidate,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
        at: datetime,
    ) -> FalsificationResult:
        stale = [item for item in evidence if not item.is_fresh(at)]
        return FalsificationResult(
            candidate_id=candidate.id,
            check_id=self.check_id,
            verdict=(
                FalsificationVerdict.FAILS if stale else FalsificationVerdict.SURVIVES
            ),
            reasoning=(
                f"{len(stale)} evidence item(s) are stale"
                if stale
                else "all evidence is inside its validity window"
            ),
            evidence_refs=[item.assurance.digest for item in evidence],
        )


class FailedMeasurementConstraintClassifier:
    @property
    def has_deterministic_fallback(self) -> bool:
        return True

    async def classify(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Constraint]:
        constraints = []
        for item in evidence:
            if item.measurement.status not in {
                MeasurementStatus.FAILED,
                MeasurementStatus.WARNING,
            }:
                continue
            identity = json.dumps(
                {
                    "name": REVIEW_REQUIRED_CONSTRAINT,
                    "scope": scope.model_dump(mode="json", exclude_none=True),
                    "subject": item.subject.model_dump(mode="json", exclude_none=True),
                    "measurement": item.measurement.name,
                    "status": item.measurement.status.value,
                    "hard": item.measurement.status == MeasurementStatus.FAILED,
                    "confidence": item.assurance.confidence,
                    "producer": item.metadata.producer,
                    "evidence_id": item.metadata.id,
                    "evidence_ref": item.assurance.digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            constraints.append(
                Constraint(
                    id=uuid5(NAMESPACE_URL, identity),
                    name=REVIEW_REQUIRED_CONSTRAINT,
                    hard=item.measurement.status == MeasurementStatus.FAILED,
                    expression={
                        "measurement": item.measurement.name,
                        "status": item.measurement.status.value,
                    },
                    confidence=item.assurance.confidence,
                    source=ConstraintSource.DETERMINISTIC,
                    rationale=(
                        f"{item.measurement.name} is {item.measurement.status.value} and "
                        "requires governed review."
                    ),
                    evidence_refs=[item.assurance.digest],
                )
            )
        return constraints


class RiskReductionObjectiveInterpreter:
    @property
    def has_deterministic_fallback(self) -> bool:
        return True

    async def interpret(
        self,
        constraints: Sequence[Constraint],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> ObjectiveSpec:
        return ObjectiveSpec(
            interpreter=(
                "https://jkershawrh.github.io/gcl-oss/interpreters/risk-reduction/v1"
            ),
            mode=ObjectiveMode.DETERMINISTIC,
            terms=[
                ObjectiveTerm(name=RISK_OBJECTIVE, weight=2 / 3),
                ObjectiveTerm(name=INTERVENTION_OBJECTIVE, weight=1 / 3),
            ],
            rationale=(
                "Respond to failed or warning measurements while preferring the least "
                "consequential sufficient proposal."
            ),
            constraint_ids=[constraint.id for constraint in constraints],
            evidence_refs=list(
                dict.fromkeys(
                    ref for constraint in constraints for ref in constraint.evidence_refs
                )
            ),
        )


class ReviewFailedMeasurementsPlanner:
    def __init__(
        self,
        constraint_names: Sequence[str] = (REVIEW_REQUIRED_CONSTRAINT,),
    ) -> None:
        if not constraint_names:
            raise ValueError("at least one governed constraint name is required")
        self._constraint_names = frozenset(constraint_names)

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
        governed_refs = {
            ref
            for constraint in constraints
            if constraint.name in self._constraint_names
            for ref in constraint.evidence_refs
        }
        concerning = [
            item
            for item in evidence
            if item.assurance.digest in governed_refs
        ]
        if not concerning:
            return None
        evidence_refs = list(dict.fromkeys(item.assurance.digest for item in concerning))
        constraint_refs = [constraint.id for constraint in constraints]
        identity = json.dumps(
            {
                "scope": scope.model_dump(mode="json", exclude_none=True),
                "subjects": sorted(
                    (item.subject.type, item.subject.id, item.subject.version or "")
                    for item in concerning
                ),
                "evidence_refs": sorted(evidence_refs),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        review = Candidate(
            id=uuid5(NAMESPACE_URL, identity + f"|{REQUEST_REVIEW_ACTION}"),
            action=REQUEST_REVIEW_ACTION,
            parameters={"queue": "model-risk"},
            consequence=Consequence.MEDIUM,
            rationale="One or more governed measurements require human review.",
            objective_values={
                RISK_OBJECTIVE: 0.4,
                INTERVENTION_OBJECTIVE: 0.2,
            },
            constraint_refs=constraint_refs,
            evidence_refs=evidence_refs,
        )
        hold = Candidate(
            id=uuid5(NAMESPACE_URL, identity + f"|{HOLD_ACTION}"),
            action=HOLD_ACTION,
            parameters={"subject_ids": sorted({item.subject.id for item in concerning})},
            consequence=Consequence.HIGH,
            rationale="A temporary hold is a more restrictive alternative.",
            objective_values={
                RISK_OBJECTIVE: 0.1,
                INTERVENTION_OBJECTIVE: 1.0,
            },
            constraint_refs=constraint_refs,
            evidence_refs=evidence_refs,
        )
        selected = min(
            (review, hold),
            key=lambda candidate: (objective_cost(objective, candidate), candidate.action),
        )
        rejected = hold if selected.id == review.id else review
        return Plan(
            candidates=[review, hold],
            selected_candidate_id=selected.id,
            rejected_alternatives=[
                RejectedAlternative(
                    candidate=rejected,
                    reason=(
                        f"weighted objective cost {objective_cost(objective, rejected):.3f} "
                        f"exceeds selected cost {objective_cost(objective, selected):.3f}"
                    ),
                )
            ],
        )


def standalone_action_registry() -> ActionRegistry:
    common_checks = ("evidence-freshness",)
    return ActionRegistry(
        [
            ActionDefinition(
                action=REQUEST_REVIEW_ACTION,
                parameter_schema={
                    "type": "object",
                    "properties": {"queue": {"type": "string", "minLength": 1}},
                    "required": ["queue"],
                    "additionalProperties": False,
                },
                allowed_consequences=frozenset({Consequence.LOW, Consequence.MEDIUM}),
                required_falsification_checks=common_checks,
            ),
            ActionDefinition(
                action=HOLD_ACTION,
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "subject_ids": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "uniqueItems": True,
                        }
                    },
                    "required": ["subject_ids"],
                    "additionalProperties": False,
                },
                allowed_consequences=frozenset({Consequence.HIGH, Consequence.CRITICAL}),
                required_falsification_checks=common_checks,
            ),
        ]
    )
