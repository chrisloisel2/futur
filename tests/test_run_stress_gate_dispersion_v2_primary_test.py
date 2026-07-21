"""
tests/test_run_stress_gate_dispersion_v2_primary_test.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 : seuil causal groupé, ancrage exact de la cible, effet primaire,
bootstrap par blocs calendaires. Synthétique, déterministe.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.normalize_stress_gate_dispersion_v2 as N
import scripts.run_stress_gate_dispersion_v2_primary_test as P

HOUR = 3_600_000
BASE = N.SIGNAL_START_MS


def _row(symbol, pair_available_at, interval, dispersion):
    dt = N.decision_timestamp_for(pair_available_at)
    return {"symbol": symbol, "pair_available_at": pair_available_at,
           "binance_interval_hours": interval, "bybit_interval_hours": interval,
           "raw_dispersion": dispersion, "decision_timestamp": dt}


def test_grouped_threshold_never_mixes_symbol_or_interval():
    rows = []
    # BTC 8h : dispersion constante basse
    for i in range(200):
        rows.append(_row("BTCUSDT", BASE + i * 8 * HOUR, 8, 0.0001))
    # SOL 2h : dispersion constante haute -> ne doit jamais influencer le seuil BTC 8h
    for i in range(200):
        rows.append(_row("SOLUSDT", BASE + i * 2 * HOUR, 2, 0.01))
    df = P.add_grouped_threshold(rows)
    btc = df[(df.symbol == "BTCUSDT")]
    # seuil BTC bien en dessous de la dispersion SOL (0.01) -> pas de contamination
    valid = btc[btc.threshold_available]
    assert (valid.stress_threshold < 0.001).all()


def test_grouped_threshold_is_causal_shift_one():
    rows = []
    for i in range(200):
        d = 0.0001
        rows.append(_row("BTCUSDT", BASE + i * 8 * HOUR, 8, d))
    # pic isolé à l'indice 199 (dernier) : ne doit pas influencer SON PROPRE seuil
    rows[-1]["raw_dispersion"] = 1.0
    df = P.add_grouped_threshold(rows)
    last = df.sort_values("pair_available_at").iloc[-1]
    assert last.threshold_available
    assert last.stress_threshold < 0.001   # le pic ne s'auto-dépasse pas


def test_insufficient_history_gives_threshold_unavailable_not_borrowed():
    rows = [_row("BTCUSDT", BASE + i * 8 * HOUR, 8, 0.0001) for i in range(10)]  # < Z_MIN=180
    df = P.add_grouped_threshold(rows)
    assert not df["threshold_available"].any()


def test_reference_price_is_open_not_close():
    symbol = "BTCUSDT"
    decision_ts = N.decision_timestamp_for(BASE)
    markprice = {symbol: {}}
    for i in range(int(N.FORWARD_HORIZON_MS / N.BAR_STEP_MS) + 1):
        ts = decision_ts + i * N.BAR_STEP_MS
        # open=100, close=999 (bien différent) pour détecter une inversion open/close
        markprice[symbol][ts] = [ts, 100.0, 105.0, 95.0, 999.0]
    df = pd.DataFrame([{"symbol": symbol, "decision_timestamp": decision_ts}])
    out = P.add_targets(df, markprice)
    assert out.iloc[0]["reference_price"] == 100.0   # open, jamais 999.0 (close)


def test_forward_trough_uses_low_and_incomplete_window_is_none():
    symbol = "BTCUSDT"
    decision_ts = N.decision_timestamp_for(BASE)
    n_bars = int(N.FORWARD_HORIZON_MS / N.BAR_STEP_MS)
    markprice = {symbol: {}}
    for i in range(n_bars + 1):
        ts = decision_ts + i * N.BAR_STEP_MS
        low = 50.0 if i == 3 else 90.0   # un creux net à la 4e barre
        markprice[symbol][ts] = [ts, 100.0, 105.0, low, 95.0]
    df = pd.DataFrame([{"symbol": symbol, "decision_timestamp": decision_ts}])
    out = P.add_targets(df, markprice)
    assert out.iloc[0]["forward_trough"] == 50.0
    assert out.iloc[0]["forward_max_drawdown"] == pytest.approx(50.0 / 100.0 - 1.0)

    # trou au milieu -> fenêtre incomplète -> None, jamais interpolé
    del markprice[symbol][decision_ts + 5 * N.BAR_STEP_MS]
    out2 = P.add_targets(df, markprice)
    assert out2.iloc[0]["forward_max_drawdown"] is None


def test_primary_effect_positive_when_stress_precedes_worse_drawdowns():
    df = pd.DataFrame([
        {"threshold_available": True, "is_stress": True, "loss_magnitude": 0.05},
        {"threshold_available": True, "is_stress": True, "loss_magnitude": 0.06},
        {"threshold_available": True, "is_stress": False, "loss_magnitude": 0.01},
        {"threshold_available": True, "is_stress": False, "loss_magnitude": 0.02},
    ])
    delta = P.primary_effect(df)
    assert delta == pytest.approx(0.055 - 0.015)


def test_bootstrap_deterministic_with_fixed_seed():
    rng_rows = []
    rs = np.random.RandomState(0)
    for i in range(400):
        rng_rows.append({"pair_available_at": BASE + i * 8 * HOUR,
                         "threshold_available": True,
                         "is_stress": bool(rs.rand() < 0.1),
                         "loss_magnitude": float(rs.rand())})
    df = pd.DataFrame(rng_rows)
    b1 = P.moving_calendar_block_bootstrap(df, resamples=200, seed=42)
    b2 = P.moving_calendar_block_bootstrap(df, resamples=200, seed=42)
    np.testing.assert_array_equal(b1, b2)


def test_bootstrap_resamples_assets_jointly_per_block():
    """Deux actifs partageant les mêmes dates de bloc doivent être tirés
    ENSEMBLE (même bloc choisi pour les deux) — pas indépendamment."""
    rows = []
    for i in range(30):
        t = BASE + i * 8 * HOUR
        rows.append({"pair_available_at": t, "threshold_available": True,
                    "is_stress": (i % 5 == 0), "loss_magnitude": 0.01, "symbol": "A"})
        rows.append({"pair_available_at": t, "threshold_available": True,
                    "is_stress": (i % 5 == 0), "loss_magnitude": 0.02, "symbol": "B"})
    df = pd.DataFrame(rows)
    blocks = P.assign_calendar_blocks(df)
    # les deux actifs à la même date doivent tomber dans le même bloc
    for t in df["pair_available_at"].unique():
        b = blocks[df["pair_available_at"] == t].unique()
        assert len(b) == 1
