"""tests/test_carry_accounting.py — funding accrual + carry delta-neutral comptabilité (Phase 37)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.portfolio.position import PositionLeg, PortfolioPosition
from src.institutional.portfolio.funding import accrue_funding_leg
from src.institutional.engines.carry_basis.funding_gate import classify_funding_regime, CARRY_OK


def _leg(leg_type, qty=1.0, price=100.0):
    return PositionLeg("l", "p", "BTCUSDT", leg_type, "2026-01-01", price, qty, qty * price, mark_price=price)


# 3-4. funding signe
def test_funding_positive_credits_short_perp():
    l = _leg("CARRY_SHORT_PERP")
    pnl = accrue_funding_leg(l, funding_rate=0.0001, mark_price=100.0)
    assert pnl > 0 and l.funding_pnl_cum == pnl


def test_funding_negative_debits_short_perp():
    l = _leg("CARRY_SHORT_PERP")
    assert accrue_funding_leg(l, -0.0001, 100.0) < 0


def test_spot_leg_no_funding():
    assert accrue_funding_leg(_leg("CARRY_LONG_SPOT"), 0.0001, 100.0) == 0.0


# 5. carry delta ~ 0
def test_carry_delta_neutral():
    pos = PortfolioPosition("p", "DELTA_NEUTRAL_CARRY", "CARRY", "BTCUSDT", "2026-01-01", [
        _leg("CARRY_LONG_SPOT", 1.0), _leg("CARRY_SHORT_PERP", 1.0)])
    assert abs(pos.net_delta_notional()) < 1e-6
    assert pos.gross_notional() == pytest.approx(200.0)


# 14. PnL séparés : carry price ~ s'annule, funding = source
def test_carry_price_legs_cancel_funding_is_pnl():
    long = _leg("CARRY_LONG_SPOT", 1.0, 100.0)
    short = _leg("CARRY_SHORT_PERP", 1.0, 100.0)
    # le prix monte à 110 : spot +10, short perp -10 → net price 0
    long.mark_price = 110.0; short.mark_price = 110.0
    assert long.price_pnl() == pytest.approx(10.0)
    assert short.price_pnl() == pytest.approx(-10.0)
    assert long.price_pnl() + short.price_pnl() == pytest.approx(0.0)
    # funding crédité au short
    accrue_funding_leg(short, 0.0002, 110.0)
    assert short.funding_pnl_cum > 0


# long spot pnl
def test_long_spot_pnl():
    l = _leg("LONG_SPOT", 2.0, 100.0); l.mark_price = 105.0
    assert l.price_pnl() == pytest.approx(10.0)


# short hedge pnl (profite quand prix baisse)
def test_short_hedge_profits_on_drop():
    l = _leg("SHORT_HEDGE", 1.0, 100.0); l.mark_price = 90.0
    assert l.price_pnl() == pytest.approx(10.0)


# funding gate : régime positif stable → carry OK ; négatif → non
def test_funding_gate_positive_stable():
    idx = pd.date_range("2026-01-01", periods=30, freq="8H", tz="UTC")
    fwin = pd.Series([8e-5] * 30, index=idx)  # funding stable positif
    assert classify_funding_regime(fwin) == CARRY_OK


def test_funding_gate_negative_blocks():
    idx = pd.date_range("2026-01-01", periods=30, freq="8H", tz="UTC")
    fwin = pd.Series([-5e-5] * 30, index=idx)
    assert classify_funding_regime(fwin) != CARRY_OK
