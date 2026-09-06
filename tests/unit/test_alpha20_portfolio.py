"""
tests/test_alpha20_portfolio.py
─────────────────────────────────────────────────────────────────────────────
Simulateur portefeuille commun (frais sur turnover, cap de marge partagé,
panne de venue, provision fiscale, frontière) et allocateur robuste (borne
basse, plafonds capacité/venue/ES). Aucun réseau.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.contracts import CostSnapshot, SleeveStats
from src.alpha20.portfolio import robust_allocator as ra
from src.alpha20.portfolio.capacity_model import capacity_eur
from src.alpha20.portfolio.joint_simulator import SleeveInput, frontier, simulate

SNAP = CostSnapshot(venue="binance_usdm", instrument="PERP", maker_bp=2.0,
                    taker_bp=5.0, as_of="2026-07-19", source="assumed",
                    slippage_bp=2.0)
IDX = pd.date_range("2026-01-01", periods=90, freq="1D", tz="UTC")


def _sleeve(name, mu, weight=0.4, venue="binance_usdm", **kw):
    rng = np.random.RandomState(hash(name) % 2 ** 31)
    r = pd.Series(mu + 0.001 * rng.randn(len(IDX)), index=IDX)
    return SleeveInput(name=name, net_returns=r, weight=weight, costs=SNAP,
                       venue=venue, **kw)


def test_simulator_conservation_and_fees():
    s = _sleeve("carry", 0.0004, weight=0.5)
    res = simulate([s], 200000.0, borrow_ann=0.08)
    su = res.summary
    # conservation : equity finale = capital + pnl sleeves − frais − borrow − taxes
    total = sum(res.by_sleeve.values()) - su["fees_eur"] - su["borrow_eur"] \
        - su["tax_provision_eur"]
    assert abs((res.equity.iloc[-1] - 200000.0) - total) < 1.0
    assert su["fees_eur"] > 0                    # le déploiement initial coûte
    assert su["tax_provision_eur"] > 0           # mois positifs provisionnés


def test_margin_cap_shared_scales_down():
    # 4 sleeves × 0.5E × 10 % IM = 20 % IM → au cap ; ×2 gross → scaling ÷2
    sl = [_sleeve(f"s{i}", 0.0002, weight=1.0) for i in range(4)]
    res = simulate(sl, 200000.0, borrow_ann=0.08)
    assert res.summary["capital_immobilise_max"] <= 0.20 * res.equity.max() + 1.0


def test_venue_outage_freezes_sleeve():
    s1 = _sleeve("hl", 0.0100, venue="hyperliquid")     # très rentable
    out = {"venue": "hyperliquid", "start": IDX[10], "days": 5}
    with_out = simulate([s1], 200000.0, 0.08, venue_outage=out)
    without = simulate([s1], 200000.0, 0.08)
    # la panne gèle 5 jours de PnL → forcément moins d'equity
    assert with_out.equity.iloc[-1] < without.equity.iloc[-1]


def test_capacity_caps_gross():
    s = _sleeve("small", 0.0004, weight=0.8, capacity_eur=10000.0)
    res = simulate([s], 200000.0, 0.08)
    assert res.summary["capacity_utilization"]["small"] > 1.0  # sur-demandé
    # le gross réellement déployé est plafonné → PnL ∝ 10k, pas 160k
    assert abs(res.by_sleeve["small"]) < 10000 * 0.0006 * len(IDX) * 2


def test_frontier_monotone_columns():
    s = _sleeve("carry", 0.0004, weight=0.4)
    fr = frontier([s], 200000.0, 0.08, gross_grid=[0.5, 1.0, 1.5])
    assert list(fr.columns) == ["gross_mult", "net_return_ann", "max_drawdown",
                                "capital_immobilise_max", "max_capacity_util"]
    assert fr["capital_immobilise_max"].is_monotonic_increasing


def test_allocator_lcb_gates_and_caps():
    idx = pd.date_range("2026-01-01", periods=200, freq="1D", tz="UTC")
    rng = np.random.RandomState(7)
    good = pd.Series(0.0008 + 0.002 * rng.randn(200), index=idx)
    noise = pd.Series(0.0001 + 0.02 * rng.randn(200), index=idx)   # LCB < 0
    stats = [
        SleeveStats("good", good, capacity_eur=500000, venue="binance_usdm",
                    rotation_cost_bp=8.0),
        SleeveStats("noise", noise, capacity_eur=500000, venue="binance_usdm",
                    rotation_cost_bp=8.0),
    ]
    w = ra.allocate(stats, nav_eur=200000.0)
    assert w["good"] > 0 and w["noise"] == 0.0   # borne basse, pas moyenne
    # capacité : sleeve minuscule plafonné à capacity/nav
    stats[0] = SleeveStats("good", good, capacity_eur=20000,
                           venue="binance_usdm", rotation_cost_bp=8.0)
    w = ra.allocate(stats, nav_eur=200000.0)
    assert w["good"] <= 0.10 + 1e-9


def test_allocator_short_history_not_allocatable():
    idx = pd.date_range("2026-07-01", periods=10, freq="1D", tz="UTC")
    s = SleeveStats("young", pd.Series(0.01, index=idx), 1e6,
                    "binance_usdm", 8.0)
    assert ra.allocate([s], 200000.0)["young"] == 0.0


def test_capacity_model_min_wins():
    c = capacity_eur(adv_usdt=10_000_000, depth_usdt_within_bps=400_000,
                     participation=0.01, safety=0.25)
    assert c == 100_000.0                        # min(100k, 100k) — égalité voulue
    assert capacity_eur(adv_usdt=10_000_000) == 100_000.0
