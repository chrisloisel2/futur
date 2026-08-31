"""tests/test_short_covering_continuation.py — SHORT_COVERING_CONTINUATION_V1
(Live Alpha Lab).

Covers: tail-decile state classification (price-up/OI-down correctly
identified, boundary cases, NaN-safety, empty input), the min-combinator
score's consistency with the strict AND rule, causal rolling-percentile
correctness (no lookahead, current bar excluded from its own population),
universe-hash determinism, and fail-closed behavior when the registry entry
isn't SHADOW_LIVE/EXECUTION_SHADOW.

Does NOT touch causality of the underlying live derivatives collector (out
of scope, read-only data source) -- only this module's own classification
logic, per the mission's test scope.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.short_covering_continuation.state import (
    OI_PCTILE_LO, OTHER, PRICE_PCTILE_HI, SHORT_COVERING, classify_state,
    classify_state_df, rolling_causal_percentile, score_short_covering)


# ── classify_state ──────────────────────────────────────────────────────────

def test_classify_state_identifies_price_up_oi_down():
    """The core mechanism: price in the top tail decile AND OI in the
    bottom tail decile simultaneously -> SHORT_COVERING."""
    assert classify_state(0.95, 0.05) == SHORT_COVERING
    assert classify_state(0.99, 0.01) == SHORT_COVERING


def test_classify_state_rejects_single_condition_only():
    """Price up alone (OI also up, or unremarkable) must NOT qualify --
    this is an AND of two conditions, not an OR."""
    assert classify_state(0.95, 0.95) == OTHER   # price up, OI also up (new longs, quadrant 1)
    assert classify_state(0.95, 0.50) == OTHER   # price up, OI unremarkable
    assert classify_state(0.50, 0.05) == OTHER   # OI down, price unremarkable
    assert classify_state(0.05, 0.05) == OTHER   # price down, OI down (delever, quadrant 4)
    assert classify_state(0.05, 0.95) == OTHER   # price down, OI up (new shorts, quadrant 3)


def test_classify_state_boundary_inclusive():
    """Exactly at the threshold counts as qualifying (>=, <=), matching the
    module's documented >= / <= semantics."""
    assert classify_state(PRICE_PCTILE_HI, OI_PCTILE_LO) == SHORT_COVERING
    # just inside the boundary on either side -> excluded
    assert classify_state(PRICE_PCTILE_HI - 1e-9, OI_PCTILE_LO) == OTHER
    assert classify_state(PRICE_PCTILE_HI, OI_PCTILE_LO + 1e-9) == OTHER


def test_classify_state_custom_thresholds():
    assert classify_state(0.80, 0.20, tau_price_hi=0.80, tau_oi_lo=0.20) == SHORT_COVERING
    assert classify_state(0.79, 0.20, tau_price_hi=0.80, tau_oi_lo=0.20) == OTHER


def test_classify_state_nan_safe():
    """Non-finite inputs must fail closed to OTHER, never raise, never
    silently qualify as SHORT_COVERING."""
    assert classify_state(np.nan, 0.05) == OTHER
    assert classify_state(0.95, np.nan) == OTHER
    assert classify_state(np.nan, np.nan) == OTHER
    assert classify_state(float("inf"), 0.05) == OTHER
    assert classify_state(None, 0.05) == OTHER
    assert classify_state(0.95, None) == OTHER


# ── score_short_covering ────────────────────────────────────────────────────

def test_score_matches_classify_state_boundary():
    """With the module's symmetric default thresholds (OI_PCTILE_LO ==
    1 - PRICE_PCTILE_HI), score>=PRICE_PCTILE_HI must be EXACTLY equivalent
    to classify_state()==SHORT_COVERING -- this is what lets the engine use
    one continuous score for classify_zone AND have zone A_TRADE line up
    exactly with the strict state rule."""
    cases = [(0.95, 0.05), (0.90, 0.10), (0.5, 0.5), (0.3, 0.8), (1.0, 0.0), (0.0, 1.0)]
    for price_pctile, oi_pctile in cases:
        score = score_short_covering(price_pctile, oi_pctile)
        state_says_qualify = classify_state(price_pctile, oi_pctile) == SHORT_COVERING
        score_says_qualify = score >= PRICE_PCTILE_HI
        assert state_says_qualify == score_says_qualify, (price_pctile, oi_pctile, score)


def test_score_min_combinator_no_compensation():
    """One extreme dimension must NOT compensate for a weak other one --
    the whole point of using min() instead of an average."""
    # price maximally extreme, OI completely unremarkable -> low score
    score = score_short_covering(1.0, 0.50)
    assert score == pytest.approx(0.50)
    assert score < PRICE_PCTILE_HI
    # both moderately in the right direction -> still below threshold
    score2 = score_short_covering(0.70, 0.30)
    assert score2 == pytest.approx(0.70)


def test_score_range_and_nan_safety():
    assert 0.0 <= score_short_covering(0.5, 0.5) <= 1.0
    assert score_short_covering(np.nan, 0.1) == 0.0
    assert score_short_covering(0.9, np.nan) == 0.0
    assert score_short_covering(None, None) == 0.0


