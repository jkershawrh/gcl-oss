from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import resources

import gcl_oss.cli as cli
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


def test_evalhub_live_uses_token_file_and_current_clock(
    tmp_path, monkeypatch, capsys
) -> None:
    fixture_path = resources.files("gcl_oss.data").joinpath(
        "evalhub-job-failed-safety.json"
    )
    with fixture_path.open(encoding="utf-8") as source:
        job = deepcopy(json.load(source))
    now = datetime.now(timezone.utc)
    job["resource"]["created_at"] = (now - timedelta(seconds=20)).isoformat()
    job["resource"]["updated_at"] = (now - timedelta(seconds=1)).isoformat()

    class FakeClient:
        base_url = "https://evalhub.example"

        def __init__(self, base_url, tenant, *, bearer_token, **kwargs):
            assert base_url == self.base_url
            assert tenant == "team-a"
            assert bearer_token == "projected-token"

        def job_url(self, job_id):
            return self.base_url + "/api/v1/evaluations/jobs/" + job_id

        def get_job(self, job_id):
            assert job_id == job["resource"]["id"]
            return job

    monkeypatch.setattr(cli, "EvalHubHTTPClient", FakeClient)
    token_file = tmp_path / "token"
    token_file.write_text("projected-token\n", encoding="utf-8")

    assert (
        main(
            [
                "evalhub-live",
                "--base-url",
                "https://evalhub.example",
                "--job-id",
                job["resource"]["id"],
                "--tenant",
                "team-a",
                "--namespace",
                "models",
                "--environment",
                "test",
                "--token-file",
                str(token_file),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "proposed"
    assert payload["proposal_receipt"]["execution_verified"] is False
    assert payload["normalized_evidence"]["scope"]["tenant"] == "team-a"
