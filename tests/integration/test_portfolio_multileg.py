"""tests/test_portfolio_multileg.py — invariants no-naked-short + backtester multi-leg (Phase 37)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.portfolio.position import PositionLeg, PortfolioPosition
from src.institutional.portfolio.invariants import (
    check_position_invariants, check_portfolio_invariants, InvariantLimits, InvariantViolation,
)


def _leg(pid, leg_type, qty=1.0, price=100.0):
    return PositionLeg("l", pid, "BTCUSDT", leg_type, "2026-01-01", price, qty, qty * price, mark_price=price)


def test_naked_short_raises():
    # un SHORT_HEDGE sans linked_position_id = short nu → crash
    pos = PortfolioPosition("p", "PORTFOLIO_HEDGE", "H", "BTCUSDT", "2026-01-01",
                            [_leg("p", "SHORT_HEDGE")], linked_position_id=None)
    with pytest.raises(InvariantViolation):
        check_position_invariants(pos, InvariantLimits())


def test_hedge_linked_ok():
    pos = PortfolioPosition("p", "PORTFOLIO_HEDGE", "H", "BTCUSDT", "2026-01-01",
                            [_leg("p", "SHORT_HEDGE")], linked_position_id="LONG_BOOK")
    check_position_invariants(pos, InvariantLimits())  # ne lève pas


def test_carry_short_without_spot_raises():
    pos = PortfolioPosition("p", "DELTA_NEUTRAL_CARRY", "C", "BTCUSDT", "2026-01-01",
                            [_leg("p", "CARRY_SHORT_PERP")])
    with pytest.raises(InvariantViolation):
        check_position_invariants(pos, InvariantLimits())


def test_carry_delta_breach_raises():
    pos = PortfolioPosition("p", "DELTA_NEUTRAL_CARRY", "C", "BTCUSDT", "2026-01-01",
                            [_leg("p", "CARRY_LONG_SPOT", 1.0), _leg("p", "CARRY_SHORT_PERP", 0.5)])
    with pytest.raises(InvariantViolation):
        check_position_invariants(pos, InvariantLimits())


def test_hedge_over_cap_raises():
    # hedge 40% > cap 30%, lié → mais cap global doit crasher
    long = PortfolioPosition("L", "DIRECTIONAL_LONG", "E", "BTCUSDT", "2026-01-01", [_leg("L", "LONG_SPOT", 50.0)])
    hedge = PortfolioPosition("H", "PORTFOLIO_HEDGE", "H", "BTCUSDT", "2026-01-01",
                              [_leg("H", "SHORT_HEDGE", 40.0)], linked_position_id="L")
    with pytest.raises(InvariantViolation):
        check_portfolio_invariants([long, hedge], equity=10000.0, limits=InvariantLimits())


def test_exposures_computed():
    long = PortfolioPosition("L", "DIRECTIONAL_LONG", "E", "BTCUSDT", "2026-01-01", [_leg("L", "LONG_SPOT", 20.0)])
    exp = check_portfolio_invariants([long], equity=10000.0, limits=InvariantLimits())
    assert exp["net_long_exposure"] == pytest.approx(0.20)  # 20*100/10000
    assert exp["short_hedge_exposure"] == 0.0


# ── backtester smoke (carry-only sur données réelles, fenêtre courte) ─────────
def test_multileg_carry_only_runs_no_naked_short():
    from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig
    cfg = MultiLegConfig(enable_long=False, enable_carry=True, enable_hedge=False)
    bt = MultiLegBacktester(long_engines=[], config=cfg, carry_assets=["BTCUSDT"])
    res = bt.run("2024-01-01", "2024-06-30")  # ne crash pas = invariants OK
    # carry : price legs s'annulent, le PnL vient du funding
    assert res.pnl_by_type["directional"] == 0.0
    assert res.pnl_by_type["hedge"] == 0.0
    assert "carry_funding" in res.pnl_by_type
    assert len(res.equity) > 100
