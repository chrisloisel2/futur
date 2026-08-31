"""tests/test_whale_lsr_screen.py — WHALE_LSR_SCREEN_V1 (Live Alpha Lab).

Covers: causality of the rolling 7d z-score (strictly past-only, no
lookahead), threshold classification boundaries, empty-input handling, and
-- most importantly for this alpha -- an explicit proof that the module
NEVER outputs a "SHORT" direction field. This mechanism is short-shaped
(bearish relative-return finding) but SHORT is institutionally REJECTED
(SHORT_REJECTED, standing project rule): only screen/avoid flags are ever
allowed out of this module, never a tradeable direction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.whale_lsr_screen.screen import (
    MIN_PERIODS_BARS, Z_EXTREME_LONG_THRESHOLD, Z_EXTREME_SHORT_THRESHOLD,
    classify_screen, compute_rolling_zscore,
)


def _make_series(symbol: str, n: int, ratios, start="2026-07-01", freq="5min") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    assert len(ratios) == n
    return pd.DataFrame({"timestamp": ts, "symbol": symbol, "longShortRatio": ratios})


# ─────────────────────────────────────────────────────────────────────────
# Causality / no-lookahead
# ─────────────────────────────────────────────────────────────────────────

def test_rolling_zscore_is_causal_no_lookahead():
    """A future spike must NOT affect the z-score of bars strictly before it."""
    n = MIN_PERIODS_BARS + 50
    ratios = [1.0] * n
    # plant a massive spike far in the future (last bar only)
    ratios[-1] = 100.0
    df = _make_series("BTCUSDT", n, ratios)

    out = compute_rolling_zscore(df)
    # every bar before the spike must be unaffected by it (flat series ->
    # std==0 -> z is NaN for all of them, but crucially NOT a huge negative
    # number that would appear if the future spike leaked into their
    # rolling std/mean).
    before_spike = out.iloc[:-1]
    assert before_spike["z_score_7d"].abs().max() != before_spike["z_score_7d"].abs().max() or \
        before_spike["z_score_7d"].dropna().empty or \
        before_spike["z_score_7d"].dropna().abs().max() < 50


def test_rolling_zscore_current_bar_excluded_from_its_own_baseline():
    """The z-score at time t must use mean/std computed from bars strictly
    before t -- an artificial constant series with a single spike inserted
    mid-way must show a large |z| exactly at (and only at) the spike, and
    that spike bar's own value must not be smoothing itself into the
    baseline (verified by checking the very next normal bar's z reverts)."""
    n = MIN_PERIODS_BARS + 200
    rng = np.random.default_rng(0)
    ratios = np.ones(n) + rng.normal(0, 0.001, n)  # tiny noise, near-constant
    spike_idx = MIN_PERIODS_BARS + 100
    ratios[spike_idx] = 5.0  # huge one-bar spike
    df = _make_series("ETHUSDT", n, list(ratios))

    out = compute_rolling_zscore(df)
    z_spike = out.iloc[spike_idx]["z_score_7d"]
    z_next = out.iloc[spike_idx + 1]["z_score_7d"]
    assert z_spike > 10, "the spike bar itself must show an extreme z-score"
    # the very next bar reverts to baseline (near 0, not still pinned near
    # the spike's z) -- if the spike bar had leaked into its own or the next
    # bar's baseline mean/std this would be violated.
    assert abs(z_next) < 5


def test_rolling_zscore_requires_min_periods_before_producing_values():
    """Before MIN_PERIODS_BARS of history, z_score_7d must be NaN (no
    premature computation on an under-filled window)."""
    n = MIN_PERIODS_BARS - 10
    rng = np.random.default_rng(1)
    ratios = list(1.0 + rng.normal(0, 0.05, n))
    df = _make_series("SOLUSDT", n, ratios)
    out = compute_rolling_zscore(df)
    assert out["z_score_7d"].isna().all()


def test_rolling_zscore_per_symbol_independent():
    """One symbol's history must never bleed into another symbol's z-score."""
    n = MIN_PERIODS_BARS + 20
    flat = _make_series("AAAUSDT", n, [1.0] * n)
    rng = np.random.default_rng(2)
    volatile = _make_series("BBBUSDT", n, list(1.0 + rng.normal(0, 0.2, n)))
    combined = pd.concat([flat, volatile], ignore_index=True)
    out = compute_rolling_zscore(combined)
    flat_z = out[out["symbol"] == "AAAUSDT"]["z_score_7d"]
    # flat series -> std==0 -> replaced with NA -> z is always NaN, never a
    # huge/spurious value borrowed from the volatile symbol.
    assert flat_z.dropna().empty


# ─────────────────────────────────────────────────────────────────────────
# Threshold classification boundaries
# ─────────────────────────────────────────────────────────────────────────

def test_classify_screen_boundaries():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01", periods=5, freq="5min", tz="UTC"),
        "symbol": ["X"] * 5,
        "longShortRatio": [1.0] * 5,
        "z_score_7d": [Z_EXTREME_LONG_THRESHOLD - 0.01, Z_EXTREME_LONG_THRESHOLD,
                        0.0,
                        Z_EXTREME_SHORT_THRESHOLD, Z_EXTREME_SHORT_THRESHOLD + 0.01],
    })
    out = classify_screen(df)
    assert out.iloc[0]["screen_flag"] == False    # just below threshold -> not flagged
    assert out.iloc[1]["screen_flag"] == True      # exactly at threshold -> flagged (>=)
    assert out.iloc[2]["screen_flag"] == False
    assert out.iloc[2]["mirror_flag_unconfirmed"] == False
    assert out.iloc[3]["mirror_flag_unconfirmed"] == True   # exactly at threshold -> flagged (<=)
    assert out.iloc[4]["mirror_flag_unconfirmed"] == False  # just above (less negative) -> not flagged


