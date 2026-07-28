"""tests/truth/test_reference_fixtures.py -- Phase 4B commit 4: independent
validation against hand-computed reference fixtures.

Every expected value in this file comes from tests/truth/oracle.py -- a
from-scratch reimplementation of the accounting convention that imports no
reducer/mutation code from src.futur.truth (no Account, no TruthEngine,
no _apply_* method). Each scenario is built TWICE, independently, from the
same plain numbers: once as real Event/payload objects driving the real
TruthEngine, once as oracle.py Decimal arithmetic -- then compared. A
mismatch here is a finding about the production code, not a test asserting
its own implementation agrees with itself.

Covers: spot buy/sell with fees, perp long/short open, increase (weighted
average), reduce (partial close), flip, partial fills, funding both
signs, borrow, liquidation with fee AND slippage (the NAV identity),
insufficient margin, multiple assets sharing collateral, MATCH/MISMATCH
reconciliation, and a full terminal close of both spot and perp.
"""
from __future__ import annotations

from decimal import Decimal

from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import (
    BorrowCostPayload,
    CashDepositPayload,
    Event,
    EventType,
    FeePayload,
    FillPayload,
    FundingPayload,
    LiquidationPayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
)
from src.futur.truth.margin import MarginConfig, can_open_additional_notional, compute_margin_state
from src.futur.truth.orders import OrderSide, OrderType
from src.futur.truth.reconciliation import ExternalSnapshot, reconcile
from tests.truth import oracle

SPOT = ProductSpec(venue="ORACLE", symbol="BTCUSD", type=ProductType.SPOT,
                  base_ccy="BTC", quote_ccy="USD", tick_size="0.01", lot_size="0.0001")
PERP = ProductSpec(venue="ORACLE", symbol="BTCUSD-PERP", type=ProductType.LINEAR_PERP,
                  base_ccy="BTC", quote_ccy="USD", tick_size="0.01", lot_size="0.0001")
PERP2 = ProductSpec(venue="ORACLE", symbol="ETHUSD-PERP", type=ProductType.LINEAR_PERP,
                   base_ccy="ETH", quote_ccy="USD", tick_size="0.01", lot_size="0.0001")


def _ev(i, event_type, payload) -> Event:
    ts = "2026-01-01T00:00:00Z"
    return Event(event_id=f"e{i}", event_type=event_type, ts_event=ts, ts_received=ts, payload=payload)


def _deposit(engine, i, amount) -> int:
    engine.apply(_ev(i, EventType.CASH_DEPOSIT, CashDepositPayload(amount, "USD")))
    return i + 1


def _trade(engine, i, instrument, order_id, side, quantity, price, fee=0.0) -> int:
    """submit -> ack -> fill, one clean execution."""
    engine.apply(_ev(i, EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id=order_id, client_order_id=f"c-{order_id}", instrument=instrument,
        side=side.value, order_type=OrderType.MARKET.value, quantity=quantity)))
    engine.apply(_ev(i + 1, EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload(order_id)))
    engine.apply(_ev(i + 2, EventType.FILL, FillPayload(
        fill_id=f"f-{order_id}", order_id=order_id, instrument=instrument, price=price,
        quantity=quantity, side=side.value, fee=fee, fee_ccy="USD")))
    return i + 3


# ── 1/2. spot buy and sell with fees ────────────────────────────────────

def test_spot_buy_with_fee_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 200_000.0)
    _trade(engine, i, SPOT, "o1", OrderSide.BUY, 2.0, 50_000.0, fee=15.0)

    expected_cash = Decimal(200000) + oracle.spot_buy_cash_delta(2.0, 50_000.0, 15.0)
    assert engine.account.cash == expected_cash == Decimal("99985.00000000")
    assert engine.account.spot_positions[SPOT.key].quantity == Decimal("2.0")


