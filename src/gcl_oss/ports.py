from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gcl_oss.contracts import (
    Candidate,
    Constraint,
    DecisionPackage,
    EvidenceEnvelope,
    FalsificationResult,
    ObjectiveSpec,
    PolicyResult,
    RejectedAlternative,
    Scope,
    SignedDecisionPackage,
)


class ProposalStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class ProposalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ProposalStatus
    consumer: str = Field(min_length=1, max_length=2048)
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_verified: bool = False
    external_id: str | None = Field(default=None, max_length=1024)
    reason: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def acknowledgement_is_not_execution(self) -> ProposalReceipt:
        if self.execution_verified:
            raise ValueError("proposal acknowledgement cannot verify execution")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: list[Candidate] = Field(min_length=1)
    selected_candidate_id: UUID
    rejected_alternatives: list[RejectedAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def selected_candidate_exists(self) -> Plan:
        candidate_id_list = [candidate.id for candidate in self.candidates]
        if len(candidate_id_list) != len(set(candidate_id_list)):
            raise ValueError("candidate ids must be unique")
        candidate_ids = set(candidate_id_list)
        if self.selected_candidate_id not in candidate_ids:
            raise ValueError("selected_candidate_id must reference a candidate")
        rejected_id_list = [item.candidate.id for item in self.rejected_alternatives]
        if len(rejected_id_list) != len(set(rejected_id_list)):
            raise ValueError("rejected alternative ids must be unique")
        rejected_ids = set(rejected_id_list)
        if self.selected_candidate_id in rejected_ids:
            raise ValueError("selected candidate cannot also be rejected")
        if not rejected_ids.issubset(candidate_ids):
            raise ValueError("rejected alternatives must reference plan candidates")
        if rejected_ids != candidate_ids - {self.selected_candidate_id}:
            raise ValueError("every non-selected candidate requires a rejected alternative")
        candidates_by_id = {candidate.id: candidate for candidate in self.candidates}
        if any(
            item.candidate != candidates_by_id[item.candidate.id]
            for item in self.rejected_alternatives
        ):
            raise ValueError("rejected alternative must match its plan candidate")
        return self


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    observed_at: datetime
    source: str = Field(min_length=1, max_length=2048)
    measurements: dict[str, Any]
    execution_observed: bool

    @model_validator(mode="after")
    def observed_at_is_aware(self) -> OutcomeRecord:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return self


@runtime_checkable
class EvidenceSource(Protocol):
    async def receive(self) -> AsyncIterator[EvidenceEnvelope]: ...


@runtime_checkable
class PolicyCheck(Protocol):
    @property
    def deterministic(self) -> bool: ...

    async def evaluate(
        self,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
    ) -> PolicyResult: ...


@runtime_checkable
class ConstraintClassifier(Protocol):
    @property
    def has_deterministic_fallback(self) -> bool: ...

    async def classify(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Constraint]: ...


@runtime_checkable
class Planner(Protocol):
    @property
    def deterministic(self) -> bool: ...

    async def propose(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        constraints: Sequence[Constraint],
        objective: ObjectiveSpec,
        scope: Scope,
    ) -> Plan | None: ...


@runtime_checkable
class ObjectiveInterpreter(Protocol):
    @property
    def has_deterministic_fallback(self) -> bool: ...

    async def interpret(
        self,
        constraints: Sequence[Constraint],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> ObjectiveSpec: ...


@runtime_checkable
class FalsificationCheck(Protocol):
    @property
    def check_id(self) -> str: ...

    @property
    def deterministic(self) -> bool: ...

    async def challenge(
        self,
        candidate: Candidate,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
        at: datetime,
    ) -> FalsificationResult: ...


@runtime_checkable
class ProposalSink(Protocol):
    async def propose(self, package: SignedDecisionPackage) -> ProposalReceipt: ...


@runtime_checkable
class ProofRecorder(Protocol):
    async def record(
        self,
        event_type: str,
        payload: BaseModel | dict[str, Any],
        correlation_id: str,
    ) -> str: ...


@runtime_checkable
class OutcomeSource(Protocol):
    async def outcomes(self, package: DecisionPackage) -> AsyncIterator[OutcomeRecord]: ...


@runtime_checkable
class Signer(Protocol):
    async def sign(self, payload: bytes, key_id: str) -> bytes: ...


@runtime_checkable
class TelemetrySink(Protocol):
    async def emit(
        self,
        name: str,
        attributes: dict[str, str | int | float | bool],
    ) -> None: ...
