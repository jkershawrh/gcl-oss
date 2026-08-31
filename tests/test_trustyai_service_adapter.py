from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from importlib import resources

import pytest

from gcl_oss.adapters.trustyai_service import (
    TRUSTYAI_SERVICE_API_REVISION,
    TrustyAIMetricKind,
    TrustyAIServiceAdapterError,
    TrustyAIServiceEvidenceSource,
    TrustyAIServiceHTTPClient,
    TrustyAIServiceTransportError,
    _SameOriginRedirectHandler,
    metric_contract,
    normalize_trustyai_metric,
)
from gcl_oss.contracts import MeasurementStatus, Scope
from gcl_oss.ports import EvidenceSource

SCOPE = Scope(tenant="team-a", namespace="models", environment="staging")
NOW = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
BASE_URL = "https://trustyai.example"


def fixture() -> dict:
    path = resources.files("gcl_oss.data").joinpath("trustyai-kstest-drift.json")
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def normalized(payload: dict | None = None):
    raw = payload or fixture()
    return normalize_trustyai_metric(
        raw["request"],
        raw["response"],
        metric_kind=raw["metric_kind"],
        source_url=BASE_URL + "/metrics/drift/kstest",
        scope=SCOPE,
        observed_at=NOW,
        model_version="v7",
    )


def test_normalizes_authenticated_kstest_drift_without_copying_raw_payload() -> None:
    item = normalized()

    assert item.metadata.id.startswith("trustyai:drift-kstest:")
    assert item.metadata.observed_at == NOW
    assert item.metadata.expires_at - item.metadata.observed_at == timedelta(minutes=15)
    assert item.subject.id == "fraud-detector"
    assert item.subject.version == "v7"
    assert item.measurement.name == "trustyai.drift.kstest.p_value"
    assert item.measurement.value == 0.001
    assert item.measurement.threshold == 0.05
    assert item.measurement.status == MeasurementStatus.FAILED
    assert item.assurance.artifact_uri is None
    assert item.extensions["org.trustyai.service/api-revision"] == (
        TRUSTYAI_SERVICE_API_REVISION
    )
    assert item.extensions["org.trustyai.service/statistic"] == 0.42
    compact = json.dumps(item.model_dump(mode="json"))
    assert "transaction_amount" not in compact
    assert "baseline" not in compact


def test_normalizes_fairness_range_without_flattening_it_to_one_threshold() -> None:
    item = normalize_trustyai_metric(
        {
            "modelId": "loan-model",
            "protectedAttribute": "group",
            "outcomeName": "approved",
            "privilegedAttribute": "a",
            "unprivilegedAttribute": "b",
            "favorableOutcome": 1,
        },
        {
            "name": "SPD",
            "value": -0.24,
            "type": "metric",
            "specificDefinition": "omitted by GCL",
            "thresholds": {
                "lowerBound": -0.1,
                "upperBound": 0.1,
                "outsideBounds": True,
            },
        },
        metric_kind=TrustyAIMetricKind.FAIRNESS_SPD,
        source_url=BASE_URL + "/metrics/group/fairness/spd",
        scope=SCOPE,
        observed_at=NOW,
    )

    assert item.measurement.value == -0.24
    assert item.measurement.threshold is None
    assert item.measurement.status == MeasurementStatus.FAILED
    assert item.extensions["org.trustyai.service/bounds"] == {
        "lower": -0.1,
        "upper": 0.1,
    }


def test_jensen_shannon_uses_the_requested_decision_statistic() -> None:
    item = normalize_trustyai_metric(
        {"modelId": "model", "statistic": "divergence"},
        {
            "status": "success",
            "value": 0.4,
            "drift_detected": True,
            "Jensen-Shannon_distance": 0.4,
            "Jensen-Shannon_divergence": 0.16,
            "threshold": 0.1,
            "statistic": "divergence",
            "method": "kde",
        },
        metric_kind=TrustyAIMetricKind.DRIFT_JENSEN_SHANNON,
        source_url=BASE_URL + "/metrics/drift/jensenshannon",
        scope=SCOPE,
        observed_at=NOW,
    )

    assert item.measurement.value == 0.16
    assert item.measurement.unit == "jensen_shannon"
    assert item.measurement.status == MeasurementStatus.FAILED