# ── rolling_causal_percentile ───────────────────────────────────────────────

def test_rolling_causal_percentile_no_lookahead():
    """A later extreme value must never change an earlier point's
    percentile -- pure causality check."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    p_before = rolling_causal_percentile(s, window=10)
    s_with_future_spike = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])  # only last value changed
    p_after = rolling_causal_percentile(s_with_future_spike, window=10)
    # everything strictly before the changed last element must be identical
    pd.testing.assert_series_equal(p_before.iloc[:-1], p_after.iloc[:-1])


def test_rolling_causal_percentile_excludes_self():
    """s[i] must be ranked against strictly-prior values only -- a
    monotonically increasing series should give the max percentile (1.0) at
    every point past the warmup, since every later increasing value is
    always the max of all *prior* values, but never let itself into its own
    reference population (which would understate it for a run of identical
    values)."""
    s = pd.Series([10.0] * 5)   # all identical -- self-inclusion would count as <= self spuriously either way
    p = rolling_causal_percentile(s, window=10)
    assert math.isnan(p.iloc[0])   # no prior history at all
    # every subsequent point has only prior 10.0s <= it -> percentile 1.0
    assert (p.iloc[1:] == 1.0).all()


def test_rolling_causal_percentile_empty_input():
    out = rolling_causal_percentile(pd.Series([], dtype="float64"), window=10)
    assert out.empty


def test_rolling_causal_percentile_nan_in_history_ignored():
    s = pd.Series([1.0, np.nan, 3.0, 2.0])
    p = rolling_causal_percentile(s, window=10)
    # at index 3 (value=2.0), prior finite history is [1.0, 3.0] -> 1 of 2 <= 2.0 -> 0.5
    assert p.iloc[3] == pytest.approx(0.5)


# ── classify_state_df ───────────────────────────────────────────────────────

def test_classify_state_df_empty_input():
    df = pd.DataFrame({"price_ret_pctile": [], "oi_delta_pctile": []})
    out = classify_state_df(df)
    assert out.empty
    assert "state" in out.columns
    assert "score" in out.columns


def test_classify_state_df_adds_state_and_score():
    df = pd.DataFrame({
        "price_ret_pctile": [0.95, 0.50, np.nan],
        "oi_delta_pctile": [0.05, 0.50, 0.05],
    })
    out = classify_state_df(df)
    assert list(out["state"]) == [SHORT_COVERING, OTHER, OTHER]
    assert out["score"].iloc[0] == pytest.approx(0.95)
    assert out["score"].iloc[2] == 0.0   # NaN input -> fail-closed score 0.0


# ── runner script: universe hash + registry fail-closed (mirrors
#    tests/test_liq_cascade_repeat_variant.py's coverage for the reference
#    Mode A runner) ────────────────────────────────────────────────────────

def test_universe_hash_deterministic():
    from scripts.run_short_covering_continuation_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])  # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_short_covering_continuation_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID", "deadbeef")


def test_check_registry_freeze_passes_for_shadow_live_entry():
    from scripts.run_short_covering_continuation_shadow import check_registry_freeze
    # SHORT_COVERING_CONTINUATION_V1 is set to SHADOW_LIVE in configs/live_alpha_registry.yaml
    check_registry_freeze("SHORT_COVERING_CONTINUATION_V1", "any-hash-not-checked-here")


def test_check_registry_freeze_fails_closed_for_blocked_alpha():
    from scripts.run_short_covering_continuation_shadow import check_registry_freeze
    # LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 is explicitly BUG_FOUND/blocked
    # (unresolved sign convention) -- same fixture used by
    # tests/test_liq_cascade_repeat_variant.py, deliberately not a status
    # expected to ever flip to SHADOW_LIVE by itself.
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1", "any-hash")


# ── ShortCoveringContinuationEngine construction guard ──────────────────────

def test_engine_requires_explicit_universe():
    from src.institutional.engines.short_covering_continuation.infer import (
        ShortCoveringContinuationEngine)
    with pytest.raises(ValueError, match="universe"):
        ShortCoveringContinuationEngine(status="SHADOW", universe=None)
    with pytest.raises(ValueError, match="universe"):
        ShortCoveringContinuationEngine(status="SHADOW", universe=[])


def test_engine_generate_empty_when_no_live_data(tmp_path, monkeypatch):
    """An asset with no data/derivatives_raw rows (e.g. the known MKRUSDT/
    PEPEUSDT/RNDRUSDT gap) must return [] from generate(), never raise."""
    import src.institutional.engines.short_covering_continuation.live_data as live_data
    from src.institutional.engines.short_covering_continuation.infer import (
        ShortCoveringContinuationEngine)

    monkeypatch.setattr(live_data, "RAW_ROOT", tmp_path / "nonexistent_stream_dir")
    eng = ShortCoveringContinuationEngine(status="SHADOW", universe=["BTCUSDT", "ETHUSDT"])
    opps = eng.generate("BTCUSDT", "2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00+00:00")
    assert opps == []