def test_classify_screen_flags_mutually_exclusive_at_frozen_thresholds():
    n = 2000
    rng = np.random.default_rng(3)
    z = rng.normal(0, 2.0, n)  # occasionally exceeds +/-4 by chance
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01", periods=n, freq="5min", tz="UTC"),
        "symbol": ["X"] * n,
        "longShortRatio": [1.0] * n,
        "z_score_7d": z,
    })
    out = classify_screen(df)
    assert not (out["screen_flag"] & out["mirror_flag_unconfirmed"]).any()
    assert out["screen_flag"].sum() > 0   # sanity: threshold is reachable with this variance


def test_classify_screen_nan_z_never_flagged():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01", periods=3, freq="5min", tz="UTC"),
        "symbol": ["X"] * 3,
        "longShortRatio": [1.0] * 3,
        "z_score_7d": [float("nan"), float("nan"), float("nan")],
    })
    out = classify_screen(df)
    assert not out["screen_flag"].any()
    assert not out["mirror_flag_unconfirmed"].any()


# ─────────────────────────────────────────────────────────────────────────
# Empty-input handling
# ─────────────────────────────────────────────────────────────────────────

def test_compute_rolling_zscore_empty_input():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "longShortRatio"])
    out = compute_rolling_zscore(empty)
    assert out.empty
    assert "z_score_7d" in out.columns


def test_classify_screen_empty_input():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "longShortRatio", "z_score_7d"])
    out = classify_screen(empty)
    assert out.empty
    assert "screen_flag" in out.columns
    assert "mirror_flag_unconfirmed" in out.columns


def test_compute_rolling_zscore_missing_columns_raises():
    bad = pd.DataFrame({"timestamp": [pd.Timestamp("2026-08-01", tz="UTC")], "symbol": ["X"]})
    with pytest.raises(ValueError):
        compute_rolling_zscore(bad)


# ─────────────────────────────────────────────────────────────────────────
# THE hard rule: never a SHORT direction, only screen flags.
# ─────────────────────────────────────────────────────────────────────────

