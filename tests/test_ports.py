from __future__ import annotations

from collections.abc import Sequence

from gcl_oss.contracts import Candidate, EvidenceEnvelope, Scope
from gcl_oss.ports import Planner, PolicyResult


class ExamplePlanner:
    @property
    def deterministic(self) -> bool:
        return True

    async def propose(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Candidate]:
        return []


def test_protocols_support_third_party_adapters_without_inheritance() -> None:
    assert isinstance(ExamplePlanner(), Planner)