def test_spot_buy_then_sell_with_fees_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 200_000.0)
    i = _trade(engine, i, SPOT, "o1", OrderSide.BUY, 2.0, 50_000.0, fee=15.0)
    cash_after_buy = engine.account.cash
    _trade(engine, i, SPOT, "o2", OrderSide.SELL, 0.8, 52_000.0, fee=10.0)

    expected_cash = cash_after_buy + oracle.spot_sell_cash_delta(0.8, 52_000.0, 10.0)
    assert engine.account.cash == expected_cash == Decimal("141575.00000000")
    assert engine.account.spot_positions[SPOT.key].quantity == Decimal("1.2")


# ── 3/4. perp long/short open ───────────────────────────────────────────

def test_perp_long_open_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 100_000.0)
    _trade(engine, i, PERP, "o1", OrderSide.BUY, 1.5, 40_000.0, fee=12.0)

    assert engine.account.cash == Decimal(100000) - Decimal("12.0") == Decimal("99988.00000000")
    pos = engine.account.perp_positions[PERP.key]
    assert pos.quantity == Decimal("1.5")
    assert pos.avg_entry_price == oracle.d(40_000.0)


def test_perp_short_open_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 100_000.0)
    _trade(engine, i, PERP, "o1", OrderSide.SELL, 1.0, 40_000.0, fee=8.0)

    assert engine.account.cash == Decimal(100000) - Decimal("8.0") == Decimal("99992.00000000")
    pos = engine.account.perp_positions[PERP.key]
    assert pos.quantity == Decimal("-1.0")
    assert pos.avg_entry_price == oracle.perp_weighted_avg_entry(0, 0, -1.0, 40_000.0) == Decimal(40000)


# ── 5/6/7. increase, reduce, flip ────────────────────────────────────────

def test_perp_increase_weighted_average_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 300_000.0)
    i = _trade(engine, i, PERP, "o1", OrderSide.BUY, 2.0, 50_000.0)
    _trade(engine, i, PERP, "o2", OrderSide.BUY, 3.0, 60_000.0)

    expected_avg = oracle.perp_weighted_avg_entry(2.0, 50_000.0, 3.0, 60_000.0)
    assert expected_avg == Decimal(56000)
    pos = engine.account.perp_positions[PERP.key]
    assert pos.quantity == Decimal("5.0")
    assert pos.avg_entry_price == expected_avg
    assert engine.account.cash == Decimal("300000.00000000")   # zero fees, pure increase


def test_perp_partial_reduce_realizes_pnl_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 300_000.0)
    i = _trade(engine, i, PERP, "o1", OrderSide.BUY, 2.0, 50_000.0)
    i = _trade(engine, i, PERP, "o2", OrderSide.BUY, 3.0, 60_000.0)   # avg now 56000, qty 5.0
    cash_before = engine.account.cash
    _trade(engine, i, PERP, "o3", OrderSide.SELL, 2.0, 58_000.0, fee=5.0)   # close 2 of 5

    realized = oracle.perp_realized_pnl(avg_entry=56_000.0, exit_price=58_000.0,
                                        closing_qty=2.0, is_long=True)
    assert realized == Decimal(4000)
    expected_cash = cash_before + realized - Decimal("5.0")
    assert engine.account.cash == expected_cash == Decimal("303995.00000000")
    pos = engine.account.perp_positions[PERP.key]
    assert pos.quantity == Decimal("3.0")
    assert pos.avg_entry_price == Decimal(56000)   # unchanged for the remainder


def test_perp_flip_long_to_short_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 200_000.0)
    i = _trade(engine, i, PERP, "o1", OrderSide.BUY, 2.0, 50_000.0)
    cash_before_flip = engine.account.cash
    _trade(engine, i, PERP, "o2", OrderSide.SELL, 5.0, 52_000.0, fee=20.0)   # closes 2, opens 3 short

    realized = oracle.perp_realized_pnl(avg_entry=50_000.0, exit_price=52_000.0,
                                        closing_qty=2.0, is_long=True)
    assert realized == Decimal(4000)
    expected_cash = cash_before_flip + realized - Decimal("20.0")
    assert engine.account.cash == expected_cash == Decimal("203980.00000000")
    pos = engine.account.perp_positions[PERP.key]
    assert pos.quantity == Decimal("-3.0")
    assert pos.avg_entry_price == Decimal(52000)   # fresh position, entry = fill price


