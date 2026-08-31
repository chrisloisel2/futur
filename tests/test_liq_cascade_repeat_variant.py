"""tests/test_liq_cascade_repeat_variant.py — LIQ_CASCADE_REPEAT_V1 (Live Alpha Lab).

Covers: repeat-bucket classification, SHORT_SQUEEZE exclusion (explicitly
blocked pending sign-convention resolution), universe-hash determinism, and
fail-closed behavior when the registry entry isn't SHADOW_LIVE/EXECUTION_SHADOW.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.liq_cascade.repeat_variant import (
    EXHAUSTION_MIN_PRIOR, classify_repeat_bucket, select_tradeable)


def test_classify_repeat_bucket_boundaries():
    assert classify_repeat_bucket(0) == "onset"
    assert classify_repeat_bucket(1) == "mid"
    assert classify_repeat_bucket(EXHAUSTION_MIN_PRIOR) == "exhaustion"
    assert classify_repeat_bucket(EXHAUSTION_MIN_PRIOR + 5) == "exhaustion"


def _events(rows):
    return pd.DataFrame(rows)


def test_select_tradeable_only_long_cascade_exhaustion():
    events = _events([
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "n_events_sym_24h": 0},   # onset -> excluded
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "n_events_sym_24h": 1},   # mid -> excluded
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "n_events_sym_24h": 2},   # exhaustion -> included
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "n_events_sym_24h": 5},   # exhaustion -> included
    ])
    out = select_tradeable(events)
    assert len(out) == 2
    assert set(out["n_events_sym_24h"]) == {2, 5}
    assert (out["direction"] == "LONG").all()
    assert (out["repeat_bucket"] == "exhaustion").all()


def test_select_tradeable_never_emits_short_squeeze():
    """SHORT_SQUEEZE is explicitly blocked (unresolved sign convention,
    detector.py:95) — must never appear in the tradeable output regardless
    of repeat count, even at exhaustion level."""
    events = _events([
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "n_events_sym_24h": 2},
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "n_events_sym_24h": 10},
    ])
    out = select_tradeable(events)
    assert out.empty

    mixed = _events([
        {"symbol": "BTCUSDT", "kind": "SHORT_SQUEEZE", "n_events_sym_24h": 3},
        {"symbol": "BTCUSDT", "kind": "LONG_CASCADE", "n_events_sym_24h": 3},
    ])
    out_mixed = select_tradeable(mixed)
    assert len(out_mixed) == 1
    assert out_mixed.iloc[0]["kind"] == "LONG_CASCADE"


def test_select_tradeable_empty_input():
    out = select_tradeable(_events([]))
    assert out.empty


def test_universe_hash_deterministic():
    from scripts.run_liq_cascade_repeat_shadow import universe_hash
    a = universe_hash(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    b = universe_hash(["SOLUSDT", "BTCUSDT", "ETHUSDT"])  # order-independent
    assert a == b
    c = universe_hash(["BTCUSDT", "ETHUSDT"])
    assert a != c


def test_check_registry_freeze_fails_closed_for_unknown_alpha():
    from scripts.run_liq_cascade_repeat_shadow import check_registry_freeze
    with pytest.raises(RuntimeError, match="absent"):
        check_registry_freeze("NOT_A_REAL_ALPHA_ID", "deadbeef")


def test_check_registry_freeze_passes_for_shadow_live_entry():
    from scripts.run_liq_cascade_repeat_shadow import check_registry_freeze
    # LIQ_CASCADE_REPEAT_V1 is set to SHADOW_LIVE in configs/live_alpha_registry.yaml
    check_registry_freeze("LIQ_CASCADE_REPEAT_V1", "any-hash-not-checked-here")


def test_check_registry_freeze_fails_closed_for_blocked_alpha():
    from scripts.run_liq_cascade_repeat_shadow import check_registry_freeze
    # LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1 is explicitly BUG_FOUND/blocked
    with pytest.raises(RuntimeError, match="status="):
        check_registry_freeze("LIQ_CASCADE_SHORT_SQUEEZE_EXHAUSTION_V1", "any-hash")