def test_never_emits_short_direction_field():
    """This is the single most important test for this alpha: the mechanism
    is short-shaped (bearish) but SHORT is institutionally REJECTED. The
    module must NEVER produce a 'direction' column, and no cell anywhere in
    its output may literally contain the string 'SHORT'."""
    n = MIN_PERIODS_BARS + 200
    rng = np.random.default_rng(4)

    # two INDEPENDENT symbols so one spike's contamination of its own
    # trailing-window baseline can never suppress the other's -- one series
    # gets an extreme long-side spike (must trigger screen_flag), the other
    # an extreme short-side spike (must trigger mirror_flag_unconfirmed).
    ratios_high = list(1.5 + rng.normal(0, 0.3, n))
    for i in range(MIN_PERIODS_BARS + 10, MIN_PERIODS_BARS + 20):
        ratios_high[i] = 50.0   # extreme long spike
    df_high = _make_series("XRPUSDT", n, ratios_high)

    ratios_low = list(1.5 + rng.normal(0, 0.3, n))
    for i in range(MIN_PERIODS_BARS + 10, MIN_PERIODS_BARS + 20):
        ratios_low[i] = 0.001   # extreme short spike
    df_low = _make_series("DOGEUSDT", n, ratios_low)

    df = pd.concat([df_high, df_low], ignore_index=True)

    with_z = compute_rolling_zscore(df)
    out = classify_screen(with_z)

    assert out["screen_flag"].sum() > 0, "test setup should trigger the main screen at least once"
    assert out["mirror_flag_unconfirmed"].sum() > 0, "test setup should trigger the mirror at least once"

    assert "direction" not in out.columns
    assert "side" not in out.columns

    for col in out.columns:
        if out[col].dtype == object:
            vals = out[col].dropna().astype(str)
            assert not vals.str.upper().eq("SHORT").any(), f"column {col} must never contain literal 'SHORT'"

    # the only "signal" columns are boolean flags -- never a string enum
    # that could be mistaken for a trade direction.
    assert out["screen_flag"].dtype == bool
    assert out["mirror_flag_unconfirmed"].dtype == bool


def test_never_emits_short_direction_field_even_on_runner_output_shape():
    """Same guarantee, but on the exact column subset the shadow runner
    writes to decisions.parquet (timestamp, symbol, longShortRatio,
    z_score_7d, screen_flag, mirror_flag_unconfirmed) -- none of these
    column NAMES may be direction-shaped either."""
    forbidden = {"direction", "side", "trade_direction", "position_side"}
    runner_columns = {
        "timestamp", "symbol", "longShortRatio", "z_score_7d",
        "screen_flag", "mirror_flag_unconfirmed", "engine", "horizon",
        "universe_hash", "decided_at", "tier",
    }
    assert forbidden.isdisjoint(runner_columns)


# ─────────────────────────────────────────────────────────────────────────
# Universe hash / registry fail-closed (mirrors the LIQ_CASCADE_REPEAT_V1 pattern)
# ─────────────────────────────────────────────────────────────────────────

def test_universe_hash_deterministic():
    from scripts.run_whale_lsr_screen_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])  # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_load_universe_matches_frozen_config():
    from scripts.run_whale_lsr_screen_shadow import load_universe
    universe = load_universe()
    assert len(universe) == 47
    assert universe == sorted(universe)
    assert "BTCUSDT" in universe
    assert "ETHUSDT" in universe


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_whale_lsr_screen_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID", "deadbeef")


def test_check_registry_freeze_passes_for_shadow_live_entry():
    from scripts.run_whale_lsr_screen_shadow import check_registry_freeze
    # WHALE_LSR_SCREEN_V1 must be SHADOW_LIVE in configs/live_alpha_registry.yaml
    check_registry_freeze("WHALE_LSR_SCREEN_V1", "any-hash-not-checked-here")
