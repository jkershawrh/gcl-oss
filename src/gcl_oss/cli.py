from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import urllib.parse
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

from gcl_oss.adapters.evalhub import EvalHubHTTPClient, normalize_evalhub_job
from gcl_oss.adapters.evalhub_oci import verify_evalhub_oci_artifacts
from gcl_oss.adapters.oci import OCIRegistryVerifier, parse_oci_reference
from gcl_oss.adapters.trustyai_service import (
    TrustyAIMetricKind,
    TrustyAIServiceHTTPClient,
    metric_contract,
    normalize_trustyai_metric,
)
from gcl_oss.builtin import (
    EvidenceFreshnessCheck,
    FailedMeasurementConstraintClassifier,
    MemoryProofRecorder,
    MinimumConfidencePolicy,
    NoOpProposalSink,
    ReviewFailedMeasurementsPlanner,
    RiskReductionObjectiveInterpreter,
    StaticSigner,
    standalone_action_registry,
)
from gcl_oss.contracts import (
    Assurance,
    EvidenceEnvelope,
    EvidenceMetadata,
    Measurement,
    MeasurementStatus,
    ProposerIdentity,
    Scope,
    Subject,
)
from gcl_oss.kernel import GovernanceKernel
from gcl_oss.policy_packs.evalhub import (
    EVALHUB_PROMOTION_CONSTRAINT,
    EvalHubEvidencePolicy,
    EvalHubPromotionConstraintClassifier,
)
from gcl_oss.policy_packs.trustyai_service import (
    TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT,
    TrustyAIRuntimeConstraintClassifier,
    TrustyAIServiceEvidencePolicy,
)
from gcl_oss.ports import ArtifactVerificationRequest
from gcl_oss.schemas import write_schemas


def _demo_evidence(now: datetime) -> EvidenceEnvelope:
    source_payload = b'{"collection":"safety","score":0.62,"threshold":0.90}'
    digest = "sha256:" + hashlib.sha256(source_payload).hexdigest()
    return EvidenceEnvelope(
        metadata=EvidenceMetadata(
            id="demo-evaluation-1",
            correlation_id="demo-cycle-1",
            observed_at=now - timedelta(seconds=5),
            expires_at=now + timedelta(minutes=15),
            producer="https://jkershawrh.github.io/gcl-oss/demo/evaluator",
            schema_uri=(
                "https://jkershawrh.github.io/gcl-oss/examples/evaluation-result/v1"
            ),
        ),
        scope=Scope(tenant="demo", namespace="models", environment="local"),
        subject=Subject(type="model", id="fraud-detector", version="v7"),
        measurement=Measurement(
            name="safety-collection",
            value=0.62,
            threshold=0.90,
            unit="score",
            status=MeasurementStatus.FAILED,
        ),
        assurance=Assurance(confidence=0.98, digest=digest),
        extensions={"io.github.jkershawrh.gcl.demo/source": "standalone-cli"},
    )


async def _run_demo() -> dict:
    now = datetime.now(timezone.utc)
    item = _demo_evidence(now)
    sink = NoOpProposalSink()
    proof = MemoryProofRecorder()
    key_id = "demo-ephemeral"
    signer = StaticSigner(key_id, os.urandom(32))
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[FailedMeasurementConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=signer,
        key_id=key_id,
        proposer=ProposerIdentity(
            id="gcl-oss-demo",
            workload_identity="spiffe://example.org/ns/local/sa/gcl-demo",
            trust_domain="example.org",
        ),
        proposal_sink=sink,
        policy_checks=[MinimumConfidencePolicy(0.8)],
        proof_recorders=[proof],
        clock=lambda: now,
    )
    result = await kernel.run([item], scope=item.scope)
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["proof_entry_count"] = len(proof.entries)
    payload["proposal_delivery_count"] = len(sink.packages)
    return payload


def _load_evalhub_payload(path: Path | None) -> dict:
    if path is not None:
        with path.open(encoding="utf-8") as source:
            payload = json.load(source)
    else:
        fixture = resources.files("gcl_oss.data").joinpath(
            "evalhub-job-failed-safety.json"
        )
        with fixture.open(encoding="utf-8") as source:
            payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("EvalHub input must be a JSON object")
    return payload


