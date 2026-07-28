"""tests/truth/test_account_spot.py -- spot accounting via Account.apply_event()."""
from __future__ import annotations

import pytest

from src.futur.truth.account import (
    Account,
    CurrencyMismatchError,
    DuplicateFillError,
    ShortSpotNotAllowedError,
)
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    Instrument,
    InstrumentType,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
)
from src.futur.truth.orders import OrderSide, OrderStatus, OrderType

SPOT = Instrument(venue="TESTX", symbol="BTCUSD", type=InstrumentType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _ev(event_type, payload, event_id="e", ts="2026-01-01T00:00:00Z") -> Event:
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _deposit(account: Account, amount: float, eid: str = "dep") -> None:
    account.apply_event(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(amount, "USD"), eid))


def _submit_ack(account: Account, order_id: str, side: OrderSide, quantity: float,
                cid: str | None = None) -> None:
    cid = cid or f"c-{order_id}"
    account.apply_event(_ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=cid, instrument=SPOT, side=side.value,
        order_type=OrderType.MARKET.value, quantity=quantity), f"sub-{order_id}"))
    account.apply_event(_ev(EventType.ORDER_ACKNOWLEDGED,
                            OrderAcknowledgedPayload(order_id), f"ack-{order_id}"))


def _fill(account: Account, order_id: str, side: OrderSide, quantity: float, price: float,
         fee: float = 0.0, fill_id: str | None = None) -> None:
    fill_id = fill_id or f"f-{order_id}"
    account.apply_event(_ev(EventType.FILL, FillPayload(
        fill_id=fill_id, order_id=order_id, instrument=SPOT, price=price,
        quantity=quantity, side=side.value, fee=fee, fee_ccy="USD"), fill_id))


def test_deposit_wrong_currency_rejected():
    account = Account(base_currency="USD")
    with pytest.raises(CurrencyMismatchError):
        account.apply_event(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(100.0, "EUR")))


def test_spot_buy_reduces_cash_and_increases_quantity():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 2.0)
    _fill(account, "o1", OrderSide.BUY, 2.0, price=50_000.0, fee=5.0)
    assert account.cash == pytest.approx(100_000.0 - 2.0 * 50_000.0 - 5.0)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(2.0)
    assert account.orders["o1"].status == OrderStatus.FILLED


def test_spot_sell_increases_cash_and_decreases_quantity():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 2.0)
    _fill(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    _submit_ack(account, "o2", OrderSide.SELL, 1.0)
    cash_before = account.cash
    _fill(account, "o2", OrderSide.SELL, 1.0, price=51_000.0, fee=2.0)
    assert account.cash == pytest.approx(cash_before + 1.0 * 51_000.0 - 2.0)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(1.0)


def test_short_spot_rejected_by_default():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.SELL, 1.0)
    with pytest.raises(ShortSpotNotAllowedError):
        _fill(account, "o1", OrderSide.SELL, 1.0, price=50_000.0)


def test_short_spot_allowed_when_explicitly_enabled():
    account = Account(allow_short_spot=True)
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.SELL, 1.0)
    _fill(account, "o1", OrderSide.SELL, 1.0, price=50_000.0)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(-1.0)


def test_mark_updates_spot_market_value_and_nav():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 2.0)
    _fill(account, "o1", OrderSide.BUY, 2.0, price=50_000.0)
    cash_after_buy = account.cash
    account.apply_event(_ev(EventType.MARK, MarkPayload(SPOT, 55_000.0), "m1"))
    assert account.spot_market_value() == pytest.approx(2.0 * 55_000.0)
    assert account.nav() == pytest.approx(cash_after_buy + 2.0 * 55_000.0)


def test_unmarked_position_contributes_nothing_not_an_error():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 1.0)
    _fill(account, "o1", OrderSide.BUY, 1.0, price=50_000.0)
    assert account.spot_market_value() == 0.0   # never marked


def test_full_close_returns_quantity_to_zero():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 3.0)
    _fill(account, "o1", OrderSide.BUY, 3.0, price=50_000.0)
    _submit_ack(account, "o2", OrderSide.SELL, 3.0)
    _fill(account, "o2", OrderSide.SELL, 3.0, price=52_000.0)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(0.0)


def test_duplicate_fill_id_rejected():
    account = Account()
    _deposit(account, 100_000.0)
    _submit_ack(account, "o1", OrderSide.BUY, 5.0)
    _fill(account, "o1", OrderSide.BUY, 2.0, price=50_000.0, fill_id="fdup")
    with pytest.raises(DuplicateFillError):
        _fill(account, "o1", OrderSide.BUY, 2.0, price=50_000.0, fill_id="fdup")


def test_accounting_identity_no_money_created_or_destroyed_by_a_round_trip():
    """Buy then sell at the SAME price with zero fees: cash must return to
    exactly its starting value -- a spot round trip with no price movement
    and no costs can neither create nor destroy money."""
    account = Account()
    _deposit(account, 100_000.0)
    cash_start = account.cash
    _submit_ack(account, "o1", OrderSide.BUY, 1.5)
    _fill(account, "o1", OrderSide.BUY, 1.5, price=48_000.0, fee=0.0)
    _submit_ack(account, "o2", OrderSide.SELL, 1.5)
    _fill(account, "o2", OrderSide.SELL, 1.5, price=48_000.0, fee=0.0)
    assert account.cash == pytest.approx(cash_start)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(0.0)
