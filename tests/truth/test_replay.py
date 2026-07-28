"""tests/truth/test_replay.py -- serialization round-trip + determinism."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.futur.truth.account import UnknownOrderError
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.orders import OrderStatus
from src.futur.truth.replay import (
    event_from_dict,
    event_to_dict,
    load_events_jsonl,
    replay,
    replay_file,
    write_events_jsonl,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "truth" / "basic_replay.jsonl"

SPOT = ProductSpec(venue="SIM", symbol="BTCUSD", type=ProductType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.01, lot_size=0.0001)


def test_fixture_file_exists():
    assert FIXTURE.exists(), f"expected the committed fixture at {FIXTURE}"


def test_event_dict_round_trip_preserves_all_fields():
    original = Event(event_id="f1", event_type=EventType.FILL, ts_event="t0", ts_received="t0",
                     payload=FillPayload(fill_id="fill1", order_id="o1", instrument=SPOT,
                                        price=100.0, quantity=2.0, side="BUY", fee=1.5,
                                        fee_ccy="USD", liquidity="TAKER", venue="SIM",
                                        external_id="ext1"))
    restored = event_from_dict(event_to_dict(original))
    assert restored.event_id == original.event_id
    assert restored.event_type == original.event_type
    assert restored.payload == original.payload   # frozen dataclass -> structural equality


def test_write_then_load_round_trip(tmp_path):
    events = [Event(event_id="d1", event_type=EventType.CASH_DEPOSIT,
                    ts_event="t0", ts_received="t0",
                    payload=CashDepositPayload(500.0, "USD"))]
    path = tmp_path / "mini.jsonl"
    write_events_jsonl(events, path)
    loaded = load_events_jsonl(path)
    assert len(loaded) == 1
    assert loaded[0].payload == events[0].payload


def test_basic_replay_fixture_processes_all_14_scenarios_without_violation():
    """The fixture itself is the proof that a realistic 22-event history
    (deposit, spot buy/sell, perp open, funding both signs, adverse mark,
    partial fill, cancel of the remainder, standalone fee, borrow,
    partial liquidation, final reconciliation) passes every invariant
    end-to-end -- if it didn't, TruthEngine.apply() would have raised
    during replay_file() itself, before this test gets to assert anything."""
    engine, summary = replay_file(FIXTURE)
    assert summary.n_events == 22
    assert engine.account.orders["o4"].status == OrderStatus.CANCELLED
    assert engine.account.last_reconciliation.verdict == "MATCH"
    # spot: bought 2.0, sold 0.5 -> 1.5 left
    assert summary.spot_positions["SIM:BTCUSD:SPOT"] == Decimal("1.5")
    # perp: opened 1.0, partial-filled +1.2 (=2.2), liquidated -1.0 -> 1.2 left
    assert summary.perp_positions["SIM:BTCUSD-PERP:LINEAR_PERP"] == Decimal("1.2")


def test_replaying_the_same_fixture_twice_gives_identical_hash_and_state():
    """The mission's core determinism requirement: two replays of the same
    events produce exactly the same ledger, state, NAV, positions, and
    final hash."""
    engine_a, summary_a = replay_file(FIXTURE)
    engine_b, summary_b = replay_file(FIXTURE)
    assert summary_a == summary_b
    assert engine_a.ledger.head_hash == engine_b.ledger.head_hash
    assert [e.event_id for e in engine_a.ledger.events()] == \
           [e.event_id for e in engine_b.ledger.events()]


def test_replaying_events_out_of_order_is_rejected_not_silently_reordered():
    """A FILL for an order whose ORDER_SUBMITTED hasn't been processed yet
    must fail loudly (UnknownOrderError), not get silently accepted in
    whatever order it happens to arrive -- order matters, deterministically,
    not just "probably.\""""
    events = load_events_jsonl(FIXTURE)
    submitted_o1, fill_f1 = events[1], events[3]
    assert submitted_o1.event_type == EventType.ORDER_SUBMITTED
    assert fill_f1.event_type == EventType.FILL
    reordered = list(events)
    reordered[1], reordered[3] = reordered[3], reordered[1]   # fill before its own order
    with pytest.raises(UnknownOrderError):
        replay(reordered)
