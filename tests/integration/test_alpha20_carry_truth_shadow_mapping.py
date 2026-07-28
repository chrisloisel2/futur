"""tests/integration/test_alpha20_carry_truth_shadow_mapping.py -- Phase 4C
commit 2: the legacy leg_ledger -> Truth Event field mapping.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from src.alpha20.tournament.truth_shadow.mapping import (
    LegLedgerToTruthEvents,
    UnmappableLegError,
    borrow_delta_event,
    product_spec_for_leg,
)
from src.futur.truth.events import EventType, ProductType

VENUE = "binance_usdm"


def _row(**overrides) -> dict:
    base = {"position_id": "CARRY_1", "leg_id": "leg_1", "asset": "BTCUSDT",
           "leg_type": "CARRY_LONG_SPOT", "position_type": "DELTA_NEUTRAL_CARRY",
           "engine": "carry", "entry_time": "2026-01-01T00:00:00Z", "exit_time": None,
           "qty": 1.5, "notional": 75000.0, "entry_price": 50_000.0, "exit_price": None,
           "price_pnl": 0.0, "funding_pnl": 0.0, "costs": 37.5, "net_pnl": -37.5}
    base.update(overrides)
    return base


def _ledger(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ── product_spec_for_leg ─────────────────────────────────────────────────

def test_spot_leg_type_maps_to_spot_product():
    spec = product_spec_for_leg("BTCUSDT", "CARRY_LONG_SPOT", VENUE)
    assert spec.type == ProductType.SPOT
    assert spec.base_ccy == "BTC"
    assert spec.quote_ccy == "USD"
    assert spec.venue == VENUE
    assert spec.symbol == "BTCUSDT"


def test_perp_leg_type_maps_to_linear_perp_product():
    spec = product_spec_for_leg("ETHUSDT", "CARRY_SHORT_PERP", VENUE)
    assert spec.type == ProductType.LINEAR_PERP
    assert spec.base_ccy == "ETH"


def test_non_usdt_asset_rejected_not_guessed():
    with pytest.raises(UnmappableLegError, match="USDT"):
        product_spec_for_leg("BTCEUR", "CARRY_LONG_SPOT", VENUE)


def test_unknown_leg_type_rejected():
    with pytest.raises(UnmappableLegError, match="LEG_TYPES"):
        product_spec_for_leg("BTCUSDT", "SOMETHING_NEW", VENUE)


# ── LegLedgerToTruthEvents: entry ────────────────────────────────────────

def test_open_leg_first_cycle_emits_order_ack_fill_then_fee_and_mark():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row())   # costs=37.5 (nonzero) and price_pnl=0.0 (mark == entry, but
                               # this is the FIRST observation so the mark is still emitted)
    events = adapter.events_for_cycle(ledger, cycle_ts="2026-01-01T00:00:00Z")
    kinds = [e.event_type for e in events]
    assert kinds == [EventType.ORDER_SUBMITTED, EventType.ORDER_ACKNOWLEDGED, EventType.FILL,
                     EventType.FEE, EventType.MARK]
    fill = events[2]
    assert fill.payload.price == Decimal(50000)
    assert fill.payload.quantity == Decimal("1.5")
    assert fill.payload.side == "BUY"   # CARRY_LONG_SPOT delta_sign +1
    assert fill.payload.fee == 0   # combined cost handled separately, see FEE test


def test_same_leg_same_cycle_state_reobserved_emits_nothing_new():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row())
    adapter.events_for_cycle(ledger, cycle_ts="2026-01-01T00:00:00Z")
    second = adapter.events_for_cycle(ledger, cycle_ts="2026-01-01T08:00:00Z")
    assert second == []   # nothing changed since last observed -- idempotent


def test_perp_leg_entry_side_is_sell():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(leg_id="leg_2", leg_type="CARRY_SHORT_PERP"))
    events = adapter.events_for_cycle(ledger, cycle_ts="2026-01-01T00:00:00Z")
    fill = events[2]
    assert fill.payload.side == "SELL"


# ── fee delta ─────────────────────────────────────────────────────────────

def test_fee_delta_emitted_as_a_separate_fee_event_not_split_across_fills():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(costs=37.5))
    events = adapter.events_for_cycle(ledger, cycle_ts="2026-01-01T00:00:00Z")
    fee_events = [e for e in events if e.event_type == EventType.FEE]
    assert len(fee_events) == 1
    assert fee_events[0].payload.amount == Decimal("37.5")

    # next cycle: total cost grew (exit incurred) -- only the DELTA is emitted
    ledger2 = _ledger(_row(costs=60.0, exit_time="2026-01-02T00:00:00Z", exit_price=51_000.0,
                           price_pnl=1500.0))
    events2 = adapter.events_for_cycle(ledger2, cycle_ts="2026-01-02T00:00:00Z")
    fee_events2 = [e for e in events2 if e.event_type == EventType.FEE]
    assert len(fee_events2) == 1
    assert fee_events2[0].payload.amount == Decimal("22.5")   # 60.0 - 37.5, not the full 60.0


def test_fee_decreasing_is_rejected_as_unmappable():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    adapter.events_for_cycle(_ledger(_row(costs=50.0)), cycle_ts="t0")
    with pytest.raises(UnmappableLegError, match="costs decreased"):
        adapter.events_for_cycle(_ledger(_row(costs=10.0)), cycle_ts="t1")


# ── funding delta ─────────────────────────────────────────────────────────

def test_funding_delta_only_for_funding_bearing_leg_types():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    # CARRY_LONG_SPOT never accrues funding (LEG_FUNDING_SIGN == 0.0)
    events = adapter.events_for_cycle(
        _ledger(_row(leg_type="CARRY_LONG_SPOT", funding_pnl=999.0)), cycle_ts="t0")
    assert not [e for e in events if e.event_type == EventType.FUNDING]


def test_funding_delta_emitted_for_carry_short_perp():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(leg_id="leg_p", leg_type="CARRY_SHORT_PERP", funding_pnl=12.5))
    events = adapter.events_for_cycle(ledger, cycle_ts="t0")
    funding_events = [e for e in events if e.event_type == EventType.FUNDING]
    assert len(funding_events) == 1
    assert funding_events[0].payload.amount == Decimal("12.5")


# ── mark ────────────────────────────────────────────────────────────────

def test_mark_price_recovered_from_price_pnl_for_open_leg():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    # BUY (delta_sign +1) leg, entry 50000, price_pnl = 1.5 * (mark - 50000) = 750 -> mark 50500
    ledger = _ledger(_row(qty=1.5, entry_price=50_000.0, price_pnl=750.0))
    events = adapter.events_for_cycle(ledger, cycle_ts="t0")
    marks = [e for e in events if e.event_type == EventType.MARK]
    assert len(marks) == 1
    assert marks[0].payload.price == Decimal(50500)


def test_mark_not_repeated_when_unchanged():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(qty=1.5, entry_price=50_000.0, price_pnl=750.0))
    adapter.events_for_cycle(ledger, cycle_ts="t0")
    events2 = adapter.events_for_cycle(ledger, cycle_ts="t1")
    assert not [e for e in events2 if e.event_type == EventType.MARK]


# ── exit / terminal close ──────────────────────────────────────────────

def test_leg_closes_emits_exit_order_ack_fill_with_opposite_side():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    adapter.events_for_cycle(_ledger(_row()), cycle_ts="t0")
    closed = _ledger(_row(exit_time="2026-01-02T00:00:00Z", exit_price=51_000.0,
                          price_pnl=1500.0, costs=45.0))
    events = adapter.events_for_cycle(closed, cycle_ts="t1")
    fills = [e for e in events if e.event_type == EventType.FILL]
    assert len(fills) == 1
    exit_fill = fills[0]
    assert exit_fill.payload.side == "SELL"   # closing a BUY-opened CARRY_LONG_SPOT
    assert exit_fill.payload.price == Decimal(51000)
    assert exit_fill.payload.quantity == Decimal("1.5")
    # closed legs stop emitting MARK
    assert not [e for e in events if e.event_type == EventType.MARK]


def test_leg_first_observed_already_closed_attributes_full_cost_to_the_closing_event():
    """The documented edge case: a leg opens and closes between two shadow
    cycles, never observed while merely open -- entry/exit fee can't be
    split, so the WHOLE combined cost is attributed to the single fee
    delta emitted alongside both fills in this cycle. The total is still
    exact; only its allocation is coarser."""
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(exit_time="2026-01-01T04:00:00Z", exit_price=50_200.0,
                         price_pnl=300.0, costs=45.0))
    events = adapter.events_for_cycle(ledger, cycle_ts="t0")
    kinds = [e.event_type for e in events]
    assert kinds.count(EventType.FILL) == 2   # entry AND exit, same cycle
    fee_events = [e for e in events if e.event_type == EventType.FEE]
    assert len(fee_events) == 1
    assert fee_events[0].payload.amount == Decimal("45.0")   # the full combined cost


# ── required-field / rejection behavior ──────────────────────────────────

def test_missing_required_column_rejected():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    row = _row()
    del row["entry_price"]
    with pytest.raises(UnmappableLegError, match="entry_price"):
        adapter.events_for_cycle(pd.DataFrame([row]), cycle_ts="t0")


def test_nan_required_field_rejected():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(qty=float("nan")))
    with pytest.raises(UnmappableLegError, match="qty"):
        adapter.events_for_cycle(ledger, cycle_ts="t0")


def test_nonpositive_qty_rejected():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(qty=0.0))
    with pytest.raises(UnmappableLegError, match="qty must be > 0"):
        adapter.events_for_cycle(ledger, cycle_ts="t0")


def test_unmappable_asset_in_ledger_rejected():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    ledger = _ledger(_row(asset="BTCEUR"))
    with pytest.raises(UnmappableLegError, match="USDT"):
        adapter.events_for_cycle(ledger, cycle_ts="t0")


def test_empty_ledger_emits_nothing():
    adapter = LegLedgerToTruthEvents(venue=VENUE)
    assert adapter.events_for_cycle(pd.DataFrame(), cycle_ts="t0") == []


# ── borrow (portfolio-level delta) ───────────────────────────────────────

def test_borrow_delta_event_sign_and_amount():
    # pnl_by_type["borrow"] accumulates NEGATIVELY in the legacy model
    ev = borrow_delta_event(cumulative_borrow_usdt=-8.0, previous_cumulative_borrow_usdt=-5.0,
                            cycle_ts="t1", cycle_index=1)
    assert ev is not None
    assert ev.event_type == EventType.BORROW_COST
    assert ev.payload.amount == Decimal(3)


def test_borrow_delta_none_when_unchanged():
    ev = borrow_delta_event(cumulative_borrow_usdt=-5.0, previous_cumulative_borrow_usdt=-5.0,
                            cycle_ts="t1", cycle_index=1)
    assert ev is None


def test_borrow_delta_wrong_direction_rejected():
    with pytest.raises(UnmappableLegError, match="wrong way"):
        borrow_delta_event(cumulative_borrow_usdt=-5.0, previous_cumulative_borrow_usdt=-8.0,
                           cycle_ts="t1", cycle_index=1)