def _load_json_object(path: Path, *, label: str) -> dict:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _load_trustyai_demo_payload(path: Path | None) -> dict:
    if path is not None:
        payload = _load_json_object(path, label="TrustyAI demonstration input")
    else:
        fixture = resources.files("gcl_oss.data").joinpath(
            "trustyai-kstest-drift.json"
        )
        with fixture.open(encoding="utf-8") as source:
            payload = json.load(source)
    if not isinstance(payload.get("request"), dict):
        raise ValueError("TrustyAI demonstration input requires a request object")
    if not isinstance(payload.get("response"), dict):
        raise ValueError("TrustyAI demonstration input requires a response object")
    if not isinstance(payload.get("metric_kind"), str):
        raise ValueError("TrustyAI demonstration input requires metric_kind")
    return payload


async def _run_evalhub_demo(
    raw_job: dict,
    *,
    source_base_url: str,
    scope: Scope,
    model_version: str | None,
) -> dict:
    resource_payload = raw_job.get("resource")
    job_id = (
        resource_payload.get("id") if isinstance(resource_payload, dict) else None
    )
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("EvalHub input requires resource.id")
    source_url = (
        source_base_url.rstrip("/")
        + "/api/v1/evaluations/jobs/"
        + urllib.parse.quote(job_id, safe="")
    )
    item = normalize_evalhub_job(
        raw_job,
        source_url=source_url,
        scope=scope,
        model_version=model_version,
    )
    # The command is a reproducible offline replay. Live hosts use their current
    # clock and reject evidence outside the configured validity window.
    now = item.metadata.observed_at + timedelta(seconds=1)
    return await _run_evalhub_cycle(
        item,
        source_base_url=source_base_url,
        now=now,
        key_id="evalhub-demo-ephemeral",
        proposer=ProposerIdentity(
            id="gcl-oss-evalhub-demo",
            workload_identity="spiffe://example.org/ns/local/sa/gcl-evalhub-demo",
            trust_domain="example.org",
        ),
    )


async def _run_evalhub_cycle(
    item: EvidenceEnvelope,
    *,
    source_base_url: str,
    now: datetime,
    key_id: str,
    proposer: ProposerIdentity,
    require_verified_oci_artifacts: bool = False,
) -> dict:
    sink = NoOpProposalSink()
    proof = MemoryProofRecorder()
    signer = StaticSigner(key_id, os.urandom(32))
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(
            constraint_names=(EVALHUB_PROMOTION_CONSTRAINT,)
        ),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[EvalHubPromotionConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=signer,
        key_id=key_id,
        proposer=proposer,
        proposal_sink=sink,
        policy_checks=[
            EvalHubEvidencePolicy(
                expected_producer_prefix=source_base_url,
                require_verified_oci_artifacts=require_verified_oci_artifacts,
            )
        ],
        proof_recorders=[proof],
        clock=lambda: now,
    )
    result = await kernel.run([item], scope=item.scope)
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["normalized_evidence"] = item.model_dump(mode="json", exclude_none=True)
    payload["proof_entry_count"] = len(proof.entries)
    payload["proposal_delivery_count"] = len(sink.packages)
    return payload


async def _run_trustyai_cycle(
    item: EvidenceEnvelope,
    *,
    source_base_url: str,
    now: datetime,
    key_id: str,
    proposer: ProposerIdentity,
) -> dict:
    sink = NoOpProposalSink()
    proof = MemoryProofRecorder()
    signer = StaticSigner(key_id, os.urandom(32))
    kernel = GovernanceKernel(
        planner=ReviewFailedMeasurementsPlanner(
            constraint_names=(TRUSTYAI_RUNTIME_REVIEW_CONSTRAINT,)
        ),
        objective_interpreter=RiskReductionObjectiveInterpreter(),
        constraint_classifiers=[TrustyAIRuntimeConstraintClassifier()],
        registry=standalone_action_registry(),
        falsification_checks=[EvidenceFreshnessCheck()],
        signer=signer,
        key_id=key_id,
        proposer=proposer,
        proposal_sink=sink,
        policy_checks=[
            TrustyAIServiceEvidencePolicy(
                expected_producer_prefix=source_base_url,
            )
        ],
        proof_recorders=[proof],
        clock=lambda: now,
    )
    result = await kernel.run([item], scope=item.scope)
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["normalized_evidence"] = item.model_dump(mode="json", exclude_none=True)
    payload["proof_entry_count"] = len(proof.entries)
    payload["proposal_delivery_count"] = len(sink.packages)
    return payload


