"""tests/test_cross_sectional_momentum_live_v2.py — CROSS_SECTIONAL_MOMENTUM_LIVE_V2
(Live Alpha Lab). CHALLENGER to CROSS_SECTIONAL_MOMENTUM_LIVE_V1 (frozen-50
universe) — same mechanism, dynamic liquid-alt universe. This file tests
ONLY the V2 module (tests/test_cross_sectional_momentum_live.py covers V1
separately, untouched).

Covers: universe.py's exchangeInfo candidate filter (PERPETUAL/USDT/TRADING/
COIN boundaries, malformed-input handling, the sanity cap), signal.py's
causal trailing-return/liquidity correctness (no lookahead), cross-sectional
top-bucket selection boundaries (liquidity filter at V2's OWN $2M threshold,
bucket-size rounding, ranking direction), weekly-rebalance-date extraction,
the full build_weekly_decisions pipeline (long-only enforcement, only-
selected-rows-appear, empty-input handling throughout), and fail-closed
behavior (unknown alpha_id, blocked alpha_id) for the runner script.

Does NOT hit the network (no exchangeInfo/klines REST calls — universe.py's
filter and signal.py's math are both pure functions over data structures
passed in directly).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.cross_sectional_momentum_live_v2.universe import (
    MAX_SANE_CANDIDATE_COUNT, candidate_symbols_from_exchange_info,
    resolve_dynamic_liquid_universe)
from src.institutional.engines.cross_sectional_momentum_live_v2.signal import (
    LOOKBACK_DAYS, MIN_LIQUIDITY_USD, TOP_FRACTION, build_weekly_decisions,
    select_top_bucket, trailing_liquidity_usd, trailing_return,
    weekly_rebalance_dates)


# ── universe.py — dynamic exchangeInfo candidate filter ────────────────────

def _sym(symbol, contract_type="PERPETUAL", quote="USDT", status="TRADING",
         underlying="COIN"):
    return {"symbol": symbol, "contractType": contract_type, "quoteAsset": quote,
            "status": status, "underlyingType": underlying}


def test_universe_accepts_trading_usdt_perpetual_coin():
    info = {"symbols": [_sym("BTCUSDT")]}
    out = resolve_dynamic_liquid_universe(info)
    assert out == ["BTCUSDT"]


def test_universe_excludes_non_perpetual_contract_type():
    info = {"symbols": [_sym("BTCUSDT_250926", contract_type="CURRENT_QUARTER")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_usdt_quote():
    info = {"symbols": [_sym("BTCUSDC", quote="USDC"), _sym("BTCUSD_PERP", quote="USD1")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_trading_status():
    info = {"symbols": [_sym("MKRUSDT", status="SETTLING"), _sym("NEWUSDT", status="PENDING_TRADING")]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_coin_underlying():
    """Binance's USDM futures exchangeInfo also lists tokenized equities and
    basket/index underlyings on the same endpoint (verified live 2026-09-01:
    EQUITY/CN_EQUITY/HK_EQUITY/KR_EQUITY/COMMODITY/PREMARKET/INDEX) -- none
    of these are eligible for a crypto cross-sectional momentum universe."""
    info = {"symbols": [
        _sym("BTCDOMUSDT", underlying="INDEX"),
        _sym("AAPLUSDT", underlying="EQUITY"),
        _sym("GOLDUSDT", underlying="COMMODITY"),
    ]}
    assert resolve_dynamic_liquid_universe(info) == []


def test_universe_excludes_non_ascii_ticker():
    """Verified live 2026-09-01: a handful of Binance USDM perpetuals carry
    non-ASCII (CJK) vanity ticker names. This alpha reuses V1's
    klines_source.py READ-ONLY, which does not percent-encode the symbol in
    its request URL (Python's http.client then raises UnicodeEncodeError) --
    rather than depend on that shared, frozen, un-modifiable-here module's
    caught-exception fallback, universe.py excludes non-ASCII tickers
    upfront, explicitly."""
    info = {"symbols": [_sym("BTCUSDT"), _sym("币安USDT")]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT"]


def test_universe_mixed_batch_keeps_only_eligible():
    info = {"symbols": [
        _sym("BTCUSDT"), _sym("ETHUSDT"),
        _sym("BTCDOMUSDT", underlying="INDEX"),
        _sym("MKRUSDT", status="SETTLING"),
        _sym("BTCUSDC", quote="USDC"),
        _sym("BTCUSDT_250926", contract_type="CURRENT_QUARTER"),
    ]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT", "ETHUSDT"]


def test_universe_sorted_and_deduplicated():
    info = {"symbols": [_sym("SOLUSDT"), _sym("BTCUSDT"), _sym("BTCUSDT")]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT", "SOLUSDT"]


def test_universe_empty_input():
    assert resolve_dynamic_liquid_universe({}) == []
    assert resolve_dynamic_liquid_universe({"symbols": []}) == []
    assert resolve_dynamic_liquid_universe(None) == []


def test_universe_malformed_entry_skipped_not_crashed():
    info = {"symbols": [_sym("BTCUSDT"), "not_a_dict", {"symbol": "INCOMPLETE"}, None]}
    assert resolve_dynamic_liquid_universe(info) == ["BTCUSDT"]


def test_candidate_symbols_returns_raw_dicts():
    info = {"symbols": [_sym("BTCUSDT")]}
    out = candidate_symbols_from_exchange_info(info)
    assert len(out) == 1
    assert out[0]["symbol"] == "BTCUSDT"


def test_universe_sanity_cap_raises():
    info = {"symbols": [_sym(f"SYM{i}USDT") for i in range(MAX_SANE_CANDIDATE_COUNT + 1)]}
    with pytest.raises(RuntimeError, match="MAX_SANE_CANDIDATE_COUNT"):
        resolve_dynamic_liquid_universe(info)


# ── signal.py — causal math (same shape as V1, own MIN_LIQUIDITY_USD) ──────

def test_min_liquidity_usd_differs_from_v1_by_design():
    """V2's threshold is a deliberate, documented judgment call (2x V1's
    $1M floor, see signal.py docstring) -- not accidentally identical."""
    from src.institutional.engines.cross_sectional_momentum_live.signal import (
        MIN_LIQUIDITY_USD as V1_MIN_LIQUIDITY_USD)
    assert MIN_LIQUIDITY_USD == pytest.approx(2_000_000.0)
    assert MIN_LIQUIDITY_USD != V1_MIN_LIQUIDITY_USD


def test_trailing_return_basic():
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 110.0])
    tret = trailing_return(close, lookback=7)
    assert tret.iloc[:7].isna().all()
    assert tret.iloc[7] == pytest.approx(110.0 / 100.0 - 1.0)


def test_trailing_return_no_lookahead():
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    r_before = trailing_return(close, lookback=7)
    close_future_spike = close.copy()
    close_future_spike.iloc[-1] = 999.0
    r_after = trailing_return(close_future_spike, lookback=7)
    pd.testing.assert_series_equal(r_before.iloc[:-1], r_after.iloc[:-1])


def test_trailing_return_empty_input():
    out = trailing_return(pd.Series([], dtype="float64"), lookback=7)
    assert out.empty


def test_trailing_liquidity_requires_full_window():
    vol = pd.Series([1_000_000.0] * 40)
    liq = trailing_liquidity_usd(vol, window=30)
    assert liq.iloc[:29].isna().all()
    assert not pd.isna(liq.iloc[29])


def test_trailing_liquidity_no_lookahead():
    vol = pd.Series([1.0] * 35)
    l_before = trailing_liquidity_usd(vol, window=30)
    vol_spike = vol.copy()
    vol_spike.iloc[-1] = 1e9
    l_after = trailing_liquidity_usd(vol_spike, window=30)
    pd.testing.assert_series_equal(l_before.iloc[:-1], l_after.iloc[:-1])


def test_trailing_liquidity_empty_input():
    out = trailing_liquidity_usd(pd.Series([], dtype="float64"), window=30)
    assert out.empty


# ── select_top_bucket ───────────────────────────────────────────────────────

def test_select_top_bucket_filters_illiquid_at_v2_threshold():
    """At V2's $2M floor, a name at $1.5M (which would have PASSED V1's $1M
    floor) is correctly excluded here -- the whole point of the higher
    threshold."""
    tret = pd.Series({"A": 0.10, "B": 0.20, "C": 0.30})
    liq = pd.Series({"A": 2_500_000.0, "B": 1_500_000.0, "C": 2_500_000.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=1.0)
    assert set(picked["symbol"]) == {"A", "C"}
    assert picked["n_eligible"].iloc[0] == 2


def test_select_top_bucket_ranks_descending_and_picks_top():
    tret = pd.Series({"A": 0.01, "B": 0.05, "C": 0.20, "D": 0.15, "E": 0.02})
    liq = pd.Series({k: 5_000_000.0 for k in tret.index})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=0.20)
    assert len(picked) == 1
    assert picked["symbol"].iloc[0] == "C"
    assert picked["n_eligible"].iloc[0] == 5


def test_select_top_bucket_ceil_rounding_on_much_larger_cohort():
    """V2's whole premise is a much larger eligible cohort than V1's ~49 —
    sanity-check the same ceil-rounding rule scales correctly at n=101."""
    tret = pd.Series({f"S{i}": float(i) for i in range(101)})
    liq = pd.Series({f"S{i}": 5_000_000.0 for i in range(101)})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=1_000_000.0, top_fraction=TOP_FRACTION)
    # ceil(0.20 * 101) == 21
    assert len(picked) == 21
    assert picked["symbol"].iloc[0] == "S100"   # highest tret first


def test_select_top_bucket_minimum_one_when_nonempty():
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
    liq = pd.Series({"A": 100.0, "B": 200.0})
    picked = select_top_bucket(tret, liq, min_liquidity_usd=MIN_LIQUIDITY_USD, top_fraction=0.20)
    assert picked.empty
    assert list(picked.columns) == ["symbol", "tret_7d", "liquidity_usd_30d",
                                     "pct_rank", "rank_in_bucket", "n_eligible"]


def test_select_top_bucket_empty_input():
    picked = select_top_bucket(pd.Series(dtype="float64"), pd.Series(dtype="float64"))
    assert picked.empty


# ── weekly_rebalance_dates ──────────────────────────────────────────────────

def test_weekly_rebalance_dates_picks_only_target_weekday():
    dates = pd.date_range("2026-01-01", "2026-01-31", freq="D", tz="UTC")
    mondays = weekly_rebalance_dates(dates, weekday=0)
    assert all(d.weekday() == 0 for d in mondays)
    assert len(mondays) == 4


def test_weekly_rebalance_dates_empty_input():
    assert weekly_rebalance_dates(pd.DatetimeIndex([]), weekday=0) == []


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


def test_build_weekly_decisions_respects_v2_liquidity_threshold():
    close, vol = _make_panel(symbols=("A", "B"))
    vol["B"] = 1_500_000.0   # would pass V1's $1M floor, must fail V2's $2M floor
    out = build_weekly_decisions(close, vol, min_liquidity_usd=MIN_LIQUIDITY_USD,
                                  top_fraction=1.0, liquidity_window=30)
    assert "B" not in set(out["symbol"]) if not out.empty else True


def test_build_weekly_decisions_no_symbols_outside_input_panel_appear():
    close, vol = _make_panel(symbols=("A", "B", "C"))
    out = build_weekly_decisions(close, vol, min_liquidity_usd=1_000_000.0, top_fraction=0.5)
    if not out.empty:
        assert set(out["symbol"]).issubset({"A", "B", "C"})


def test_build_weekly_decisions_insufficient_history_yields_no_rows():
    close, vol = _make_panel(n_days=5)
    out = build_weekly_decisions(close, vol)
    assert out.empty


# ── runner script: fail-closed registry checks ──────────────────────────────

def test_universe_hash_deterministic():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])   # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID")


def test_check_registry_freeze_passes_for_signal_shadow_entry():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_LIVE_V2 must be SIGNAL_SHADOW in the registry
    # for this test to pass -- i.e. this also guards against the registry
    # entry accidentally regressing to a non-writable operational_status.
    check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_LIVE_V2")


def test_check_registry_freeze_fails_closed_for_data_blocked_alpha():
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    # CROSS_SECTIONAL_MOMENTUM_PIT_V1 is explicitly DATA_BLOCKED -- untouched
    # sibling entry, never expected to flip to SIGNAL_SHADOW by this script.
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_PIT_V1")


def test_check_registry_freeze_v1_entry_untouched():
    """V1's own entry must still be exactly SIGNAL_SHADOW and readable —
    this test would fail if V2's build had accidentally mutated V1's block."""
    from scripts.run_cross_sectional_momentum_live_v2_shadow import check_registry_freeze
    check_registry_freeze("CROSS_SECTIONAL_MOMENTUM_LIVE_V1")
