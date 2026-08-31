from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gcl_oss.contracts import (
    Candidate,
    DecisionPackage,
    EvidenceEnvelope,
    FalsificationResult,
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


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1, max_length=256)
    allowed: bool
    reason: str = Field(min_length=1, max_length=8192)
    evidence_refs: list[str] = Field(default_factory=list)


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    observed_at: datetime
    source: str = Field(min_length=1, max_length=2048)
    measurements: dict[str, Any]
    execution_observed: bool


@runtime_checkable
class EvidenceSource(Protocol):
    async def receive(self) -> AsyncIterator[EvidenceEnvelope]: ...


@runtime_checkable
class PolicyCheck(Protocol):
    async def evaluate(
        self,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
    ) -> PolicyResult: ...


@runtime_checkable
class Planner(Protocol):
    @property
    def deterministic(self) -> bool: ...

    async def propose(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Candidate]: ...


@runtime_checkable
class FalsificationCheck(Protocol):
    async def challenge(
        self,
        candidate: Candidate,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
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
class KeyProvider(Protocol):
    async def signing_key(self, key_id: str) -> bytes: ...

    async def verification_key(self, key_id: str) -> bytes: ...


@runtime_checkable
class TelemetrySink(Protocol):
    async def emit(
        self,
        name: str,
        attributes: dict[str, str | int | float | bool],
    ) -> None: ...
