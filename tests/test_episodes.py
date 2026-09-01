"""tests/test_episodes.py — declustering pour FORWARD_LIVE (item 11 :
raw_signals != independent evidence)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.live_alpha_lab.episodes import decluster, summarize


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


def test_close_same_symbol_signals_form_one_cluster():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T02:00:00Z")},   # 2h après -> même cluster (<24h)
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.raw_signals == 2
    assert summary.independent_episodes == 1


def test_far_apart_same_symbol_signals_are_separate_episodes():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-05T00:00:00Z")},   # 4j après -> nouveau cluster
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.independent_episodes == 2


def test_different_symbols_never_share_a_cluster():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "ETHUSDT", "ts": _ts("2026-09-01T00:00:01Z")},   # même instant, symbole différent
    ])
    summary = summarize(df, "ts", cluster_window_hours=24.0)
    assert summary.independent_episodes == 2


def test_empty_input():
    summary = summarize(pd.DataFrame(), "ts")
    assert summary.raw_signals == 0 and summary.independent_episodes == 0


def test_decluster_adds_cluster_id_without_dropping_rows():
    df = pd.DataFrame([
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T00:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-01T01:00:00Z")},
        {"symbol": "BTCUSDT", "ts": _ts("2026-09-10T00:00:00Z")},
    ])
    out = decluster(df, "ts", cluster_window_hours=24.0)
    assert len(out) == 3   # aucune ligne supprimée
    assert out["cluster_id"].nunique() == 2
