"""tests/test_cross_sectional_momentum_live.py — CROSS_SECTIONAL_MOMENTUM_LIVE_V1
(Live Alpha Lab).

Covers: causal trailing-return correctness (no lookahead), causal trailing-
liquidity correctness (full-window requirement), cross-sectional top-bucket
selection boundaries (liquidity filter, bucket-size rounding, ranking
direction), weekly-rebalance-date extraction, the full build_weekly_decisions
pipeline (long-only enforcement, only-selected-rows-appear), empty-input
handling throughout, the pure kline-row parser, and fail-closed behavior
(unknown alpha_id, blocked alpha_id, universe-hash determinism) for the
runner script — mirrors tests/test_short_covering_continuation.py's coverage
for the reference Mode A runner pattern.

Does NOT hit the network (klines_source's REST fetch is out of scope here --
only its pure `rows_to_frame` transform is tested).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.cross_sectional_momentum_live.klines_source import (
    CACHE_COLUMNS, rows_to_frame)
from src.institutional.engines.cross_sectional_momentum_live.signal import (
    LOOKBACK_DAYS, MIN_LIQUIDITY_USD, TOP_FRACTION, build_weekly_decisions,
    select_top_bucket, trailing_liquidity_usd, trailing_return,
    weekly_rebalance_dates)


# ── trailing_return ─────────────────────────────────────────────────────────

def test_trailing_return_basic():
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 110.0])
    tret = trailing_return(close, lookback=7)
    # only index 7 has 7 strictly-prior observations behind it
    assert tret.iloc[:7].isna().all()
    assert tret.iloc[7] == pytest.approx(110.0 / 100.0 - 1.0)


def test_trailing_return_no_lookahead():
    """A later value changing must never change an EARLIER point's trailing
    return -- pure causality check, same style as
    test_rolling_causal_percentile_no_lookahead in the sibling module."""
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    r_before = trailing_return(close, lookback=7)
    close_future_spike = close.copy()
    close_future_spike.iloc[-1] = 999.0   # only the LAST value changes
    r_after = trailing_return(close_future_spike, lookback=7)
    pd.testing.assert_series_equal(r_before.iloc[:-1], r_after.iloc[:-1])


def test_trailing_return_empty_input():
    out = trailing_return(pd.Series([], dtype="float64"), lookback=7)
    assert out.empty


def test_trailing_return_uses_only_lookback_and_current():
    """close[t]/close[t-lookback]-1 must be indifferent to any value strictly
    BETWEEN t-lookback and t (only the two endpoints matter)."""
    close_a = pd.Series([100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 200.0])
    close_b = pd.Series([100.0, 999.0, 0.001, 50.0, 1.0, 1.0, 1.0, 200.0])
    ra = trailing_return(close_a, lookback=7)
    rb = trailing_return(close_b, lookback=7)
    assert ra.iloc[7] == pytest.approx(rb.iloc[7])
    assert ra.iloc[7] == pytest.approx(1.0)   # 200/100 - 1


# ── trailing_liquidity_usd ──────────────────────────────────────────────────

def test_trailing_liquidity_requires_full_window():
    vol = pd.Series([1_000_000.0] * 40)
    liq = trailing_liquidity_usd(vol, window=30)
    assert liq.iloc[:29].isna().all()
    assert not pd.isna(liq.iloc[29])


def test_trailing_liquidity_is_causal_median():
    vol = pd.Series(list(range(1, 31)), dtype="float64")   # 1..30
    liq = trailing_liquidity_usd(vol, window=30)
    # window [t-29..t] for t=29 (0-indexed) covers values 1..30 -> median 15.5
    assert liq.iloc[29] == pytest.approx(15.5)


def test_trailing_liquidity_empty_input():
    out = trailing_liquidity_usd(pd.Series([], dtype="float64"), window=30)
    assert out.empty


def test_trailing_liquidity_no_lookahead():
    vol = pd.Series([1.0] * 35)
    l_before = trailing_liquidity_usd(vol, window=30)
    vol_spike = vol.copy()
    vol_spike.iloc[-1] = 1e9
    l_after = trailing_liquidity_usd(vol_spike, window=30)
    pd.testing.assert_series_equal(l_before.iloc[:-1], l_after.iloc[:-1])


# ── select_top_bucket ───────────────────────────────────────────────────────

def test_select_top_bucket_filters_illiquid():
    tret = pd.Series({"A": 0.10, "B": 0.20, "C": 0.30})
    liq = pd.Series({"A": 2_000_000.0, "B": 500_000.0, "C": 2_000_000.0})  # B illiquid
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=1.0)
    assert set(picked["symbol"]) == {"A", "C"}
    assert picked["n_eligible"].iloc[0] == 2


def test_select_top_bucket_ranks_descending_and_picks_top():
    tret = pd.Series({"A": 0.01, "B": 0.05, "C": 0.20, "D": 0.15, "E": 0.02})
    liq = pd.Series({k: 5_000_000.0 for k in tret.index})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    # 5 eligible, ceil(0.2*5)=1 -> only the single best (C, tret=0.20)
    assert len(picked) == 1
    assert picked["symbol"].iloc[0] == "C"
    assert picked["n_eligible"].iloc[0] == 5


def test_select_top_bucket_ceil_rounding():
    """n_eligible=7, top_fraction=0.20 -> ceil(1.4)=2 selected, not floor(1)."""
    tret = pd.Series({f"S{i}": float(i) for i in range(7)})
    liq = pd.Series({f"S{i}": 5_000_000.0 for i in range(7)})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert len(picked) == 2
    assert set(picked["symbol"]) == {"S6", "S5"}   # top-2 by tret


def test_select_top_bucket_minimum_one_when_nonempty():
    """Even a single eligible name must select 1, not 0, when top_fraction
    would round down to nothing."""
    tret = pd.Series({"ONLY": 0.05})
    liq = pd.Series({"ONLY": 5_000_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert len(picked) == 1
    assert picked["symbol"].iloc[0] == "ONLY"


def test_select_top_bucket_excludes_nan_return():
    tret = pd.Series({"A": np.nan, "B": 0.10})
    liq = pd.Series({"A": 5_000_000.0, "B": 5_000_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=1.0)
    assert list(picked["symbol"]) == ["B"]


def test_select_top_bucket_empty_when_nothing_eligible():
    tret = pd.Series({"A": 0.10, "B": 0.20})
    liq = pd.Series({"A": 100.0, "B": 200.0})   # both far below the floor
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert picked.empty
    assert list(picked.columns) == ["symbol", "tret_7d", "liquidity_usd_30d",
                                     "pct_rank", "rank_in_bucket", "n_eligible"]


def test_select_top_bucket_empty_input():
    picked = select_top_bucket(pd.Series(dtype="float64"), pd.Series(dtype="float64"))
    assert picked.empty


def test_select_top_bucket_pct_rank_over_full_cohort():
    tret = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04})
    liq = pd.Series({k: 5_000_000.0 for k in tret.index})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=1.0)
    # best performer (D) should have pct_rank == 1.0 over the full 4-name cohort
    d_row = picked[picked["symbol"] == "D"].iloc[0]
    assert d_row["pct_rank"] == pytest.approx(1.0)


# ── weekly_rebalance_dates ──────────────────────────────────────────────────

def test_weekly_rebalance_dates_picks_only_target_weekday():
    dates = pd.date_range("2026-01-01", "2026-01-31", freq="D", tz="UTC")   # 2026-01-01 is a Thursday
    mondays = weekly_rebalance_dates(dates, weekday=0)
    assert all(d.weekday() == 0 for d in mondays)
    assert len(mondays) == 4   # 4 Mondays in Jan 2026 (5th, 12th, 19th, 26th)


def test_weekly_rebalance_dates_empty_input():
    assert weekly_rebalance_dates(pd.DatetimeIndex([]), weekday=0) == []


def test_weekly_rebalance_dates_deduplicates():
    dates = pd.DatetimeIndex(["2026-01-05", "2026-01-05", "2026-01-12"], tz="UTC")
    mondays = weekly_rebalance_dates(dates, weekday=0)
    assert len(mondays) == 2


# ── build_weekly_decisions (full pipeline) ──────────────────────────────────

def _make_panel(n_days=60, symbols=("A", "B", "C", "D", "E"), start="2026-01-01", seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_days, freq="D", tz="UTC")
    close = pd.DataFrame(
        {s: 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n_days)) for s in symbols}, index=idx)
    vol = pd.DataFrame({s: 5_000_000.0 for s in symbols}, index=idx)
    return close, vol


def test_build_weekly_decisions_empty_panel():
    out = build_weekly_decisions(pd.DataFrame(), pd.DataFrame())
    assert out.empty
    assert list(out.columns) == ["event_time", "symbol", "tret_7d", "liquidity_usd_30d",
                                  "pct_rank", "rank_in_bucket", "n_eligible_universe", "direction"]


def test_build_weekly_decisions_long_only():
    close, vol = _make_panel()
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0, top_fraction=0.40)
    if not out.empty:
        assert (out["direction"] == "LONG").all()


def test_build_weekly_decisions_only_on_rebalance_weekday():
    close, vol = _make_panel(n_days=40)
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0,
                                  top_fraction=1.0, rebalance_weekday=0)
    if not out.empty:
        assert all(pd.Timestamp(t).weekday() == 0 for t in out["event_time"].unique())


def test_build_weekly_decisions_respects_liquidity_filter():
    close, vol = _make_panel(symbols=("A", "B"))
    vol["B"] = 100.0   # far below the $1M floor for the whole history
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0,
                                  top_fraction=1.0, liquidity_window=30)
    assert "B" not in set(out["symbol"]) if not out.empty else True


def test_build_weekly_decisions_no_symbols_outside_frozen_universe_appear():
    """The pipeline never invents a symbol name -- output symbols are always
    a subset of the input panel's columns."""
    close, vol = _make_panel(symbols=("A", "B", "C"))
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0, top_fraction=0.5)
    if not out.empty:
        assert set(out["symbol"]).issubset({"A", "B", "C"})


