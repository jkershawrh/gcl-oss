from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
ACTION_NAMESPACE_PATTERN = r"^[a-z][a-z0-9.-]*/[a-z][a-z0-9._-]*$"
EXTENSION_KEY_PATTERN = ACTION_NAMESPACE_PATTERN
Digest = Annotated[str, Field(pattern=DIGEST_PATTERN)]
ExtensionKey = Annotated[str, Field(pattern=EXTENSION_KEY_PATTERN)]
NormalizedCost = Annotated[float, Field(ge=0.0, le=1.0)]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MeasurementStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    UNKNOWN = "unknown"


class Consequence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FalsificationVerdict(str, Enum):
    SURVIVES = "survives"
    FAILS = "fails"


class ConstraintSource(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class ObjectiveMode(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class EvidenceMetadata(ContractModel):
    id: str = Field(min_length=1, max_length=512)
    correlation_id: str = Field(min_length=1, max_length=512)
    causation_id: str | None = Field(default=None, max_length=512)
    observed_at: datetime
    expires_at: datetime
    producer: str = Field(min_length=1, max_length=2048)
    schema_uri: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def valid_window(self) -> EvidenceMetadata:
        observed = _require_aware(self.observed_at, "observed_at")
        expires = _require_aware(self.expires_at, "expires_at")
        if expires <= observed:
            raise ValueError("expires_at must be later than observed_at")
        return self


class Scope(ContractModel):
    tenant: str = Field(min_length=1, max_length=256)
    namespace: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=256)


class Subject(ContractModel):
    type: str = Field(min_length=1, max_length=128)
    id: str = Field(min_length=1, max_length=1024)
    version: str | None = Field(default=None, max_length=256)


class Measurement(ContractModel):
    name: str = Field(min_length=1, max_length=256)
    value: float | int | bool | str
    unit: str | None = Field(default=None, max_length=64)
    threshold: float | int | bool | str | None = None
    status: MeasurementStatus = MeasurementStatus.UNKNOWN
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> Measurement:
        if self.window_start is None and self.window_end is None:
            return self
        if self.window_start is None or self.window_end is None:
            raise ValueError("window_start and window_end must be provided together")
        start = _require_aware(self.window_start, "window_start")
        end = _require_aware(self.window_end, "window_end")
        if end <= start:
            raise ValueError("window_end must be later than window_start")
        return self


class Assurance(ContractModel):
    confidence: float = Field(ge=0.0, le=1.0)
    digest: Digest
    artifact_uri: str | None = Field(default=None, max_length=2048)


class EvidenceReference(ContractModel):
    id: str = Field(min_length=1, max_length=512)
    producer: str = Field(min_length=1, max_length=2048)
    schema_uri: str = Field(min_length=1, max_length=2048)
    artifact_digest: Digest
    envelope_digest: Digest
    artifact_uri: str | None = Field(default=None, max_length=2048)


class PolicyResult(ContractModel):
    check_id: str = Field(min_length=1, max_length=256)
    allowed: bool
    reason: str = Field(min_length=1, max_length=8192)
    evidence_refs: list[Digest] = Field(default_factory=list)

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("policy evidence_refs must be unique")
        return value


class Constraint(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(pattern=ACTION_NAMESPACE_PATTERN)
    hard: bool
    expression: dict[str, Any] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    source: ConstraintSource
    rationale: str = Field(min_length=1, max_length=8192)
    evidence_refs: list[Digest] = Field(min_length=1)
    extensions: dict[ExtensionKey, Any] = Field(default_factory=dict)

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("constraint evidence_refs must be unique")
        return value


class ObjectiveTerm(ContractModel):
    name: str = Field(pattern=ACTION_NAMESPACE_PATTERN)
    weight: float = Field(ge=0.0, le=1.0)
    sense: Literal["minimize"] = "minimize"


class ObjectiveSpec(ContractModel):
    interpreter: str = Field(min_length=1, max_length=2048)
    mode: ObjectiveMode
    terms: list[ObjectiveTerm] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=8192)
    constraint_ids: list[UUID] = Field(min_length=1)
    evidence_refs: list[Digest] = Field(min_length=1)
    extensions: dict[ExtensionKey, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def meaningful_weights(self) -> ObjectiveSpec:
        if not any(term.weight > 0 for term in self.terms):
            raise ValueError("at least one objective term must have a positive weight")
        if not math.isclose(
            sum(term.weight for term in self.terms),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("objective term weights must sum to 1.0")
        if len({term.name for term in self.terms}) != len(self.terms):
            raise ValueError("objective term names must be unique")
        if len(set(self.constraint_ids)) != len(self.constraint_ids):
            raise ValueError("objective constraint_ids must be unique")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("objective evidence_refs must be unique")
        return self


class EvidenceEnvelope(ContractModel):
    api_version: Literal["io.github.jkershawrh.gcl/v1alpha1"] = (
        "io.github.jkershawrh.gcl/v1alpha1"
    )
    kind: Literal["EvidenceEnvelope"] = "EvidenceEnvelope"
    metadata: EvidenceMetadata
    scope: Scope
    subject: Subject
    measurement: Measurement
    assurance: Assurance
    extensions: dict[ExtensionKey, Any] = Field(default_factory=dict)

    def is_fresh(self, at: datetime | None = None) -> bool:
        current = _require_aware(at or _utc_now(), "at")
        observed = _require_aware(self.metadata.observed_at, "observed_at")
        expires = _require_aware(self.metadata.expires_at, "expires_at")
        return observed <= current < expires


class Candidate(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    action: str = Field(pattern=ACTION_NAMESPACE_PATTERN)
    parameters: dict[str, Any] = Field(default_factory=dict)
    consequence: Consequence
    rationale: str = Field(min_length=1, max_length=8192)
    objective_values: dict[ExtensionKey, NormalizedCost] = Field(min_length=1)
    constraint_refs: list[UUID] = Field(min_length=1)
    evidence_refs: list[Digest] = Field(min_length=1)

    @field_validator("constraint_refs", "evidence_refs")
    @classmethod
    def unique_refs(cls, value: list) -> list:
        if len(set(value)) != len(value):
            raise ValueError("candidate references must be unique")
        return value


def objective_cost(objective: ObjectiveSpec, candidate: Candidate) -> float:
    weights = {term.name: term.weight for term in objective.terms}
    if set(candidate.objective_values) != set(weights):
        raise ValueError("candidate must provide a value for every objective term")
    return sum(weights[name] * candidate.objective_values[name] for name in weights)


class FalsificationResult(ContractModel):
    candidate_id: UUID
    check_id: str = Field(min_length=1, max_length=256)
    verdict: FalsificationVerdict
    reasoning: str = Field(min_length=1, max_length=8192)
    evidence_refs: list[Digest] = Field(default_factory=list)

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("falsification evidence_refs must be unique")
        return value


class RejectedAlternative(ContractModel):
    candidate: Candidate
    reason: str = Field(min_length=1, max_length=8192)


class ProposerIdentity(ContractModel):
    id: str = Field(min_length=1, max_length=512)
    workload_identity: str = Field(min_length=1, max_length=2048)
    trust_domain: str = Field(min_length=1, max_length=512)


class DecisionPackage(ContractModel):
    api_version: Literal["io.github.jkershawrh.gcl/v1alpha1"] = (
        "io.github.jkershawrh.gcl/v1alpha1"
    )
    kind: Literal["DecisionPackage"] = "DecisionPackage"
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime
    correlation_id: str = Field(min_length=1, max_length=512)
    scope: Scope
    proposer: ProposerIdentity
    evidence: list[EvidenceReference] = Field(min_length=1)
    evidence_refs: list[Digest] = Field(min_length=1)
    policy_results: list[PolicyResult] = Field(default_factory=list)
    constraints: list[Constraint] = Field(min_length=1)
    objective: ObjectiveSpec
    candidates: list[Candidate] = Field(min_length=1)
    selected_candidate_id: UUID
    rejected_alternatives: list[RejectedAlternative] = Field(default_factory=list)
    falsification_results: list[FalsificationResult] = Field(min_length=1)
    extensions: dict[ExtensionKey, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_package(self) -> DecisionPackage:
        created = _require_aware(self.created_at, "created_at")
        expires = _require_aware(self.expires_at, "expires_at")
        if expires <= created:
            raise ValueError("expires_at must be later than created_at")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        evidence_identities = [(item.producer, item.id) for item in self.evidence]
        if len(evidence_identities) != len(set(evidence_identities)):
            raise ValueError("evidence manifest identities must be unique")
        envelope_digests = [item.envelope_digest for item in self.evidence]
        if len(envelope_digests) != len(set(envelope_digests)):
            raise ValueError("evidence manifest envelope digests must be unique")
        if {item.artifact_digest for item in self.evidence} != set(self.evidence_refs):
            raise ValueError("evidence_refs must match the evidence manifest digests")

        policy_ids = [result.check_id for result in self.policy_results]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("policy check ids must be unique")
        if any(not result.allowed for result in self.policy_results):
            raise ValueError("a decision package cannot contain a denied policy result")

        constraint_ids = [constraint.id for constraint in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint ids must be unique")
        if set(self.objective.constraint_ids) != set(constraint_ids):
            raise ValueError("objective must reference every package constraint")

        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate ids must be unique")
        if self.selected_candidate_id not in candidate_ids:
            raise ValueError("selected_candidate_id must reference a candidate")
        known_constraints = set(constraint_ids)
        if any(
            not set(candidate.constraint_refs).issubset(known_constraints)
            for candidate in self.candidates
        ):
            raise ValueError("candidate references an unknown constraint")
        selected_candidate = next(
            candidate
            for candidate in self.candidates
            if candidate.id == self.selected_candidate_id
        )
        hard_constraints = {
            constraint.id for constraint in self.constraints if constraint.hard
        }
        if not hard_constraints.issubset(selected_candidate.constraint_refs):
            raise ValueError("selected candidate must cover every hard constraint")
        candidate_costs = {
            candidate.id: objective_cost(self.objective, candidate)
            for candidate in self.candidates
        }
        if candidate_costs[self.selected_candidate_id] > min(candidate_costs.values()):
            raise ValueError("selected candidate must minimize the weighted objective")

        falsification_keys = [
            (result.candidate_id, result.check_id) for result in self.falsification_results
        ]
        if len(falsification_keys) != len(set(falsification_keys)):
            raise ValueError("falsification results must be unique per candidate and check")
        if any(
            result.candidate_id not in candidate_ids for result in self.falsification_results
        ):
            raise ValueError("falsification result references an unknown candidate")

        selected_results = [
            result
            for result in self.falsification_results
            if result.candidate_id == self.selected_candidate_id
        ]
        if not selected_results:
            raise ValueError("selected candidate requires a falsification result")
        if any(result.verdict != FalsificationVerdict.SURVIVES for result in selected_results):
            raise ValueError("selected candidate must survive every falsification result")

        rejected_ids = [
            alternative.candidate.id for alternative in self.rejected_alternatives
        ]
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("rejected alternative ids must be unique")
        if self.selected_candidate_id in rejected_ids:
            raise ValueError("selected candidate cannot also be rejected")
        expected_rejected = set(candidate_ids) - {self.selected_candidate_id}
        if set(rejected_ids) != expected_rejected:
            raise ValueError("every non-selected candidate requires a rejected alternative")
        candidates_by_id = {candidate.id: candidate for candidate in self.candidates}
        if any(
            alternative.candidate != candidates_by_id[alternative.candidate.id]
            for alternative in self.rejected_alternatives
        ):
            raise ValueError("rejected alternative must match its plan candidate")

        known_evidence = set(self.evidence_refs)
        nested_refs = {
            ref for result in self.policy_results for ref in result.evidence_refs
        } | {
            ref for constraint in self.constraints for ref in constraint.evidence_refs
        } | set(self.objective.evidence_refs) | {
            ref for candidate in self.candidates for ref in candidate.evidence_refs
        } | {
            ref for result in self.falsification_results for ref in result.evidence_refs
        }
        if not nested_refs.issubset(known_evidence):
            raise ValueError("nested evidence reference is absent from evidence_refs")

        return self

    def is_fresh(self, at: datetime | None = None) -> bool:
        current = _require_aware(at or _utc_now(), "at")
        created = _require_aware(self.created_at, "created_at")
        expires = _require_aware(self.expires_at, "expires_at")
        return created <= current < expires


def canonical_json(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_digest(model: BaseModel) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(model)).hexdigest()


class SignedDecisionPackage(ContractModel):
    package: DecisionPackage
    digest: str = Field(pattern=DIGEST_PATTERN)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def digest_matches(self) -> SignedDecisionPackage:
        if self.digest != sha256_digest(self.package):
            raise ValueError("digest does not match the decision package")
        return self

    @classmethod
    def sign(
        cls,
        package: DecisionPackage,
        private_key_seed: bytes,
        key_id: str,
    ) -> SignedDecisionPackage:
        if len(private_key_seed) != 32:
            raise ValueError("Ed25519 private key seed must be exactly 32 bytes")
        signature = Ed25519PrivateKey.from_private_bytes(private_key_seed).sign(
            canonical_json(package)
        )
        return cls.from_signature(package, signature, key_id)

    @classmethod
    def from_signature(
        cls,
        package: DecisionPackage,
        signature: bytes,
        key_id: str,
    ) -> SignedDecisionPackage:
        if len(signature) != 64:
            raise ValueError("Ed25519 signature must be exactly 64 bytes")
        encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return cls(
            package=package,
            digest=sha256_digest(package),
            signature=encoded,
            key_id=key_id,
        )

    def verify(
        self,
        public_key: bytes,
        *,
        expected_key_id: str | None = None,
        expected_scope: Scope | None = None,
        expected_trust_domain: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        if len(public_key) != 32:
            return False
        if expected_key_id is not None and self.key_id != expected_key_id:
            return False
        if expected_scope is not None and self.package.scope != expected_scope:
            return False
        if (
            expected_trust_domain is not None
            and self.package.proposer.trust_domain != expected_trust_domain
        ):
            return False
        if not self.package.is_fresh(at):
            return False
        signature = base64.urlsafe_b64decode(self.signature + "==")
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                signature,
                canonical_json(self.package),
            )
        except InvalidSignature:
            return False
        return True
