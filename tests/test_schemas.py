from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from gcl_oss.schemas import schema_documents, write_schemas

ROOT = Path(__file__).resolve().parents[1]


def test_committed_schemas_match_models() -> None:
    expected = schema_documents()
    for filename, schema in expected.items():
        committed = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        assert committed == schema


def test_schema_export_is_deterministic(tmp_path: Path) -> None:
    first = write_schemas(tmp_path / "first")
    second = write_schemas(tmp_path / "second")
    assert [path.name for path in first] == [path.name for path in second]
    for left, right in zip(first, second):
        assert left.read_bytes() == right.read_bytes()


def test_evalhub_style_golden_evidence_validates() -> None:
    from gcl_oss.contracts import EvidenceEnvelope

    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "evalhub-failed-safety.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(
        schema_documents()["evidence-envelope.v1alpha1.schema.json"]
    ).validate(payload)
    item = EvidenceEnvelope.model_validate(payload)
    assert item.extensions["evalhub.io/job-id"] == "42"