def test_build_weekly_decisions_insufficient_history_yields_no_rows():
    """Fewer days than LOOKBACK_DAYS + LIQUIDITY_WINDOW_DAYS -> every
    trailing feature is NaN everywhere -> zero decisions, not a crash."""
    close, vol = _make_panel(n_days=5)
    out = build_weekly_decisions(close, vol)
    assert out.empty


# ── kline row parsing (pure transform, no I/O) ──────────────────────────────

def _fake_kline_row(open_time_ms: int, close: float, quote_vol: float) -> list:
    return [open_time_ms, "0", "0", "0", str(close), "0",
            open_time_ms + 86_399_999, str(quote_vol), 0, "0", "0", "0"]


def test_rows_to_frame_basic():
    rows = [
        _fake_kline_row(1_700_000_000_000, 100.0, 5_000_000.0),
        _fake_kline_row(1_700_086_400_000, 101.0, 6_000_000.0),
    ]
    df = rows_to_frame(rows)
    assert list(df.columns) == CACHE_COLUMNS
    assert len(df) == 2
    assert df["close"].iloc[0] == pytest.approx(100.0)
    assert df["quote_volume"].iloc[1] == pytest.approx(6_000_000.0)


def test_rows_to_frame_empty_input():
    df = rows_to_frame([])
    assert df.empty
    assert list(df.columns) == CACHE_COLUMNS


