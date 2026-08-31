from __future__ import annotations

from collections.abc import Sequence

from gcl_oss.contracts import Constraint, EvidenceEnvelope, ObjectiveSpec, PolicyResult, Scope
from gcl_oss.ports import ConstraintClassifier, Plan, Planner


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


def test_protocols_support_third_party_adapters_without_inheritance() -> None:
    assert isinstance(ExampleClassifier(), ConstraintClassifier)
    assert isinstance(ExamplePlanner(), Planner)
