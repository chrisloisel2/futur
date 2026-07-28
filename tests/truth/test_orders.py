"""tests/truth/test_orders.py -- order state machine + fill application."""
from __future__ import annotations

import pytest

from src.futur.truth.events import Instrument, InstrumentType
from src.futur.truth.orders import (
    Fill,
    InvalidOrderTransition,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    validate_transition,
)


def _instrument() -> Instrument:
    return Instrument(venue="TESTX", symbol="BTCUSD", type=InstrumentType.SPOT,
                      base_ccy="BTC", quote_ccy="USD", tick_size=0.5, lot_size=0.001)


def _order(quantity=1.0) -> Order:
    return Order(order_id="o1", client_order_id="c1", instrument=_instrument(),
                side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=quantity)


def test_zero_quantity_order_rejected_at_construction():
    with pytest.raises(ValueError):
        _order(quantity=0.0)
    with pytest.raises(ValueError):
        _order(quantity=-1.0)


def test_limit_order_requires_limit_price():
    with pytest.raises(ValueError):
        Order(order_id="o1", client_order_id="c1", instrument=_instrument(),
             side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=1.0)


def test_valid_transitions_succeed():
    o = _order()
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    o.transition_to(OrderStatus.CANCELLED)
    assert o.status == OrderStatus.CANCELLED


@pytest.mark.parametrize("current,target", [
    (OrderStatus.CREATED, OrderStatus.ACKNOWLEDGED),   # skips SUBMITTED
    (OrderStatus.CREATED, OrderStatus.FILLED),
    (OrderStatus.FILLED, OrderStatus.CANCELLED),        # terminal -> anything
    (OrderStatus.CANCELLED, OrderStatus.ACKNOWLEDGED),
    (OrderStatus.REJECTED, OrderStatus.SUBMITTED),
])
def test_invalid_transitions_rejected(current, target):
    with pytest.raises(InvalidOrderTransition):
        validate_transition(current, target)


def test_partial_fill_then_full_fill():
    o = _order(quantity=10.0)
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    o.apply_fill(4.0)
    assert o.status == OrderStatus.PARTIALLY_FILLED
    assert o.filled_quantity == 4.0
    assert o.remaining_quantity == 6.0
    o.apply_fill(6.0)
    assert o.status == OrderStatus.FILLED
    assert o.filled_quantity == 10.0
    assert o.remaining_quantity == 0.0


def test_overfill_rejected():
    o = _order(quantity=5.0)
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    o.apply_fill(3.0)
    with pytest.raises(ValueError):
        o.apply_fill(3.0)   # 3 + 3 = 6 > 5


def test_fill_after_cancellation_rejected():
    o = _order()
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    o.transition_to(OrderStatus.CANCELLED)
    with pytest.raises(InvalidOrderTransition):
        o.apply_fill(1.0)


def test_fill_after_rejection_rejected():
    o = _order()
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.REJECTED)
    with pytest.raises(InvalidOrderTransition):
        o.apply_fill(1.0)


def test_fill_before_acknowledgement_rejected():
    o = _order()
    o.transition_to(OrderStatus.SUBMITTED)
    with pytest.raises(InvalidOrderTransition):
        o.apply_fill(1.0)


def test_zero_or_negative_fill_quantity_rejected():
    o = _order()
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    with pytest.raises(ValueError):
        o.apply_fill(0.0)
    with pytest.raises(ValueError):
        o.apply_fill(-1.0)


def test_client_order_id_is_a_plain_stable_field_not_regenerated():
    """Idempotence: the same client_order_id string round-trips unchanged
    through the order's lifecycle -- a caller re-submitting with the same
    client_order_id (e.g. after a network retry) can recognize it's the
    same intent."""
    o = _order()
    cid = o.client_order_id
    o.transition_to(OrderStatus.SUBMITTED)
    o.transition_to(OrderStatus.ACKNOWLEDGED)
    o.apply_fill(1.0)
    assert o.client_order_id == cid


def test_fill_rejects_non_positive_price_quantity_or_fee():
    instr = _instrument()
    with pytest.raises(ValueError):
        Fill(fill_id="f1", order_id="o1", instrument=instr, price=0.0,
            quantity=1.0, side=OrderSide.BUY, fee=0.0, fee_ccy="USD")
    with pytest.raises(ValueError):
        Fill(fill_id="f1", order_id="o1", instrument=instr, price=100.0,
            quantity=0.0, side=OrderSide.BUY, fee=0.0, fee_ccy="USD")
    with pytest.raises(ValueError):
        Fill(fill_id="f1", order_id="o1", instrument=instr, price=100.0,
            quantity=1.0, side=OrderSide.BUY, fee=-0.01, fee_ccy="USD")
