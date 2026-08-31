from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from importlib import resources

import pytest

from gcl_oss.adapters.evalhub import (
    EVALHUB_API_REVISION,
    EvalHubAdapterError,
    EvalHubEvidenceSource,
    EvalHubHTTPClient,
    EvalHubJobNotTerminalError,
    EvalHubTransportError,
    normalize_evalhub_job,
)
from gcl_oss.contracts import MeasurementStatus, Scope

SCOPE = Scope(tenant="team-a", namespace="models", environment="staging")
SOURCE_URL = (
    "https://evalhub.example/api/v1/evaluations/jobs/"
    "a1b2c3d4-5678-9abc-def0-1234567890ab"
)


def fixture() -> dict:
    path = resources.files("gcl_oss.data").joinpath(
        "evalhub-job-failed-safety.json"
    )
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def normalized(raw: dict | None = None):
    return normalize_evalhub_job(
        raw or fixture(),
        source_url=SOURCE_URL,
        scope=SCOPE,
        model_version="v7",
    )


def test_normalizes_failed_collection_with_digest_pinned_oci_provenance() -> None:
    item = normalized()

    assert item.metadata.id == "evalhub:a1b2c3d4-5678-9abc-def0-1234567890ab"
    assert item.scope == SCOPE
    assert item.subject.id == "fraud-detector"
    assert item.subject.version == "v7"
    assert item.measurement.name == "evalhub.collection.compliance"
    assert item.measurement.status == MeasurementStatus.FAILED
    assert item.measurement.value == 0.62
    assert item.measurement.threshold == 0.9
    assert item.assurance.digest == "sha256:" + "b" * 64
    assert item.assurance.artifact_uri is not None
    assert item.assurance.artifact_uri.endswith("@" + item.assurance.digest)
    assert (
        item.extensions["io.github.eval-hub/provenance-mode"]
        == "oci-manifest"
    )
    assert item.extensions["io.github.eval-hub/api-revision"] == EVALHUB_API_REVISION
    compact_extensions = json.dumps(item.extensions)
    assert "model-risk@example.com" not in compact_extensions
    assert "https://model.example/v1" not in compact_extensions


def test_rejects_non_terminal_job() -> None:
    raw = fixture()
    raw["status"]["state"] = "running"
    del raw["resource"]["updated_at"]
    with pytest.raises(EvalHubJobNotTerminalError, match="not terminal"):
        normalized(raw)


def test_rejects_cross_tenant_job() -> None:
    raw = fixture()
    raw["resource"]["tenant"] = "team-b"
    with pytest.raises(EvalHubAdapterError, match="tenant"):
        normalized(raw)


def test_rejects_source_url_that_could_leak_credentials() -> None:
    with pytest.raises(EvalHubAdapterError, match="credentials"):
        normalize_evalhub_job(
            fixture(),
            source_url="https://user:secret@evalhub.example/api/v1/jobs/42",
            scope=SCOPE,
        )


def test_rejects_completed_job_without_results() -> None:
    raw = fixture()
    del raw["results"]
    with pytest.raises(EvalHubAdapterError, match="no results"):
        normalized(raw)


def test_rejects_mismatched_oci_reference_and_digest() -> None:
    raw = fixture()
    raw["results"]["benchmarks"][0]["artifacts"]["oci_digest"] = (
        "sha256:" + "c" * 64
    )
    with pytest.raises(EvalHubAdapterError, match="does not match"):
        normalized(raw)


def test_missing_oci_artifact_falls_back_to_bound_api_response() -> None:
    raw = fixture()
    raw["results"]["benchmarks"][0]["artifacts"] = {}
    item = normalized(raw)

    assert (
        item.extensions["io.github.eval-hub/provenance-mode"]
        == "authenticated-api-response"
    )
    assert item.assurance.artifact_uri is None
    assert item.assurance.digest == item.extensions[
        "io.github.eval-hub/raw-response-digest"
    ]


