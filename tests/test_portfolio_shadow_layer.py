"""tests/test_portfolio_shadow_layer.py — PORTFOLIO_SHADOW_LAYER (Live Alpha
Lab, phase "PHASE PORTFOLIO FORWARD"). Covers: intent adapters, correlation
dedup, budget/exposure caps, the WHALE_LSR_SCREEN gate, and step()
idempotency (replaying the same target twice produces zero delta the 2nd
time, matching the discipline already established for the alpha runners).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.live_alpha_lab.gate import active_screen_symbols, apply_screen
from src.institutional.live_alpha_lab.intents import PortfolioIntent, build_intents
from src.institutional.live_alpha_lab.portfolio import aggregate, load_state, step
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def _intent(alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
           correlation_family="FAM1", instrument="BTCUSDT", direction="LONG",
           frac=1.0, confidence=1.0, multi_leg=False, leg_b=None):
    ts = _ts("2026-09-01T00:00:00Z")
    return PortfolioIntent(
        alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
        correlation_family=correlation_family, timestamp=ts, instrument=instrument,
        direction=direction, target_position_fraction=frac, confidence=confidence,
        horizon_hours=4.0, expiry=ts + pd.Timedelta(hours=4),
        multi_leg=multi_leg, leg_instrument_b=leg_b,
    )


# ── adapters ────────────────────────────────────────────────────────────────

def test_build_intents_liq_cascade():
    df = pd.DataFrame([{"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"}])
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
            "correlation_family": "LIQ_CASCADE_DETECTOR"}
    out = build_intents("LIQ_CASCADE_REPEAT_V1", entry, df)
    assert len(out) == 1
    assert out[0].direction == "LONG"
    assert out[0].target_position_fraction == 1.0
    assert out[0].horizon_hours == 4.0


def test_build_intents_short_covering_zone_weights():
    df = pd.DataFrame([
        {"timestamp": _ts("2026-09-01T00:00:00Z"), "asset": "AAVEUSDT",
         "direction": "LONG", "decision_zone": "A_TRADE", "p_success": 0.8, "confidence": 0.9},
        {"timestamp": _ts("2026-09-01T01:00:00Z"), "asset": "ETHUSDT",
         "direction": "LONG", "decision_zone": "B_SHADOW", "p_success": 0.6, "confidence": 0.5},
    ])
    entry = {"family": "liquidation", "risk_bucket": "LIQUIDATION_FAMILY",
            "correlation_family": "OI_STATE_FAMILY"}
    out = build_intents("SHORT_COVERING_CONTINUATION_V1", entry, df)
    assert len(out) == 2
    a_trade = next(i for i in out if i.instrument == "AAVEUSDT")
    b_shadow = next(i for i in out if i.instrument == "ETHUSDT")
    assert a_trade.target_position_fraction > b_shadow.target_position_fraction


def test_build_intents_unknown_alpha_raises():
    with pytest.raises(KeyError):
        build_intents("NOT_A_REAL_ALPHA", {}, pd.DataFrame([{"x": 1}]))


def test_build_intents_gate_alpha_returns_empty_not_error():
    assert build_intents("WHALE_LSR_SCREEN_V1", {}, pd.DataFrame([{"x": 1}])) == []


def test_build_intents_empty_decisions_returns_empty():
    assert build_intents("LIQ_CASCADE_REPEAT_V1", {}, pd.DataFrame()) == []


def test_build_intents_cross_sectional_equal_weights_real_basket_size():
    """Regression : le ledger réel (V1 et V2) n'a PAS de colonne bucket_size,
    et utilise `event_time` (pas `timestamp`) -- un premier build assumait
    les deux à tort (defaulting silencieusement à un panier de taille 1,
    donc 100% du budget par nom au lieu d'un partage équitable). Vérifie
    contre le schéma réel : basket_size = nombre de lignes au même event_time."""
    df = pd.DataFrame([
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"},
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "ETHUSDT"},
        {"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "SOLUSDT"},
        {"event_time": _ts("2026-09-08T00:00:00Z"), "symbol": "BTCUSDT"},  # rebalance suivant, panier de 1
    ])
    entry = {"family": "cross_sectional", "risk_bucket": "CROSS_SECTIONAL_FAMILY",
            "correlation_family": "CROSS_SECTIONAL_XSMOM"}
    out = build_intents("CROSS_SECTIONAL_MOMENTUM_LIVE_V1", entry, df)
    assert len(out) == 4
    week1 = [i for i in out if i.timestamp == _ts("2026-09-01T00:00:00Z")]
    week2 = [i for i in out if i.timestamp == _ts("2026-09-08T00:00:00Z")]
    assert all(i.target_position_fraction == pytest.approx(1.0 / 3) for i in week1)
    assert week2[0].target_position_fraction == 1.0


def test_build_intents_cross_sectional_v2_uses_same_adapter():
    df = pd.DataFrame([{"event_time": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT"}])
    entry = {"family": "cross_sectional", "risk_bucket": "CROSS_SECTIONAL_FAMILY",
            "correlation_family": "CROSS_SECTIONAL_XSMOM"}
    out = build_intents("CROSS_SECTIONAL_MOMENTUM_LIVE_V2", entry, df)
    assert len(out) == 1 and out[0].target_position_fraction == 1.0


# ── gate ────────────────────────────────────────────────────────────────────

def test_screen_blocks_long_on_screened_symbol():
    assert apply_screen(1.0, "BTCUSDT", "LONG", {"BTCUSDT"}) == 0.0


def test_screen_does_not_affect_unscreened_symbol():
    assert apply_screen(1.0, "ETHUSDT", "LONG", {"BTCUSDT"}) == 1.0


def test_active_screen_symbols_respects_lookback_window():
    df = pd.DataFrame([
        {"timestamp": _ts("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT", "screen_flag": True},
        {"timestamp": _ts("2026-08-01T00:00:00Z"), "symbol": "ETHUSDT", "screen_flag": True},
    ])
    out = active_screen_symbols(df, _ts("2026-09-01T02:00:00Z"), lookback_hours=24.0)
    assert out == {"BTCUSDT"}   # ETHUSDT trop vieux, hors fenêtre


# ── dedup / aggregate ─────────────────────────────────────────────────────

def test_aggregate_dedups_correlated_intents_same_instrument():
    """Deux alphas du MÊME correlation_family sur le MÊME instrument ne
    doivent jamais sommer -- le plus fort target_position_fraction gagne."""
    intents = [
        _intent(alpha_id="A1", correlation_family="LIQ_CASCADE_DETECTOR", frac=0.5),
        _intent(alpha_id="A2", correlation_family="LIQ_CASCADE_DETECTOR", frac=1.0),
    ]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=1.0, max_per_asset_fraction=1.0)
    target, owner = aggregate(intents, config, screened_symbols=set())
    assert len(target) == 1
    assert owner["BTCUSDT"] == "A2"   # le plus confiant/plus fort gagne


def test_aggregate_does_not_dedup_different_correlation_families():
    intents = [
        _intent(alpha_id="A1", correlation_family="FAM1", frac=0.5),
        _intent(alpha_id="A2", correlation_family="FAM2", frac=0.5),
    ]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)
    target, owner = aggregate(intents, config, screened_symbols=set())
    # même instrument, familles différentes -> les deux contribuent (sommés), pas dédupliqués
    assert target["BTCUSDT"] > 0


def test_aggregate_respects_per_asset_cap():
    intents = [_intent(frac=1.0)]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=1.0, max_per_asset_fraction=0.05)
    target, _ = aggregate(intents, config, screened_symbols=set())
    assert abs(target["BTCUSDT"]) <= 100_000 * 0.05 + 1e-6


def test_aggregate_respects_gross_cap():
    intents = [_intent(instrument="BTCUSDT", frac=1.0), _intent(instrument="ETHUSDT", frac=1.0,
                                                                 correlation_family="FAM2")]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=0.5, max_per_asset_fraction=1.0)
    target, _ = aggregate(intents, config, screened_symbols=set())
    gross = sum(abs(v) for v in target.values())
    assert gross <= 100_000 * 0.5 + 1e-6


def test_aggregate_screen_zeroes_out_target():
    intents = [_intent(instrument="BTCUSDT", frac=1.0)]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0})
    target, _ = aggregate(intents, config, screened_symbols={"BTCUSDT"})
    assert "BTCUSDT" not in target or target["BTCUSDT"] == 0


def test_aggregate_multi_leg_produces_two_opposite_instruments():
    intents = [_intent(instrument="BTCUSDT_QUARTERLY", direction="LONG", frac=1.0,
                       multi_leg=True, leg_b="BTCUSDT_PERP",
                       risk_bucket="RELATIVE_VALUE_FAMILY", correlation_family="CALENDAR_BASIS_CURVE")]
    config = PortfolioConfig(name="TEST", capital_eur=100_000,
                             family_budget_fraction={"RELATIVE_VALUE_FAMILY": 1.0},
                             max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)
    target, _ = aggregate(intents, config, screened_symbols=set())
    assert target["BTCUSDT_QUARTERLY"] > 0
    assert target["BTCUSDT_PERP"] < 0
    assert abs(target["BTCUSDT_QUARTERLY"]) == abs(target["BTCUSDT_PERP"])


# ── step / idempotency ──────────────────────────────────────────────────────

def test_step_idempotent_second_call_zero_delta(tmp_path, monkeypatch):
    import src.institutional.live_alpha_lab.portfolio as portfolio_mod
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    name = "TEST_IDEMPOTENT"
    config = PortfolioConfig(name=name, capital_eur=100_000)
    target = {"BTCUSDT": 10_000.0}
    ts = _ts("2026-09-01T00:00:00Z")

    s1 = step(name, config, target, ts)
    fees_after_first = s1.cumulative_fees_usd
    assert fees_after_first > 0   # un vrai delta a été exécuté

    s2 = step(name, config, target, ts + pd.Timedelta(minutes=5))
    assert s2.cumulative_fees_usd == fees_after_first   # aucun nouveau delta -> aucun nouveau coût
    assert s2.positions["BTCUSDT"] == 10_000.0


def test_step_delta_only_on_change(tmp_path, monkeypatch):
    import src.institutional.live_alpha_lab.portfolio as portfolio_mod
    monkeypatch.setattr(portfolio_mod, "PORTFOLIO_DIR", tmp_path)
    name = "TEST_DELTA"
    config = PortfolioConfig(name=name, capital_eur=100_000)
    ts = _ts("2026-09-01T00:00:00Z")

    step(name, config, {"BTCUSDT": 10_000.0}, ts)
    s2 = step(name, config, {"BTCUSDT": 15_000.0}, ts + pd.Timedelta(hours=1))
    # le cout doit correspondre au DELTA (5000), pas au nouveau notional total (15000)
    expected_fee_2nd_step = 5_000.0 * 5.0 / 10_000  # TAKER_FEE_BPS
    assert s2.cumulative_fees_usd == pytest.approx(
        10_000.0 * 5.0 / 10_000 + expected_fee_2nd_step, rel=1e-6)
