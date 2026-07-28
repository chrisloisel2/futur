"""tests/truth/test_events.py -- ProductSpec and Event/payload construction."""
from __future__ import annotations

import pytest

from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    MarkPayload,
    OrderRejectedPayload,
    ProductSpec,
    ProductType,
)


def _instrument(itype=ProductType.SPOT) -> ProductSpec:
    return ProductSpec(venue="TESTX", symbol="BTCUSD", type=itype,
                      base_ccy="BTC", quote_ccy="USD",
                      tick_size=0.5, lot_size=0.001)


def test_instrument_key_distinguishes_spot_and_perp_same_symbol():
    spot = _instrument(ProductType.SPOT)
    perp = _instrument(ProductType.LINEAR_PERP)
    assert spot.key != perp.key


def test_instrument_rejects_non_positive_tick_or_lot_size():
    with pytest.raises(ValueError):
        ProductSpec(venue="TESTX", symbol="X", type=ProductType.SPOT,
                  base_ccy="X", quote_ccy="USD", tick_size=0.0, lot_size=1.0)
    with pytest.raises(ValueError):
        ProductSpec(venue="TESTX", symbol="X", type=ProductType.SPOT,
                  base_ccy="X", quote_ccy="USD", tick_size=1.0, lot_size=-1.0)


def test_event_payload_must_match_event_type():
    # correct pairing constructs fine
    Event(event_id="e1", event_type=EventType.CASH_DEPOSIT,
         ts_event="t0", ts_received="t0",
         payload=CashDepositPayload(amount=100.0, currency="USD"))
    # mismatched payload is rejected at construction, not silently accepted
    with pytest.raises(TypeError):
        Event(event_id="e2", event_type=EventType.CASH_DEPOSIT,
             ts_event="t0", ts_received="t0",
             payload=MarkPayload(instrument=_instrument(), price=100.0))


def test_event_sort_key_orders_by_received_then_sequence_then_id():
    e1 = Event(event_id="b", event_type=EventType.ORDER_REJECTED,
              ts_event="t0", ts_received="t0",
              payload=OrderRejectedPayload(order_id="o1", reason="x"),
              sequence=2)
    e2 = Event(event_id="a", event_type=EventType.ORDER_REJECTED,
              ts_event="t0", ts_received="t0",
              payload=OrderRejectedPayload(order_id="o1", reason="x"),
              sequence=1)
    assert e2.sort_key() < e1.sort_key()   # sequence breaks the tie, not event_id


def test_with_sequence_returns_new_frozen_instance():
    e = Event(event_id="e1", event_type=EventType.CASH_DEPOSIT,
             ts_event="t0", ts_received="t0",
             payload=CashDepositPayload(amount=1.0, currency="USD"))
    assert e.sequence == -1
    e2 = e.with_sequence(5)
    assert e2.sequence == 5
    assert e.sequence == -1          # original untouched (frozen dataclass)
    assert e2.event_id == e.event_id