@pytest.mark.parametrize(
    ("kind", "raw_request", "raw_response", "expected_name", "expected_status"),
    [
        (
            TrustyAIMetricKind.DRIFT_KSTEST,
            {"modelId": "model"},
            {
                "status": "success",
                "value": 0.1,
                "drift_detected": False,
                "p_value": 0.5,
                "alpha": 0.05,
            },
            "trustyai.drift.kstest.p_value",
            MeasurementStatus.PASSED,
        ),
        (
            TrustyAIMetricKind.DRIFT_COMPARE_MEANS,
            {"modelId": "model"},
            {
                "status": "success",
                "value": 3.0,
                "drift_detected": True,
                "p_value": 0.01,
                "alpha": 0.05,
            },
            "trustyai.drift.comparemeans.p_value",
            MeasurementStatus.FAILED,
        ),
        (
            TrustyAIMetricKind.DRIFT_JENSEN_SHANNON,
            {"modelId": "model", "statistic": "distance"},
            {
                "status": "success",
                "value": 0.2,
                "drift_detected": False,
                "Jensen-Shannon_distance": 0.2,
                "Jensen-Shannon_divergence": 0.04,
                "threshold": 0.3,
                "statistic": "distance",
            },
            "trustyai.drift.jensenshannon",
            MeasurementStatus.PASSED,
        ),
        (
            TrustyAIMetricKind.DRIFT_MMD,
            {"modelId": "model"},
            {
                "status": "success",
                "value": 0.3,
                "drift_detected": True,
                "threshold": 0.2,
            },
            "trustyai.drift.mmd",
            MeasurementStatus.FAILED,
        ),
        (
            TrustyAIMetricKind.FAIRNESS_SPD,
            {"modelId": "model"},
            {
                "name": "SPD",
                "value": 0.0,
                "type": "metric",
                "thresholds": {
                    "lowerBound": -0.1,
                    "upperBound": 0.1,
                    "outsideBounds": False,
                },
            },
            "trustyai.fairness.spd",
            MeasurementStatus.PASSED,
        ),
        (
            TrustyAIMetricKind.FAIRNESS_DIR,
            {"modelId": "model"},
            {
                "name": "DIR",
                "value": 0.7,
                "type": "metric",
                "thresholds": {
                    "lowerBound": 0.8,
                    "upperBound": 1.2,
                    "outsideBounds": True,
                },
            },
            "trustyai.fairness.dir",
            MeasurementStatus.FAILED,
        ),
    ],
)
def test_normalizes_every_supported_compute_contract(
    kind: TrustyAIMetricKind,
    raw_request: dict,
    raw_response: dict,
    expected_name: str,
    expected_status: MeasurementStatus,
) -> None:
    contract = metric_contract(kind)
    item = normalize_trustyai_metric(
        raw_request,
        raw_response,
        metric_kind=kind,
        source_url=BASE_URL + contract.endpoint,
        scope=SCOPE,
        observed_at=NOW,
    )

    assert item.measurement.name == expected_name
    assert item.measurement.status == expected_status
    assert item.metadata.schema_uri.endswith(".py")


def test_rejects_response_verdict_that_disagrees_with_metric_semantics() -> None:
    raw = fixture()
    raw["response"]["drift_detected"] = False
    with pytest.raises(TrustyAIServiceAdapterError, match="p-value drift"):
        normalized(raw)


