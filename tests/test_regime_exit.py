"""tests/test_regime_exit.py + intra-position governor (Phase 43-44)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.portfolio.regime_exit import should_exit_long_on_regime_flip
from src.institutional.risk.intra_position_governor import decide_position_risk, IntraGovernorConfig


# ── regime flip exit ─────────────────────────────────────────────────────────
def test_long_holds_in_bull():
    assert should_exit_long_on_regime_flip("DIRECTIONAL_LONG", "BULL") is False


def test_long_holds_in_recovery():
    assert should_exit_long_on_regime_flip("DIRECTIONAL_LONG", "RECOVERY") is False


def test_long_holds_through_neutral_hysteresis():
    # NEUTRAL = on tient (hystérésis anti-whipsaw) ; le DD governor protège
    assert should_exit_long_on_regime_flip("DIRECTIONAL_LONG", "NEUTRAL") is False


def test_long_exits_on_hostile_regimes():
    for r in ("BEAR", "CRASH", "UNKNOWN"):
        assert should_exit_long_on_regime_flip("DIRECTIONAL_LONG", r) is True


def test_carry_not_exited_by_regime_flip():
    assert should_exit_long_on_regime_flip("DELTA_NEUTRAL_CARRY", "BEAR") is False


def test_hedge_not_exited_by_regime_flip():
    assert should_exit_long_on_regime_flip("PORTFOLIO_HEDGE", "CRASH") is False


# ── intra-position governor (close-only) ─────────────────────────────────────
def test_hold_when_calm():
    assert decide_position_risk("DIRECTIONAL_LONG", 0.002, -0.005) == "HOLD"


def test_close_position_on_position_dd():
    assert decide_position_risk("DIRECTIONAL_LONG", 0.022, -0.01) == "CLOSE_POSITION"
    assert decide_position_risk("DIRECTIONAL_LONG", 0.012, -0.01) == "HOLD"  # 1.2% < 2% (anti-whipsaw)


def test_close_all_longs_on_portfolio_dd():
    assert decide_position_risk("DIRECTIONAL_LONG", 0.0, -0.026) == "CLOSE_ALL_DIRECTIONAL_LONGS"


def test_kill_on_deep_portfolio_dd():
    assert decide_position_risk("DIRECTIONAL_LONG", 0.0, -0.031) == "KILL"


def test_carry_not_closed_by_position_dd():
    # le DD position ne ferme QUE les longs directionnels (carry géré ailleurs)
    assert decide_position_risk("DELTA_NEUTRAL_CARRY", 0.05, -0.01) == "HOLD"


def test_no_reduce_half_in_simple_version():
    # version simple = close-only, jamais REDUCE_HALF
    actions = {decide_position_risk("DIRECTIONAL_LONG", dd, pdd)
               for dd in (0.0, 0.008, 0.012) for pdd in (0.0, -0.021, -0.026, -0.031)}
    assert "REDUCE_HALF" not in actions