# ── 8. partial fills across two FILL events on one order ───────────────

def test_partial_fills_across_two_fill_events_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 300_000.0)
    engine.apply(_ev(i, EventType.ORDER_SUBMITTED, OrderSubmittedPayload(
        order_id="o1", client_order_id="c1", instrument=SPOT, side="BUY",
        order_type="MARKET", quantity=3.0)))
    engine.apply(_ev(i + 1, EventType.ORDER_ACKNOWLEDGED, OrderAcknowledgedPayload("o1")))
    engine.apply(_ev(i + 2, EventType.FILL, FillPayload(
        fill_id="f1", order_id="o1", instrument=SPOT, price=50_000.0, quantity=1.2,
        side="BUY", fee=5.0, fee_ccy="USD")))
    cash_after_first = engine.account.cash
    engine.apply(_ev(i + 3, EventType.FILL, FillPayload(
        fill_id="f2", order_id="o1", instrument=SPOT, price=50_500.0, quantity=1.8,
        side="BUY", fee=7.0, fee_ccy="USD")))

    expected_after_first = Decimal(300000) + oracle.spot_buy_cash_delta(1.2, 50_000.0, 5.0)
    assert cash_after_first == expected_after_first == Decimal("239995.00000000")
    expected_after_second = cash_after_first + oracle.spot_buy_cash_delta(1.8, 50_500.0, 7.0)
    assert engine.account.cash == expected_after_second == Decimal("149088.00000000")
    assert engine.account.spot_positions[SPOT.key].quantity == Decimal("3.0")
    assert engine.account.orders["o1"].status.value == "FILLED"


# ── 9/10. funding both signs, borrow ────────────────────────────────────

def test_funding_both_signs_and_borrow_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 100_000.0)
    engine.apply(_ev(i, EventType.FUNDING, FundingPayload(PERP, 25.5, "USD")))
    engine.apply(_ev(i + 1, EventType.FUNDING, FundingPayload(PERP, -13.25, "USD")))
    engine.apply(_ev(i + 2, EventType.BORROW_COST, BorrowCostPayload(42.75, "USD")))

    expected = Decimal(100000) + oracle.d(25.5) - oracle.d(13.25) - oracle.d(42.75)
    assert engine.account.cash == expected == Decimal("99969.50000000")


def test_standalone_fee_funding_and_borrow_never_double_counted():
    """All three flow types acting on top of one already-fee-charged
    position -- oracle sums the SAME flat list of signed deltas the
    documented convention describes; if the engine's cash disagreed, that
    would mean something got folded into `cash` more than once (or not at
    all)."""
    engine = TruthEngine()
    i = _deposit(engine, 0, 100_000.0)
    i = _trade(engine, i, PERP, "o1", OrderSide.BUY, 1.0, 50_000.0, fee=10.0)
    engine.apply(_ev(i, EventType.FUNDING, FundingPayload(PERP, 5.0, "USD")))
    engine.apply(_ev(i + 1, EventType.FEE, FeePayload(3.0, "USD", reason="maintenance")))
    engine.apply(_ev(i + 2, EventType.BORROW_COST, BorrowCostPayload(7.0, "USD")))
    engine.apply(_ev(i + 3, EventType.FUNDING, FundingPayload(PERP, -2.0, "USD")))

    deltas = [Decimal(100000), -Decimal("10.0"), Decimal("5.0"),
             -Decimal("3.0"), -Decimal("7.0"), -Decimal("2.0")]
    expected = sum(deltas[1:], start=deltas[0])
    assert engine.account.cash == expected == Decimal("99983.00000000")


# ── 11. liquidation with fee AND slippage -- the NAV identity ──────────

