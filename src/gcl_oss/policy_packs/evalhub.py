"""Deterministic policy pack for EvalHub promotion evidence."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from gcl_oss.adapters.evalhub import (
    EVALHUB_API_REVISION,
    EVALHUB_EXTENSION_NAMESPACE,
    EVALHUB_JOB_SCHEMA_URI,
)
from gcl_oss.adapters.oci import OCI_DISTRIBUTION_VERIFIER
from gcl_oss.contracts import (
    Constraint,
    ConstraintSource,
    EvidenceEnvelope,
    MeasurementStatus,
    PolicyResult,
    Scope,
)
from gcl_oss.ports import ArtifactVerificationReceipt

EVALHUB_POLICY_PACK_URI = (
    "https://github.com/jkershawrh/gcl-oss/"
    "tree/main/src/gcl_oss/policy_packs/evalhub.py"
)
EVALHUB_PROMOTION_CONSTRAINT = (
    "io.github.jkershawrh.gcl.evalhub/promotion-review-required"
)
_EXT_API_REVISION = f"{EVALHUB_EXTENSION_NAMESPACE}/api-revision"
_EXT_JOB_ID = f"{EVALHUB_EXTENSION_NAMESPACE}/job-id"
_EXT_JOB_STATE = f"{EVALHUB_EXTENSION_NAMESPACE}/job-state"
_EXT_JOB_TEST_PRESENT = f"{EVALHUB_EXTENSION_NAMESPACE}/job-test-present"
_EXT_OCI_ARTIFACTS = f"{EVALHUB_EXTENSION_NAMESPACE}/oci-artifacts"
_EXT_OCI_VERIFICATIONS = f"{EVALHUB_EXTENSION_NAMESPACE}/oci-verifications"
_EXT_PROVENANCE_MODE = f"{EVALHUB_EXTENSION_NAMESPACE}/provenance-mode"
_EXT_RAW_RESPONSE_DIGEST = f"{EVALHUB_EXTENSION_NAMESPACE}/raw-response-digest"
_EXT_RESULT_KIND = f"{EVALHUB_EXTENSION_NAMESPACE}/result-kind"
_TERMINAL_STATES = {"completed", "failed", "cancelled", "partially_failed"}
_RESULT_KINDS = {"collection", "job-result", "job-execution"}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _url_is_at_or_below(value: str, prefix: str) -> bool:
    candidate = urllib.parse.urlsplit(value)
    expected = urllib.parse.urlsplit(prefix.rstrip("/") + "/")
    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
        return False
    expected_path = expected.path.rstrip("/")
    return candidate.path == expected_path or candidate.path.startswith(expected_path + "/")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _provenance_problem(item: EvidenceEnvelope) -> str | None:
    mode = item.extensions.get(_EXT_PROVENANCE_MODE)
    if mode == "authenticated-api-response":
        response_digest = item.extensions.get(_EXT_RAW_RESPONSE_DIGEST)
        if not isinstance(response_digest, str) or not _DIGEST_RE.fullmatch(
            response_digest
        ):
            return "invalid authenticated API response digest"
        if item.assurance.digest != response_digest or item.assurance.artifact_uri:
            return "API response provenance does not match assurance"
        return None
    if mode != "oci-manifest":
        return "unsupported provenance mode"

    artifacts = item.extensions.get(_EXT_OCI_ARTIFACTS)
    if not isinstance(artifacts, list) or not artifacts:
        return "OCI manifest is empty"
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return "OCI manifest entry is not an object"
        digest = artifact.get("digest")
        reference = artifact.get("reference")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            return "OCI manifest contains an invalid digest"
        if (
            not isinstance(reference, str)
            or not reference.endswith("@" + digest)
            or len(reference) > 2048
            or any(character.isspace() for character in reference)
        ):
            return "OCI manifest contains a mismatched reference"
    if len(artifacts) == 1:
        artifact = artifacts[0]
        if (
            item.assurance.digest != artifact["digest"]
            or item.assurance.artifact_uri != artifact["reference"]
        ):
            return "single OCI artifact does not match assurance"
    elif (
        item.assurance.digest != _canonical_digest(artifacts)
        or item.assurance.artifact_uri is not None
    ):
        return "OCI manifest digest does not match assurance"
    return None


def _artifact_identity(artifact: object) -> tuple[str, int, str, str, str] | None:
    if not isinstance(artifact, dict):
        return None
    benchmark_id = artifact.get("benchmark_id")
    benchmark_index = artifact.get("benchmark_index")
    provider_id = artifact.get("provider_id")
    reference = artifact.get("reference")
    digest = artifact.get("digest")
    if (
        not isinstance(benchmark_id, str)
        or isinstance(benchmark_index, bool)
        or not isinstance(benchmark_index, int)
        or not isinstance(provider_id, str)
        or not isinstance(reference, str)
        or not isinstance(digest, str)
    ):
        return None
    return benchmark_id, benchmark_index, provider_id, reference, digest


def _verification_problem(
    item: EvidenceEnvelope,
    trusted_verifiers: frozenset[str],
) -> str | None:
    artifacts = item.extensions.get(_EXT_OCI_ARTIFACTS)
    verifications = item.extensions.get(_EXT_OCI_VERIFICATIONS)
    if not isinstance(artifacts, list) or not artifacts:
        return "OCI manifest is empty"
    if not isinstance(verifications, list) or len(verifications) != len(artifacts):
        return "OCI content verification receipts are incomplete"

    expected = [_artifact_identity(artifact) for artifact in artifacts]
    observed = [_artifact_identity(verification) for verification in verifications]
    if (
        None in expected
        or None in observed
        or len(set(expected)) != len(expected)
        or sorted(expected) != sorted(observed)
    ):
        return "OCI content verification receipts do not match the manifest"

    for verification in verifications:
        if not isinstance(verification, dict):
            return "OCI content verification entry is not an object"
        raw_receipt = verification.get("receipt")
        try:
            receipt = ArtifactVerificationReceipt.model_validate(raw_receipt)
        except Exception:
            return "OCI content verification receipt is invalid"
        if receipt.verifier not in trusted_verifiers:
            return "OCI content verification used an untrusted verifier"
        if (
            receipt.artifact_uri != verification.get("reference")
            or receipt.artifact_digest != verification.get("digest")
        ):
            return "OCI content verification receipt is bound to different content"
        if not receipt.content:
            return "OCI content verification did not verify descriptor payloads"
        if not (
            item.metadata.observed_at <= receipt.verified_at <= item.metadata.expires_at
        ):
            return "OCI content verification is outside the evidence validity window"
    return None


class EvalHubEvidencePolicy:
    """Admit authenticated, terminal EvalHub evidence with explicit provenance."""

    def __init__(
        self,
        *,
        expected_producer_prefix: str,
        require_oci_artifacts: bool = True,
        require_verified_oci_artifacts: bool = False,
        trusted_artifact_verifiers: Sequence[str] = (OCI_DISTRIBUTION_VERIFIER,),
        minimum_confidence: float = 1.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(expected_producer_prefix)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("expected_producer_prefix must be an absolute HTTP(S) URL")
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self._expected_producer_prefix = expected_producer_prefix
        self._require_oci_artifacts = require_oci_artifacts
        self._require_verified_oci_artifacts = require_verified_oci_artifacts
        self._trusted_artifact_verifiers = frozenset(trusted_artifact_verifiers)
        if require_verified_oci_artifacts and not self._trusted_artifact_verifiers:
            raise ValueError("at least one trusted artifact verifier is required")
        self._minimum_confidence = minimum_confidence

    @property
    def deterministic(self) -> bool:
        return True

    async def evaluate(
        self,
        evidence: Sequence[EvidenceEnvelope],
        scope: Scope,
    ) -> PolicyResult:
        reasons: list[str] = []
        for item in evidence:
            if item.metadata.schema_uri != EVALHUB_JOB_SCHEMA_URI:
                reasons.append(f"{item.metadata.id}: unsupported EvalHub schema")
            if not _url_is_at_or_below(
                item.metadata.producer, self._expected_producer_prefix
            ):
                reasons.append(f"{item.metadata.id}: unexpected producer")
            if item.scope != scope:
                reasons.append(f"{item.metadata.id}: scope mismatch")
            if item.extensions.get(_EXT_API_REVISION) != EVALHUB_API_REVISION:
                reasons.append(f"{item.metadata.id}: unpinned API revision")
            state = item.extensions.get(_EXT_JOB_STATE)
            if state not in _TERMINAL_STATES:
                reasons.append(f"{item.metadata.id}: job is not terminal")
            result_kind = item.extensions.get(_EXT_RESULT_KIND)
            if result_kind not in _RESULT_KINDS:
                reasons.append(f"{item.metadata.id}: unsupported result kind")
            job_id = item.extensions.get(_EXT_JOB_ID)
            if not isinstance(job_id, str) or item.metadata.id != f"evalhub:{job_id}":
                reasons.append(f"{item.metadata.id}: job identity mismatch")
            if result_kind == "job-execution" and state not in {"failed", "cancelled"}:
                reasons.append(f"{item.metadata.id}: job execution state mismatch")
            if result_kind != "job-execution" and state in {"failed", "cancelled"}:
                reasons.append(f"{item.metadata.id}: result state mismatch")
            if (
                result_kind in {"collection", "job-result"}
                and state in {"completed", "partially_failed"}
                and not item.extensions.get(_EXT_JOB_TEST_PRESENT)
            ):
                reasons.append(f"{item.metadata.id}: compliance result has no test")
            provenance_problem = _provenance_problem(item)
            if provenance_problem:
                reasons.append(f"{item.metadata.id}: {provenance_problem}")
            if (
                self._require_oci_artifacts
                and result_kind != "job-execution"
                and item.extensions.get(_EXT_PROVENANCE_MODE) != "oci-manifest"
            ):
                reasons.append(f"{item.metadata.id}: complete OCI provenance is required")
            if self._require_verified_oci_artifacts:
                verification_problem = _verification_problem(
                    item,
                    self._trusted_artifact_verifiers,
                )
                if verification_problem:
                    reasons.append(f"{item.metadata.id}: {verification_problem}")
            if item.assurance.confidence < self._minimum_confidence:
                reasons.append(f"{item.metadata.id}: confidence is below the policy floor")

        return PolicyResult(
            check_id="evalhub-evidence-v1alpha1",
            allowed=not reasons,
            reason=(
                (
                    "all EvalHub evidence satisfies the pinned terminal-result contract "
                    "with registry-verified OCI content"
                    if self._require_verified_oci_artifacts
                    else "all EvalHub evidence satisfies the pinned terminal-result contract"
                )
                if not reasons
                else "; ".join(reasons)
            ),
            evidence_refs=[item.assurance.digest for item in evidence],
        )


class EvalHubPromotionConstraintClassifier:
    """Derive review-before-promotion constraints from concerning EvalHub results."""

    @property
    def has_deterministic_fallback(self) -> bool:
        return True

    async def classify(
        self,
        evidence: Sequence[EvidenceEnvelope],
        policy_results: Sequence[PolicyResult],
        scope: Scope,
    ) -> Sequence[Constraint]:
        constraints = []
        for item in evidence:
            if item.metadata.schema_uri != EVALHUB_JOB_SCHEMA_URI:
                continue
            if item.measurement.status not in {
                MeasurementStatus.FAILED,
                MeasurementStatus.WARNING,
            }:
                continue
            hard = item.measurement.status == MeasurementStatus.FAILED
            expression = {
                "effect": (
                    "block-promotion-pending-review"
                    if hard
                    else "require-review-before-promotion"
                ),
                "job_id": item.extensions[_EXT_JOB_ID],
                "job_state": item.extensions[_EXT_JOB_STATE],
                "measurement": item.measurement.name,
                "result_kind": item.extensions[_EXT_RESULT_KIND],
                "status": item.measurement.status.value,
            }
            identity = json.dumps(
                {
                    "constraint": EVALHUB_PROMOTION_CONSTRAINT,
                    "scope": scope.model_dump(mode="json", exclude_none=True),
                    "subject": item.subject.model_dump(mode="json", exclude_none=True),
                    "expression": expression,
                    "evidence_ref": item.assurance.digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            constraints.append(
                Constraint(
                    id=uuid5(NAMESPACE_URL, identity),
                    name=EVALHUB_PROMOTION_CONSTRAINT,
                    hard=hard,
                    expression=expression,
                    confidence=item.assurance.confidence,
                    source=ConstraintSource.DETERMINISTIC,
                    rationale=(
                        "EvalHub reported a failed evaluation; promotion requires "
                        "independent review."
                        if hard
                        else "EvalHub reported incomplete or cancelled evaluation evidence; "
                        "promotion requires independent review."
                    ),
                    evidence_refs=[item.assurance.digest],
                    extensions={
                        "io.github.jkershawrh.gcl.evalhub/policy-pack": (
                            EVALHUB_POLICY_PACK_URI
                        )
                    },
                )
            )
        return constraints
