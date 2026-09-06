"""
tests/test_capacity.py — capacité par alpha
(src/institutional/live_alpha_lab/capacity.py) et comptage des ordres
plafonnés (portfolio.PortfolioState).

Le mode d'échec à rendre impossible : qu'un symbole dont on ne connaît PAS la
liquidité soit traité comme illimité. Une lacune de données passerait alors
pour une bonne nouvelle -- « aucun ordre plafonné » -- ce qui est exactement
l'inverse de ce qu'elle signifie.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.institutional.live_alpha_lab import capacity as C


def _probes(rows):
    """rows: (symbol, top_bid, top_ask, quote_vol_24h)"""
    return pd.DataFrame([{
        "probe_at": pd.Timestamp("2026-09-06T09:00:00Z"), "symbol": s,
        "spread_bps": 1.0, "top_bid_notional_usd": tb, "top_ask_notional_usd": ta,
        "quote_volume_24h_usd": qv, "trade_count_24h": 1000,
    } for s, tb, ta, qv in rows])


def test_top_of_book_is_the_mean_of_both_sides():
    liq = C.liquidity_by_symbol(_probes([("AUSDT", 100.0, 300.0, 1e6)]))
    assert C.capacity_notional("AUSDT", "TOP_OF_BOOK", liquidity=liq) == pytest.approx(200.0)


def test_adv_fraction_is_prorated_by_horizon():
    liq = C.liquidity_by_symbol(_probes([("AUSDT", 100.0, 100.0, 24_000_000.0)]))
    # 1 % de (24 M x 4/24) = 1 % de 4 M = 40 000
    cap = C.capacity_notional("AUSDT", "ADV_FRACTION", horizon_hours=4.0, liquidity=liq)
    assert cap == pytest.approx(40_000.0)
    cap24 = C.capacity_notional("AUSDT", "ADV_FRACTION", horizon_hours=24.0, liquidity=liq)
    assert cap24 == pytest.approx(240_000.0)


def test_unknown_symbol_is_unmeasurable_not_unlimited():
    liq = C.liquidity_by_symbol(_probes([("AUSDT", 100.0, 100.0, 1e6)]))
    assert C.capacity_notional("INCONNUUSDT", "TOP_OF_BOOK", liquidity=liq) is None
    assert C.capacity_notional("INCONNUUSDT", "ADV_FRACTION", liquidity=liq) is None


def test_missing_volume_does_not_fall_back_to_the_book():
    liq = C.liquidity_by_symbol(_probes([("AUSDT", 100.0, 100.0, float("nan"))]))
    assert C.capacity_notional("AUSDT", "TOP_OF_BOOK", liquidity=liq) == pytest.approx(100.0)
    assert C.capacity_notional("AUSDT", "ADV_FRACTION", liquidity=liq) is None


def test_binding_rate_counts_unmeasurable_separately():
    """Un symbole non mesurable n'est ni « plafonné » ni « passé » : il est
    compté à part, sinon l'absence de donnée gonflerait le taux de succès."""
    liq = C.liquidity_by_symbol(_probes([("AUSDT", 100.0, 100.0, 1e6)]))
    orders = pd.DataFrame([
        {"symbol": "AUSDT", "notional_usd": 50.0},     # sous le plafond
        {"symbol": "AUSDT", "notional_usd": 500.0},    # au-dessus
        {"symbol": "INCONNUUSDT", "notional_usd": 1e9},  # non mesurable
    ])
    r = C.binding_rate(orders, "TOP_OF_BOOK", liquidity=liq)
    assert r["n_orders"] == 3
    assert r["n_measurable"] == 2
    assert r["n_unmeasurable"] == 1
    assert r["n_capped"] == 1
    assert r["pct_capped"] == 50.0          # sur les mesurables, pas sur le total
    assert r["notional_refused_usd"] == pytest.approx(400.0)


def test_alpha_capacity_follows_the_symbols_actually_traded():
    """Un alpha qui ne touche que des symboles minces n'hérite pas de la
    liquidité de BTC parce que BTC est dans l'univers déclaré."""
    liq = C.liquidity_by_symbol(_probes([
        ("MINCEUSDT", 50.0, 50.0, 1e6), ("BTCUSDT", 300_000.0, 300_000.0, 1e10)]))
    caps = C.alpha_capacity(pd.Series(["MINCEUSDT"] * 9 + ["BTCUSDT"]),
                            "TOP_OF_BOOK", liquidity=liq)
    assert caps["n_measurable"] == 10
    assert caps["per_trade_median_usd"] == pytest.approx(50.0)
    assert caps["per_trade_p10_usd"] == pytest.approx(50.0)


def test_unknown_policy_raises():
    with pytest.raises(ValueError):
        C.capacity_notional("BTCUSDT", "PAS_UNE_POLITIQUE")


def test_open_interest_policy_needs_its_own_input():
    """La politique actuelle du simulateur n'est PAS dérivable de la sonde de
    carnet : elle a besoin de l'open interest, qui vient d'ailleurs."""
    assert C.capacity_notional("BTCUSDT", "OPEN_INTEREST") is None
    assert C.capacity_notional("BTCUSDT", "OPEN_INTEREST",
                               oi_notional_usd=1e9) == pytest.approx(2e6)


# ── comptage des ordres plafonnés dans l'état du portefeuille ────────────────

def test_portfolio_state_carries_capped_counters():
    from src.institutional.live_alpha_lab.portfolio import PortfolioState
    st = PortfolioState()
    assert st.capped_order_count == 0
    assert st.capped_notional_usd == 0.0
