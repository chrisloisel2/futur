"""
tests/test_alpha20_risk.py
─────────────────────────────────────────────────────────────────────────────
Governor unifié ALPHA20_LOW_RISK (échelle DD, déclassement sur limite),
risque de venue, stress engine, jambe nue ≤ 30 s. Aucun réseau.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.execution.hedge_coordinator import PairPlan, PairState
from src.alpha20.risk import global_governor as gg
from src.alpha20.risk import stress_engine, venue_risk


def test_profile_matches_spec():
    p = gg.load_profile()
    assert (p.dd_reduce, p.dd_cash, p.dd_kill) == (0.010, 0.020, 0.025)
    assert p.naked_leg_max_s == 30 and p.margin_used_cap == 0.20


def test_dd_ladder():
    assert gg.evaluate({"drawdown": 0.005}).state == "risk_on"
    assert gg.evaluate({"drawdown": 0.012}).state == "risk_reduced"
    assert gg.evaluate({"drawdown": 0.021}).state == "cash"
    d = gg.evaluate({"drawdown": 0.03})
    assert d.state == "kill" and d.scale == 0.0 and "dd_kill" in d.reasons


def test_limit_breach_downgrades_one_notch():
    d = gg.evaluate({"drawdown": 0.0, "daily_loss": 0.006})
    assert d.state == "risk_reduced" and "daily_loss" in d.reasons
    d = gg.evaluate({"drawdown": 0.012, "margin_used": 0.25})
    assert d.state == "cash"                     # reduced + breach → cash
    d = gg.evaluate({"drawdown": 0.0, "naked_leg_age_s": 45})
    assert d.state == "risk_reduced" and "naked_leg_age_s" in d.reasons


def test_venue_unsecured_cap():
    pos = [{"venue": "hyperliquid", "margin_usdt": 30000,
            "unsettled_pnl_usdt": 5000}]
    b = venue_risk.breaches(pos, {"hyperliquid": 2000}, nav_usdt=200000)
    assert "hyperliquid" in b and abs(b["hyperliquid"] - 0.185) < 1e-9
    assert venue_risk.breaches(pos, {}, nav_usdt=400000) == {}


def test_stress_engine_all_scenarios():
    state = {"nav_usdt": 200000, "gross_usdt": 150000, "net_delta_usdt": 8000,
             "borrow_usdt": 20000, "funding_ann_usdt": 9000,
             "spread_cost_bp_gross": 2.0,
             "venues": {"binance_usdm": 25000, "hyperliquid": 10000},
             "stable_collateral_usdt": 50000,
             "legs": [{"notional_usdt": 90000, "hedged": True},
                      {"notional_usdt": 5000, "hedged": False}]}
    r = stress_engine.run_all(state)
    assert set(r) == {"funding_flip", "borrow_x4", "spread_x5", "gap_20pct",
                      "vol_x3", "stablecoin_down_10pct", "venue_down_24h",
                      "leg_liquidated", "stale_data", "partial_fills"}
    assert abs(r["stablecoin_down_10pct"] - 0.025) < 1e-9      # 5000/200000
    assert abs(r["venue_down_24h"] - 0.125) < 1e-9             # 25000/200000
    assert abs(r["gap_20pct"] - (8000 * .2 + 5000 * .2) / 200000) < 1e-9
    assert stress_engine.worst(state) == max(r.values())


def test_naked_leg_watchdog():
    plan = PairPlan("p1", [{"venue": "binance_usdm", "symbol": "BTCUSDT",
                            "side": 1, "qty": 1.0, "kind": "spot"},
                           {"venue": "binance_usdm", "symbol": "BTCUSDT",
                            "side": -1, "qty": 1.0, "kind": "perp"}])
    st = PairState(plan)
    assert st.naked_age_s(1_000_000) == 0.0      # rien de rempli
    st.fills[0] = {"ts_ms": 1_000_000, "qty": 1.0}
    assert not st.must_unwind(1_000_000 + 29_000)
    assert st.must_unwind(1_000_000 + 31_000)    # contrat 30 s violé
    st.fills[1] = {"ts_ms": 1_032_000, "qty": 1.0}
    assert st.naked_age_s(1_100_000) == 0.0      # paire complète
