from __future__ import annotations

import json

import pytest

from research_kernel.ledger import LedgerError, RunLedger


def test_runs_are_appended_and_counted(tmp_path):
    led = RunLedger(tmp_path / "runs.jsonl")
    led.append({"mechanism_id": "a_v1", "status": "REJECTED"})
    led.append({"mechanism_id": "a_v1", "status": "COST_WALL"})
    led.append({"mechanism_id": "b_v1", "status": "DATA_BROKEN"})
    assert led.count_runs("a_v1") == 2
    assert led.verify()


def test_eleven_runs_of_one_rule_are_visible(tmp_path):
    led = RunLedger(tmp_path / "runs.jsonl")
    for i in range(11):
        led.append({"mechanism_id": "a_v1", "status": "REJECTED", "attempt": i})
    assert led.count_runs("a_v1") == 11


def test_editing_an_entry_breaks_the_chain(tmp_path):
    path = tmp_path / "runs.jsonl"
    led = RunLedger(path)
    led.append({"mechanism_id": "a_v1", "status": "REJECTED"})
    led.append({"mechanism_id": "a_v1", "status": "COST_WALL"})
    lines = path.read_text().splitlines()
    payload = json.loads(lines[0])
    payload["status"] = "PAPER_ELIGIBLE"
    lines[0] = json.dumps(payload, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerError, match="edited"):
        led.verify()


def test_removing_an_entry_breaks_the_chain(tmp_path):
    path = tmp_path / "runs.jsonl"
    led = RunLedger(path)
    for i in range(3):
        led.append({"mechanism_id": "a_v1", "attempt": i})
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(LedgerError):
        led.verify()


def test_an_empty_ledger_verifies(tmp_path):
    assert RunLedger(tmp_path / "nothing.jsonl").verify()
