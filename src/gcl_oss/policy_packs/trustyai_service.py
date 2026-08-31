"""Deterministic evidence policy and runtime constraints for TrustyAI Service."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from gcl_oss.adapters.trustyai_service import (
    TRUSTYAI_METRIC_CONTRACTS,
    TRUSTYAI_SERVICE_API_REVISION,
    TRUSTYAI_SERVICE_EXTENSION_NAMESPACE,
    TrustyAIMetricKind,
    metric_contract,
)
from gcl_oss.contracts import (
    Constraint,
    ConstraintSource,
    EvidenceEnvelope,
    MeasurementStatus,
    PolicyResult,
    Scope,
)

TRUSTYAI_SERVICE_POLICY_PACK_URI = (
    "https://github.com/jkershawrh/gcl-oss/"
    "tree/main/src/gcl_oss/policy_packs/trustyai_service.py"
)
TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT = (
    "io.github.jkershawrh.gcl.trustyai/runtime-review-required"
)

_EXT_API_REVISION = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/api-revision"
_EXT_BOUNDS = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/bounds"
_EXT_ENDPOINT = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/endpoint"
_EXT_METRIC_FAMILY = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/metric-family"
_EXT_METRIC_KIND = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/metric-kind"
_EXT_MODEL_ID = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/model-id"
_EXT_PROVENANCE_MODE = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/provenance-mode"
_EXT_REQUEST_DIGEST = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/request-digest"
_EXT_RESPONSE_DIGEST = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/response-digest"
_EXT_STATISTIC = f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/statistic"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTRACTS_BY_SCHEMA = {
    contract.schema_uri: contract for contract in TRUSTYAI_METRIC_CONTRACTS.values()
}


def _url_is_at_or_below(value: str, prefix: str) -> bool:
    candidate = urllib.parse.urlsplit(value)
    expected = urllib.parse.urlsplit(prefix.rstrip("/") + "/")
    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
        return False
    expected_path = expected.path.rstrip("/")
    return candidate.path == expected_path or candidate.path.startswith(
        expected_path + "/"
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _contract_problem(item: EvidenceEnvelope) -> str | None:
    contract = _CONTRACTS_BY_SCHEMA.get(item.metadata.schema_uri)
    if contract is None:
        return "unsupported TrustyAI metric schema"
    if item.extensions.get(_EXT_API_REVISION) != TRUSTYAI_SERVICE_API_REVISION:
        return "unpinned TrustyAI API revision"
    if item.extensions.get(_EXT_METRIC_KIND) != contract.kind.value:
        return "TrustyAI metric kind does not match schema"
    if item.extensions.get(_EXT_METRIC_FAMILY) != contract.family:
        return "TrustyAI metric family does not match schema"
    if item.extensions.get(_EXT_ENDPOINT) != contract.endpoint:
        return "TrustyAI endpoint does not match schema"
    producer = urllib.parse.urlsplit(item.metadata.producer)
    if producer.path.rstrip("/") != contract.endpoint:
        return "TrustyAI producer path does not match schema"
    if item.extensions.get(_EXT_MODEL_ID) != item.subject.id:
        return "TrustyAI model identity mismatch"
    if item.extensions.get(_EXT_PROVENANCE_MODE) != "authenticated-compute-response":
        return "unsupported TrustyAI provenance mode"
    if item.assurance.artifact_uri is not None:
        return "TrustyAI compute response cannot claim immutable artifact provenance"
    if not _DIGEST_RE.fullmatch(item.assurance.digest):
        return "invalid TrustyAI exchange digest"
    for key in (_EXT_REQUEST_DIGEST, _EXT_RESPONSE_DIGEST):
        digest = item.extensions.get(key)
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            return "invalid TrustyAI request or response digest"
    if item.measurement.name != contract.measurement_name:
        return "TrustyAI measurement name does not match schema"
    if item.measurement.unit != contract.unit:
        return "TrustyAI measurement unit does not match schema"
    if item.measurement.status not in {
        MeasurementStatus.PASSED,
        MeasurementStatus.FAILED,
    }:
        return "TrustyAI metric verdict must be passed or failed"

    value = _number(item.measurement.value)
    if value is None:
        return "TrustyAI measurement value is not numeric"
    if contract.response_shape == "p-value":
        threshold = _number(item.measurement.threshold)
        if threshold is None or not 0.0 < threshold < 1.0:
            return "TrustyAI p-value threshold is invalid"
        failed = value < threshold
        statistic = _number(item.extensions.get(_EXT_STATISTIC))
        if statistic is None:
            return "TrustyAI drift statistic is missing"
    elif contract.response_shape == "threshold":
        threshold = _number(item.measurement.threshold)
        if threshold is None:
            return "TrustyAI drift threshold is invalid"
        failed = value > threshold
    else:
        if item.measurement.threshold is not None:
            return "TrustyAI fairness range cannot be flattened to one threshold"
        bounds = item.extensions.get(_EXT_BOUNDS)
        if not isinstance(bounds, dict):
            return "TrustyAI fairness bounds are missing"
        lower = _number(bounds.get("lower"))
        upper = _number(bounds.get("upper"))
        if lower is None or upper is None or upper < lower:
            return "TrustyAI fairness bounds are invalid"
        failed = value < lower or value > upper
    expected_status = MeasurementStatus.FAILED if failed else MeasurementStatus.PASSED
    if item.measurement.status != expected_status:
        return "TrustyAI measurement status does not match metric semantics"
    return None


class TrustyAIServiceEvidencePolicy:
    """Admit pinned authenticated TrustyAI metric-compute evidence."""

    def __init__(
        self,
        *,
        expected_producer_prefix: str,
        minimum_confidence: float = 1.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(expected_producer_prefix)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("expected_producer_prefix must be an absolute HTTP(S) URL")
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self._expected_producer_prefix = expected_producer_prefix
        self._minimum_confidence = minimum_confidence

    @property
    def deterministic(self) -> bool:
        return True

    async def evaluate(
        self,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
    ) -> PolicyResult:
        reasons: list[str] = []
        for item in evidence:
            if not _url_is_at_or_below(
                item.metadata.producer,
                self._expected_producer_prefix,
            ):
                reasons.append(f"{item.metadata.id}: unexpected producer")
            if item.scope != scope:
                reasons.append(f"{item.metadata.id}: scope mismatch")
            problem = _contract_problem(item)
            if problem:
                reasons.append(f"{item.metadata.id}: {problem}")
            if item.assurance.confidence < self._minimum_confidence:
                reasons.append(
                    f"{item.metadata.id}: confidence is below the policy floor"
                )
        return PolicyResult(
            check_id="trustyai-service-evidence-v1alpha1",
            allowed=not reasons,
            reason=(
                "all TrustyAI Service evidence satisfies the pinned authenticated "
                "metric-compute contract"
                if not reasons
                else "; ".join(reasons)
            ),
            evidence_refs=[item.assurance.digest for item in evidence],
        )


class TrustyAIRuntimeConstraintClassifier:
    """Derive runtime-review constraints from concerning TrustyAI metrics."""

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
            if item.metadata.schema_uri not in _CONTRACTS_BY_SCHEMA:
                continue
            if item.measurement.status != MeasurementStatus.FAILED:
                continue
            kind = TrustyAIMetricKind(item.extensions[_EXT_METRIC_KIND])
            contract = metric_contract(kind)
            expression = {
                "effect": "require-runtime-review",
                "metric_family": contract.family,
                "metric_kind": contract.kind.value,
                "model_id": item.subject.id,
                "status": item.measurement.status.value,
            }
            identity = json.dumps(
                {
                    "constraint": TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT,
                    "scope": scope.model_dump(mode="json", exclude_none=True),
                    "subject": item.subject.model_dump(mode="json", exclude_none=True),
                    "expression": expression,
                    "evidence_ref": item.assurance.digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            constraints.append(
                Constraint(
                    id=uuid5(NAMESPACE_URL, identity),
                    name=TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT,
                    hard=True,
                    expression=expression,
                    confidence=item.assurance.confidence,
                    source=ConstraintSource.DETERMINISTIC,
                    rationale=(
                        "TrustyAI reported runtime drift or a fairness result outside "
                        "its configured bounds; independent review is required."
                    ),
                    evidence_refs=[item.assurance.digest],
                    extensions={
                        "io.github.jkershawrh.gcl.trustyai/policy-pack": (
                            TRUSTYAI_SERVICE_POLICY_PACK_URI
                        )
                    },
                )
            )
        return constraints
