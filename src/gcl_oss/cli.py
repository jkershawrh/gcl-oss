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

from gcl_oss.adapters.evalhub import normalize_evalhub_job
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
    sink = NoOpProposalSink()
    proof = MemoryProofRecorder()
    key_id = "evalhub-demo-ephemeral"
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
        proposer=ProposerIdentity(
            id="gcl-oss-evalhub-demo",
            workload_identity="spiffe://example.org/ns/local/sa/gcl-evalhub-demo",
            trust_domain="example.org",
        ),
        proposal_sink=sink,
        policy_checks=[
            EvalHubEvidencePolicy(expected_producer_prefix=source_base_url)
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
    raise AssertionError(f"unhandled command: {args.command}")
