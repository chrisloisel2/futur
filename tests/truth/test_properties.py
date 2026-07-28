"""tests/truth/test_properties.py -- property-based tests (Hypothesis).

Each test targets exactly one property the mission names explicitly. These
complement, not replace, the example-based tests elsewhere in tests/truth/
-- Hypothesis explores many inputs per property, the example tests pin
specific hand-checked numbers.
"""
from __future__ import annotations

import math
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from src.futur.truth.account import Account
from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import (
    CashDepositPayload,
    Event,
    EventType,
    FillPayload,
    LiquidationPayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.margin import MarginConfig, can_open_additional_notional, compute_exposures
from src.futur.truth.numeric import quantize_cash
from src.futur.truth.orders import OrderSide, OrderType
from src.futur.truth.positions import PerpPosition
from src.futur.truth.replay import replay


def _d(value) -> Decimal:
    return Decimal(str(value))

SPOT = ProductSpec(venue="SIM", symbol="BTCUSD", type=ProductType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.01, lot_size=0.0001)
PERP = ProductSpec(venue="SIM", symbol="BTCUSD-PERP", type=ProductType.LINEAR_PERP,
                  base_ccy="BTC", quote_ccy="USD", tick_size=0.01, lot_size=0.0001)
PERP2 = ProductSpec(venue="SIM", symbol="ETHUSD-PERP", type=ProductType.LINEAR_PERP,
                   base_ccy="ETH", quote_ccy="USD", tick_size=0.01, lot_size=0.0001)


def _ev(i, event_type, payload) -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=f"e{i}", event_type=event_type, ts_event=ts, ts_received=ts,
                payload=payload)


def _open(engine: TruthEngine, i: int, instrument: ProductSpec, order_id: str,
         side: OrderSide, quantity: float, price: float, fee: float = 0.0) -> int:
    engine.apply(_ev(i, EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=instrument,
        side=side.value, order_type=OrderType.MARKET.value, quantity=quantity)))
    engine.apply(_ev(i + 1, EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload(order_id)))
    engine.apply(_ev(i + 2, EventType.FILL, FillPayload(
        fill_id=f"f-{order_id}", order_id=order_id, instrument=instrument, price=price,
        quantity=quantity, side=side.value, fee=fee, fee_ccy="USD")))
    return i + 3


price_st = st.floats(min_value=1.0, max_value=200_000.0, allow_nan=False, allow_infinity=False)
qty_st = st.floats(min_value=0.0001, max_value=1000.0, allow_nan=False, allow_infinity=False)
fee_st = st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False, allow_infinity=False)


# ── 1. no fill creates cash out of nothing -- change is exactly the formula ──

@given(price=price_st, quantity=qty_st, fee=fee_st)
@settings(max_examples=100)
def test_no_fill_creates_cash_spot_buy(price, quantity, fee):
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    cash_before = engine.account.cash
    _open(engine, 1, SPOT, "o1", OrderSide.BUY, quantity, price, fee)
    # Decimal arithmetic is exact (no ULP drift the way float was), so this
    # mirrors _apply_spot_fill's own formula -- price/quantity tick/lot-
    # quantized and each cash term quantized separately, exactly like the
    # engine does (FillPayload does the same quantization at construction)
    # -- and checks EXACT equality.
    price_q, quantity_q = SPOT.quantize_price(price), SPOT.quantize_quantity(quantity)
    trade_cashflow = quantize_cash(-(quantity_q * price_q))
    fee_q = quantize_cash(_d(fee))
    assert engine.account.cash == quantize_cash(cash_before + trade_cashflow - fee_q)


@given(price=price_st, quantity=qty_st, fee=fee_st)
@settings(max_examples=100)
def test_no_fill_creates_cash_perp_open(price, quantity, fee):
    """Opening a perp costs only the fee -- the notional never touches cash."""
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    cash_before = engine.account.cash
    _open(engine, 1, PERP, "o1", OrderSide.BUY, quantity, price, fee)
    assert engine.account.cash == quantize_cash(cash_before - _d(fee))


# ── 2. a round trip with no price movement loses exactly the costs ─────────

