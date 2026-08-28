"""tests/test_hedge_governor_backtest.py — hedge lié, borné, fermé sans long (Phase 37)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig
from src.institutional.risk.hedge_governor import HedgeGovernorV1, HedgeConfig


def _leg_types(res):
    return set(res.leg_ledger["leg_type"]) if len(res.leg_ledger) else set()


# 7. hedge seul (sans long) ne doit jamais ouvrir de short (run G du brief)
def test_hedge_without_long_opens_nothing(synthetic_load_enriched):
    """Phase 3: this machine (and any clean clone) has no local
    data/enriched/BTCUSDT_1h_enriched.parquet -- synthetic_load_enriched
    (tests/integration/conftest.py) substitutes a deterministic synthetic
    price series so MultiLegBacktester.run() has *some* valid prices to
    load. Only the structural invariant is asserted (no naked short opens
    without a long leg) -- never a P&L number, which synthetic data cannot
    honestly support."""
    cfg = MultiLegConfig(enable_long=False, enable_carry=False, enable_hedge=True)
    bt = MultiLegBacktester(long_engines=[], config=cfg, carry_assets=[])
    res = bt.run("2024-01-01", "2024-03-31")
    assert "SHORT_HEDGE" not in _leg_types(res)        # aucun short nu
    assert res.pnl_by_type["hedge"] == 0.0


# hedge governor: sans long exposure → NO_HEDGE, notional 0
def test_governor_no_hedge_without_long():
    g = HedgeGovernorV1()
    d = g.decide(equity=10000, long_exposure=0.0, beta_to_btc=1.0, btc_regime="HARD_BEAR")
    assert d.state == "NO_HEDGE" and d.hedge_notional == 0.0


# hedge notional borné par long exposure
def test_hedge_notional_le_long_exposure():
    g = HedgeGovernorV1(HedgeConfig(max_hedge_cap=0.9))
    g.decide(equity=10000, long_exposure=1000, beta_to_btc=5.0)        # peak
    d = g.decide(equity=9800, long_exposure=1000, beta_to_btc=5.0, btc_regime="HARD_BEAR")
    # beta_adj=1000*5*0.6=3000 mais borné par long_exposure=1000
    assert d.hedge_notional <= 1000.0 + 1e-6
