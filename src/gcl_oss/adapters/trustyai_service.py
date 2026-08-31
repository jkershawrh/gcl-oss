"""Authenticated TrustyAI Service metric-compute evidence adapter.

The adapter calls only compute endpoints. It never creates or deletes scheduled metric
requests, uploads inference data, or changes a TrustyAIService resource.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gcl_oss.contracts import (
    Assurance,
    EvidenceEnvelope,
    EvidenceMetadata,
    Measurement,
    MeasurementStatus,
    Scope,
    Subject,
)

TRUSTYAI_SERVICE_API_REVISION = "f78ca0e91cc24745fdaacb8f8ae893b059c03a0c"
TRUSTYAI_SERVICE_EXTENSION_NAMESPACE = "org.trustyai.service"
_SOURCE_ROOT = (
    "https://raw.githubusercontent.com/trustyai-explainability/trustyai-service/"
    f"{TRUSTYAI_SERVICE_API_REVISION}/src/trustyai_service/endpoints/metrics"
)


class TrustyAIServiceAdapterError(ValueError):
    """A TrustyAI Service metric response cannot be safely normalized."""


class TrustyAIServiceTransportError(RuntimeError):
    """An authenticated TrustyAI Service request did not produce usable JSON."""


class TrustyAIMetricKind(str, Enum):
    DRIFT_KSTEST = "drift-kstest"
    DRIFT_COMPARE_MEANS = "drift-comparemeans"
    DRIFT_JENSEN_SHANNON = "drift-jensenshannon"
    DRIFT_MMD = "drift-mmd"
    FAIRNESS_SPD = "fairness-spd"
    FAIRNESS_DIR = "fairness-dir"


@dataclass(frozen=True)
class TrustyAIMetricContract:
    kind: TrustyAIMetricKind
    endpoint: str
    family: Literal["drift", "fairness"]
    schema_uri: str
    response_shape: Literal["p-value", "threshold", "range"]
    measurement_name: str
    unit: str


TRUSTYAI_METRIC_CONTRACTS: dict[TrustyAIMetricKind, TrustyAIMetricContract] = {
    TrustyAIMetricKind.DRIFT_KSTEST: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.DRIFT_KSTEST,
        endpoint="/metrics/drift/kstest",
        family="drift",
        schema_uri=f"{_SOURCE_ROOT}/drift/kolmogorov_smirnov.py",
        response_shape="p-value",
        measurement_name="trustyai.drift.kstest.p_value",
        unit="p_value",
    ),
    TrustyAIMetricKind.DRIFT_COMPARE_MEANS: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.DRIFT_COMPARE_MEANS,
        endpoint="/metrics/drift/comparemeans",
        family="drift",
        schema_uri=f"{_SOURCE_ROOT}/drift/compare_means.py",
        response_shape="p-value",
        measurement_name="trustyai.drift.comparemeans.p_value",
        unit="p_value",
    ),
    TrustyAIMetricKind.DRIFT_JENSEN_SHANNON: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.DRIFT_JENSEN_SHANNON,
        endpoint="/metrics/drift/jensenshannon",
        family="drift",
        schema_uri=f"{_SOURCE_ROOT}/drift/jensen_shannon.py",
        response_shape="threshold",
        measurement_name="trustyai.drift.jensenshannon",
        unit="jensen_shannon",
    ),
    TrustyAIMetricKind.DRIFT_MMD: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.DRIFT_MMD,
        endpoint="/metrics/drift/mmd",
        family="drift",
        schema_uri=f"{_SOURCE_ROOT}/drift/mmd.py",
        response_shape="threshold",
        measurement_name="trustyai.drift.mmd",
        unit="statistic",
    ),
    TrustyAIMetricKind.FAIRNESS_SPD: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.FAIRNESS_SPD,
        endpoint="/metrics/group/fairness/spd",
        family="fairness",
        schema_uri=f"{_SOURCE_ROOT}/fairness/group/spd.py",
        response_shape="range",
        measurement_name="trustyai.fairness.spd",
        unit="difference",
    ),
    TrustyAIMetricKind.FAIRNESS_DIR: TrustyAIMetricContract(
        kind=TrustyAIMetricKind.FAIRNESS_DIR,
        endpoint="/metrics/group/fairness/dir",
        family="fairness",
        schema_uri=f"{_SOURCE_ROOT}/fairness/group/dir.py",
        response_shape="range",
        measurement_name="trustyai.fairness.dir",
        unit="ratio",
    ),
}


def metric_contract(kind: TrustyAIMetricKind | str) -> TrustyAIMetricContract:
    try:
        parsed = kind if isinstance(kind, TrustyAIMetricKind) else TrustyAIMetricKind(kind)
    except ValueError as exc:
        raise TrustyAIServiceAdapterError("unsupported TrustyAI metric kind") from exc
    return TRUSTYAI_METRIC_CONTRACTS[parsed]


class _TrustyAIModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )


class _PValueDriftResponse(_TrustyAIModel):
    status: Literal["success"]
    value: float
    drift_detected: bool
    p_value: float = Field(ge=0.0, le=1.0)
    alpha: float = Field(gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def verdict_matches_p_value(self) -> _PValueDriftResponse:
        if self.drift_detected != (self.p_value < self.alpha):
            raise ValueError("drift verdict does not match p-value and alpha")
        return self


class _ThresholdDriftResponse(_TrustyAIModel):
    status: Literal["success"]
    value: float
    drift_detected: bool
    threshold: float

    @model_validator(mode="after")
    def verdict_matches_threshold(self) -> _ThresholdDriftResponse:
        if self.drift_detected != (self.value > self.threshold):
            raise ValueError("drift verdict does not match value and threshold")
        return self


class _JensenShannonResponse(_TrustyAIModel):
    status: Literal["success"]
    value: float
    drift_detected: bool
    threshold: float
    statistic: Literal["distance", "divergence"]
    distance: float = Field(alias="Jensen-Shannon_distance")
    divergence: float = Field(alias="Jensen-Shannon_divergence")

    @model_validator(mode="after")
    def verdict_matches_statistic(self) -> _JensenShannonResponse:
        decision_value = self.distance if self.statistic == "distance" else self.divergence
        if self.drift_detected != (decision_value > self.threshold):
            raise ValueError(
                "Jensen-Shannon verdict does not match selected statistic and threshold"
            )
        return self


class _FairnessThresholds(_TrustyAIModel):
    lower_bound: float = Field(alias="lowerBound")
    upper_bound: float = Field(alias="upperBound")
    outside_bounds: bool = Field(alias="outsideBounds")

    @model_validator(mode="after")
    def valid_bounds(self) -> _FairnessThresholds:
        if self.upper_bound < self.lower_bound:
            raise ValueError("fairness upper bound cannot be below lower bound")
        return self


class _FairnessResponse(_TrustyAIModel):
    name: Literal["SPD", "DIR"]
    value: float
    type: Literal["metric"]
    thresholds: _FairnessThresholds

    @model_validator(mode="after")
    def verdict_matches_bounds(self) -> _FairnessResponse:
        outside = (
            self.value < self.thresholds.lower_bound
            or self.value > self.thresholds.upper_bound
        )
        if self.thresholds.outside_bounds != outside:
            raise ValueError("fairness verdict does not match value and bounds")
        return self


def _canonical_bytes(value: Any, *, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrustyAIServiceAdapterError(f"{label} must be finite JSON") from exc


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_json(value: Any, *, label: str) -> str:
    return _digest_bytes(_canonical_bytes(value, label=label))


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TrustyAIServiceAdapterError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _validated_source_url(source_url: str, contract: TrustyAIMetricContract) -> str:
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TrustyAIServiceAdapterError("source_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TrustyAIServiceAdapterError(
            "source_url cannot contain credentials, query, or fragment"
        )
    if parsed.path.rstrip("/") != contract.endpoint:
        raise TrustyAIServiceAdapterError(
            "source_url path does not match the selected TrustyAI metric"
        )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def normalize_trustyai_metric(
    raw_request: Mapping[str, Any],
    raw_response: Mapping[str, Any],
    *,
    metric_kind: TrustyAIMetricKind | str,
    source_url: str,
    scope: Scope,
    observed_at: datetime,
    validity: timedelta = timedelta(minutes=15),
    confidence: float = 1.0,
    correlation_id: str | None = None,
    model_version: str | None = None,
) -> EvidenceEnvelope:
    """Normalize one authenticated TrustyAI metric-compute response."""

    contract = metric_contract(metric_kind)
    source_url = _validated_source_url(source_url, contract)
    if validity <= timedelta(0):
        raise TrustyAIServiceAdapterError("validity must be positive")
    if not 0.0 <= confidence <= 1.0:
        raise TrustyAIServiceAdapterError("confidence must be between 0 and 1")
    observed_at = _aware_utc(observed_at, "observed_at")
    request_payload = dict(raw_request)
    response_payload = dict(raw_response)
    request_bytes = _canonical_bytes(request_payload, label="TrustyAI request")
    response_bytes = _canonical_bytes(response_payload, label="TrustyAI response")

    model_id = request_payload.get("modelId")
    if (
        not isinstance(model_id, str)
        or not model_id
        or model_id.strip() != model_id
        or len(model_id) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in model_id)
    ):
        raise TrustyAIServiceAdapterError("TrustyAI request requires a valid modelId")

    request_metric_name = request_payload.get("metricName")
    expected_metric_names = {
        TrustyAIMetricKind.DRIFT_KSTEST: {None, "KSTest"},
        TrustyAIMetricKind.DRIFT_COMPARE_MEANS: {None, "CompareMeans"},
        TrustyAIMetricKind.DRIFT_JENSEN_SHANNON: {None, "JensenShannon"},
        TrustyAIMetricKind.DRIFT_MMD: {None, "MMD"},
        TrustyAIMetricKind.FAIRNESS_SPD: {None, "SPD"},
        TrustyAIMetricKind.FAIRNESS_DIR: {None, "DIR"},
    }[contract.kind]
    if request_metric_name not in expected_metric_names:
        raise TrustyAIServiceAdapterError(
            "TrustyAI request metricName does not match the selected metric"
        )

    bounds: dict[str, float] | None = None
    statistic_value: float | None = None
    if contract.response_shape == "p-value":
        try:
            result = _PValueDriftResponse.model_validate(response_payload)
        except Exception as exc:
            raise TrustyAIServiceAdapterError(
                "TrustyAI p-value drift response is invalid"
            ) from exc
        measurement_value = result.p_value
        measurement_threshold: float | None = result.alpha
        measurement_status = (
            MeasurementStatus.FAILED
            if result.drift_detected
            else MeasurementStatus.PASSED
        )
        statistic_value = result.value
    elif contract.response_shape == "threshold":
        try:
            if contract.kind == TrustyAIMetricKind.DRIFT_JENSEN_SHANNON:
                result = _JensenShannonResponse.model_validate(response_payload)
                request_statistic = request_payload.get("statistic", "distance")
                if request_statistic != result.statistic:
                    raise TrustyAIServiceAdapterError(
                        "Jensen-Shannon response statistic does not match request"
                    )
            else:
                result = _ThresholdDriftResponse.model_validate(response_payload)
        except TrustyAIServiceAdapterError:
            raise
        except Exception as exc:
            raise TrustyAIServiceAdapterError(
                "TrustyAI threshold drift response is invalid"
            ) from exc
        measurement_value = (
            result.distance
            if isinstance(result, _JensenShannonResponse)
            and result.statistic == "distance"
            else result.divergence
            if isinstance(result, _JensenShannonResponse)
            else result.value
        )
        measurement_threshold = result.threshold
        measurement_status = (
            MeasurementStatus.FAILED
            if result.drift_detected
            else MeasurementStatus.PASSED
        )
    else:
        try:
            result = _FairnessResponse.model_validate(response_payload)
        except Exception as exc:
            raise TrustyAIServiceAdapterError(
                "TrustyAI fairness response is invalid"
            ) from exc
        expected_name = (
            "SPD" if contract.kind == TrustyAIMetricKind.FAIRNESS_SPD else "DIR"
        )
        if result.name != expected_name:
            raise TrustyAIServiceAdapterError(
                "TrustyAI fairness response name does not match selected metric"
            )
        measurement_value = result.value
        measurement_threshold = None
        measurement_status = (
            MeasurementStatus.FAILED
            if result.thresholds.outside_bounds
            else MeasurementStatus.PASSED
        )
        bounds = {
            "lower": result.thresholds.lower_bound,
            "upper": result.thresholds.upper_bound,
        }

    request_digest = _digest_bytes(request_bytes)
    response_digest = _digest_bytes(response_bytes)
    exchange_digest = _digest_json(
        {
            "api_revision": TRUSTYAI_SERVICE_API_REVISION,
            "metric_kind": contract.kind.value,
            "request": request_payload,
            "response": response_payload,
        },
        label="TrustyAI exchange",
    )
    evidence_suffix = exchange_digest.removeprefix("sha256:")
    extensions: dict[str, Any] = {
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/api-revision": (
            TRUSTYAI_SERVICE_API_REVISION
        ),
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/endpoint": contract.endpoint,
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/metric-family": contract.family,
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/metric-kind": contract.kind.value,
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/model-id": model_id,
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/provenance-mode": (
            "authenticated-compute-response"
        ),
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/request-digest": request_digest,
        f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/response-digest": response_digest,
    }
    if bounds is not None:
        extensions[f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/bounds"] = bounds
    if statistic_value is not None:
        extensions[f"{TRUSTYAI_SERVICE_EXTENSION_NAMESPACE}/statistic"] = (
            statistic_value
        )

    return EvidenceEnvelope(
        metadata=EvidenceMetadata(
            id=f"trustyai:{contract.kind.value}:{evidence_suffix[:32]}",
            correlation_id=(
                correlation_id
                or f"trustyai:{scope.tenant}:{model_id}:{evidence_suffix[:16]}"
            ),
            causation_id=request_digest,
            observed_at=observed_at,
            expires_at=observed_at + validity,
            producer=source_url,
            schema_uri=contract.schema_uri,
        ),
        scope=scope,
        subject=Subject(type="model", id=model_id, version=model_version),
        measurement=Measurement(
            name=contract.measurement_name,
            value=measurement_value,
            threshold=measurement_threshold,
            unit=contract.unit,
            status=measurement_status,
        ),
        assurance=Assurance(
            confidence=confidence,
            digest=exchange_digest,
        ),
        extensions=extensions,
    )


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_origin: tuple[str, str, int | None]) -> None:
        super().__init__()
        self._allowed_origin = allowed_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code not in {307, 308}:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "TrustyAI Service redirect would not preserve POST semantics",
                headers,
                fp,
            )
        if _origin(newurl) != self._allowed_origin:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "TrustyAI Service redirect changed origin",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class TrustyAIServiceHTTPClient:
    """Bounded authenticated client for TrustyAI metric compute endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout: float = 10.0,
        ca_file: Path | str | None = None,
        allow_insecure_http: bool = False,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, query, or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("base_url cannot contain a path")
        if parsed.scheme != "https" and not allow_insecure_http:
            raise ValueError(
                "TrustyAI Service transport requires HTTPS unless explicitly overridden"
            )
        if bearer_token is not None and any(
            character in bearer_token for character in "\r\n\x00"
        ):
            raise ValueError("bearer_token contains invalid characters")
        if bearer_token is not None and len(bearer_token.encode("utf-8")) > 64 * 1024:
            raise ValueError("bearer_token exceeds 64 KiB")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if min(max_request_bytes, max_response_bytes) <= 0:
            raise ValueError("request and response limits must be positive")

        context = ssl.create_default_context()
        if ca_file is not None:
            context.load_verify_locations(cafile=str(ca_file))
        handlers: list[Any] = [_SameOriginRedirectHandler(_origin(self.base_url))]
        if parsed.scheme == "https":
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes

    def metric_url(self, kind: TrustyAIMetricKind | str) -> str:
        return self.base_url + metric_contract(kind).endpoint

    def compute(
        self,
        kind: TrustyAIMetricKind | str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = _canonical_bytes(dict(payload), label="TrustyAI request")
        if len(body) > self._max_request_bytes:
            raise TrustyAIServiceTransportError(
                "TrustyAI Service request exceeds configured limit"
            )
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "User-Agent": "gcl-oss-trustyai-adapter/1",
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        request = urllib.request.Request(
            self.metric_url(kind),
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                content_type = (
                    response.headers.get("Content-Type", "")
                    .partition(";")[0]
                    .strip()
                    .lower()
                )
                if content_type != "application/json":
                    raise TrustyAIServiceTransportError(
                        "TrustyAI Service response is not application/json"
                    )
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise TrustyAIServiceTransportError(
                        "compressed TrustyAI Service responses are not accepted"
                    )
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        length = int(declared_length)
                    except ValueError as exc:
                        raise TrustyAIServiceTransportError(
                            "TrustyAI Service returned an invalid Content-Length"
                        ) from exc
                    if length < 0 or length > self._max_response_bytes:
                        raise TrustyAIServiceTransportError(
                            "TrustyAI Service response exceeds configured limit"
                        )
                response_body = response.read(self._max_response_bytes + 1)
        except TrustyAIServiceTransportError:
            raise
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise TrustyAIServiceTransportError(
                f"TrustyAI Service returned HTTP {code} for metric computation"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise TrustyAIServiceTransportError(
                "TrustyAI Service metric computation failed"
            ) from exc
        if len(response_body) > self._max_response_bytes:
            raise TrustyAIServiceTransportError(
                "TrustyAI Service response exceeds configured limit"
            )
        try:
            decoded = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrustyAIServiceTransportError(
                "TrustyAI Service returned invalid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise TrustyAIServiceTransportError(
                "TrustyAI Service response must be a JSON object"
            )
        return decoded


class TrustyAIServiceEvidenceSource:
    """EvidenceSource that computes an explicit sequence of TrustyAI metrics once."""

    def __init__(
        self,
        client: TrustyAIServiceHTTPClient,
        metric_requests: Sequence[
            tuple[TrustyAIMetricKind | str, Mapping[str, Any]]
        ],
        *,
        scope: Scope,
        validity: timedelta = timedelta(minutes=15),
        confidence: float = 1.0,
        model_version: str | None = None,
        clock: Callable[[], datetime] | None = None,
        run_in_thread: Callable[..., Any] = asyncio.to_thread,
    ) -> None:
        if not metric_requests:
            raise ValueError("at least one TrustyAI metric request is required")
        self._client = client
        self._requests = tuple(
            (metric_contract(kind).kind, dict(payload))
            for kind, payload in metric_requests
        )
        self._scope = scope
        self._validity = validity
        self._confidence = confidence
        self._model_version = model_version
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_in_thread = run_in_thread

    async def receive(self) -> AsyncIterator[EvidenceEnvelope]:
        for kind, request in self._requests:
            response = await self._run_in_thread(self._client.compute, kind, request)
            yield normalize_trustyai_metric(
                request,
                response,
                metric_kind=kind,
                source_url=self._client.metric_url(kind),
                scope=self._scope,
                observed_at=self._clock(),
                validity=self._validity,
                confidence=self._confidence,
                model_version=self._model_version,
            )