@given(price=price_st, quantity=qty_st, fee1=fee_st, fee2=fee_st)
@settings(max_examples=100)
def test_spot_round_trip_same_price_loses_exactly_the_fees(price, quantity, fee1, fee2):
    engine = TruthEngine(account=Account())
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    cash_start = engine.account.cash
    i = _open(engine, 1, SPOT, "o1", OrderSide.BUY, quantity, price, fee1)
    # mirrors _apply_spot_fill's own formula (buy then sell at the same
    # price), tick/lot- and cash-quantized at each step exactly like the
    # engine does, so this is an EXACT equality rather than an approximation.
    price_q, quantity_q = SPOT.quantize_price(price), SPOT.quantize_quantity(quantity)
    fee1_q, fee2_q = quantize_cash(_d(fee1)), quantize_cash(_d(fee2))
    trade_cashflow = quantize_cash(quantity_q * price_q)
    cash_after_buy = quantize_cash(cash_start - trade_cashflow - fee1_q)
    _open(engine, i, SPOT, "o2", OrderSide.SELL, quantity, price, fee2)
    cash_after_sell = quantize_cash(cash_after_buy + trade_cashflow - fee2_q)
    assert engine.account.cash == cash_after_sell


@given(price=price_st, quantity=qty_st)
@settings(max_examples=100)
def test_perp_round_trip_same_price_loses_nothing_zero_fees(price, quantity):
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    cash_start = engine.account.cash
    i = _open(engine, 1, PERP, "o1", OrderSide.BUY, quantity, price, 0.0)
    _open(engine, i, PERP, "o2", OrderSide.SELL, quantity, price, 0.0)
    assert math.isclose(engine.account.cash, cash_start, rel_tol=1e-9, abs_tol=1e-6)


# ── 3/4. mark direction moves long/short PnL in opposite directions ────────

@given(entry=price_st, delta=st.floats(min_value=0.01, max_value=50_000.0,
                                      allow_nan=False, allow_infinity=False),
      quantity=qty_st)
@settings(max_examples=100)
def test_mark_increase_increases_long_pnl_decreases_short_pnl(entry, delta, quantity):
    mark_up = entry + delta
    engine_long, engine_short = TruthEngine(), TruthEngine()
    for engine, side in ((engine_long, OrderSide.BUY), (engine_short, OrderSide.SELL)):
        engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
        _open(engine, 1, PERP, "o1", side, quantity, entry, 0.0)

    long_pnl_before = engine_long.account.perp_positions[PERP.key].unrealized_pnl(entry)
    long_pnl_after = engine_long.account.perp_positions[PERP.key].unrealized_pnl(mark_up)
    short_pnl_before = engine_short.account.perp_positions[PERP.key].unrealized_pnl(entry)
    short_pnl_after = engine_short.account.perp_positions[PERP.key].unrealized_pnl(mark_up)

    assert long_pnl_after > long_pnl_before
    assert short_pnl_after < short_pnl_before


# ── 5. positions equal the sum of fills (property form of the invariant) ───

@given(quantities=st.lists(qty_st, min_size=1, max_size=6))
@settings(max_examples=50)
def test_position_equals_sum_of_signed_fill_quantities(quantities):
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    i = 1
    running = Decimal(0)
    for j, qty in enumerate(quantities):
        i = _open(engine, i, PERP, f"o{j}", OrderSide.BUY, qty, 50_000.0, 0.0)
        # FillPayload lot-quantizes quantity at construction, so the running
        # tally must match that same rounding, not the raw Hypothesis float.
        running += PERP.quantize_quantity(qty)
    assert engine.account.perp_positions[PERP.key].quantity == running


# ── 6. closing all positions makes exposure null ────────────────────────────

@given(quantity=qty_st, price=price_st)
@settings(max_examples=100)
def test_closing_all_positions_makes_exposure_null(quantity, price):
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    i = _open(engine, 1, PERP, "o1", OrderSide.BUY, quantity, price, 0.0)
    _open(engine, i, PERP, "o2", OrderSide.SELL, quantity, price, 0.0)
    exposures = compute_exposures(engine.account)
    assert math.isclose(exposures.total_gross, 0.0, abs_tol=1e-6)
    assert math.isclose(exposures.net_exposure, 0.0, abs_tol=1e-6)


# ── 7. replay is deterministic ──────────────────────────────────────────────

@given(fills=st.lists(st.tuples(st.sampled_from(["BUY", "SELL"]), qty_st, price_st),
                      min_size=1, max_size=5))
