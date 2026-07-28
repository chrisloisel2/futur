"""tests/truth/test_cli.py -- `futur truth replay`/`validate` via the CLI parser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from futur.cli import main

FIXTURE = str(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "truth" / "basic_replay.jsonl")

_INSTRUMENT = {"venue": "SIM", "symbol": "BTCUSD", "type": "SPOT", "base_ccy": "BTC",
              "quote_ccy": "USD", "tick_size": 0.5, "lot_size": 0.001,
              "contract_multiplier": 1.0}


def _write_jsonl(path: Path, events: list) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_futur_help_still_works_and_lists_truth():
    # argparse's --help raises SystemExit(0) directly, it never returns
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_truth_group_alone_prints_help_and_succeeds(capsys):
    assert main(["truth"]) == 0
    out = capsys.readouterr().out
    assert "replay" in out and "validate" in out


def test_truth_replay_prints_summary_and_succeeds(capsys):
    exit_code = main(["truth", "replay", FIXTURE])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "events replayed:   22" in out
    assert "final ledger hash:" in out


def test_truth_validate_succeeds_on_a_valid_fixture(capsys):
    exit_code = main(["truth", "validate", FIXTURE])
    assert exit_code == 0
    assert "VALID" in capsys.readouterr().out


def test_truth_validate_fails_on_a_domain_error(tmp_path):
    bad = tmp_path / "bad.jsonl"
    _write_jsonl(bad, [
        {"event_id": "d1", "event_type": "CASH_DEPOSIT",
         "payload": {"amount": 100.0, "currency": "EUR"},
         "ts_event": "t0", "ts_received": "t0"},
    ])
    # CurrencyMismatchError, not an InvariantViolation -- propagates as a
    # normal exception (still a non-zero exit if actually run as a process)
    with pytest.raises(Exception, match="multi-currency"):
        main(["truth", "validate", str(bad)])


def test_truth_replay_reports_invariant_violation_cleanly(tmp_path, capsys):
    """Specifically an InvariantViolation reached through a real fixture
    file via the CLI, not a domain ValueError -- the one case
    _cmd_truth_replay catches and turns into a clean message + exit 1.
    Two ORDER_SUBMITTED events sharing a client_order_id but disagreeing on
    quantity: nothing in Account.apply_event() itself rejects this (no
    per-event validation covers cross-order consistency), but
    invariants.check() does, right after the second one is applied --
    exactly the kind of violation only reachable through a realistic event
    sequence, not a hand-corrupted object."""
    bad = tmp_path / "conflicting_client_id.jsonl"
    _write_jsonl(bad, [
        {"event_id": "e1", "event_type": "ORDER_SUBMITTED",
         "payload": {"order_id": "o1", "client_order_id": "cX", "instrument": _INSTRUMENT,
                    "side": "BUY", "order_type": "MARKET", "quantity": 1.0,
                    "limit_price": None},
         "ts_event": "t0", "ts_received": "t0"},
        {"event_id": "e2", "event_type": "ORDER_SUBMITTED",
         "payload": {"order_id": "o2", "client_order_id": "cX", "instrument": _INSTRUMENT,
                    "side": "SELL", "order_type": "MARKET", "quantity": 2.0,
                    "limit_price": None},
         "ts_event": "t0", "ts_received": "t0"},
    ])
    exit_code = main(["truth", "replay", str(bad)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "INVALID -- invariant violation" in err
    assert "conflicting orders" in err
