"""Bind host-produced OCI verification receipts into normalized EvalHub evidence."""

from __future__ import annotations

from copy import deepcopy

from gcl_oss.adapters.evalhub import (
    EVALHUB_EXTENSION_NAMESPACE,
    EvalHubAdapterError,
)
from gcl_oss.contracts import EvidenceEnvelope
from gcl_oss.ports import (
    ArtifactVerificationReceipt,
    ArtifactVerificationRequest,
    ArtifactVerifier,
)

_EXT_OCI_ARTIFACTS = f"{EVALHUB_EXTENSION_NAMESPACE}/oci-artifacts"
_EXT_OCI_VERIFICATIONS = f"{EVALHUB_EXTENSION_NAMESPACE}/oci-verifications"
_EXT_PROVENANCE_MODE = f"{EVALHUB_EXTENSION_NAMESPACE}/provenance-mode"


async def verify_evalhub_oci_artifacts(
    item: EvidenceEnvelope,
    verifier: ArtifactVerifier,
) -> EvidenceEnvelope:
    """Verify every normalized OCI reference and return a receipt-bound copy."""

    if item.extensions.get(_EXT_PROVENANCE_MODE) != "oci-manifest":
        raise EvalHubAdapterError("EvalHub evidence has no complete OCI manifest")
    artifacts = item.extensions.get(_EXT_OCI_ARTIFACTS)
    if not isinstance(artifacts, list) or not artifacts:
        raise EvalHubAdapterError("EvalHub OCI manifest is empty")

    receipts = []
    verified: dict[tuple[str, str], ArtifactVerificationReceipt] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise EvalHubAdapterError("EvalHub OCI manifest entry is not an object")
        reference = artifact.get("reference")
        digest = artifact.get("digest")
        if not isinstance(reference, str) or not isinstance(digest, str):
            raise EvalHubAdapterError("EvalHub OCI manifest entry is incomplete")
        key = (reference, digest)
        receipt = verified.get(key)
        if receipt is None:
            receipt = await verifier.verify(
                ArtifactVerificationRequest(
                    artifact_uri=reference,
                    expected_digest=digest,
                )
            )
            if (
                receipt.artifact_uri != reference
                or receipt.artifact_digest != digest
                or not receipt.verified
            ):
                raise EvalHubAdapterError(
                    "artifact verifier returned a receipt for different content"
                )
            verified[key] = receipt
        receipts.append(
            {
                "benchmark_id": artifact.get("benchmark_id"),
                "benchmark_index": artifact.get("benchmark_index"),
                "provider_id": artifact.get("provider_id"),
                "reference": reference,
                "digest": digest,
                "receipt": receipt.model_dump(mode="json", exclude_none=True),
            }
        )

    extensions = deepcopy(item.extensions)
    extensions[_EXT_OCI_VERIFICATIONS] = receipts
    return item.model_copy(update={"extensions": extensions})