def test_multiple_oci_artifacts_use_a_bound_manifest_without_false_single_uri() -> None:
    raw = fixture()
    second = deepcopy(raw["results"]["benchmarks"][0])
    second["id"] = "toxicity"
    second["provider_id"] = "lm_evaluation_harness"
    second["benchmark_index"] = 1
    second["artifacts"] = {
        "oci_reference": (
            "quay.io/example/evalhub-results@sha256:" + "c" * 64
        ),
        "oci_digest": "sha256:" + "c" * 64,
    }
    raw["results"]["benchmarks"].append(second)
    item = normalized(raw)

    assert item.assurance.artifact_uri is None
    assert item.assurance.digest not in {"sha256:" + "b" * 64, "sha256:" + "c" * 64}
    assert len(item.extensions["io.github.eval-hub/oci-artifacts"]) == 2


def test_partially_failed_job_cannot_be_reported_as_passing_evidence() -> None:
    raw = fixture()
    raw["status"]["state"] = "partially_failed"
    raw["results"]["test"]["pass"] = True
    item = normalized(raw)
    assert item.measurement.status == MeasurementStatus.WARNING


def test_cancelled_job_is_distinct_from_a_failed_model_test() -> None:
    raw = fixture()
    raw["status"]["state"] = "cancelled"
    raw.pop("results")
    item = normalized(raw)

    assert item.measurement.name == "evalhub.job.execution"
    assert item.measurement.value == "cancelled"
    assert item.measurement.status == MeasurementStatus.WARNING
    assert item.extensions["io.github.eval-hub/result-kind"] == "job-execution"


def test_validity_is_derived_from_the_observation_time() -> None:
    item = normalize_evalhub_job(
        fixture(),
        source_url=SOURCE_URL,
        scope=SCOPE,
        validity=timedelta(minutes=7),
    )
    assert item.metadata.expires_at - item.metadata.observed_at == timedelta(minutes=7)


class FakeResponse:
    def __init__(self, payload: bytes, content_length: str | None = None) -> None:
        self._payload = payload
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit: int) -> bytes:
        return self._payload[:limit]


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def test_http_client_sends_bearer_and_tenant_without_sdk_dependency() -> None:
    client = EvalHubHTTPClient(
        "https://evalhub.example",
        "team-a",
        bearer_token="secret-token",
    )
    opener = FakeOpener(FakeResponse(json.dumps(fixture()).encode()))
    client._opener = opener

    assert client.get_job("job/with slash")["resource"]["tenant"] == "team-a"
    assert opener.request is not None
    assert opener.request.full_url.endswith("/job%2Fwith%20slash")
    assert opener.request.get_header("X-tenant") == "team-a"
    assert opener.request.get_header("Authorization") == "Bearer secret-token"
    assert opener.timeout == 10.0


def test_http_client_requires_explicit_opt_in_for_plain_http() -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        EvalHubHTTPClient("http://localhost:8080", "team-a")
    client = EvalHubHTTPClient(
        "http://localhost:8080",
        "team-a",
        allow_insecure_http=True,
    )
    assert client.job_url("42").endswith("/api/v1/evaluations/jobs/42")


def test_http_client_limits_response_size_before_json_parsing() -> None:
    client = EvalHubHTTPClient(
        "https://evalhub.example",
        "team-a",
        max_response_bytes=10,
    )
    client._opener = FakeOpener(FakeResponse(b"{}", content_length="11"))
    with pytest.raises(EvalHubTransportError, match="exceeds"):
        client.get_job("42")


def test_http_client_rejects_invalid_declared_response_size() -> None:
    client = EvalHubHTTPClient("https://evalhub.example", "team-a")
    client._opener = FakeOpener(FakeResponse(b"{}", content_length="not-a-number"))
    with pytest.raises(EvalHubTransportError, match="invalid Content-Length"):
        client.get_job("42")


class FakeClient:
    tenant = "team-a"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def job_url(self, job_id: str) -> str:
        return "https://evalhub.example/api/v1/evaluations/jobs/" + job_id

    def get_job(self, job_id: str) -> dict:
        self.calls.append(job_id)
        return deepcopy(self.payload)


def test_evidence_source_fetches_each_explicit_job_once() -> None:
    client = FakeClient(fixture())
    source = EvalHubEvidenceSource(client, ["job-1"], scope=SCOPE)

    async def collect():
        return [item async for item in source.receive()]

    items = asyncio.run(collect())
    assert len(items) == 1
    assert client.calls == ["job-1"]