async def _run_trustyai_demo(
    raw: dict,
    *,
    source_base_url: str,
    scope: Scope,
    model_version: str | None,
) -> dict:
    contract = metric_contract(raw["metric_kind"])
    observed_at = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    item = normalize_trustyai_metric(
        raw["request"],
        raw["response"],
        metric_kind=contract.kind,
        source_url=source_base_url.rstrip("/") + contract.endpoint,
        scope=scope,
        observed_at=observed_at,
        model_version=model_version,
    )
    return await _run_trustyai_cycle(
        item,
        source_base_url=source_base_url,
        now=observed_at + timedelta(seconds=1),
        key_id="trustyai-demo-ephemeral",
        proposer=ProposerIdentity(
            id="gcl-oss-trustyai-demo",
            workload_identity="spiffe://example.org/ns/local/sa/gcl-trustyai-demo",
            trust_domain="example.org",
        ),
    )


def _read_bearer_token(path: Path | None) -> str | None:
    if path is None:
        return None
    if path.stat().st_size > 64 * 1024:
        raise ValueError("bearer token file exceeds 64 KiB")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("bearer token file is empty")
    if any(character in token for character in "\r\n"):
        raise ValueError("bearer token file contains multiple lines")
    return token


async def _run_evalhub_live(args: argparse.Namespace) -> dict:
    scope = Scope(
        tenant=args.tenant,
        namespace=args.namespace,
        environment=args.environment,
    )
    client = EvalHubHTTPClient(
        args.base_url,
        args.tenant,
        bearer_token=_read_bearer_token(args.token_file),
        timeout=args.timeout,
        ca_file=args.ca_file,
        allow_insecure_http=args.allow_insecure_http,
    )
    raw_job = await asyncio.to_thread(client.get_job, args.job_id)
    item = normalize_evalhub_job(
        raw_job,
        source_url=client.job_url(args.job_id),
        scope=scope,
        validity=timedelta(minutes=args.validity_minutes),
        model_version=args.model_version,
    )
    if args.verify_oci:
        item = await verify_evalhub_oci_artifacts(item, _oci_verifier(args))
    workload_identity = args.workload_identity or (
        f"spiffe://{args.trust_domain}/ns/{scope.namespace}/sa/gcl-oss-qualifier"
    )
    return await _run_evalhub_cycle(
        item,
        source_base_url=client.base_url,
        now=datetime.now(timezone.utc),
        key_id="evalhub-live-ephemeral",
        proposer=ProposerIdentity(
            id="gcl-oss-evalhub-live",
            workload_identity=workload_identity,
            trust_domain=args.trust_domain,
        ),
        require_verified_oci_artifacts=args.verify_oci,
    )


async def _run_trustyai_live(args: argparse.Namespace) -> dict:
    scope = Scope(
        tenant=args.tenant,
        namespace=args.namespace,
        environment=args.environment,
    )
    request_payload = _load_json_object(
        args.request,
        label="TrustyAI metric request",
    )
    metric_kind = TrustyAIMetricKind(args.metric)
    client = TrustyAIServiceHTTPClient(
        args.base_url,
        bearer_token=_read_bearer_token(args.token_file),
        timeout=args.timeout,
        ca_file=args.ca_file,
        allow_insecure_http=args.allow_insecure_http,
        max_request_bytes=args.max_request_bytes,
        max_response_bytes=args.max_response_bytes,
    )
    raw_response = await asyncio.to_thread(
        client.compute,
        metric_kind,
        request_payload,
    )
    observed_at = datetime.now(timezone.utc)
    item = normalize_trustyai_metric(
        request_payload,
        raw_response,
        metric_kind=metric_kind,
        source_url=client.metric_url(metric_kind),
        scope=scope,
        observed_at=observed_at,
        validity=timedelta(minutes=args.validity_minutes),
        model_version=args.model_version,
    )
    workload_identity = args.workload_identity or (
        f"spiffe://{args.trust_domain}/ns/{scope.namespace}/sa/gcl-oss-qualifier"
    )
    return await _run_trustyai_cycle(
        item,
        source_base_url=client.base_url,
        now=datetime.now(timezone.utc),
        key_id="trustyai-live-ephemeral",
        proposer=ProposerIdentity(
            id="gcl-oss-trustyai-live",
            workload_identity=workload_identity,
            trust_domain=args.trust_domain,
        ),
    )


