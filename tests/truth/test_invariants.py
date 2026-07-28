"""tests/truth/test_invariants.py -- each invariant, tested individually.

Most of these hand-construct a corrupted Account/Ledger rather than trying
to reach the corruption through apply_event() -- apply_event's own logic
already prevents most of these from arising naturally (that's the point of
having them in commits 1-5). This file tests the DETECTOR itself: given a
state that violates invariant X, does check() actually catch it?
"""
from __future__ import annotations

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
from src.futur.truth.invariants import InvariantViolation, check
from src.futur.truth.ledger import Ledger
from src.futur.truth.margin import MarginConfig
from src.futur.truth.orders import Order, OrderSide, OrderStatus, OrderType
from src.futur.truth.positions import PerpPosition, SpotPosition

SPOT = Instrument(venue="TESTX", symbol="BTCUSD", type=InstrumentType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)
PERP = Instrument(venue="TESTX", symbol="BTCUSD-PERP", type=InstrumentType.PERPETUAL,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _ev(event_type, payload, event_id="e") -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=event_id, event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _empty_ledger() -> Ledger:
    return Ledger()


def test_valid_empty_state_passes():
    check(Account(), _empty_ledger(), MarginConfig())


def test_valid_account_after_real_events_passes():
    account, ledger = Account(), Ledger()
    e = ledger.append(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1000.0, "USD"), "d1"))
    account.apply_event(e)
    check(account, ledger, MarginConfig())


def test_cash_not_finite_rejected():
    account = Account(cash=float("nan"))
    with pytest.raises(InvariantViolation, match="cash"):
        check(account, _empty_ledger(), MarginConfig())


def test_negative_mark_price_rejected():
    account = Account()
    account.marks[SPOT.key] = -100.0
    with pytest.raises(InvariantViolation, match="mark price"):
        check(account, _empty_ledger(), MarginConfig())


def test_non_finite_position_quantity_rejected():
    account = Account()
    account.perp_positions[PERP.key] = PerpPosition(instrument=PERP, quantity=float("inf"))
    with pytest.raises(InvariantViolation, match="not finite"):
        check(account, _empty_ledger(), MarginConfig())


def test_overfilled_order_rejected():
    account = Account()
    order = Order(order_id="o1", client_order_id="c1", instrument=SPOT,
                 side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1.0)
    order.status = OrderStatus.FILLED
    order.filled_quantity = 5.0     # corrupted by hand -- apply_fill() would never allow this
    account.orders["o1"] = order
    with pytest.raises(InvariantViolation, match="filled_quantity"):
        check(account, _empty_ledger(), MarginConfig())


def test_naked_short_spot_without_flag_rejected():
    account = Account(allow_short_spot=False)
    account.spot_positions[SPOT.key] = SpotPosition(instrument=SPOT, quantity=-1.0)
    with pytest.raises(InvariantViolation, match="negative"):
        check(account, _empty_ledger(), MarginConfig())


def test_naked_short_spot_with_flag_allowed():
    """Built through a real, consistent event flow (not a hand-corrupted
    position) -- a bare hand-set negative quantity with no matching fill
    would separately (and correctly) trip the "positions equal sum of
    fills" check, which isn't what this test is about."""
    account, ledger = Account(allow_short_spot=True), Ledger()
    for ev in (
        _ev(EventType.CASH_DEPOSIT, CashDepositPayload(100_000.0, "USD"), "d1"),
        _ev(EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
            order_id="o1", client_order_id="c1", instrument=SPOT, side="SELL",
            order_type="MARKET", quantity=1.0), "sub1"),
        _ev(EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload("o1"), "ack1"),
        _ev(EventType.FILL, FillPayload(
            fill_id="f1", order_id="o1", instrument=SPOT, price=100.0, quantity=1.0,
            side="SELL", fee=0.0, fee_ccy="USD"), "fill1"),
    ):
        stamped = ledger.append(ev)
        account.apply_event(stamped)
    assert account.spot_positions[SPOT.key].quantity == pytest.approx(-1.0)
    check(account, ledger, MarginConfig())   # does not raise


def test_maintenance_margin_exceeding_initial_rejected():
    """Can't happen via MarginConfig's own construction-time check -- this
    proves invariants.py has its own independent guard too, not just a
    single point of enforcement."""
    account = Account(cash=1_000_000.0)
    account.perp_positions[PERP.key] = PerpPosition(instrument=PERP, quantity=10.0,
                                                    avg_entry_price=50_000.0)
    account.marks[PERP.key] = 50_000.0
    bad_config = object.__new__(MarginConfig)   # bypass __post_init__'s own check
    object.__setattr__(bad_config, "initial_margin_rate", 0.05)
    object.__setattr__(bad_config, "maintenance_margin_rate", 0.10)
    with pytest.raises(InvariantViolation, match="maintenance_margin_required"):
        check(account, _empty_ledger(), bad_config)


def test_ledger_non_monotonic_sequence_rejected():
    account, ledger = Account(), Ledger()
    ledger.append(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(1.0, "USD"), "d1"))
    # hand-corrupt: splice in an entry with a wrong sequence
    from src.futur.truth.ledger import LedgerEntry, hash_entry
    bad_event = _ev(EventType.CASH_DEPOSIT, CashDepositPayload(1.0, "USD"), "d2").with_sequence(5)
    ledger._entries.append(LedgerEntry(event=bad_event, cumulative_hash=hash_entry(ledger.head_hash, bad_event)))
    with pytest.raises(InvariantViolation, match="sequence not monotonic"):
        check(account, ledger, MarginConfig())


def test_client_order_id_conflicting_payload_rejected():
    account = Account()
    o1 = Order(order_id="o1", client_order_id="c1", instrument=SPOT,
              side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1.0)
    o2 = Order(order_id="o2", client_order_id="c1", instrument=SPOT,
              side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=2.0)   # conflicts
    account.orders = {"o1": o1, "o2": o2}
    account.orders_by_client_id = {"c1": ["o1", "o2"]}
    with pytest.raises(InvariantViolation, match="conflicting orders"):
        check(account, _empty_ledger(), MarginConfig())


def test_client_order_id_identical_resubmission_allowed():
    account = Account()
    o1 = Order(order_id="o1", client_order_id="c1", instrument=SPOT,
              side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1.0)
    o2 = Order(order_id="o2", client_order_id="c1", instrument=SPOT,
              side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1.0)   # identical retry
    account.orders = {"o1": o1, "o2": o2}
    account.orders_by_client_id = {"c1": ["o1", "o2"]}
    check(account, _empty_ledger(), MarginConfig())   # does not raise


def test_position_not_matching_sum_of_fills_rejected():
    account, ledger = Account(), Ledger()
    ledger.append(_ev(EventType.CASH_DEPOSIT, CashDepositPayload(100_000.0, "USD"), "d1"))
    account.apply_event(ledger.entries[-1].event)
    # hand-corrupt the spot position without a matching fill on the ledger
    account.spot_positions[SPOT.key] = SpotPosition(instrument=SPOT, quantity=3.0)
    with pytest.raises(InvariantViolation, match="sum of fills"):
        check(account, ledger, MarginConfig())


def test_cash_not_matching_categorized_flows_rejected():
    account = Account()
    account.cash = 500.0   # set directly, bypassing _apply_cash_deposit's category tracking
    with pytest.raises(InvariantViolation, match="does not equal the sum"):
        check(account, _empty_ledger(), MarginConfig())
