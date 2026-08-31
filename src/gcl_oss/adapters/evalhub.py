"""EvalHub API v1 evidence adapter.

The normalizer is deliberately separate from transport. It converts one terminal
EvalHub job resource into one compact GCL evidence envelope without copying the
complete upstream response into the decision package.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

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

EVALHUB_API_REVISION = "42c09dc6aa0a9f6b1cd1e2bb1b7cacc616dcf13e"
EVALHUB_JOB_SCHEMA_URI = (
    "https://raw.githubusercontent.com/eval-hub/eval-hub/"
    f"{EVALHUB_API_REVISION}/docs/openapi.json"
    "#/components/schemas/EvaluationJobResource"
)
EVALHUB_EXTENSION_NAMESPACE = "io.github.eval-hub"
EVALHUB_JOB_PATH = "/api/v1/evaluations/jobs/{job_id}"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "partially_failed"}
)


class EvalHubAdapterError(ValueError):
    """An EvalHub response cannot be safely normalized."""


class EvalHubJobNotTerminalError(EvalHubAdapterError):
    """An EvalHub job is not yet suitable as terminal evidence."""


class EvalHubTransportError(RuntimeError):
    """The authenticated EvalHub request did not produce a usable response."""


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


class _EvalHubModel(BaseModel):
    # Upstream may add fields without breaking API v1. This adapter validates the
    # security- and governance-relevant subset and ignores additions by design.
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class EvalHubJobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_FAILED = "partially_failed"


class EvalHubResource(_EvalHubModel):
    id: str = Field(min_length=1, max_length=512)
    tenant: str = Field(min_length=1, max_length=256)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def valid_times(self) -> EvalHubResource:
        created = _aware_utc(self.created_at, "resource.created_at")
        updated = _aware_utc(self.updated_at, "resource.updated_at")
        if updated < created:
            raise ValueError("resource.updated_at cannot precede resource.created_at")
        return self


class EvalHubStatus(_EvalHubModel):
    state: EvalHubJobState


class EvalHubTestResult(_EvalHubModel):
    score: float
    threshold: float
    passed: bool = Field(alias="pass")


class EvalHubBenchmarkTest(_EvalHubModel):
    primary_score: float
    threshold: float
    passed: bool = Field(alias="pass")


class EvalHubBenchmarkResult(_EvalHubModel):
    id: str = Field(min_length=1, max_length=512)
    provider_id: str = Field(min_length=1, max_length=512)
    benchmark_index: int = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    test: EvalHubBenchmarkTest | None = None


class EvalHubResults(_EvalHubModel):
    benchmarks: list[EvalHubBenchmarkResult] = Field(default_factory=list)
    test: EvalHubTestResult | None = None

    @model_validator(mode="after")
    def unique_benchmarks(self) -> EvalHubResults:
        identities = [
            (item.provider_id, item.id, item.benchmark_index) for item in self.benchmarks
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("results contain duplicate benchmark identities")
        return self


class EvalHubModelRef(_EvalHubModel):
    name: str = Field(min_length=1, max_length=1024)


class EvalHubCollectionRef(_EvalHubModel):
    id: str = Field(min_length=1, max_length=512)


class EvalHubJobResource(_EvalHubModel):
    resource: EvalHubResource
    status: EvalHubStatus
    results: EvalHubResults | None = None
    name: str = Field(min_length=1, max_length=1024)
    model: EvalHubModelRef
    collection: EvalHubCollectionRef | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvalHubAdapterError("EvalHub response must be finite JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _oci_artifacts(
    benchmarks: Sequence[EvalHubBenchmarkResult],
) -> tuple[list[dict[str, Any]], bool]:
    artifacts: list[dict[str, Any]] = []
    complete = bool(benchmarks)
    for benchmark in sorted(
        benchmarks,
        key=lambda item: (item.benchmark_index, item.provider_id, item.id),
    ):
        reference = benchmark.artifacts.get("oci_reference")
        digest = benchmark.artifacts.get("oci_digest")
        if reference is None and digest is None:
            complete = False
            continue
        if not isinstance(reference, str) or not reference:
            raise EvalHubAdapterError(
                f"benchmark {benchmark.id} has an OCI digest without a reference"
            )
        if len(reference) > 2048 or any(character.isspace() for character in reference):
            raise EvalHubAdapterError(
                f"benchmark {benchmark.id} has an invalid OCI reference"
            )
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise EvalHubAdapterError(
                f"benchmark {benchmark.id} has an invalid OCI digest"
            )
        if not reference.endswith("@" + digest):
            raise EvalHubAdapterError(
                f"benchmark {benchmark.id} OCI reference does not match its digest"
            )
        artifacts.append(
            {
                "benchmark_id": benchmark.id,
                "benchmark_index": benchmark.benchmark_index,
                "provider_id": benchmark.provider_id,
                "digest": digest,
                "reference": reference,
            }
        )
    return artifacts, complete and len(artifacts) == len(benchmarks)


def _benchmark_tests(
    benchmarks: Sequence[EvalHubBenchmarkResult],
) -> list[dict[str, Any]]:
    summaries = []
    for benchmark in sorted(
        benchmarks,
        key=lambda item: (item.benchmark_index, item.provider_id, item.id),
    ):
        summary: dict[str, Any] = {
            "benchmark_id": benchmark.id,
            "benchmark_index": benchmark.benchmark_index,
            "provider_id": benchmark.provider_id,
        }
        if benchmark.test is not None:
            summary["test"] = {
                "primary_score": benchmark.test.primary_score,
                "threshold": benchmark.test.threshold,
                "pass": benchmark.test.passed,
            }
        summaries.append(summary)
    return summaries


def normalize_evalhub_job(
    raw_job: Mapping[str, Any],
    *,
    source_url: str,
    scope: Scope,
    validity: timedelta = timedelta(minutes=15),
    confidence: float = 1.0,
    correlation_id: str | None = None,
    model_version: str | None = None,
) -> EvidenceEnvelope:
    """Normalize one terminal EvalHub API v1 job resource.

    ``confidence`` describes confidence in the authenticated contract mapping; it
    is not statistical confidence in an evaluation metric.
    """

    if validity <= timedelta(0):
        raise EvalHubAdapterError("validity must be positive")
    if not 0.0 <= confidence <= 1.0:
        raise EvalHubAdapterError("confidence must be between 0 and 1")
    parsed_url = urllib.parse.urlsplit(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise EvalHubAdapterError("source_url must be an absolute HTTP(S) URL")
    if (
        parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise EvalHubAdapterError(
            "source_url cannot contain credentials, query, or fragment"
        )

    raw_status = raw_job.get("status")
    raw_state = raw_status.get("state") if isinstance(raw_status, Mapping) else None
    if raw_state in {
        EvalHubJobState.PENDING.value,
        EvalHubJobState.RUNNING.value,
    }:
        raw_resource = raw_job.get("resource")
        raw_id = (
            raw_resource.get("id")
            if isinstance(raw_resource, Mapping)
            else "unknown"
        )
        raise EvalHubJobNotTerminalError(
            f"EvalHub job {raw_id} is not terminal: {raw_state}"
        )

    try:
        job = EvalHubJobResource.model_validate(dict(raw_job))
    except Exception as exc:
        raise EvalHubAdapterError("EvalHub job response is missing required fields") from exc

    state = job.status.state.value
    if state not in _TERMINAL_STATES:
        raise EvalHubJobNotTerminalError(
            f"EvalHub job {job.resource.id} is not terminal: {state}"
        )
    if job.resource.tenant != scope.tenant:
        raise EvalHubAdapterError(
            "EvalHub resource tenant does not match the configured GCL scope"
        )

    results = job.results
    if state in {EvalHubJobState.COMPLETED.value, EvalHubJobState.PARTIALLY_FAILED.value}:
        if results is None:
            raise EvalHubAdapterError(
                f"terminal EvalHub job {job.resource.id} has no results section"
            )

    response_digest = _canonical_digest(dict(raw_job))
    benchmark_results = results.benchmarks if results is not None else []
    oci_artifacts, complete_oci = _oci_artifacts(benchmark_results)
    if complete_oci:
        if len(oci_artifacts) == 1:
            evidence_digest = oci_artifacts[0]["digest"]
            artifact_uri = oci_artifacts[0]["reference"]
        else:
            evidence_digest = _canonical_digest(oci_artifacts)
            artifact_uri = None
        provenance_mode = "oci-manifest"
    else:
        evidence_digest = response_digest
        artifact_uri = None
        provenance_mode = "authenticated-api-response"

    overall_test = results.test if results is not None else None
    if state == EvalHubJobState.FAILED.value:
        measurement_name = "evalhub.job.execution"
        measurement_value: float | str = state
        threshold: float | None = None
        measurement_status = MeasurementStatus.FAILED
        result_kind = "job-execution"
    elif state == EvalHubJobState.CANCELLED.value:
        measurement_name = "evalhub.job.execution"
        measurement_value = state
        threshold = None
        measurement_status = MeasurementStatus.WARNING
        result_kind = "job-execution"
    else:
        measurement_name = (
            "evalhub.collection.compliance"
            if job.collection is not None
            else "evalhub.job.compliance"
        )
        measurement_value = overall_test.score if overall_test is not None else state
        threshold = overall_test.threshold if overall_test is not None else None
        if state == EvalHubJobState.PARTIALLY_FAILED.value:
            measurement_status = MeasurementStatus.WARNING
        elif overall_test is None:
            measurement_status = MeasurementStatus.UNKNOWN
        else:
            measurement_status = (
                MeasurementStatus.PASSED
                if overall_test.passed
                else MeasurementStatus.FAILED
            )
        result_kind = "collection" if job.collection is not None else "job-result"

    observed_at = _aware_utc(job.resource.updated_at, "resource.updated_at")
    created_at = _aware_utc(job.resource.created_at, "resource.created_at")
    window = (
        {"window_start": created_at, "window_end": observed_at}
        if observed_at > created_at
        else {}
    )
    extensions: dict[str, Any] = {
        f"{EVALHUB_EXTENSION_NAMESPACE}/api-revision": EVALHUB_API_REVISION,
        f"{EVALHUB_EXTENSION_NAMESPACE}/benchmark-tests": _benchmark_tests(
            benchmark_results
        ),
        f"{EVALHUB_EXTENSION_NAMESPACE}/job-id": job.resource.id,
        f"{EVALHUB_EXTENSION_NAMESPACE}/job-state": state,
        f"{EVALHUB_EXTENSION_NAMESPACE}/job-test-present": overall_test is not None,
        f"{EVALHUB_EXTENSION_NAMESPACE}/oci-artifacts": oci_artifacts,
        f"{EVALHUB_EXTENSION_NAMESPACE}/provenance-mode": provenance_mode,
        f"{EVALHUB_EXTENSION_NAMESPACE}/raw-response-digest": response_digest,
        f"{EVALHUB_EXTENSION_NAMESPACE}/result-kind": result_kind,
    }
    if job.collection is not None:
        extensions[f"{EVALHUB_EXTENSION_NAMESPACE}/collection-id"] = job.collection.id

    return EvidenceEnvelope(
        metadata=EvidenceMetadata(
            id=f"evalhub:{job.resource.id}",
            correlation_id=(
                correlation_id or f"evalhub:{job.resource.tenant}:{job.resource.id}"
            ),
            causation_id=job.resource.id,
            observed_at=observed_at,
            expires_at=observed_at + validity,
            producer=source_url,
            schema_uri=EVALHUB_JOB_SCHEMA_URI,
        ),
        scope=scope,
        subject=Subject(type="model", id=job.model.name, version=model_version),
        measurement=Measurement(
            name=measurement_name,
            value=measurement_value,
            threshold=threshold,
            unit="score" if overall_test is not None else None,
            status=measurement_status,
            **window,
        ),
        assurance=Assurance(
            confidence=confidence,
            digest=evidence_digest,
            artifact_uri=artifact_uri,
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
        if _origin(newurl) != self._allowed_origin:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "EvalHub redirect changed origin",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class EvalHubHTTPClient:
    """Small authenticated reader for EvalHub's job resource endpoint."""

    def __init__(
        self,
        base_url: str,
        tenant: str,
        *,
        bearer_token: str | None = None,
        timeout: float = 10.0,
        ca_file: Path | str | None = None,
        allow_insecure_http: bool = False,
        max_response_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, query, or fragment")
        if parsed.scheme != "https" and not allow_insecure_http:
            raise ValueError("EvalHub transport requires HTTPS unless explicitly overridden")
        if not tenant or any(character in tenant for character in "\r\n"):
            raise ValueError("tenant must be a non-empty HTTP header value")
        if bearer_token is not None and any(
            character in bearer_token for character in "\r\n"
        ):
            raise ValueError("bearer_token cannot contain a newline")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

        self.tenant = tenant
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        handlers: list[Any] = [_SameOriginRedirectHandler(_origin(self.base_url))]
        if parsed.scheme == "https":
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)

    def job_url(self, job_id: str) -> str:
        if not job_id or any(character in job_id for character in "\r\n"):
            raise ValueError("job_id must be non-empty and cannot contain a newline")
        return self.base_url + EVALHUB_JOB_PATH.format(
            job_id=urllib.parse.quote(job_id, safe="")
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "X-Tenant": self.tenant,
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        request = urllib.request.Request(self.job_url(job_id), headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise EvalHubTransportError(
                            "EvalHub returned an invalid Content-Length"
                        ) from exc
                    if declared_length < 0:
                        raise EvalHubTransportError(
                            "EvalHub returned an invalid Content-Length"
                        )
                    if declared_length > self._max_response_bytes:
                        raise EvalHubTransportError(
                            "EvalHub response exceeds configured limit"
                        )
                body = response.read(self._max_response_bytes + 1)
        except EvalHubTransportError:
            raise
        except urllib.error.HTTPError as exc:
            raise EvalHubTransportError(
                f"EvalHub returned HTTP {exc.code} for the job request"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise EvalHubTransportError("EvalHub job request failed") from exc
        if len(body) > self._max_response_bytes:
            raise EvalHubTransportError("EvalHub response exceeds configured limit")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvalHubTransportError("EvalHub returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise EvalHubTransportError("EvalHub job response must be a JSON object")
        return decoded


class EvalHubEvidenceSource:
    """EvidenceSource that fetches explicit EvalHub job IDs once."""

    def __init__(
        self,
        client: EvalHubHTTPClient,
        job_ids: Sequence[str],
        *,
        scope: Scope,
        validity: timedelta = timedelta(minutes=15),
        confidence: float = 1.0,
        correlation_id: str | None = None,
        model_version: str | None = None,
        run_in_thread: Callable[..., Any] = asyncio.to_thread,
    ) -> None:
        if not job_ids:
            raise ValueError("at least one EvalHub job ID is required")
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("EvalHub job IDs must be unique")
        if client.tenant != scope.tenant:
            raise ValueError("EvalHub client tenant must match the GCL scope tenant")
        self._client = client
        self._job_ids = tuple(job_ids)
        self._scope = scope
        self._validity = validity
        self._confidence = confidence
        self._correlation_id = correlation_id
        self._model_version = model_version
        self._run_in_thread = run_in_thread

    async def receive(self) -> AsyncIterator[EvidenceEnvelope]:
        for job_id in self._job_ids:
            raw = await self._run_in_thread(self._client.get_job, job_id)
            yield normalize_evalhub_job(
                raw,
                source_url=self._client.job_url(job_id),
                scope=self._scope,
                validity=self._validity,
                confidence=self._confidence,
                correlation_id=self._correlation_id,
                model_version=self._model_version,
            )
