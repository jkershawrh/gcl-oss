from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from gcl_oss.contracts import (
    DecisionPackage,
    EvidenceEnvelope,
    EvidenceReference,
    FalsificationVerdict,
    ProposerIdentity,
    Scope,
    SignedDecisionPackage,
    canonical_json,
    objective_cost,
    sha256_digest,
)
from gcl_oss.ports import (
    ConstraintClassifier,
    FalsificationCheck,
    ObjectiveInterpreter,
    Planner,
    PolicyCheck,
    ProofRecorder,
    ProposalReceipt,
    ProposalSink,
    Signer,
)
from gcl_oss.registry import ActionRegistry

EVENT_NAMESPACE = "io.github.jkershawrh.gcl"
CYCLE_KEY_EXTENSION = "io.github.jkershawrh.gcl/cycle-key"


class KernelStatus(str, Enum):
    PROPOSED = "proposed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    REJECTED = "rejected"
    NO_CANDIDATE = "no_candidate"


class KernelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KernelStatus
    cycle_key: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    correlation_id: str
    signed_package: SignedDecisionPackage | None = None
    proposal_receipt: ProposalReceipt | None = None
    reasons: tuple[str, ...] = ()
    proof_receipts: tuple[str, ...] = ()
    replayed: bool = False


class InMemoryReplayCache:
    def __init__(self) -> None:
        self._results: dict[str, KernelResult] = {}

    def get(self, cycle_key: str) -> KernelResult | None:
        result = self._results.get(cycle_key)
        if result is None:
            return None
        return result.model_copy(update={"replayed": True})

    def put(self, result: KernelResult) -> None:
        self._results[result.cycle_key] = result.model_copy(update={"replayed": False})


def _evidence_sort_key(item: EvidenceEnvelope) -> tuple[str, ...]:
    return (
        item.metadata.producer,
        item.metadata.id,
        item.assurance.digest,
        sha256_digest(item),
        item.metadata.correlation_id,
    )