def test_rejects_wrong_source_path_or_request_metric_name() -> None:
    raw = fixture()
    with pytest.raises(TrustyAIServiceAdapterError, match="path"):
        normalize_trustyai_metric(
            raw["request"],
            raw["response"],
            metric_kind=raw["metric_kind"],
            source_url=BASE_URL + "/metrics/group/fairness/spd",
            scope=SCOPE,
            observed_at=NOW,
        )

    raw["request"]["metricName"] = "SPD"
    with pytest.raises(TrustyAIServiceAdapterError, match="metricName"):
        normalized(raw)


def test_rejects_ambiguous_model_identity() -> None:
    raw = fixture()
    raw["request"]["modelId"] = " fraud-detector"
    with pytest.raises(TrustyAIServiceAdapterError, match="modelId"):
        normalized(raw)


def test_rejects_non_finite_payload_before_digesting() -> None:
    raw = fixture()
    raw["response"]["p_value"] = float("nan")
    with pytest.raises(TrustyAIServiceAdapterError, match="finite JSON"):
        normalized(raw)


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        content_type: str = "application/json",
        content_length: str | None = None,
    ) -> None:
        self._payload = payload
        self.headers = {"Content-Type": content_type}
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


def test_http_client_posts_bounded_json_with_file_sourced_bearer() -> None:
    raw = fixture()
    client = TrustyAIServiceHTTPClient(BASE_URL, bearer_token="projected-token")
    opener = FakeOpener(FakeResponse(json.dumps(raw["response"]).encode()))
    client._opener = opener

    assert client.compute(raw["metric_kind"], raw["request"])["drift_detected"] is True
    assert opener.request is not None
    assert opener.request.method == "POST"
    assert opener.request.full_url.endswith("/metrics/drift/kstest")
    assert opener.request.get_header("Authorization") == "Bearer projected-token"
    assert opener.request.get_header("Content-type") == "application/json"
    assert json.loads(opener.request.data) == raw["request"]
    assert opener.timeout == 10.0


def test_http_client_requires_tls_and_json_with_bounded_response() -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        TrustyAIServiceHTTPClient("http://localhost:8081")

    client = TrustyAIServiceHTTPClient(BASE_URL, max_response_bytes=10)
    client._opener = FakeOpener(
        FakeResponse(b"{}", content_length="11")
    )
    with pytest.raises(TrustyAIServiceTransportError, match="exceeds"):
        client.compute(TrustyAIMetricKind.DRIFT_KSTEST, {"modelId": "model"})

    client._opener = FakeOpener(FakeResponse(b"{}", content_type="text/plain"))
    with pytest.raises(TrustyAIServiceTransportError, match="application/json"):
        client.compute(TrustyAIMetricKind.DRIFT_KSTEST, {"modelId": "model"})


def test_redirect_handler_allows_only_same_origin_method_preserving_redirects() -> None:
    handler = _SameOriginRedirectHandler(("https", "trustyai.example", None))
    request = urllib.request.Request(
        BASE_URL + "/metrics/drift/kstest",
        data=b"{}",
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError, match="preserve POST"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            BASE_URL + "/other",
        )
    with pytest.raises(urllib.error.HTTPError, match="changed origin"):
        handler.redirect_request(
            request,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://attacker.example/other",
        )

class FakeClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = []

    def metric_url(self, kind) -> str:
        return BASE_URL + "/metrics/drift/kstest"

    def compute(self, kind, request) -> dict:
        self.calls.append((kind, request))
        return self.response


def test_evidence_source_implements_port_without_inheritance() -> None:
    raw = fixture()
    client = FakeClient(raw["response"])
    source = TrustyAIServiceEvidenceSource(
        client,
        [(raw["metric_kind"], raw["request"])],
        scope=SCOPE,
        clock=lambda: NOW,
        run_in_thread=lambda function, *args: asyncio.sleep(
            0,
            result=function(*args),
        ),
    )

    async def receive():
        return [item async for item in source.receive()]

    items = asyncio.run(receive())
    assert isinstance(source, EvidenceSource)
    assert len(items) == 1
    assert items[0].measurement.status == MeasurementStatus.FAILED
    assert len(client.calls) == 1