def test_rows_to_frame_dedupes_same_day_keeps_last():
    rows = [
        _fake_kline_row(1_700_000_000_000, 100.0, 1.0),
        _fake_kline_row(1_700_000_000_000, 999.0, 2.0),   # same open_time, later in list wins
    ]
    df = rows_to_frame(rows)
    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(999.0)


# ── runner script: universe hash + registry fail-closed (mirrors
#    tests/test_short_covering_continuation.py's coverage for the reference
#    Mode A runner) ────────────────────────────────────────────────────────

def test_universe_hash_deterministic():
    from scripts.run_cross_sectional_momentum_live_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])   # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_cross_sectional_momentum_live_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID", "deadbeef")


def test_check_registry_freeze_passes_for_signal_shadow_entry():
    from scripts.run_cross_sectional_momentum_live_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_LIVE_V1 is set to SIGNAL_SHADOW in the registry.
    check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_LIVE_V1", "any-hash-not-checked-here")


def test_check_registry_freeze_fails_closed_for_data_blocked_alpha():
    from scripts.run_cross_sectional_momentum_live_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_PIT_V1 is explicitly DATA_BLOCKED -- untouched
    # sibling entry, deliberately never expected to flip to SIGNAL_SHADOW by
    # itself (see mission: do not touch that entry).
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_PIT_V1", "any-hash")
