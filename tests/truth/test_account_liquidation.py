"""tests/truth/test_account_liquidation.py -- forced closure + reconciliation recording."""
from __future__ import annotations

import pytest

from src.futur.truth.account import Account, UnknownPositionError
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    Instrument,
    InstrumentType,
    LiquidationPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
    ReconciliationPayload,
)
from src.futur.truth.orders import OrderSide, OrderType

PERP = Instrument(venue="TESTX", symbol="BTCUSD-PERP", type=InstrumentType.PERPETUAL,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _deposit(account: Account, amount: float, eid: str = "dep") -> None:
    account.apply_event(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(amount, "USD"), eid))


def _open(account: Account, order_id: str, side: OrderSide, quantity: float, price: float) -> None:
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=PERP, side=side.value,
        order_type=OrderType.MARKET.value, quantity=quantity), f"sub-{order_id}"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED,
                            OrderAcknowledgedPayload(order_id), f"ack-{order_id}"))
    account.apply_event(_ev(EventType.FILL, FillPayload(
        fill_id=f"f-{order_id}", order_id=order_id, instrument=PERP, price=price,
        quantity=quantity, side=side.value, fee=0.0, fee_ccy="USD"), f"fill-{order_id}"))


def test_liquidation_without_a_position_rejected():
    account = Account()
    _deposit(account, 10_000.0)
    with pytest.raises(UnknownPositionError):
        account.apply_event(_ev(EventType.LIQUIDATION, LiquidationPayload(
            instrument=PERP, quantity_closed=1.0, price=40_000.0, fee=10.0), "liq1"))


def test_liquidation_over_position_size_rejected():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.BUY, 1.0, price=50_000.0)
    with pytest.raises(ValueError, match="exceeds open position"):
        account.apply_event(_ev(EventType.LIQUIDATION, LiquidationPayload(
            instrument=PERP, quantity_closed=2.0, price=40_000.0, fee=10.0), "liq1"))


def test_partial_liquidation_realizes_pnl_and_reduces_position():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    cash_before = account.cash
    account.apply_event(_ev(EventType.LIQUIDATION, LiquidationPayload(
        instrument=PERP, quantity_closed=1.0, price=40_000.0, fee=25.0), "liq1"))
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(1.0)
    assert pos.avg_entry_price == pytest.approx(50_000.0)   # unchanged for what's left
    expected_realized = (40_000.0 - 50_000.0) * 1.0   # a loss
    assert account.cash == pytest.approx(cash_before + expected_realized - 25.0)


def test_full_liquidation_zeroes_position_and_resets_avg_price():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.SELL, 1.0, price=50_000.0)   # short
    cash_before = account.cash
    account.apply_event(_ev(EventType.LIQUIDATION, LiquidationPayload(
        instrument=PERP, quantity_closed=1.0, price=60_000.0, fee=15.0), "liq1"))
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(0.0)
    assert pos.avg_entry_price == pytest.approx(0.0)
    expected_realized = (60_000.0 - 50_000.0) * 1.0 * -1.0   # short losing as price rises
    assert account.cash == pytest.approx(cash_before + expected_realized - 15.0)


def test_reconciliation_event_recorded_on_account():
    account = Account()
    _deposit(account, 100.0)
    payload = ReconciliationPayload(verdict="MATCH", details={"cash_diff": 0.0})
    account.apply_event(_ev(EventType.RECONCILIATION, payload, "rec1"))
    assert account.last_reconciliation is payload
