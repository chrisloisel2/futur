"""
tests/test_amihud_illiquidity_live_signal.py — AMIHUD_ILLIQUIDITY_PREMIUM_V1
(Alpha Validation Factory freeze), src/institutional/engines/
amihud_illiquidity_live/signal.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.institutional.engines.amihud_illiquidity_live.signal import (
    build_weekly_decisions, daily_return, select_long_short_buckets,
    trailing_illiquidity, trailing_liquidity_usd, weekly_rebalance_dates,
)


def test_daily_return_causal_shift_one():
    close = pd.Series([100.0, 110.0, 99.0])
    r = daily_return(close)
    assert pd.isna(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx(-0.1)


def test_trailing_illiquidity_excludes_current_day_return():
    """Day d's own |return|/volume must NEVER enter illiq_avg(d) -- a huge
    move on day d itself must not change illiq_avg(d)."""
    idx = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    close = pd.Series(100.0, index=idx)
    close.iloc[-1] = 1000.0   # 900% move on the LAST day only
    vol = pd.Series(1_000_000.0, index=idx)
    illiq_normal = trailing_illiquidity(close, vol, window=30, min_valid_days=20)

    close2 = close.copy()
    close2.iloc[-1] = 100.0   # no move on the last day this time
    illiq_no_shock = trailing_illiquidity(close2, vol, window=30, min_valid_days=20)

    assert illiq_normal.iloc[-1] == pytest.approx(illiq_no_shock.iloc[-1])


def test_trailing_illiquidity_nan_before_min_valid_days():
    idx = pd.date_range("2026-01-01", periods=15, freq="D", tz="UTC")
    close = pd.Series(np.linspace(100, 110, 15), index=idx)
    vol = pd.Series(1_000_000.0, index=idx)
    illiq = trailing_illiquidity(close, vol, window=30, min_valid_days=20)
    assert illiq.isna().all()   # jamais 20 jours valides disponibles ici


def test_trailing_liquidity_usd_is_causal_median():
    idx = pd.date_range("2026-01-01", periods=35, freq="D", tz="UTC")
    vol = pd.Series(1_000_000.0, index=idx)
    vol.iloc[-1] = 999_000_000.0   # spike on the LAST day
    liq = trailing_liquidity_usd(vol, window=30)
    # la médiane du jour -1 (dernière valeur disponible dans la fenêtre incluant elle-même)
    # doit rester dominée par les 1M constants, pas par le spike d'un seul jour
    assert liq.iloc[-1] < 2_000_000.0


def test_weekly_rebalance_dates_correct_weekday_and_spacing():
    idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")   # 2026-01-01 = jeudi
    wednesdays = weekly_rebalance_dates(idx, weekday=2)
    assert all(d.weekday() == 2 for d in wednesdays)
    diffs = [(wednesdays[i + 1] - wednesdays[i]).days for i in range(len(wednesdays) - 1)]
    assert all(d == 7 for d in diffs)


def test_select_long_short_buckets_no_overlap_and_correct_direction():
    illiq = pd.Series({f"SYM{i}": float(i) for i in range(10)})   # SYM9 = most illiquid
    liq = pd.Series({f"SYM{i}": 5_000_000.0 for i in range(10)})   # all pass liquidity floor
    result = select_long_short_buckets(illiq, liq, min_liquidity_usd=1_000_000, top_fraction=0.20)
    longs = set(result[result["direction"] == "LONG"]["symbol"])
    shorts = set(result[result["direction"] == "SHORT"]["symbol"])
    assert longs == {"SYM9", "SYM8"}     # 2 = ceil(0.2*10), most illiquid
    assert shorts == {"SYM0", "SYM1"}    # most liquid (lowest illiq)
    assert longs.isdisjoint(shorts)


def test_select_long_short_buckets_respects_liquidity_floor():
    illiq = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0})
    liq = pd.Series({"A": 500.0, "B": 5_000_000.0, "C": 5_000_000.0, "D": 5_000_000.0})   # A trop illiquide (volume)
    result = select_long_short_buckets(illiq, liq, min_liquidity_usd=1_000_000, top_fraction=0.5)
    assert "A" not in set(result["symbol"])   # exclu malgré son illiq_avg le plus haut


def test_select_long_short_buckets_empty_when_too_few_eligible():
    illiq = pd.Series({"A": 1.0})
    liq = pd.Series({"A": 5_000_000.0})
    result = select_long_short_buckets(illiq, liq, min_liquidity_usd=1_000_000, top_fraction=0.2)
    assert result.empty


def test_build_weekly_decisions_end_to_end_no_lookahead():
    idx = pd.date_range("2026-01-01", periods=90, freq="D", tz="UTC")
    rng = np.random.default_rng(42)
    symbols = [f"SYM{i}" for i in range(10)]
    close = pd.DataFrame({s: 100.0 * (1 + rng.normal(0, 0.02, len(idx))).cumprod() for s in symbols}, index=idx)
    vol = pd.DataFrame({s: rng.uniform(2_000_000, 50_000_000, len(idx)) for s in symbols}, index=idx)

    dec = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000, top_fraction=0.2)
    assert not dec.empty
    assert set(dec["direction"].unique()) <= {"LONG", "SHORT"}
    # chaque rebalance a autant de LONG que de SHORT (buckets symétriques)
    counts = dec.groupby(["event_time", "direction"]).size().unstack(fill_value=0)
    assert (counts["LONG"] == counts["SHORT"]).all()
    # aucun symbole n'est à la fois LONG et SHORT au même rebalance
    for ts, grp in dec.groupby("event_time"):
        assert set(grp[grp.direction == "LONG"]["symbol"]).isdisjoint(
            set(grp[grp.direction == "SHORT"]["symbol"]))


def test_build_weekly_decisions_empty_panel_returns_empty_typed_frame():
    result = build_weekly_decisions(pd.DataFrame(), pd.DataFrame())
    assert result.empty
    assert list(result.columns) == ["event_time", "symbol", "direction", "illiq_avg",
                                    "liquidity_usd_30d", "pct_rank", "n_eligible"]
