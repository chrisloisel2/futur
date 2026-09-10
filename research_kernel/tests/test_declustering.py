"""The measured chaining trap, pinned."""

from __future__ import annotations

import pandas as pd
import pytest

from research_kernel.declustering import (
    DeclusteringError,
    assert_step_exceeds_window,
    episode_counts_all_methods,
    n_independent,
)


def test_single_link_melts_ten_days_into_one_episode():
    ts = pd.date_range("2026-01-01", periods=80, freq="3H", tz="UTC")
    counts = episode_counts_all_methods(ts, ["BTCUSDT"] * 80, 86400)
    assert counts["first_link"] == 1
    assert counts["complete_link"] > 1
    assert counts["fixed_windows"] == 10


def test_daily_cross_section_over_six_years():
    ts = pd.date_range("2020-01-01", periods=2190, freq="1D", tz="UTC")
    counts = episode_counts_all_methods(ts, None, 86400)
    assert counts["first_link"] == 1
    # complete link counts by pairs at an exactly daily cadence: 24h is not > 24h
    assert counts["complete_link"] == 1095
    assert counts["fixed_windows"] == 2190


def test_one_hour_of_window_changes_the_sample_size_by_a_factor_of_2190():
    ts = pd.date_range("2020-01-01", periods=2190, freq="1D", tz="UTC")
    assert n_independent(ts, None, 86400, "first_link") == 1
    assert n_independent(ts, None, 82800, "first_link") == 2190


def test_bursts_spaced_beyond_the_window_stay_separate():
    stamps = []
    for day in range(10):
        base = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=2 * day)
        stamps.extend(base + pd.Timedelta(minutes=m) for m in range(8))
    counts = episode_counts_all_methods(stamps, ["BTCUSDT"] * len(stamps), 86400)
    assert counts["first_link"] == 10
    assert counts["complete_link"] == 10


def test_symbols_are_declustered_independently():
    ts = list(pd.date_range("2026-01-01", periods=4, freq="1D", tz="UTC")) * 2
    syms = ["BTCUSDT"] * 4 + ["ETHUSDT"] * 4
    assert n_independent(ts, syms, 3600, "fixed_windows") == 8


def test_rebalance_step_must_exceed_the_window():
    assert_step_exceeds_window(172800, 86400)
    with pytest.raises(DeclusteringError):
        assert_step_exceeds_window(86400, 86400)


def test_unknown_method_is_refused():
    with pytest.raises(DeclusteringError):
        n_independent(pd.date_range("2026-01-01", periods=3, tz="UTC"), None, 60, "vibes")
