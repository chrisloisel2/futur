"""
data_v2/events/costs.py
─────────────────────────────────────────────────────────────────────────────
Event Scanner V1 cost model, per reports/EVENT_SCANNER_V1_PROTOCOL.md:
"cost x1 = 2 x taker fee + 1 tick of slippage per side". A round trip has
two sides (entry, exit); each side pays the taker fee once and loses one
tick to slippage, so cost_x1 = 2*taker_fee_rate + 2*(tick_size/entry_price).
cost_x2 doubles the whole figure as a stress multiplier (existing project
convention, e.g. scripts/backtest_ctrend_v1.py's cost_mult).

taker_fee_rate defaults to configs/alpha20.yaml's binance_usdm taker
convention (5bp) -- the only sourced figure available per research/
edge_factory/liquidation_relative_reversal_v1/DATA_INVENTORY.yaml's own
fees section ("assumed defaults ... remain the only usable figures").

Pre-unblinding fix (2026-08-10, review round 3): an earlier version of
scanner.py subtracted a single flat COST_X1/COST_X2 (30/60bps) from every
event's return regardless of symbol or price level -- this can both kill a
real edge on a cheap/fine-tick symbol and understate cost on an expensive/
coarse-tick one. Per-event cost (this module) is now the PRIMARY figure;
the flat constants are kept as an explicit, separately-reported STRESS
test (STRESS_COST_X1/X2), never silently substituted for the real formula.
"""
from __future__ import annotations

DEFAULT_TAKER_FEE_RATE = 0.0005  # 5bp, configs/alpha20.yaml binance_usdm taker

STRESS_COST_X1 = 0.0030
STRESS_COST_X2 = STRESS_COST_X1 * 2


def compute_event_cost(
    entry_price: float, tick_size: float, *, taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE
) -> tuple[float, float]:
    """Returns (cost_x1, cost_x2) as fractions of entry_price."""
    if entry_price is None or entry_price <= 0 or tick_size is None or tick_size < 0:
        return float("nan"), float("nan")
    slippage_per_side = tick_size / entry_price
    cost_x1 = 2 * taker_fee_rate + 2 * slippage_per_side
    return cost_x1, cost_x1 * 2