def test_liquidation_with_fee_and_slippage_nav_identity_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 100_000.0)
    i = _trade(engine, i, PERP, "o1", OrderSide.BUY, 2.0, 50_000.0)
    engine.apply(_ev(i, EventType.MARK, MarkPayload(PERP, 50_000.0)))
    i += 1

    nav_before_expected = oracle.nav(cash=engine.account.cash, spot_positions_at_mark=[],
                                     perp_unrealized_pnls=[oracle.perp_unrealized(2.0, 50_000.0, 50_000.0)])
    nav_before_actual = engine.account.nav()
    assert nav_before_actual == nav_before_expected == Decimal("100000.00000000")

    engine.apply(_ev(i, EventType.LIQUIDATION, LiquidationPayload(
        instrument=PERP, quantity_closed=1.0, price=50_000.0, fee=30.0, slippage=12.0)))

    realized = oracle.perp_realized_pnl(50_000.0, 50_000.0, 1.0, is_long=True)
    assert realized == 0
    expected_cash_after = Decimal(100000) + realized - Decimal("30.0") - Decimal("12.0")
    assert engine.account.cash == expected_cash_after == Decimal("99958.00000000")

    nav_after_expected = oracle.nav(cash=engine.account.cash, spot_positions_at_mark=[],
                                    perp_unrealized_pnls=[oracle.perp_unrealized(1.0, 50_000.0, 50_000.0)])
    nav_after_actual = engine.account.nav()
    assert nav_after_actual == nav_after_expected == Decimal("99958.00000000")

    # the required identity, verified with BOTH the oracle's own numbers
    # and the engine's own numbers:
    assert nav_before_expected - nav_after_expected == Decimal("42.0")   # fee(30) + slippage(12)
    assert nav_before_actual - nav_after_actual == Decimal("42.0")


# ── 12. insufficient margin ──────────────────────────────────────────────

def test_insufficient_margin_rejected_matches_oracle():
    config = MarginConfig(initial_margin_rate=0.20, maintenance_margin_rate=0.10)
    engine = TruthEngine(margin_config=config)
    _deposit(engine, 0, 10_000.0)

    required_too_big = oracle.margin_required(60_000.0, 0.20)
    assert required_too_big == Decimal(12000) > Decimal(10000)   # oracle: not enough
    assert can_open_additional_notional(engine.account, config, 60_000.0) is False

    required_ok = oracle.margin_required(40_000.0, 0.20)
    assert required_ok == Decimal(8000) <= Decimal(10000)   # oracle: enough
    assert can_open_additional_notional(engine.account, config, 40_000.0) is True


# ── 13. multiple assets sharing collateral ──────────────────────────────

def test_multiple_assets_share_one_collateral_pool_matches_oracle():
    config = MarginConfig()   # default 0.10 / 0.05
    engine = TruthEngine(margin_config=config)
    i = _deposit(engine, 0, 100_000.0)
    _trade(engine, i, PERP, "o1", OrderSide.BUY, 2.0, 50_000.0)   # notional 100,000

    state = compute_margin_state(engine.account, config)
    expected_im_a = oracle.margin_required(100_000.0, 0.10)
    assert state.initial_margin_required == expected_im_a == Decimal(10000)
    expected_available = oracle.margin_available(Decimal(100000), expected_im_a)
    assert state.margin_available == expected_available == Decimal(90000)

    # a SECOND instrument's notional is checked against what's LEFT after A,
    # not against the full 100,000 equity in isolation
    assert can_open_additional_notional(engine.account, config, 850_000.0) is True    # 85,000 <= 90,000
    assert can_open_additional_notional(engine.account, config, 950_000.0) is False   # 95,000 >  90,000

    # actually open the second position (a DIFFERENT instrument, PERP2) and
    # verify the combined margin state, not just the hypothetical check above
    _trade(engine, i + 3, PERP2, "o2", OrderSide.BUY, 2.0, 30_000.0)   # notional 60,000

    combined_state = compute_margin_state(engine.account, config)
    expected_im_combined = expected_im_a + oracle.margin_required(60_000.0, 0.10)
    assert expected_im_combined == Decimal(16000)
    assert combined_state.initial_margin_required == expected_im_combined
    expected_combined_available = oracle.margin_available(Decimal(100000), expected_im_combined)
    assert combined_state.margin_available == expected_combined_available == Decimal(84000)


# ── 14. MATCH / MISMATCH reconciliation ─────────────────────────────────

