"""tests/truth/test_adversarial.py -- Phase 4B commit 3: ledger durability,
corruption/truncation detection, crash recovery, idempotence after restart,
and no invalid event partially mutating the account (ACK/fill races,
cancel/fill races, overfills).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.futur.truth.account import Account, ShortSpotNotAllowedError
from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    OrderAcknowledgedPayload,
    OrderCancelledPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.ledger import DuplicateEventError, Ledger, LedgerCorruptionError
from src.futur.truth.orders import InvalidOrderTransition, OrderSide, OrderType

SPOT = ProductSpec(venue="TESTX", symbol="BTCUSD", type=ProductType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size="0.5", lot_size="0.001")


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _deposit(eid, amount=100_000.0) -> Event:
    return _ev(EventType.CASH_DEPOSIT, CashDepositPayload(amount, "USD"), eid)


def _submit(order_id, side, quantity, eid=None) -> Event:
    return _ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=SPOT,
        side=side.value, order_type=OrderType.MARKET.value, quantity=quantity),
        eid or f"sub-{order_id}")


def _ack(order_id, eid=None) -> Event:
    return _ev(EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload(order_id),
              eid or f"ack-{order_id}")


def _fill(order_id, side, quantity, price, fill_id=None, eid=None) -> Event:
    fill_id = fill_id or f"f-{order_id}"
    return _ev(EventType.FILL, FillPayload(
        fill_id=fill_id, order_id=order_id, instrument=SPOT, price=price,
        quantity=quantity, side=side.value, fee=0.0, fee_ccy="USD"), eid or fill_id)


# ── durability + reload ──────────────────────────────────────────────────

def test_wal_persists_and_reloads_identical_state(tmp_path):
    wal = tmp_path / "ledger.jsonl"
    ledger = Ledger(wal_path=wal)
    ledger.append(_deposit("d1"))
    ledger.append(_submit("o1", OrderSide.BUY, 1.0))
    ledger.append(_ack("o1"))
    ledger.append(_fill("o1", OrderSide.BUY, 1.0, 50_000.0))
    head_before = ledger.head_hash
    n_before = len(ledger)
    ledger.close()

    reloaded = Ledger(wal_path=wal)
    assert reloaded.head_hash == head_before
    assert len(reloaded) == n_before
    assert [e.event_id for e in reloaded.events()] == ["d1", "sub-o1", "ack-o1", "f-o1"]
    reloaded.close()


def test_reload_resumes_sequence_and_chains_new_appends_correctly(tmp_path):
    wal = tmp_path / "ledger.jsonl"
    ledger = Ledger(wal_path=wal)
    ledger.append(_deposit("d1"))
    ledger.append(_deposit("d2", amount=1.0))
    ledger.close()

    reloaded = Ledger(wal_path=wal)
    stamped = reloaded.append(_deposit("d3", amount=1.0))
    assert stamped.sequence == 2   # continues, doesn't reset to 0
    assert len(reloaded) == 3
    reloaded.close()


def test_reload_rejects_a_duplicate_event_id_already_on_disk(tmp_path):
    wal = tmp_path / "ledger.jsonl"
    ledger = Ledger(wal_path=wal)
    ledger.append(_deposit("d1"))
    ledger.close()

    reloaded = Ledger(wal_path=wal)
    with pytest.raises(DuplicateEventError):
        reloaded.append(_deposit("d1", amount=999.0))
    reloaded.close()


def test_crash_then_resume_produces_the_same_hash_as_an_uninterrupted_run(tmp_path):
    """The idempotence property that matters in practice: replaying
    "first half, (simulated) crash, reload, second half" must land on
    EXACTLY the hash an uninterrupted run of all events would produce."""
    events = [
        _deposit("d1"), _submit("o1", OrderSide.BUY, 1.0), _ack("o1"),
        _fill("o1", OrderSide.BUY, 1.0, 50_000.0),
        _submit("o2", OrderSide.SELL, 0.4), _ack("o2"),
        _fill("o2", OrderSide.SELL, 0.4, 51_000.0),
    ]

    uninterrupted = Ledger(wal_path=tmp_path / "uninterrupted.jsonl")
    for e in events:
        uninterrupted.append(e)
    expected_hash = uninterrupted.head_hash
    uninterrupted.close()

    wal = tmp_path / "resumed.jsonl"
    first_run = Ledger(wal_path=wal)
    for e in events[:4]:
        first_run.append(e)
    first_run.close()   # simulated crash/restart boundary

    resumed = Ledger(wal_path=wal)
    for e in events[4:]:
        resumed.append(e)
    assert resumed.head_hash == expected_hash
    resumed.close()


# ── corruption / truncation detection ───────────────────────────────────

def test_truncated_last_line_is_detected_as_corruption(tmp_path):
    wal = tmp_path / "ledger.jsonl"
    ledger = Ledger(wal_path=wal)
    ledger.append(_deposit("d1"))
    ledger.append(_deposit("d2", amount=1.0))
    ledger.close()

    with open(wal, "rb+") as f:
        f.seek(-5, 2)   # chop off the tail of the last line -- simulated crash mid-write
        f.truncate()

    with pytest.raises(LedgerCorruptionError):
        Ledger(wal_path=wal)


def test_tampered_content_breaks_the_hash_chain_and_is_detected(tmp_path):
    wal = tmp_path / "ledger.jsonl"
    ledger = Ledger(wal_path=wal)
    ledger.append(_deposit("d1"))
    ledger.append(_deposit("d2", amount=1.0))
    ledger.close()

    lines = wal.read_text().splitlines()
    record = json.loads(lines[0])
    record["event"]["payload"]["amount"] = "999999.0"   # tamper, hash left stale
    lines[0] = json.dumps(record)
    wal.write_text("\n".join(lines) + "\n")

    with pytest.raises(LedgerCorruptionError):
        Ledger(wal_path=wal)


# ── atomicity: no invalid event partially mutates the account ──────────

def test_rejected_short_spot_fill_leaves_the_order_and_account_untouched():
    """Before commit 3's fix, `_apply_fill` called `order.apply_fill()`
    (mutating the Order's filled_quantity/status) *before*
    `_apply_spot_fill` got a chance to reject the fill for going short --
    leaving the Order marked filled while cash/position never moved. This
    proves the whole event now rolls back atomically."""
    engine = TruthEngine(account=Account(allow_short_spot=False))
    engine.apply(_deposit("d1"))
    engine.apply(_submit("o1", OrderSide.SELL, 1.0))
    engine.apply(_ack("o1"))

    order_before = engine.account.orders["o1"].status
    filled_before = engine.account.orders["o1"].filled_quantity
    cash_before = engine.account.cash

    with pytest.raises(ShortSpotNotAllowedError):
        engine.apply(_fill("o1", OrderSide.SELL, 1.0, 50_000.0))

    assert engine.account.orders["o1"].status == order_before
    assert engine.account.orders["o1"].filled_quantity == filled_before
    assert engine.account.cash == cash_before
    assert "f-o1" not in engine.account.seen_fill_ids
    assert SPOT.key not in engine.account.spot_positions
    # (Phase 4C) the ledger rolls back too -- a rejected event is not
    # durably reserved, so it never shows up in history and its event_id
    # is free to be resubmitted (e.g. corrected).
    assert len(engine.ledger) == 3
    assert "f-o1" not in {e.event_id for e in engine.ledger.events()}


def test_overfill_is_rejected_without_mutating_the_already_partial_fill():
    engine = TruthEngine()
    engine.apply(_deposit("d1"))
    engine.apply(_submit("o1", OrderSide.BUY, 1.0))
    engine.apply(_ack("o1"))
    engine.apply(_fill("o1", OrderSide.BUY, 0.4, 50_000.0, fill_id="f1"))

    filled_before = engine.account.orders["o1"].filled_quantity
    qty_before = engine.account.spot_positions[SPOT.key].quantity
    cash_before = engine.account.cash

    with pytest.raises(ValueError, match="over-fill"):
        engine.apply(_fill("o1", OrderSide.BUY, 0.8, 50_000.0, fill_id="f2"))

    assert engine.account.orders["o1"].filled_quantity == filled_before
    assert engine.account.spot_positions[SPOT.key].quantity == qty_before
    assert engine.account.cash == cash_before


def test_wal_backed_engine_rejects_overfill_and_restart_matches_pre_rejection_state(tmp_path):
    """Phase 4C commit 1's mandated durability/atomicity test: a WAL-backed
    engine, valid events applied, then an over-fill attempted. The
    rejected event must leave the ledger's state, hash, and sequence
    IDENTICAL to just before it, and a restart from the WAL must land on
    that exact same state -- with the rejected event_id neither persisted
    nor reserved (a resubmission under the same id must succeed)."""
    wal = tmp_path / "engine.jsonl"
    ledger = Ledger(wal_path=wal)
    engine = TruthEngine(ledger=ledger)
    engine.apply(_deposit("d1"))
    engine.apply(_submit("o1", OrderSide.BUY, 1.0))
    engine.apply(_ack("o1"))
    engine.apply(_fill("o1", OrderSide.BUY, 0.4, 50_000.0, fill_id="f1"))

    hash_before = engine.ledger.head_hash
    sequence_before = len(engine.ledger)
    cash_before = engine.account.cash
    filled_before = engine.account.orders["o1"].filled_quantity

    with pytest.raises(ValueError, match="over-fill"):
        engine.apply(_fill("o1", OrderSide.BUY, 0.8, 50_000.0, fill_id="f2"))   # 0.4+0.8 > 1.0

    # state, hash, and sequence identical to just before the rejected event
    assert engine.ledger.head_hash == hash_before
    assert len(engine.ledger) == sequence_before
    assert engine.account.cash == cash_before
    assert engine.account.orders["o1"].filled_quantity == filled_before
    engine.ledger.close()

    # restart: reload from the WAL alone
    reloaded_ledger = Ledger(wal_path=wal)
    assert reloaded_ledger.head_hash == hash_before
    assert len(reloaded_ledger) == sequence_before
    persisted_ids = {e.event_id for e in reloaded_ledger.events()}
    assert "f2" not in persisted_ids   # rejected event_id never persisted

    reloaded_engine = TruthEngine.from_ledger(reloaded_ledger)
    assert reloaded_engine.account.cash == cash_before
    assert reloaded_engine.account.orders["o1"].filled_quantity == filled_before

    # rejected event_id not reserved -- a fresh event under the SAME id
    # (e.g. the corrected fill, or an unrelated event reusing the id once
    # freed) is accepted, not blocked as a phantom duplicate
    stamped = reloaded_engine.apply(_fill("o1", OrderSide.BUY, 0.6, 50_000.0, fill_id="f2"))
    assert stamped.event_id == "f2"
    assert reloaded_engine.account.orders["o1"].filled_quantity == Decimal("1.0")
    reloaded_engine.ledger.close()


# ── races: ACK/fill and cancel/fill ─────────────────────────────────────

def test_fill_arriving_before_acknowledgement_is_rejected_not_silently_accepted():
    """A FILL racing ahead of its own order's ACK -- the order is still
    SUBMITTED, not yet ACKNOWLEDGED, so apply_fill's own transition guard
    must reject it (not treat "submitted" as good enough)."""
    engine = TruthEngine()
    engine.apply(_deposit("d1"))
    engine.apply(_submit("o1", OrderSide.BUY, 1.0))
    # no ACK event -- a fill arrives first (a race in a real venue feed)

    cash_before = engine.account.cash
    with pytest.raises(InvalidOrderTransition):
        engine.apply(_fill("o1", OrderSide.BUY, 1.0, 50_000.0))

    assert engine.account.orders["o1"].filled_quantity == 0
    assert engine.account.cash == cash_before
    assert SPOT.key not in engine.account.spot_positions


def test_fill_arriving_after_cancellation_is_rejected_not_silently_accepted():
    """A FILL racing a CANCEL -- once CANCELLED (terminal), no fill may
    still land, even if the venue's fill message was already in flight."""
    engine = TruthEngine()
    engine.apply(_deposit("d1"))
    engine.apply(_submit("o1", OrderSide.BUY, 1.0))
    engine.apply(_ack("o1"))
    engine.apply(_ev(EventType.ORDER_CANCELLED, OrderCancelledPayload("o1", reason="race"), "can1"))

    cash_before = engine.account.cash
    with pytest.raises(InvalidOrderTransition):
        engine.apply(_fill("o1", OrderSide.BUY, 1.0, 50_000.0))

    assert engine.account.orders["o1"].filled_quantity == 0
    assert engine.account.cash == cash_before
    assert SPOT.key not in engine.account.spot_positions


def test_duplicate_event_id_at_the_ledger_layer_rejected_before_reaching_account():
    engine = TruthEngine()
    engine.apply(_deposit("d1"))
    with pytest.raises(DuplicateEventError):
        engine.apply(_deposit("d1", amount=999.0))
    assert engine.account.cash == 100_000.0   # unaffected -- rejected before Account ever saw it
