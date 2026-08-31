from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from gcl_oss.contracts import (
    Constraint,
    DecisionPackage,
    EvidenceEnvelope,
    EvidenceReference,
    ObjectiveSpec,
    PolicyResult,
    SignedDecisionPackage,
)
from gcl_oss.ports import ProposalReceipt
from gcl_oss.registry import ActionDefinition

SCHEMA_BASE = "https://raw.githubusercontent.com/jkershawrh/gcl-oss/main/schemas"

SCHEMA_MODELS: dict[str, tuple[type[BaseModel], str]] = {
    "action-definition.v1alpha1.schema.json": (
        ActionDefinition,
        f"{SCHEMA_BASE}/action-definition.v1alpha1.schema.json",
    ),
    "constraint.v1alpha1.schema.json": (
        Constraint,
        f"{SCHEMA_BASE}/constraint.v1alpha1.schema.json",
    ),
    "decision-package.v1alpha1.schema.json": (
        DecisionPackage,
        f"{SCHEMA_BASE}/decision-package.v1alpha1.schema.json",
    ),
    "evidence-envelope.v1alpha1.schema.json": (
        EvidenceEnvelope,
        f"{SCHEMA_BASE}/evidence-envelope.v1alpha1.schema.json",
    ),
    "evidence-reference.v1alpha1.schema.json": (
        EvidenceReference,
        f"{SCHEMA_BASE}/evidence-reference.v1alpha1.schema.json",
    ),
    "objective-spec.v1alpha1.schema.json": (
        ObjectiveSpec,
        f"{SCHEMA_BASE}/objective-spec.v1alpha1.schema.json",
    ),
    "policy-result.v1alpha1.schema.json": (
        PolicyResult,
        f"{SCHEMA_BASE}/policy-result.v1alpha1.schema.json",
    ),
    "proposal-receipt.v1alpha1.schema.json": (
        ProposalReceipt,
        f"{SCHEMA_BASE}/proposal-receipt.v1alpha1.schema.json",
    ),
    "signed-decision-package.v1alpha1.schema.json": (
        SignedDecisionPackage,
        f"{SCHEMA_BASE}/signed-decision-package.v1alpha1.schema.json",
    ),
}


def schema_documents() -> dict[str, dict]:
    documents = {}
    for filename, (model, schema_id) in SCHEMA_MODELS.items():
        schema = model.model_json_schema(mode="validation")
        schema["$id"] = schema_id
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        documents[filename] = schema
    return documents


def write_schemas(output: Path) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, schema in schema_documents().items():
        destination = output / filename
        destination.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(destination)
    return tuple(written)