def _add_registry_arguments(
    parser: argparse.ArgumentParser,
    *,
    allow_required: bool,
) -> None:
    parser.add_argument(
        "--registry-allow",
        action="append",
        default=[],
        required=allow_required,
        metavar="HOST[:PORT]",
        help="exact registry authority allowed for verification; repeatable",
    )
    parser.add_argument(
        "--registry-auth-file",
        type=Path,
        help="Docker config JSON containing registry credentials",
    )
    parser.add_argument(
        "--registry-auth-host-allow",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="additional exact token-service authority; repeatable",
    )
    parser.add_argument("--registry-username")
    parser.add_argument(
        "--registry-password-file",
        type=Path,
        help="file containing the registry password or projected token",
    )
    parser.add_argument("--registry-ca-file", type=Path)
    parser.add_argument("--registry-timeout", type=float, default=10.0)
    parser.add_argument(
        "--registry-max-manifest-bytes",
        type=int,
        default=4 * 1024 * 1024,
    )
    parser.add_argument(
        "--registry-max-blob-bytes",
        type=int,
        default=32 * 1024 * 1024,
    )
    parser.add_argument(
        "--registry-max-total-bytes",
        type=int,
        default=64 * 1024 * 1024,
    )
    parser.add_argument("--allow-insecure-registry", action="store_true")


def _oci_verifier(args: argparse.Namespace) -> OCIRegistryVerifier:
    return OCIRegistryVerifier(
        allowed_registries=args.registry_allow,
        allowed_auth_hosts=args.registry_auth_host_allow,
        auth_file=args.registry_auth_file,
        username=args.registry_username,
        password_file=args.registry_password_file,
        ca_file=args.registry_ca_file,
        timeout=args.registry_timeout,
        max_manifest_bytes=args.registry_max_manifest_bytes,
        max_blob_bytes=args.registry_max_blob_bytes,
        max_total_bytes=args.registry_max_total_bytes,
        allow_insecure_http=args.allow_insecure_registry,
    )


