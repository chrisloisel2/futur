"""tests/test_liq_cascade_far_from_low_variant.py — LIQ_CASCADE_FAR_FROM_LOW_V1 (Live Alpha Lab).

Covers: near/far low-bucket classification (frozen threshold), SHORT_SQUEEZE
exclusion (explicitly blocked pending sign-convention resolution, same reason
as LIQ_CASCADE_REPEAT_V1), NaN/missing-feature handling, empty-input handling
(including a truly empty DataFrame with no columns), and fail-closed behavior
of this alpha's own runner when the registry entry isn't
SHADOW_LIVE/EXECUTION_SHADOW.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.liq_cascade.far_from_low_variant import (
    FAR_FROM_LOW_MIN_DIST_24H, classify_low_bucket, select_tradeable)


def test_classify_low_bucket_boundaries():
    assert classify_low_bucket(0.0) == "near"                              # exactly at the low
    assert classify_low_bucket(FAR_FROM_LOW_MIN_DIST_24H - 1e-9) == "near"  # just under threshold
    assert classify_low_bucket(FAR_FROM_LOW_MIN_DIST_24H) == "far"          # exactly at threshold
    assert classify_low_bucket(FAR_FROM_LOW_MIN_DIST_24H + 0.10) == "far"   # well above


def test_classify_low_bucket_nan_is_never_far():
    """Missing dist_low_24h (insufficient warmup) must never be classified far
    -- fail closed on missing feature, never tradeable by accident."""
    assert classify_low_bucket(float("nan")) == "near"
    assert classify_low_bucket(np.nan) == "near"


def _events(rows):
    return pd.DataFrame(rows)


def test_select_tradeable_only_long_cascade_far_from_low():
    events = _events([
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": 0.0},                          # at low -> excluded
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": FAR_FROM_LOW_MIN_DIST_24H / 2}, # mid -> excluded
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": FAR_FROM_LOW_MIN_DIST_24H},     # far -> included
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": 0.20},                          # far -> included
    ])
    out = select_tradeable(events)
    assert len(out) == 2
    assert set(out["dist_low_24h"]) == {FAR_FROM_LOW_MIN_DIST_24H, 0.20}
    assert (out["direction"] == "LONG").all()
    assert (out["low_bucket"] == "far").all()


def test_select_tradeable_never_emits_short_squeeze():
    """SHORT_SQUEEZE is explicitly blocked (unresolved sign convention,
    detector.py:95) — must never appear in the tradeable output regardless
    of distance from the local low, even far from it."""
    events = _events([
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "dist_low_24h": 0.10},
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "dist_low_24h": 0.30},
    ])
    out = select_tradeable(events)
    assert out.empty

    mixed = _events([
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "dist_low_24h": 0.15},
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": 0.15},
    ])
    out_mixed = select_tradeable(mixed)
    assert len(out_mixed) == 1
    assert out_mixed.iloc[0]["kind"] == "LONG_CASCADE"


def test_select_tradeable_excludes_nan_dist_low():
    """An event with a missing dist_low_24h (e.g. insufficient rolling warmup)
    must never be emitted, even if kind==LONG_CASCADE."""
    events = _events([
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": float("nan")},
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "dist_low_24h": 0.10},
    ])
    out = select_tradeable(events)
    assert len(out) == 1
    assert out.iloc[0]["dist_low_24h"] == 0.10


def test_select_tradeable_empty_input():
    out = select_tradeable(_events([]))
    assert out.empty


def test_select_tradeable_empty_input_no_columns():
    """Truly empty DataFrame with no columns at all -- the reference alpha
    (LIQ_CASCADE_REPEAT_V1) had a real bug class here (crash on `events["kind"]`
    when there are zero columns to index). Must not crash."""
    out = select_tradeable(pd.DataFrame())
    assert out.empty
    assert list(out.columns) == ["low_bucket", "direction"]


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_liq_cascade_far_from_low_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID", "deadbeef")


def test_check_registry_freeze_passes_for_shadow_live_entry():
    from scripts.run_liq_cascade_far_from_low_shadow import check_registry_freeze
    # LIQ_CASCADE_FAR_FROM_LOW_V1 is set to SHADOW_LIVE in configs/live_alpha_registry.yaml
    check_registry_freeze("LIQ_CASCADE_FAR_FROM_LOW_V1", "any-hash-not-checked-here")


def test_check_registry_freeze_fails_closed_for_blocked_alpha():
    from scripts.run_liq_cascade_far_from_low_shadow import check_registry_freeze
    # LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 is explicitly BUG_FOUND/blocked
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1", "any-hash")
