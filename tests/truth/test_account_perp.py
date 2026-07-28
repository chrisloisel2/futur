"""tests/truth/test_account_perp.py -- perpetual accounting via Account.apply_event()."""
from __future__ import annotations

import pytest

from src.futur.truth.account import Account
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    FundingPayload,
    Instrument,
    InstrumentType,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
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


def _open(account: Account, order_id: str, side: OrderSide, quantity: float, price: float,
         fee: float = 0.0) -> None:
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=PERP, side=side.value,
        order_type=OrderType.MARKET.value, quantity=quantity), f"sub-{order_id}"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED,
                            OrderAcknowledgedPayload(order_id), f"ack-{order_id}"))
    account.apply_event(_ev(EventType.FILL, FillPayload(
        fill_id=f"f-{order_id}", order_id=order_id, instrument=PERP, price=price,
        quantity=quantity, side=side.value, fee=fee, fee_ccy="USD"), f"fill-{order_id}"))


def _mark(account: Account, price: float, eid: str = "mk") -> None:
    account.apply_event(_ev(EventType.MARK, MarkPayload(PERP, price), eid))


def test_opening_long_does_not_touch_full_notional_only_fee():
    account = Account()
    _deposit(account, 100_000.0)
    cash_before = account.cash
    _open(account, "o1", OrderSide.BUY, 2.0, price=50_000.0, fee=10.0)
    # notional would be 100,000 -- only the fee actually leaves cash
    assert account.cash == pytest.approx(cash_before - 10.0)
    assert account.perp_positions[PERP.key].quantity == pytest.approx(2.0)
    assert account.perp_positions[PERP.key].avg_entry_price == pytest.approx(50_000.0)


def test_opening_short():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.SELL, 1.5, price=50_000.0)
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(-1.5)
    assert pos.side == "SHORT"
    assert pos.avg_entry_price == pytest.approx(50_000.0)


def test_increasing_long_weighted_average_entry_price():
    account = Account()
    _deposit(account, 200_000.0)
    _open(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    _open(account, "o2", OrderSide.BUY, 2.0, price=60_000.0)
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(4.0)
    assert pos.avg_entry_price == pytest.approx((2 * 50_000.0 + 2 * 60_000.0) / 4.0)


def test_partial_reduction_realizes_pnl_and_keeps_avg_price():
    account = Account()
    _deposit(account, 200_000.0)
    _open(account, "o1", OrderSide.BUY, 4.0, price=50_000.0)
    cash_before_close = account.cash
    _open(account, "o2", OrderSide.SELL, 1.0, price=55_000.0)   # close 1 of 4
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(3.0)
    assert pos.avg_entry_price == pytest.approx(50_000.0)       # unchanged for the remainder
    expected_realized = (55_000.0 - 50_000.0) * 1.0
    assert account.cash == pytest.approx(cash_before_close + expected_realized)


def test_full_close_realizes_full_pnl_and_resets_avg_price():
    account = Account()
    _deposit(account, 200_000.0)
    _open(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    cash_before_close = account.cash
    _open(account, "o2", OrderSide.SELL, 2.0, price=48_000.0)   # loss
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(0.0)
    assert pos.avg_entry_price == pytest.approx(0.0)
    expected_realized = (48_000.0 - 50_000.0) * 2.0             # negative
    assert account.cash == pytest.approx(cash_before_close + expected_realized)


def test_flip_from_long_to_short_realizes_pnl_on_old_and_opens_new_at_fill_price():
    account = Account()
    _deposit(account, 200_000.0)
    _open(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    cash_before_flip = account.cash
    _open(account, "o2", OrderSide.SELL, 5.0, price=52_000.0)   # closes 2 long, opens 3 short
    pos = account.perp_positions[PERP.key]
    assert pos.quantity == pytest.approx(-3.0)
    assert pos.avg_entry_price == pytest.approx(52_000.0)        # fresh position, entry = fill price
    expected_realized = (52_000.0 - 50_000.0) * 2.0
    assert account.cash == pytest.approx(cash_before_flip + expected_realized)


def test_short_pnl_sign_is_opposite_of_long():
    """A mark increase must help a long and hurt a short by the same
    magnitude, opposite sign -- tested directly on unrealized PnL."""
    long_account, short_account = Account(), Account()
    _deposit(long_account, 100_000.0)
    _deposit(short_account, 100_000.0)
    _open(long_account, "o1", OrderSide.BUY, 1.0, price=50_000.0)
    _open(short_account, "o1", OrderSide.SELL, 1.0, price=50_000.0)
    _mark(long_account, 55_000.0)
    _mark(short_account, 55_000.0)
    long_pnl = long_account.perp_positions[PERP.key].unrealized_pnl(55_000.0)
    short_pnl = short_account.perp_positions[PERP.key].unrealized_pnl(55_000.0)
    assert long_pnl == pytest.approx(5_000.0)
    assert short_pnl == pytest.approx(-5_000.0)


def test_unrealized_pnl_uses_mark_not_last_trade_price():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.BUY, 1.0, price=50_000.0)   # last trade = 50,000
    _mark(account, 53_000.0)                                     # mark diverges from last trade
    assert account.perp_unrealized_pnl() == pytest.approx(3_000.0)
    assert account.nav() == pytest.approx(account.cash + 3_000.0)


def test_funding_positive_and_negative_move_cash_directly():
    account = Account()
    _deposit(account, 100_000.0)
    _open(account, "o1", OrderSide.BUY, 1.0, price=50_000.0)
    cash_before = account.cash
    account.apply_event(_ev(EventType.FUNDING, FundingPayload(PERP, 12.5, "USD"), "fund1"))
    assert account.cash == pytest.approx(cash_before + 12.5)
    account.apply_event(_ev(EventType.FUNDING, FundingPayload(PERP, -7.0, "USD"), "fund2"))
    assert account.cash == pytest.approx(cash_before + 12.5 - 7.0)