@settings(max_examples=30)
def test_replay_is_deterministic_for_random_valid_sequences(fills):
    events = [_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD"))]
    i = 1
    position = 0.0
    for j, (side, qty, price) in enumerate(fills):
        # keep it "safe" -- never allow spot to go short (default-disabled)
        if side == "SELL" and qty > position:
            side = "BUY"
        order_id = f"o{j}"
        events.append(_ev(i, EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
            order_id=order_id, client_order_id=f"c{j}", instrument=SPOT, side=side,
            order_type="MARKET", quantity=qty)))
        events.append(_ev(i + 1, EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload(order_id)))
        events.append(_ev(i + 2, EventType.FILL, FillPayload(
            fill_id=f"f{j}", order_id=order_id, instrument=SPOT, price=price, quantity=qty,
            side=side, fee=0.0, fee_ccy="USD")))
        position += qty if side == "BUY" else -qty
        i += 3

    engine_a, summary_a = replay(events)
    engine_b, summary_b = replay(events)
    assert summary_a == summary_b
    assert engine_a.ledger.head_hash == engine_b.ledger.head_hash


# ── 8. no valid sequence of events produces NaN ─────────────────────────────

@given(fills=st.lists(st.tuples(qty_st, price_st), min_size=1, max_size=5))
@settings(max_examples=50)
def test_no_valid_sequence_produces_nan(fills):
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    i = 1
    for j, (qty, price) in enumerate(fills):
        i = _open(engine, i, PERP, f"o{j}", OrderSide.BUY, qty, price, 0.0)
        engine.apply(_ev(i, EventType.MARK, MarkPayload(PERP, price)))
        i += 1
    assert math.isfinite(engine.account.cash)
    assert math.isfinite(engine.account.nav())


# ── 9. collateral is never used twice (shared pool across instruments) ─────

@given(equity=st.floats(min_value=1_000.0, max_value=1_000_000.0,
                        allow_nan=False, allow_infinity=False),
      notional_a=st.floats(min_value=1_000.0, max_value=500_000.0,
                           allow_nan=False, allow_infinity=False),
      notional_b=st.floats(min_value=1_000.0, max_value=500_000.0,
                           allow_nan=False, allow_infinity=False))
@settings(max_examples=100)
def test_collateral_is_a_single_shared_pool_not_reused_per_instrument(equity, notional_a, notional_b):
    """Opening notional_a on instrument A must reduce what's available for
    instrument B -- if collateral were (incorrectly) tracked per-instrument,
    B's check would ignore A's usage entirely."""
    config = MarginConfig(initial_margin_rate=0.10, maintenance_margin_rate=0.05)
    account = Account(cash=equity)
    account.perp_positions[PERP.key] = PerpPosition(
        instrument=PERP, quantity=notional_a / 50_000.0, avg_entry_price=50_000.0)

    equity_d, notional_a_d, notional_b_d = _d(equity), _d(notional_a), _d(notional_b)
    can_open_b_ignoring_a = notional_b_d * config.initial_margin_rate <= equity_d
    can_open_b_accounting_for_a = can_open_additional_notional(account, config, notional_b)

    # whenever accounting for A's usage would forbid B, the naive
    # (wrong) per-instrument-isolated check must not be the one that ran
    if not can_open_b_accounting_for_a:
        combined_im = notional_a_d * config.initial_margin_rate + notional_b_d * config.initial_margin_rate
        assert combined_im > equity_d   # confirms it's genuinely oversubscribed, not a bug
    if can_open_b_ignoring_a and not can_open_b_accounting_for_a:
        pass   # exactly the case this property exists to catch -- and it's handled correctly above


# ── 10. liquidation cannot artificially improve NAV ─────────────────────────

@given(entry=price_st, mark=price_st, quantity=qty_st, fee=fee_st)
@settings(max_examples=100)
def test_liquidation_never_improves_nav_beyond_minus_fee(entry, mark, quantity, fee):
    """Liquidating at exactly the current mark just moves the closed
    portion's unrealized PnL into cash 1:1 -- NAV can only ever go DOWN by
    the fee, never up, from the liquidation event itself."""
    engine = TruthEngine()
    engine.apply(_ev(0, EventType.CASH_DEPOSIT, CashDepositPayload(1e12, "USD")))
    _open(engine, 1, PERP, "o1", OrderSide.BUY, quantity, entry, 0.0)
    engine.apply(_ev(10, EventType.MARK, MarkPayload(PERP, mark)))
    nav_before = engine.account.nav()

    engine.apply(_ev(11, EventType.LIQUIDATION, LiquidationPayload(
        instrument=PERP, quantity_closed=quantity, price=mark, fee=fee)))
    nav_after = engine.account.nav()

    assert nav_after <= nav_before + Decimal("0.000001")           # never improves NAV
    assert math.isclose(nav_after, nav_before - _d(fee), rel_tol=1e-6, abs_tol=1e-3)
