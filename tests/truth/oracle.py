"""tests/truth/oracle.py -- independent reference-computation oracle.

Phase 4B commit 4 ("validation indépendante"): every function here is a
hand-written, from-scratch reimplementation of one piece of the accounting
convention documented in docs/TRUTH_ACCOUNTING.md, computed directly from
Decimal arithmetic. This module imports NOTHING from src.futur.truth --
no Account, no TruthEngine, no Ledger, no margin.py, no numeric.py, not
even ProductSpec. Its only job is to independently predict what the real
engine SHOULD produce for a hand-designed scenario, so that when
test_reference_fixtures.py compares its answer to the real engine's
answer, a mismatch is a genuine finding about the production code -- not
two branches of the same implementation agreeing with themselves.

Deliberately re-derives things numeric.py already provides (its own
`d()`/`q()` helpers, its own quantum) rather than importing them, so a bug
shared between "the engine's rounding" and "the oracle's rounding" isn't
structurally impossible to catch.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

QUANTUM = Decimal("0.00000001")


def d(x) -> Decimal:
    """Same rule the engine commits to: float always through str(), never
    Decimal(float) directly -- re-derived here, not imported, so this
    module has no dependency on numeric.to_decimal being correct."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def q(x) -> Decimal:
    return d(x).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def spot_buy_cash_delta(quantity, price, fee) -> Decimal:
    """Buying spends quantity*price + fee."""
    return q(-(d(quantity) * d(price)) - d(fee))


def spot_sell_cash_delta(quantity, price, fee) -> Decimal:
    """Selling receives quantity*price - fee."""
    return q(d(quantity) * d(price) - d(fee))


def perp_weighted_avg_entry(old_qty, old_avg, add_qty, add_price) -> Decimal:
    old_qty, old_avg, add_qty, add_price = d(old_qty), d(old_avg), d(add_qty), d(add_price)
    new_qty = old_qty + add_qty
    if new_qty == 0:
        return Decimal(0)
    return (old_qty * old_avg + add_qty * add_price) / new_qty


def perp_realized_pnl(avg_entry, exit_price, closing_qty, is_long) -> Decimal:
    """PnL on the CLOSED portion of a perp position: (exit - entry) for a
    long, (entry - exit) for a short, times the quantity being closed."""
    sign = Decimal(1) if is_long else Decimal(-1)
    return q((d(exit_price) - d(avg_entry)) * d(closing_qty) * sign)


def perp_unrealized(quantity_signed, avg_entry, mark) -> Decimal:
    """quantity_signed: positive for long, negative for short -- a single
    signed formula covers both, same as the sign convention documented for
    realized PnL above collapses to when closing_qty == |quantity_signed|."""
    return q(d(quantity_signed) * (d(mark) - d(avg_entry)))


def nav(cash, spot_positions_at_mark, perp_unrealized_pnls) -> Decimal:
    """spot_positions_at_mark: iterable of (quantity, mark) pairs.
    perp_unrealized_pnls: iterable of already-signed unrealized PnL
    Decimals (see perp_unrealized above)."""
    total = d(cash)
    for qty, mark in spot_positions_at_mark:
        total += d(qty) * d(mark)
    for u in perp_unrealized_pnls:
        total += d(u)
    return q(total)


def margin_required(notional, rate) -> Decimal:
    return q(d(notional) * d(rate))


def margin_available(nav_value, initial_margin_required) -> Decimal:
    return q(d(nav_value) - d(initial_margin_required))
