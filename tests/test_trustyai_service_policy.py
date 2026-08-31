from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from importlib import resources

from gcl_oss.adapters.trustyai_service import normalize_trustyai_metric
from gcl_oss.contracts import MeasurementStatus, Scope
from gcl_oss.policy_packs.trustyai_service import (
    TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT,
    TrustyAIRuntimeConstraintClassifier,
    TrustyAIServiceEvidencePolicy,
)

SCOPE = Scope(tenant="team-a", namespace="models", environment="staging")
NOW = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)


def evidence():
    path = resources.files("gcl_oss.data").joinpath("trustyai-kstest-drift.json")
    with path.open(encoding="utf-8") as source:
        raw = json.load(source)
    return normalize_trustyai_metric(
        raw["request"],
        raw["response"],
        metric_kind=raw["metric_kind"],
        source_url="https://trustyai.example/metrics/drift/kstest",
        scope=SCOPE,
        observed_at=NOW,
    )


def evaluate(item, *, scope: Scope = SCOPE):
    policy = TrustyAIServiceEvidencePolicy(
        expected_producer_prefix="https://trustyai.example"
    )
    return asyncio.run(policy.evaluate([item], scope))


def test_policy_admits_pinned_authenticated_compute_evidence() -> None:
    result = evaluate(evidence())
    assert result.allowed is True
    assert "pinned authenticated metric-compute contract" in result.reason


def test_policy_rejects_tampered_semantic_status() -> None:
    item = evidence()
    tampered = item.model_copy(
        update={
            "measurement": item.measurement.model_copy(
                update={"status": MeasurementStatus.PASSED}
            )
        }
    )
    result = evaluate(tampered)
    assert result.allowed is False
    assert "status does not match" in result.reason


def test_policy_rejects_unexpected_producer_or_scope() -> None:
    item = evidence()
    producer_tampered = item.model_copy(
        update={
            "metadata": item.metadata.model_copy(
                update={
                    "producer": "https://attacker.example/metrics/drift/kstest"
                }
            )
        }
    )
    assert evaluate(producer_tampered).allowed is False

    wrong_scope = Scope(tenant="team-b", namespace="models", environment="staging")
    assert evaluate(item, scope=wrong_scope).allowed is False


def test_runtime_classifier_derives_hard_review_constraint() -> None:
    item = evidence()
    classifier = TrustyAIRuntimeConstraintClassifier()
    constraints = asyncio.run(classifier.classify([item], [], SCOPE))

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.name == TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT
    assert constraint.hard is True
    assert constraint.expression["effect"] == "require-runtime-review"
    assert constraint.expression["metric_family"] == "drift"
    assert constraint.evidence_refs == [item.assurance.digest]


def test_runtime_classifier_emits_nothing_for_passing_evidence() -> None:
    item = evidence()
    passing = item.model_copy(
        update={
            "measurement": item.measurement.model_copy(
                update={
                    "value": 0.5,
                    "status": MeasurementStatus.PASSED,
                }
            )
        }
    )
    constraints = asyncio.run(
        TrustyAIRuntimeConstraintClassifier().classify([passing], [], SCOPE)
    )
    assert constraints == []
