"""tests/truth/test_reconciliation.py -- account vs. external snapshot diffing."""
from __future__ import annotations

import copy

import pytest

from src.futur.truth.account import Account
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    Instrument,
    InstrumentType,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
)
from src.futur.truth.reconciliation import ExternalSnapshot, reconcile, to_event_payload

SPOT = Instrument(venue="TESTX", symbol="BTCUSD", type=InstrumentType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _account_with_position() -> Account:
    account = Account()
    account.apply_event(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(100_000.0, "USD"), "d1"))
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id="o1", client_order_id="c1", instrument=SPOT, side="BUY",
        order_type="MARKET", quantity=1.0), "sub1"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload("o1"), "ack1"))
    account.apply_event(_ev(EventType.FILL, FillPayload(
        fill_id="f1", order_id="o1", instrument=SPOT, price=50_000.0, quantity=1.0,
        side="BUY", fee=0.0, fee_ccy="USD"), "fill1"))
    return account


def test_match_when_everything_agrees():
    account = _account_with_position()
    snapshot = ExternalSnapshot(cash=account.cash, nav=account.nav(),
                                spot_positions={SPOT.key: 1.0})
    result = reconcile(account, snapshot)
    assert result.verdict == "MATCH"
    assert result.cash_diff == 0.0
    assert result.nav_diff == 0.0
    assert result.spot_quantity_diffs == {}


def test_cash_mismatch_detected():
    account = _account_with_position()
    snapshot = ExternalSnapshot(cash=account.cash - 500.0, nav=account.nav(),
                                spot_positions={SPOT.key: 1.0})
    result = reconcile(account, snapshot)
    assert result.verdict == "MISMATCH"
    assert result.cash_diff == pytest.approx(500.0)


def test_nav_mismatch_detected():
    account = _account_with_position()
    snapshot = ExternalSnapshot(cash=account.cash, nav=account.nav() + 1_000.0,
                                spot_positions={SPOT.key: 1.0})
    result = reconcile(account, snapshot)
    assert result.verdict == "MISMATCH"
    assert result.nav_diff == pytest.approx(-1_000.0)


def test_quantity_mismatch_detected():
    account = _account_with_position()
    snapshot = ExternalSnapshot(cash=account.cash, nav=account.nav(),
                                spot_positions={SPOT.key: 0.8})   # external thinks less
    result = reconcile(account, snapshot)
    assert result.verdict == "MISMATCH"
    assert result.spot_quantity_diffs[SPOT.key] == pytest.approx(0.2)


def test_missing_order_detected():
    """External believes an order is still open that the account has no
    record of at all (or already closed)."""
    account = _account_with_position()
    snapshot = ExternalSnapshot(cash=account.cash, nav=account.nav(),
                                spot_positions={SPOT.key: 1.0},
                                open_order_ids=frozenset({"o1", "o-ghost"}))
    result = reconcile(account, snapshot)
    assert result.verdict == "MISMATCH"
    assert "o-ghost" in result.missing_orders


def test_unknown_order_detected():
    """Account believes an order is open that external has no record of."""
    account = _account_with_position()
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id="o2", client_order_id="c2", instrument=SPOT, side="BUY",
        order_type="MARKET", quantity=1.0), "sub2"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload("o2"), "ack2"))
    snapshot = ExternalSnapshot(cash=account.cash, nav=account.nav(),
                                spot_positions={SPOT.key: 1.0}, open_order_ids=frozenset())
    result = reconcile(account, snapshot)
    assert result.verdict == "MISMATCH"
    assert "o2" in result.unknown_orders


def test_reconcile_never_mutates_the_account():
    account = _account_with_position()
    before = copy.deepcopy(account)
    reconcile(account, ExternalSnapshot(cash=0.0, nav=0.0))   # deliberately mismatched
    assert account.cash == before.cash
    assert account.spot_positions.keys() == before.spot_positions.keys()
    for key in account.spot_positions:
        assert account.spot_positions[key].quantity == before.spot_positions[key].quantity


def test_to_event_payload_carries_verdict_and_details():
    account = _account_with_position()
    result = reconcile(account, ExternalSnapshot(cash=account.cash - 1.0, nav=account.nav()))
    payload = to_event_payload(result)
    assert payload.verdict == "MISMATCH"
    assert payload.details["cash_diff"] == pytest.approx(1.0)
