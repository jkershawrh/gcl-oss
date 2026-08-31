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
