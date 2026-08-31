from __future__ import annotations

from uuid import uuid4

import pytest

from gcl_oss.builtin import standalone_action_registry
from gcl_oss.contracts import Candidate, Consequence


def test_registry_validates_parameters_and_consequence() -> None:
    registry = standalone_action_registry()
    candidate = Candidate(
        action="io.github.jkershawrh.gcl.governance/request_review",
        parameters={"queue": "model-risk"},
        consequence=Consequence.MEDIUM,
        rationale="review required",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.4},
        constraint_refs=[uuid4()],
        evidence_refs=["sha256:" + "a" * 64],
    )
    definition = registry.validate(candidate)
    assert definition.required_falsification_checks == ("evidence-freshness",)


def test_registry_rejects_unknown_parameters() -> None:
    registry = standalone_action_registry()
    candidate = Candidate(
        action="io.github.jkershawrh.gcl.governance/request_review",
        parameters={"queue": "model-risk", "execute": True},
        consequence=Consequence.MEDIUM,
        rationale="review required",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.4},
        constraint_refs=[uuid4()],
        evidence_refs=["sha256:" + "a" * 64],
    )
    with pytest.raises(ValueError, match="do not match"):
        registry.validate(candidate)


def test_registry_rejects_unregistered_action() -> None:
    registry = standalone_action_registry()
    candidate = Candidate(
        action="unknown.example/act",
        parameters={},
        consequence=Consequence.LOW,
        rationale="unknown action",
        objective_values={"io.github.jkershawrh.gcl.governance/risk": 0.4},
        constraint_refs=[uuid4()],
        evidence_refs=["sha256:" + "a" * 64],
    )
    with pytest.raises(ValueError, match="not registered"):
        registry.validate(candidate)