def cycle_key_for(evidence: Sequence[EvidenceEnvelope], scope: Scope) -> str:
    items = sorted(_evidence_sort_key(item) for item in evidence)
    payload = {
        "scope": scope.model_dump(mode="json", exclude_none=True),
        "evidence": items,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class GovernanceKernel:
    def __init__(
        self,
        *,
        planner: Planner,
        objective_interpreter: ObjectiveInterpreter,
        constraint_classifiers: Sequence[ConstraintClassifier],
        registry: ActionRegistry,
        falsification_checks: Sequence[FalsificationCheck],
        signer: Signer,
        key_id: str,
        proposer: ProposerIdentity,
        proposal_sink: ProposalSink,
        policy_checks: Sequence[PolicyCheck] = (),
        proof_recorders: Sequence[ProofRecorder] = (),
        replay_cache: InMemoryReplayCache | None = None,
        decision_ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not planner.deterministic:
            raise ValueError("planner must declare deterministic action selection")
        if not objective_interpreter.has_deterministic_fallback:
            raise ValueError("objective interpreter must provide a deterministic fallback")
        if not constraint_classifiers:
            raise ValueError("at least one constraint classifier is required")
        if any(
            not classifier.has_deterministic_fallback
            for classifier in constraint_classifiers
        ):
            raise ValueError("constraint classifiers must provide a deterministic fallback")
        if any(not check.deterministic for check in policy_checks):
            raise ValueError("policy checks must be deterministic")
        if decision_ttl <= timedelta(0):
            raise ValueError("decision_ttl must be positive")
        check_ids = [check.check_id for check in falsification_checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("falsification check ids must be unique")
        if any(not check.deterministic for check in falsification_checks):
            raise ValueError("falsification checks must be deterministic")
        self._planner = planner
        self._objective_interpreter = objective_interpreter
        self._constraint_classifiers = tuple(constraint_classifiers)
        self._registry = registry
        self._falsification_checks = {check.check_id: check for check in falsification_checks}
        self._signer = signer
        self._key_id = key_id
        self._proposer = proposer
        self._proposal_sink = proposal_sink
        self._policy_checks = tuple(policy_checks)
        self._proof_recorders = tuple(proof_recorders)
        self._replay_cache = (
            replay_cache if replay_cache is not None else InMemoryReplayCache()
        )
        self._decision_ttl = decision_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

    async def _record(
        self,
        event_type: str,
        payload: BaseModel | dict,
        correlation_id: str,
    ) -> list[str]:
        receipts = []
        for recorder in self._proof_recorders:
            receipts.append(await recorder.record(event_type, payload, correlation_id))
        return receipts

    async def _reject(
        self,
        *,
        status: KernelStatus,
        cycle_key: str,
        correlation_id: str,
        reasons: Sequence[str],
        proof_receipts: Sequence[str],
    ) -> KernelResult:
        recorded = await self._record(
            f"{EVENT_NAMESPACE}.decision.rejected.v1alpha1",
            {
                "status": status.value,
                "cycle_key": cycle_key,
                "reasons": list(reasons),
            },
            correlation_id,
        )
        result = KernelResult(
            status=status,
            cycle_key=cycle_key,
            correlation_id=correlation_id,
            reasons=tuple(reasons),
            proof_receipts=tuple([*proof_receipts, *recorded]),
        )
        self._replay_cache.put(result)
        return result

    async def run(
        self,
        evidence: Sequence[EvidenceEnvelope],
        *,
        scope: Scope,
    ) -> KernelResult:
        # The built-in replay cache is process-local. Serializing each kernel
        # instance prevents concurrent retries from delivering the same
        # proposal twice. Distributed hosts must provide equivalent durable
        # idempotency before running multiple kernel instances.
        loop = asyncio.get_running_loop()
        lock = self._run_locks.setdefault(loop, asyncio.Lock())
        async with lock:
            return await self._run_once(evidence, scope=scope)

    async def _run_once(
        self,
        evidence: Sequence[EvidenceEnvelope],
        *,
        scope: Scope,
    ) -> KernelResult:
        if not evidence:
            raise ValueError("at least one evidence envelope is required")
        evidence = tuple(sorted(evidence, key=_evidence_sort_key))

        key = cycle_key_for(evidence, scope)
        replay = self._replay_cache.get(key)
        if replay is not None:
            return replay

        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("kernel clock must return a timezone-aware datetime")
        now = now.astimezone(timezone.utc)

        correlation_ids = {item.metadata.correlation_id for item in evidence}
        if len(correlation_ids) != 1:
            raise ValueError("all evidence in a cycle must share one correlation_id")
        correlation_id = next(iter(correlation_ids))
        evidence_identities = [
            (item.metadata.producer, item.metadata.id) for item in evidence
        ]
        if len(evidence_identities) != len(set(evidence_identities)):
            raise ValueError("duplicate producer and evidence id in one cycle")

        proof_receipts: list[str] = []
        for item in evidence:
            if item.scope != scope:
                reason = "evidence scope does not match the requested cycle scope"
                proof_receipts.extend(
                    await self._record(
                        f"{EVENT_NAMESPACE}.evidence.rejected.v1alpha1",
                        {"reason": reason, "evidence_id": item.metadata.id},
                        correlation_id,
                    )
                )
                return await self._reject(
                    status=KernelStatus.REJECTED,
                    cycle_key=key,
                    correlation_id=correlation_id,
                    reasons=[reason],
                    proof_receipts=proof_receipts,
                )
            if not item.is_fresh(now):
                reason = f"evidence is outside its validity window: {item.metadata.id}"
                proof_receipts.extend(
                    await self._record(
                        f"{EVENT_NAMESPACE}.evidence.rejected.v1alpha1",
                        {"reason": reason, "evidence_id": item.metadata.id},
                        correlation_id,
                    )
                )
                return await self._reject(
                    status=KernelStatus.REJECTED,
                    cycle_key=key,
                    correlation_id=correlation_id,
                    reasons=[reason],
                    proof_receipts=proof_receipts,
                )
            proof_receipts.extend(
                await self._record(
                    f"{EVENT_NAMESPACE}.evidence.accepted.v1alpha1",
                    item,
                    correlation_id,
                )
            )

        known_evidence = {item.assurance.digest for item in evidence}
        policy_results = []
        for check in self._policy_checks:
            result = await check.evaluate(evidence, scope)
            if not set(result.evidence_refs).issubset(known_evidence):
                raise ValueError("policy result references evidence outside the cycle")
            policy_results.append(result)
            proof_receipts.extend(
                await self._record(
                    f"{EVENT_NAMESPACE}.policy.evaluated.v1alpha1",
                    result,
                    correlation_id,
                )
            )
        denied = [result for result in policy_results if not result.allowed]
        if denied:
            reasons = [f"{result.check_id}: {result.reason}" for result in denied]
            return await self._reject(
                status=KernelStatus.REJECTED,
                cycle_key=key,
                correlation_id=correlation_id,
                reasons=reasons,
                proof_receipts=proof_receipts,
            )

        policy_ids = [result.check_id for result in policy_results]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("policy checks returned duplicate check ids")

        constraints = []
        for classifier in self._constraint_classifiers:
            classified = await classifier.classify(evidence, policy_results, scope)
            for constraint in classified:
                if not set(constraint.evidence_refs).issubset(known_evidence):
                    raise ValueError("constraint references evidence outside the cycle")
                constraints.append(constraint)
        constraint_ids = [constraint.id for constraint in constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint classifiers returned duplicate constraint ids")
        if not constraints:
            return await self._reject(
                status=KernelStatus.NO_CANDIDATE,
                cycle_key=key,
                correlation_id=correlation_id,
                reasons=["no evidence-derived constraints were produced"],
                proof_receipts=proof_receipts,
            )
        constraints.sort(key=lambda constraint: (constraint.name, str(constraint.id)))
        for constraint in constraints:
            proof_receipts.extend(
                await self._record(
                    f"{EVENT_NAMESPACE}.constraint.classified.v1alpha1",
                    constraint,
                    correlation_id,
                )
            )
        constraint_ids = [constraint.id for constraint in constraints]

        objective = await self._objective_interpreter.interpret(
            constraints,
            policy_results,
            scope,
        )
        if not set(objective.evidence_refs).issubset(known_evidence):
            raise ValueError("objective references evidence outside the cycle")
        if set(objective.constraint_ids) != set(constraint_ids):
            raise ValueError("objective must reference every cycle constraint")
        proof_receipts.extend(
            await self._record(
                f"{EVENT_NAMESPACE}.objective.interpreted.v1alpha1",
                objective,
                correlation_id,
            )
        )

        plan = await self._planner.propose(
            evidence,
            policy_results,
            constraints,
            objective,
            scope,
        )
        if plan is None:
            return await self._reject(
                status=KernelStatus.NO_CANDIDATE,
                cycle_key=key,
                correlation_id=correlation_id,
                reasons=["planner produced no candidate"],
                proof_receipts=proof_receipts,
            )

        definitions = {}
        for candidate in plan.candidates:
            if not set(candidate.evidence_refs).issubset(known_evidence):
                raise ValueError("candidate references evidence outside the cycle")
            if not set(candidate.constraint_refs).issubset(constraint_ids):
                raise ValueError("candidate references a constraint outside the cycle")
            definitions[candidate.id] = self._registry.validate(candidate)

        selected = next(
            candidate
            for candidate in plan.candidates
            if candidate.id == plan.selected_candidate_id
        )
        candidate_costs = {
            candidate.id: objective_cost(objective, candidate)
            for candidate in plan.candidates
        }
        if candidate_costs[selected.id] > min(candidate_costs.values()):
            raise ValueError("planner selected a candidate that does not minimize the objective")
        definition = definitions[selected.id]
        hard_constraint_ids = {
            constraint.id for constraint in constraints if constraint.hard
        }
        if not hard_constraint_ids.issubset(selected.constraint_refs):
            raise ValueError("selected candidate does not cover every hard constraint")
        missing_checks = [
            check_id
            for check_id in definition.required_falsification_checks
            if check_id not in self._falsification_checks
        ]
        if missing_checks:
            raise ValueError(
                f"required falsification checks are not configured for {selected.action}: "
                + ", ".join(missing_checks)
            )

        falsification_results = []
        for check_id in definition.required_falsification_checks:
            result = await self._falsification_checks[check_id].challenge(
                selected,
                evidence,
                scope,
                now,
            )
            if result.candidate_id != selected.id:
                raise ValueError(f"falsification check {check_id} returned the wrong candidate id")
            if result.check_id != check_id:
                raise ValueError(f"falsification check {check_id} returned the wrong check id")
            if not set(result.evidence_refs).issubset(known_evidence):
                raise ValueError(
                    f"falsification check {check_id} references evidence outside the cycle"
                )
            falsification_results.append(result)
            proof_receipts.extend(
                await self._record(
                    f"{EVENT_NAMESPACE}.falsification.completed.v1alpha1",
                    result,
                    correlation_id,
                )
            )

        failed = [
            result
            for result in falsification_results
            if result.verdict == FalsificationVerdict.FAILS
        ]
        if failed:
            reasons = [f"{result.check_id}: {result.reasoning}" for result in failed]
            return await self._reject(
                status=KernelStatus.REJECTED,
                cycle_key=key,
                correlation_id=correlation_id,
                reasons=reasons,
                proof_receipts=proof_receipts,
            )

        package_expires_at = min(
            now + self._decision_ttl,
            *(item.metadata.expires_at.astimezone(timezone.utc) for item in evidence),
        )
        package = DecisionPackage(
            created_at=now,
            expires_at=package_expires_at,
            correlation_id=correlation_id,
            scope=scope,
            proposer=self._proposer,
            evidence=[
                EvidenceReference(
                    id=item.metadata.id,
                    producer=item.metadata.producer,
                    schema_uri=item.metadata.schema_uri,
                    artifact_digest=item.assurance.digest,
                    envelope_digest=sha256_digest(item),
                    artifact_uri=item.assurance.artifact_uri,
                )
                for item in evidence
            ],
            evidence_refs=list(dict.fromkeys(item.assurance.digest for item in evidence)),
            policy_results=policy_results,
            constraints=constraints,
            objective=objective,
            candidates=list(plan.candidates),
            selected_candidate_id=selected.id,
            rejected_alternatives=list(plan.rejected_alternatives),
            falsification_results=falsification_results,
            extensions={CYCLE_KEY_EXTENSION: key},
        )
        signature = await self._signer.sign(canonical_json(package), self._key_id)
        signed = SignedDecisionPackage.from_signature(package, signature, self._key_id)
        try:
            receipt = await self._proposal_sink.propose(signed.model_copy(deep=True))
        except Exception as exc:
            reason = f"proposal delivery outcome is unknown: {type(exc).__name__}"
            result = KernelResult(
                status=KernelStatus.DELIVERY_UNKNOWN,
                cycle_key=key,
                correlation_id=correlation_id,
                signed_package=signed,
                reasons=(reason,),
                proof_receipts=tuple(proof_receipts),
            )
            self._replay_cache.put(result)
            try:
                delivery_receipts = await self._record(
                    f"{EVENT_NAMESPACE}.decision.delivery_unknown.v1alpha1",
                    {"package_digest": signed.digest, "reason": reason},
                    correlation_id,
                )
            except Exception:
                return result
            result = result.model_copy(
                update={"proof_receipts": tuple(proof_receipts + delivery_receipts)}
            )
            self._replay_cache.put(result)
            return result
        if receipt.package_digest != signed.digest:
            reason = "proposal receipt digest does not match the signed package"
            result = KernelResult(
                status=KernelStatus.DELIVERY_UNKNOWN,
                cycle_key=key,
                correlation_id=correlation_id,
                signed_package=signed,
                proposal_receipt=receipt,
                reasons=(reason,),
                proof_receipts=tuple(proof_receipts),
            )
            self._replay_cache.put(result)
            try:
                delivery_receipts = await self._record(
                    f"{EVENT_NAMESPACE}.decision.delivery_unknown.v1alpha1",
                    {
                        "package_digest": signed.digest,
                        "proposal_receipt": receipt.model_dump(mode="json"),
                        "reason": reason,
                    },
                    correlation_id,
                )
            except Exception:
                return result
            result = result.model_copy(
                update={"proof_receipts": tuple(proof_receipts + delivery_receipts)}
            )
            self._replay_cache.put(result)
            return result
        result = KernelResult(
            status=KernelStatus.PROPOSED,
            cycle_key=key,
            correlation_id=correlation_id,
            signed_package=signed,
            proposal_receipt=receipt,
            proof_receipts=tuple(proof_receipts),
        )
        # Cache the successful delivery before attempting the final proof write.
        # This ensures a local retry does not deliver the proposal again if the
        # recorder fails after the external sink has already acknowledged it.
        self._replay_cache.put(result)
        try:
            decision_receipts = await self._record(
                f"{EVENT_NAMESPACE}.decision.proposed.v1alpha1",
                {
                    "signed_package": signed.model_dump(mode="json"),
                    "proposal_receipt": receipt.model_dump(mode="json"),
                },
                correlation_id,
            )
        except Exception as exc:
            result = result.model_copy(
                update={
                    "reasons": (
                        f"proof recording failed after proposal delivery: {type(exc).__name__}",
                    )
                }
            )
            self._replay_cache.put(result)
            return result
        result = result.model_copy(
            update={"proof_receipts": tuple(proof_receipts + decision_receipts)}
        )
        self._replay_cache.put(result)
        return result
