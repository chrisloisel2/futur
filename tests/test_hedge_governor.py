"""
tests/test_hedge_governor.py — Hedge Governor V1 (Phase 35).
Vérifie : pas de hedge sans long, sizing borné, ratio croît avec le DD,
DD en fenêtre glissante (ré-armement), garde-fous short.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.risk.hedge_governor import (
    HedgeGovernorV1, HedgeConfig, SHORT_DIRECTIONAL_ENABLED, NAKED_SHORT_ALLOWED,
)


def test_guardrails_constants():
    assert SHORT_DIRECTIONAL_ENABLED is False
    assert NAKED_SHORT_ALLOWED is False


def test_no_hedge_without_long():
    g = HedgeGovernorV1()
    d = g.decide(equity=10000, long_exposure=0.0, beta_to_btc=1.0)
    assert d.state == "NO_HEDGE" and d.hedge_notional == 0.0


def test_no_hedge_when_healthy():
    g = HedgeGovernorV1()
    d = g.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0, btc_regime="EXPANSION")
    assert d.state == "NO_HEDGE"


def test_hedge_ratio_increases_with_drawdown():
    g = HedgeGovernorV1()
    g.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0)      # peak 10000
    light = g.decide(equity=9880, long_exposure=5000, beta_to_btc=1.0)   # -1.2%
    heavy = g.decide(equity=9750, long_exposure=5000, beta_to_btc=1.0)   # -2.5%? cash
    assert light.hedge_ratio == 0.25
    # -2.1% → heavy 0.60 (avant le seuil cash 2.5%)
    g2 = HedgeGovernorV1()
    g2.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0)
    h = g2.decide(equity=9790, long_exposure=5000, beta_to_btc=1.0)  # -2.1%
    assert h.hedge_ratio == 0.60


def test_hedge_notional_bounded_by_cap_and_exposure():
    g = HedgeGovernorV1(HedgeConfig(max_hedge_cap=0.30))
    g.decide(equity=10000, long_exposure=9000, beta_to_btc=3.0)  # peak
    d = g.decide(equity=9820, long_exposure=9000, beta_to_btc=3.0, btc_regime="HARD_BEAR")  # -1.8%
    # beta_adj_long*ratio = 9000*3*0.6 = 16200, mais cap = 0.30*9820≈2946 et long=9000
    assert d.state == "BTC_PARTIAL_HEDGE"
    assert d.hedge_notional <= 0.30 * 9820 + 1e-6
    assert d.hedge_notional <= 9000


def test_hard_bear_forces_hedge():
    g = HedgeGovernorV1()
    g.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0, beta_to_eth=0.2)
    d = g.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0, beta_to_eth=0.2,
                 btc_regime="HARD_BEAR")
    assert d.state == "BTC_PARTIAL_HEDGE" and d.hedge_ratio == 0.60


def test_picks_higher_beta_asset():
    g = HedgeGovernorV1()
    g.decide(equity=10000, long_exposure=5000, beta_to_btc=0.2, beta_to_eth=1.0)
    d = g.decide(equity=9850, long_exposure=5000, beta_to_btc=0.2, beta_to_eth=1.0,
                 btc_regime="HARD_BEAR")  # -1.5%, hedge (pas kill)
    assert d.hedge_asset == "ETHUSDT" and d.state == "ETH_PARTIAL_HEDGE"


def test_kill_on_deep_drawdown():
    g = HedgeGovernorV1()
    g.decide(equity=10000, long_exposure=5000, beta_to_btc=1.0)
    d = g.decide(equity=9650, long_exposure=5000, beta_to_btc=1.0)  # -3.5%
    assert d.state == "KILL"


def test_rolling_dd_rearms_after_recovery():
    """Le DD glissant doit se ré-armer après récupération (≠ ratchet monotone)."""
    g = HedgeGovernorV1(HedgeConfig(dd_window_bars=5))
    for eq in [10000, 9700, 9700, 9700, 9700, 9700]:  # remplit la fenêtre au creux
        g.decide(equity=eq, long_exposure=5000, beta_to_btc=1.0)
    # une fois la fenêtre pleine de valeurs basses, le peak glissant redescend
    d = g.decide(equity=9700, long_exposure=5000, beta_to_btc=1.0)
    assert d.drawdown > -0.01  # DD quasi nul car peak fenêtre = niveau courant
