"""
tests/unit/test_event_scanner_v1.py
─────────────────────────────────────────────────────────────────────────────
Event Scanner V1 (reports/EVENT_SCANNER_V1_PROTOCOL.md) mechanics, on
SYNTHETIC data only -- this project's real Data V2 corpus is not
DATA_V2_READY yet (see the protocol's "Order of operations"), so these
tests prove the detector/label/scanner CODE is correct, not that any
family has edge. Do not read these numbers as findings.

Gate:
    python3 -m pytest tests/unit/test_event_scanner_v1.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.events.detectors import (
    _apply_cooldown, _trailing_percentile_rank, detect_crowding, detect_deleveraging,
    detect_forced_flow_reversal, detect_relative_value_dislocation,
)
from data_v2.events.labels import label_events, label_events_multi_symbol
from data_v2.events.scanner import build_family_report, _classify
from data_v2.events.schema import REQUIRED_COLUMNS, validate_schema


def _baseline_frame(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": idx,
        "symbol": "FOOUSDT",
        "close": 100 + np.cumsum(rng.normal(0, 0.05, n)),
        "oi": 1_000_000 + rng.normal(0, 1000, n).cumsum(),
        "oi_delta_pct_1h": rng.normal(0, 0.005, n),
        "aggressive_buy_usd": rng.uniform(1000, 5000, n),
        "aggressive_sell_usd": rng.uniform(1000, 5000, n),
        "signed_volume": rng.normal(0, 1000, n),
        "CVD": rng.normal(0, 1000, n).cumsum(),
        "funding_rate": rng.normal(0, 0.0001, n),
        "basis": rng.normal(0, 0.0005, n),
        "basis_z_1d": rng.normal(0, 0.5, n),
        "basis_z_7d": rng.normal(0, 0.5, n),
        "residual_return_1h": rng.normal(0, 0.002, n),
        "volume": rng.uniform(1e5, 5e5, n),
    })


# ── schema ──────────────────────────────────────────────────────────────


def test_schema_validates_required_columns():
    df = _baseline_frame(50)
    validate_schema(df)  # must not raise


def test_schema_rejects_missing_column():
    df = _baseline_frame(50).drop(columns=["oi"])
    with pytest.raises(ValueError):
        validate_schema(df)


# ── detector helpers ────────────────────────────────────────────────────


def test_trailing_percentile_rank_is_causal_and_correct():
    s = pd.Series([1.0, 2.0, 3.0, 100.0, 4.0])
    ranks = _trailing_percentile_rank(s, window_bars=3)
    # the 100.0 spike is the max of its own trailing window [2.0, 3.0, 100.0]
    assert ranks.iloc[3] == pytest.approx(1.0)
    # the next bar's window [3.0, 100.0, 4.0] -- 4.0 is NOT the max (100.0 still dominates)
    assert ranks.iloc[4] < 1.0


def test_apply_cooldown_keeps_only_first_fire_in_window():
    mask = pd.Series([False, True, True, True, False, True, False])
    out = _apply_cooldown(mask, cooldown_bars=3)
    assert out.tolist() == [False, True, False, False, False, True, False]


# ── DELEVERAGING ────────────────────────────────────────────────────────


def test_detect_deleveraging_fires_on_synthetic_extreme():
    df = _baseline_frame(500, seed=1)
    trigger_idx = 400
    df.loc[trigger_idx, "residual_return_1h"] = -0.05  # big down shock
    df.loc[trigger_idx, "oi_delta_pct_1h"] = -0.10
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000
    df.loc[trigger_idx, "volume"] = 1e8

    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert result.family == "DELEVERAGING"
    assert len(result.events) >= 1
    assert result.events["timestamp"].iloc[0] == df.loc[trigger_idx, "timestamp"]


def test_detect_deleveraging_silent_on_pure_noise():
    df = _baseline_frame(500, seed=2)
    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert len(result.events) == 0  # no synthetic shock injected -- should not fire


def test_detect_deleveraging_requires_all_four_conditions():
    df = _baseline_frame(500, seed=3)
    trigger_idx = 400
    # only the price shock, nothing else -- must NOT fire alone
    df.loc[trigger_idx, "residual_return_1h"] = -0.05
    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert len(result.events) == 0


# ── CROWDING ────────────────────────────────────────────────────────────


def test_detect_crowding_fires_when_flow_confirms_crowded_side():
    df = _baseline_frame(2000, seed=4)
    trigger_idx = 1800
    df.loc[trigger_idx, "funding_rate"] = 0.01  # extreme positive (longs pay)
    df.loc[trigger_idx, "basis_z_1d"] = 3.0
    df.loc[trigger_idx, "oi_delta_pct_1h"] = 0.05
    df.loc[trigger_idx, "aggressive_buy_usd"] = 1_000_000  # buying INTO the crowded long side
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1000

    result = detect_crowding(df, symbol="FOOUSDT")
    assert len(result.events) >= 1
    assert result.events["crowded_side"].iloc[0] == "long"


def test_detect_crowding_silent_when_flow_fades_not_confirms():
    df = _baseline_frame(2000, seed=5)
    trigger_idx = 1800
    df.loc[trigger_idx, "funding_rate"] = 0.01
    df.loc[trigger_idx, "basis_z_1d"] = 3.0
    df.loc[trigger_idx, "oi_delta_pct_1h"] = 0.05
    df.loc[trigger_idx, "aggressive_buy_usd"] = 1000  # SELLING into a crowded long -> fading, not confirming
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000

    result = detect_crowding(df, symbol="FOOUSDT")
    assert len(result.events) == 0


# ── RELATIVE_VALUE_DISLOCATION ──────────────────────────────────────────


def test_detect_relative_value_dislocation_needs_cross_sectional_extremity():
    # 20 symbols, not 5: with too few symbols, one outlier inflates its own
    # cross-sectional std enough to cap its own z-score below any realistic
    # threshold (with N=5 and 4 exact-zero peers, z is mathematically
    # capped at ~1.79 regardless of spike size -- a small-N panel artifact,
    # not a detector bug) -- 20 symbols is closer to the real ~300-symbol
    # universe's dilution and lets a genuine single-symbol dislocation clear z=2.
    n = 1000
    panel = {f"SYM{i}USDT": _baseline_frame(n, seed=10 + i) for i in range(20)}
    trigger_idx = 900
    ts = panel["SYM0USDT"].loc[trigger_idx, "timestamp"]
    for sym, df in panel.items():
        df.loc[df["timestamp"] == ts, "residual_return_1h"] = 0.0
        df.loc[df["timestamp"] == ts, "basis_z_1d"] = 0.0
        df.loc[df["timestamp"] == ts, "signed_volume"] = 0.0
    # SYM0 alone dislocates: big positive residual, positive relative basis/flow
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "residual_return_1h"] = 0.05
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "basis_z_1d"] = 3.0
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "signed_volume"] = 1_000_000

    result = detect_relative_value_dislocation(panel)
    assert (result.events["symbol"] == "SYM0USDT").any()


# ── FORCED_FLOW_REVERSAL ────────────────────────────────────────────────


def test_detect_forced_flow_reversal_fires_on_liquidation_and_oi_collapse():
    df = _baseline_frame(500, seed=6)
    df["liq_long_usd_5m"] = 0.0
    df["liq_short_usd_5m"] = 0.0
    trigger_idx = 400
    df.loc[trigger_idx, "liq_long_usd_5m"] = 5_000_000
    df.loc[trigger_idx, "oi_delta_pct_1h"] = -0.15
    df.loc[trigger_idx, "residual_return_1h"] = -0.06

    result = detect_forced_flow_reversal(df, symbol="FOOUSDT")
    assert len(result.events) >= 1


# ── labels ──────────────────────────────────────────────────────────────


def test_label_events_computes_expected_residual_return():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    # flat except a known +1% bump exactly 12 bars (1h) after the event
    residual = np.zeros(n)
    event_idx = 50
    residual[event_idx + 12] = 0.01
    frame = pd.DataFrame({"timestamp": idx, "residual_return_1h": residual})
    events = pd.DataFrame({"timestamp": [idx[event_idx]], "symbol": ["FOOUSDT"]})

    labelled = label_events(events, frame, family="DELEVERAGING")  # DELEVERAGING -> direction +1 (long)
    assert labelled["residual_ret_1h"].iloc[0] == pytest.approx(0.01, abs=1e-6)
    assert labelled["residual_ret_15m"].iloc[0] == pytest.approx(0.0, abs=1e-6)  # bump hasn't happened yet at 15m


def test_label_events_direction_flips_sign_for_short_scored_families():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    residual = np.zeros(n)
    residual[50 + 12] = 0.01
    frame = pd.DataFrame({"timestamp": idx, "residual_return_1h": residual})
    events = pd.DataFrame({"timestamp": [idx[50]], "symbol": ["FOOUSDT"]})

    labelled = label_events(events, frame, family="CROWDING")  # CROWDING -> direction -1 (short)
    assert labelled["residual_ret_1h"].iloc[0] == pytest.approx(-0.01, abs=1e-6)


def test_label_events_multi_symbol_groups_correctly():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    panel = {
        "AUSDT": pd.DataFrame({"timestamp": idx, "residual_return_1h": np.zeros(n)}),
        "BUSDT": pd.DataFrame({"timestamp": idx, "residual_return_1h": np.zeros(n)}),
    }
    panel["AUSDT"].loc[62, "residual_return_1h"] = 0.02
    events = pd.DataFrame({"timestamp": [idx[50], idx[50]], "symbol": ["AUSDT", "BUSDT"]})

    labelled = label_events_multi_symbol(events, panel, family="DELEVERAGING")
    a_ret = labelled.loc[labelled["symbol"] == "AUSDT", "residual_ret_1h"].iloc[0]
    b_ret = labelled.loc[labelled["symbol"] == "BUSDT", "residual_ret_1h"].iloc[0]
    assert a_ret == pytest.approx(0.02, abs=1e-6)
    assert b_ret == pytest.approx(0.0, abs=1e-6)


# ── scanner ─────────────────────────────────────────────────────────────


def _fake_labelled_events(n: int, mean_ret_1h: float, seed: int = 0, years=(2024,)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = rng.choice(pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D"), size=n)
    df = pd.DataFrame({"timestamp": pd.to_datetime(dates, utc=True), "symbol": "FOOUSDT"})
    for h in ("15m", "1h", "4h", "8h"):
        df[f"residual_ret_{h}"] = rng.normal(mean_ret_1h, 0.01, n)
        df[f"MFE_{h}"] = df[f"residual_ret_{h}"].abs()
        df[f"MAE_{h}"] = -df[f"residual_ret_{h}"].abs()
    return df


def test_scanner_classifies_strong_positive_edge_as_candidate():
    events = _fake_labelled_events(500, mean_ret_1h=0.02, seed=1, years=(2022, 2023, 2024, 2025))
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "CANDIDATE"


def test_scanner_classifies_negative_expectancy_as_kill():
    events = _fake_labelled_events(500, mean_ret_1h=-0.01, seed=2)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"


def test_scanner_classifies_small_n_as_kill_regardless_of_expectancy():
    events = _fake_labelled_events(30, mean_ret_1h=0.05, seed=3)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert "N=" in report.classification_reason or "pooled N" in report.classification_reason


def test_scanner_classifies_marginal_edge_as_weak_not_candidate():
    # positive after cost x1 but not after cost x2 -> WEAK
    events = _fake_labelled_events(500, mean_ret_1h=0.0035, seed=4, years=(2022, 2023, 2024, 2025))
    report = build_family_report(events, family="CROWDING")
    assert report.classification in ("WEAK", "KILL")  # never CANDIDATE on this marginal a signal
    assert report.classification != "CANDIDATE"


def test_scanner_kills_on_sign_flip_across_years():
    # strongly positive year dominates the pool (pooled net > 0, passes the
    # first gate) but a second year is mildly negative -- must still KILL
    # on sign-flip, not pass just because the pooled average is positive.
    pos_years = _fake_labelled_events(200, mean_ret_1h=0.05, seed=5, years=(2022,))
    neg_years = _fake_labelled_events(200, mean_ret_1h=-0.01, seed=6, years=(2023,))
    events = pd.concat([pos_years, neg_years], ignore_index=True)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert "sign" in report.classification_reason.lower()


def test_no_events_reports_kill_not_a_crash():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "residual_ret_15m", "residual_ret_1h",
                                   "residual_ret_4h", "residual_ret_8h", "MFE_15m", "MFE_1h",
                                   "MFE_4h", "MFE_8h", "MAE_15m", "MAE_1h", "MAE_4h", "MAE_8h"])
    report = build_family_report(empty, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert report.n_total == 0