def test_reconciliation_match_and_mismatch_against_oracle_numbers():
    engine = TruthEngine()
    i = _deposit(engine, 0, 200_000.0)
    _trade(engine, i, SPOT, "o1", OrderSide.BUY, 2.0, 50_000.0, fee=15.0)

    expected_cash = Decimal(200000) + oracle.spot_buy_cash_delta(2.0, 50_000.0, 15.0)
    assert expected_cash == engine.account.cash

    matching_snapshot = ExternalSnapshot(venue="ORACLE_VENUE", cash=expected_cash,
                                        nav=expected_cash, spot_positions={SPOT.key: Decimal("2.0")})
    result = reconcile(engine.account, matching_snapshot)
    assert result.verdict == "MATCH"

    mismatching_snapshot = ExternalSnapshot(venue="ORACLE_VENUE", cash=expected_cash - Decimal(500),
                                           nav=expected_cash, spot_positions={SPOT.key: Decimal("2.0")})
    result = reconcile(engine.account, mismatching_snapshot)
    assert result.verdict == "MISMATCH"
    assert result.cash_diff == Decimal(500)


# ── 15. full terminal close (spot AND perp both back to zero) ──────────

def test_full_terminal_close_of_spot_and_perp_matches_oracle():
    engine = TruthEngine()
    i = _deposit(engine, 0, 300_000.0)
    i = _trade(engine, i, SPOT, "o1", OrderSide.BUY, 2.0, 50_000.0, fee=10.0)
    i = _trade(engine, i, SPOT, "o2", OrderSide.SELL, 2.0, 51_000.0, fee=10.0)
    i = _trade(engine, i, PERP, "o3", OrderSide.BUY, 1.0, 40_000.0, fee=5.0)
    _trade(engine, i, PERP, "o4", OrderSide.SELL, 1.0, 42_000.0, fee=5.0)

    spot_buy = oracle.spot_buy_cash_delta(2.0, 50_000.0, 10.0)
    spot_sell = oracle.spot_sell_cash_delta(2.0, 51_000.0, 10.0)
    perp_open = -Decimal("5.0")
    perp_close_realized = oracle.perp_realized_pnl(40_000.0, 42_000.0, 1.0, is_long=True)
    perp_close = perp_close_realized - Decimal("5.0")
    expected_cash = Decimal(300000) + spot_buy + spot_sell + perp_open + perp_close
    assert expected_cash == Decimal("303970.00000000")
    assert engine.account.cash == expected_cash

    assert engine.account.spot_positions[SPOT.key].quantity == 0
    assert engine.account.perp_positions[PERP.key].quantity == 0
    assert engine.account.perp_positions[PERP.key].avg_entry_price == 0
    # nothing open -- NAV is exactly cash, independent of any mark
    assert engine.account.nav() == expected_cash


# ── explicit NAV = cash + spot_mv + perp_unrealized decomposition ──────

def test_nav_decomposition_identity_with_both_spot_and_perp_open():
    engine = TruthEngine()
    i = _deposit(engine, 0, 300_000.0)
    i = _trade(engine, i, SPOT, "o1", OrderSide.BUY, 1.0, 50_000.0)
    i = _trade(engine, i, PERP, "o2", OrderSide.BUY, 2.0, 40_000.0)
    engine.apply(_ev(i, EventType.MARK, MarkPayload(SPOT, 53_000.0)))
    engine.apply(_ev(i + 1, EventType.MARK, MarkPayload(PERP, 38_000.0)))

    spot_mv_expected = oracle.d(1.0) * oracle.d(53_000.0)
    perp_u_expected = oracle.perp_unrealized(2.0, 40_000.0, 38_000.0)
    nav_expected = oracle.nav(engine.account.cash, [(1.0, 53_000.0)], [perp_u_expected])

    assert engine.account.spot_market_value() == spot_mv_expected == Decimal(53000)
    assert engine.account.perp_unrealized_pnl() == perp_u_expected == Decimal(-4000)
    assert engine.account.nav() == nav_expected
    assert engine.account.nav() == (
        engine.account.cash + engine.account.spot_market_value() + engine.account.perp_unrealized_pnl())