async def _run_oci_verify(args: argparse.Namespace) -> dict:
    reference = parse_oci_reference(args.reference)
    receipt = await _oci_verifier(args).verify(
        ArtifactVerificationRequest(
            artifact_uri=args.reference,
            expected_digest=reference.digest,
        )
    )
    return receipt.model_dump(mode="json", exclude_none=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gcl-oss",
        description="GCL OSS contract and standalone governance tools",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="run the offline proposal-only demonstration")
    evalhub_demo = subcommands.add_parser(
        "evalhub-demo",
        help="normalize a terminal EvalHub job and run a signed offline governance cycle",
    )
    evalhub_demo.add_argument(
        "--input",
        type=Path,
        help="EvalHub job resource JSON; defaults to the packaged failed-safety fixture",
    )
    evalhub_demo.add_argument(
        "--source-base-url",
        default="https://evalhub.example",
    )
    evalhub_demo.add_argument("--tenant", default="team-a")
    evalhub_demo.add_argument("--namespace", default="models")
    evalhub_demo.add_argument("--environment", default="staging")
    evalhub_demo.add_argument("--model-version", default="v7")
    trustyai_demo = subcommands.add_parser(
        "trustyai-demo",
        help="normalize a TrustyAI metric response and run a signed offline cycle",
    )
    trustyai_demo.add_argument(
        "--input",
        type=Path,
        help="request/response fixture; defaults to packaged KS drift evidence",
    )
    trustyai_demo.add_argument(
        "--source-base-url",
        default="https://trustyai.example",
    )
    trustyai_demo.add_argument("--tenant", default="team-a")
    trustyai_demo.add_argument("--namespace", default="models")
    trustyai_demo.add_argument("--environment", default="staging")
    trustyai_demo.add_argument("--model-version", default="v7")
    evalhub_live = subcommands.add_parser(
        "evalhub-live",
        help="fetch one terminal EvalHub job and run a signed proposal-only cycle",
    )
    evalhub_live.add_argument("--base-url", required=True)
    evalhub_live.add_argument("--job-id", required=True)
    evalhub_live.add_argument("--tenant", required=True)
    evalhub_live.add_argument("--namespace", required=True)
    evalhub_live.add_argument("--environment", required=True)
    evalhub_live.add_argument("--model-version")
    evalhub_live.add_argument("--token-file", type=Path)
    evalhub_live.add_argument("--ca-file", type=Path)
    evalhub_live.add_argument("--timeout", type=float, default=10.0)
    evalhub_live.add_argument("--validity-minutes", type=int, default=15)
    evalhub_live.add_argument("--allow-insecure-http", action="store_true")
    evalhub_live.add_argument("--trust-domain", default="cluster.local")
    evalhub_live.add_argument("--workload-identity")
    evalhub_live.add_argument(
        "--verify-oci",
        action="store_true",
        help="fetch and verify every OCI manifest and descriptor before policy admission",
    )
    _add_registry_arguments(evalhub_live, allow_required=False)
    trustyai_live = subcommands.add_parser(
        "trustyai-live",
        help="compute one TrustyAI metric and run a signed proposal-only cycle",
    )
    trustyai_live.add_argument("--base-url", required=True)
    trustyai_live.add_argument(
        "--metric",
        required=True,
        choices=[kind.value for kind in TrustyAIMetricKind],
    )
    trustyai_live.add_argument("--request", required=True, type=Path)
    trustyai_live.add_argument("--tenant", required=True)
    trustyai_live.add_argument("--namespace", required=True)
    trustyai_live.add_argument("--environment", required=True)
    trustyai_live.add_argument("--model-version")
    trustyai_live.add_argument("--token-file", type=Path)
    trustyai_live.add_argument("--ca-file", type=Path)
    trustyai_live.add_argument("--timeout", type=float, default=10.0)
    trustyai_live.add_argument("--validity-minutes", type=int, default=15)
    trustyai_live.add_argument("--max-request-bytes", type=int, default=1024 * 1024)
    trustyai_live.add_argument("--max-response-bytes", type=int, default=1024 * 1024)
    trustyai_live.add_argument("--allow-insecure-http", action="store_true")
    trustyai_live.add_argument("--trust-domain", default="cluster.local")
    trustyai_live.add_argument("--workload-identity")
    oci_verify = subcommands.add_parser(
        "oci-verify",
        help="verify a digest-pinned OCI manifest and all config/layer content",
    )
    oci_verify.add_argument("--reference", required=True)
    _add_registry_arguments(oci_verify, allow_required=True)
    schemas = subcommands.add_parser("schemas", help="write the versioned JSON Schemas")
    schemas.add_argument("--output", type=Path, default=Path("schemas"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "schemas":
        paths = write_schemas(args.output)
        print(json.dumps({"written": [str(path) for path in paths]}, indent=2))
        return 0
    if args.command == "demo":
        print(json.dumps(asyncio.run(_run_demo()), indent=2, sort_keys=True))
        return 0
    if args.command == "evalhub-demo":
        raw_job = _load_evalhub_payload(args.input)
        scope = Scope(
            tenant=args.tenant,
            namespace=args.namespace,
            environment=args.environment,
        )
        payload = asyncio.run(
            _run_evalhub_demo(
                raw_job,
                source_base_url=args.source_base_url,
                scope=scope,
                model_version=args.model_version,
            )
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "trustyai-demo":
        raw = _load_trustyai_demo_payload(args.input)
        scope = Scope(
            tenant=args.tenant,
            namespace=args.namespace,
            environment=args.environment,
        )
        payload = asyncio.run(
            _run_trustyai_demo(
                raw,
                source_base_url=args.source_base_url,
                scope=scope,
                model_version=args.model_version,
            )
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "evalhub-live":
        payload = asyncio.run(_run_evalhub_live(args))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "trustyai-live":
        payload = asyncio.run(_run_trustyai_live(args))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "oci-verify":
        payload = asyncio.run(_run_oci_verify(args))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
