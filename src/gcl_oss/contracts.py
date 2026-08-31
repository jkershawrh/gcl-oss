from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
ACTION_NAMESPACE_PATTERN = r"^[a-z][a-z0-9.-]*/[a-z][a-z0-9._-]*$"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
    digest: str = Field(pattern=DIGEST_PATTERN)
    artifact_uri: str | None = Field(default=None, max_length=2048)


class EvidenceEnvelope(ContractModel):
    api_version: Literal["gcl.io/v1alpha1"] = "gcl.io/v1alpha1"
    kind: Literal["EvidenceEnvelope"] = "EvidenceEnvelope"
    metadata: EvidenceMetadata
    scope: Scope
    subject: Subject
    measurement: Measurement
    assurance: Assurance
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extensions")
    @classmethod
    def namespaced_extensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if "/" not in key:
                raise ValueError("extension keys must use a namespaced form")
        return value

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
    evidence_refs: list[str] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_refs must be unique")
        return value


class FalsificationResult(ContractModel):
    candidate_id: UUID
    check_id: str = Field(min_length=1, max_length=256)
    verdict: FalsificationVerdict
    reasoning: str = Field(min_length=1, max_length=8192)
    evidence_refs: list[str] = Field(default_factory=list)


class RejectedAlternative(ContractModel):
    candidate: Candidate
    reason: str = Field(min_length=1, max_length=8192)


class ProposerIdentity(ContractModel):
    id: str = Field(min_length=1, max_length=512)
    workload_identity: str = Field(min_length=1, max_length=2048)
    trust_domain: str = Field(min_length=1, max_length=512)


class DecisionPackage(ContractModel):
    api_version: Literal["gcl.io/v1alpha1"] = "gcl.io/v1alpha1"
    kind: Literal["DecisionPackage"] = "DecisionPackage"
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime
    correlation_id: str = Field(min_length=1, max_length=512)
    scope: Scope
    proposer: ProposerIdentity
    evidence_refs: list[str] = Field(min_length=1)
    candidates: list[Candidate] = Field(min_length=1)
    selected_candidate_id: UUID
    rejected_alternatives: list[RejectedAlternative] = Field(default_factory=list)
    falsification_results: list[FalsificationResult] = Field(min_length=1)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_package(self) -> DecisionPackage:
        created = _require_aware(self.created_at, "created_at")
        expires = _require_aware(self.expires_at, "expires_at")
        if expires <= created:
            raise ValueError("expires_at must be later than created_at")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")

        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate ids must be unique")
        if self.selected_candidate_id not in candidate_ids:
            raise ValueError("selected_candidate_id must reference a candidate")

        selected_results = [
            result
            for result in self.falsification_results
            if result.candidate_id == self.selected_candidate_id
        ]
        if not selected_results:
            raise ValueError("selected candidate requires a falsification result")
        if any(result.verdict != FalsificationVerdict.SURVIVES for result in selected_results):
            raise ValueError("selected candidate must survive every falsification result")

        rejected_ids = {alternative.candidate.id for alternative in self.rejected_alternatives}
        if self.selected_candidate_id in rejected_ids:
            raise ValueError("selected candidate cannot also be rejected")

        known_evidence = set(self.evidence_refs)
        nested_refs = {
            ref for candidate in self.candidates for ref in candidate.evidence_refs
        } | {
            ref for result in self.falsification_results for ref in result.evidence_refs
        }
        if not nested_refs.issubset(known_evidence):
            raise ValueError("nested evidence reference is absent from evidence_refs")

        for key in self.extensions:
            if "/" not in key:
                raise ValueError("extension keys must use a namespaced form")
        return self

    def is_fresh(self, at: datetime | None = None) -> bool:
        current = _require_aware(at or _utc_now(), "at")
        created = _require_aware(self.created_at, "created_at")
        expires = _require_aware(self.expires_at, "expires_at")
        return created <= current < expires


def canonical_json(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        at: datetime | None = None,
    ) -> bool:
        if len(public_key) != 32:
            return False
        if expected_key_id is not None and self.key_id != expected_key_id:
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
