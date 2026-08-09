"""
tests/unit/test_available_at.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 12: market_available_at (when the fact is knowable in
principle) must stay separate from archive_published_at (when THIS
ingestion pathway actually has the bytes). Two concrete leakage modes this
locks in:
  1. A kline is never available before its own close_time -- market_
     available_at must be event_time + bar_seconds, not event_time (open
     time) itself.
  2. A historical aggTrade must not acquire an artificial J+1 delay on its
     market_available_at -- the real Vision daily-archive publication lag
     belongs on archive_published_at only, not smeared into the "when did
     this genuinely happen" fact.

Gate:
    python3 -m pytest tests/unit/test_available_at.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.temporal.available_at import BATCH_PUBLICATION_LAG, add_temporal_columns


def test_kline_market_available_at_equals_close_time_not_open_time():
    open_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"open_time": [open_time]})
    out = add_temporal_columns(df, event_time_col="open_time", source_kind="binance_vision_monthly", bar_seconds=300)
    expected_close = open_time + pd.Timedelta(seconds=300)
    assert out["market_available_at"].iloc[0] == expected_close
    assert out["market_available_at"].iloc[0] > open_time  # never at open time


def test_kline_available_at_never_before_close_time():
    open_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"open_time": [open_time]})
    out = add_temporal_columns(df, event_time_col="open_time", source_kind="binance_vision_monthly", bar_seconds=300)
    close_time = open_time + pd.Timedelta(seconds=300)
    assert (out["available_at"] >= close_time).all()


def test_aggtrade_market_available_at_not_artificially_delayed_by_archive_lag():
    # aggTrades are point events (bar_seconds=0): market_available_at must
    # equal the raw event time exactly, regardless of source_kind -- the
    # archive's J+1-ish publication lag must show up ONLY on
    # archive_published_at, never contaminate market_available_at.
    event_time = pd.Timestamp("2024-06-15 10:00:00.123", tz="UTC")
    df = pd.DataFrame({"transact_time": [event_time]})
    out = add_temporal_columns(df, event_time_col="transact_time", source_kind="binance_vision_daily", bar_seconds=0)

    assert out["market_available_at"].iloc[0] == event_time  # unshifted
    expected_lag = BATCH_PUBLICATION_LAG["binance_vision_daily"]
    assert out["archive_published_at"].iloc[0] == event_time + expected_lag
    # the two must actually differ -- this is the whole point of splitting them
    assert out["archive_published_at"].iloc[0] > out["market_available_at"].iloc[0]


def test_live_stream_archive_published_at_uses_real_recv_time_not_a_fixed_lag():
    event_time = pd.Timestamp("2024-06-15 10:00:00.000", tz="UTC")
    recv_time = pd.Timestamp("2024-06-15 10:00:00.150", tz="UTC")  # 150ms real network latency
    df = pd.DataFrame({"timestamp": [event_time], "recv_time": [recv_time]})
    out = add_temporal_columns(
        df, event_time_col="timestamp", source_kind="live_stream", bar_seconds=0, received_at_col="recv_time"
    )
    assert out["archive_published_at"].iloc[0] == recv_time
    assert out["market_available_at"].iloc[0] == event_time
    # available_at driven by the later of the two (here, network receipt)
    assert out["available_at"].iloc[0] > recv_time  # + ingestion_margin


def test_available_at_is_max_of_market_and_archive_when_bar_close_exceeds_archive_lag():
    # a pathological but real-possible case: a very large bar_seconds (e.g.
    # a daily bar) whose close time is LATER than a short archive lag --
    # available_at must track the later of the two, not just archive lag.
    open_time = pd.Timestamp("2024-06-15 00:00:00", tz="UTC")
    df = pd.DataFrame({"open_time": [open_time]})
    out = add_temporal_columns(
        df, event_time_col="open_time", source_kind="binance_rest_snapshot", bar_seconds=24 * 3600  # 1 day bar
    )
    close_time = open_time + pd.Timedelta(days=1)
    assert out["available_at"].iloc[0] >= close_time
    assert out["market_available_at"].iloc[0] == close_time
