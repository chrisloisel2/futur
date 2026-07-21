"""
tests/test_run_stress_gate_dispersion_v2_phase3.py
─────────────────────────────────────────────────────────────────────────────
Phase 3 : stabilité temporelle, leave-one-out (actif/année/épisode),
panel exact-ms, contrôles causaux de la régression incrémentale.
Synthétique, déterministe.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.normalize_stress_gate_dispersion_v2 as N
import scripts.run_stress_gate_dispersion_v2_phase3 as P3

HOUR = 3_600_000
DAY = 24 * HOUR


def test_split_by_period_boundary_is_exclusive_on_period1():
    cutoff_ms = int(datetime.fromisoformat(
        P3.PERIOD_SPLIT_ISO.replace("Z", "+00:00")).timestamp() * 1000)
    df = pd.DataFrame({"pair_available_at": [cutoff_ms - 1, cutoff_ms, cutoff_ms + 1]})
    p1, p2 = P3.split_by_period(df)
    assert list(p1["pair_available_at"]) == [cutoff_ms - 1]
    assert list(p2["pair_available_at"]) == [cutoff_ms, cutoff_ms + 1]


def test_leave_one_asset_out_removes_only_that_asset():
    df = pd.DataFrame({"symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"], "x": [1, 2, 3]})
    out = P3.leave_one_asset_out(df, "BTCUSDT")
    assert list(out["symbol"]) == ["ETHUSDT"]


def test_leave_one_year_out_removes_only_that_year():
    y2023 = int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    y2024 = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    df = pd.DataFrame({"pair_available_at": [y2023, y2024]})
    out = P3.leave_one_year_out(df, 2023)
    assert list(out["pair_available_at"]) == [y2024]


def test_identify_stress_episodes_groups_within_24h_gap():
    base = N.SIGNAL_START_MS
    df = pd.DataFrame({
        "pair_available_at": [base, base + 10 * HOUR, base + 20 * HOUR,   # même épisode (<=24h d'écart)
                              base + 50 * HOUR],                          # nouvel épisode (>24h après le précédent)
        "is_stress": [True, True, True, True],
    })
    episodes = P3.identify_stress_episodes(df)
    assert len(episodes) == 2
    assert len(episodes[0]["indices"]) == 3
    assert len(episodes[1]["indices"]) == 1


def test_identify_stress_episodes_ignores_non_stress_rows():
    base = N.SIGNAL_START_MS
    df = pd.DataFrame({
        "pair_available_at": [base, base + 1 * HOUR],
        "is_stress": [True, False],
    })
    episodes = P3.identify_stress_episodes(df)
    assert len(episodes) == 1
    assert len(episodes[0]["indices"]) == 1


def test_leave_one_episode_out_keeps_non_stress_rows():
    base = N.SIGNAL_START_MS
    df = pd.DataFrame({
        "pair_available_at": [base, base + 1 * HOUR],
        "is_stress": [True, False],
    }, index=[10, 11])
    episodes = P3.identify_stress_episodes(df)
    out = P3.leave_one_episode_out(df, episodes[0])
    assert list(out.index) == [11]   # la ligne non-stress reste


def test_exact_ms_subpanel_filters_zero_offset_only():
    df = pd.DataFrame({"timestamp_offset_ms": [0, 5, -3, 0]})
    out = P3.exact_ms_subpanel(df)
    assert len(out) == 2


def test_trailing_controls_never_use_bars_at_or_after_decision_timestamp():
    symbol = "BTCUSDT"
    decision_ts = N.SIGNAL_START_MS + 1000 * N.BAR_STEP_MS
    mp = {}
    n_trailing = N.FORWARD_HORIZON_MS // N.BAR_STEP_MS
    for i in range(n_trailing):
        ts = decision_ts - N.FORWARD_HORIZON_MS + i * N.BAR_STEP_MS
        mp[ts] = [ts, 100.0, 101.0, 99.0, 100.0]
    # barre à decision_ts elle-même a un low absurde (0.01) : si elle était
    # utilisée par erreur, trailing_dd serait catastrophique
    mp[decision_ts] = [decision_ts, 100.0, 101.0, 0.01, 100.0]
    dd, rv = P3.trailing_24h_controls(symbol, decision_ts, mp)
    assert dd is not None
    assert dd > -0.5   # pas contaminé par le low=0.01 de la barre de décision


def test_trailing_controls_missing_bar_returns_none_not_interpolated():
    symbol = "BTCUSDT"
    decision_ts = N.SIGNAL_START_MS + 1000 * N.BAR_STEP_MS
    mp = {}
    n_trailing = N.FORWARD_HORIZON_MS // N.BAR_STEP_MS
    for i in range(n_trailing):
        ts = decision_ts - N.FORWARD_HORIZON_MS + i * N.BAR_STEP_MS
        mp[ts] = [ts, 100.0, 101.0, 99.0, 100.0]
    del mp[decision_ts - N.FORWARD_HORIZON_MS + 5 * N.BAR_STEP_MS]   # trou
    dd, rv = P3.trailing_24h_controls(symbol, decision_ts, mp)
    assert dd is None and rv is None
