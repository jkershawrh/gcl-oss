from __future__ import annotations

import json

from gcl_oss.cli import main


def test_offline_demo_produces_one_proposal(capsys) -> None:
    assert main(["demo"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "proposed"
    assert payload["proposal_receipt"]["execution_verified"] is False
    assert payload["proposal_delivery_count"] == 1
    assert payload["proof_entry_count"] == 6


def test_evalhub_demo_normalizes_failed_collection_and_proposes_review(capsys) -> None:
    assert main(["evalhub-demo"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "proposed"
    assert payload["normalized_evidence"]["measurement"]["status"] == "failed"
    assert payload["normalized_evidence"]["extensions"][
        "io.github.eval-hub/provenance-mode"
    ] == "oci-manifest"
    assert payload["proposal_receipt"]["execution_verified"] is False
    assert payload["proposal_delivery_count"] == 1
    package = payload["signed_package"]["package"]
    selected = next(
        candidate
        for candidate in package["candidates"]
        if candidate["id"] == package["selected_candidate_id"]
    )
    assert selected["action"].endswith("/request_review")
