"""tests/truth/test_engine.py -- TruthEngine.apply() ties everything together."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import CashDepositPayload, Event, EventType
from src.futur.truth.invariants import InvariantViolation
from src.futur.truth.margin import MarginConfig


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def test_apply_appends_to_ledger_and_updates_account():
    engine = TruthEngine()
    engine.apply(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1000.0, "USD"), "d1"))
    assert engine.account.cash == 1000.0
    assert len(engine.ledger) == 1
    assert engine.ledger.events()[0].sequence == 0


def test_apply_stamps_sequence_regardless_of_caller_supplied_value():
    engine = TruthEngine()
    ev = _ev(EventType.CASH_DEPOSIT, CashDepositPayload(1.0, "USD"), "d1")
    stamped = engine.apply(ev)
    assert stamped.sequence == 0
    assert ev.sequence == -1   # original untouched


def test_apply_raises_and_leaves_the_ledger_and_account_exactly_as_before():
    """A violation doesn't get silently swallowed -- InvariantViolation
    still propagates -- but (Phase 4C) it no longer poisons the engine:
    the event that caused it is rolled back from BOTH the ledger and the
    account, so its event_id is not reserved and the exact same event_id
    can be resubmitted afterward."""
    engine = TruthEngine()
    engine.account.cash = Decimal("500.0")   # corrupt state directly, bypassing normal bookkeeping
    with pytest.raises(InvariantViolation):
        engine.apply(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1.0, "USD"), "d1"))
    assert len(engine.ledger) == 0   # the rejected event never became part of history
    assert engine.account.cash == Decimal("500.0")   # restored exactly, not just "close"

    # un-corrupt the account by hand (simulating a restart with correct
    # state) and prove the SAME event_id is not blocked
    engine.account.cash = Decimal(0)
    stamped = engine.apply(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1.0, "USD"), "d1"))
    assert stamped.event_id == "d1"
    assert len(engine.ledger) == 1


def test_custom_margin_config_is_used_by_invariant_checks():
    with pytest.raises(ValueError):
        TruthEngine(margin_config=MarginConfig(initial_margin_rate=0.01,
                                               maintenance_margin_rate=0.02))


def test_from_ledger_rebuilds_account_state_not_just_the_ledger():
    engine = TruthEngine()
    engine.apply(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1000.0, "USD"), "d1"))
    engine.apply(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(500.0, "USD"), "d2"))

    rebuilt = TruthEngine.from_ledger(engine.ledger)
    assert rebuilt.account.cash == engine.account.cash == Decimal("1500.00000000")
    assert rebuilt.ledger.head_hash == engine.ledger.head_hash
    assert rebuilt is not engine
    assert rebuilt.account is not engine.account
