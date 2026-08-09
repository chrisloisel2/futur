"""
tests/unit/test_available_at.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 12: market_available_at (when the fact is knowable in
principle) must stay separate from archive_published_at (when THIS
ingestion pathway actually has the bytes) -- and, critically, research
causality must use research_available_at (what a trader connected to
Binance could have known), not get stuck on the archive's own J+1-ish
publication lag when the source is provably live-observable. Concrete
failure modes this locks in:
  1. A kline is never available before its own close_time -- market_
     available_at must be event_time + bar_seconds, not event_time (open
     time) itself.
  2. A historical aggTrade must not acquire an artificial J+1 delay on its
     market_available_at -- the real Vision daily-archive publication lag
     belongs on archive_published_at only.
  3. THE fix from review: for a provably live-observable source (aggTrades/
     klines/OI all genuinely broadcast/pollable on Binance in real time,
     predating essentially this entire dataset's history), a backtest's
     causal cutoff (research_available_at) must track market_available_at,
     NOT the archive's J+1 publication lag -- an earlier version made
     research_available_at == max(market, archive), which understated what
     a real trader could have known by a full day for every batch-sourced
     row in this corpus.
  4. execution_available_at (for live/paper replay) is correctly the
     SLOWER of the two -- receipt + processing latency -- since "provably
     observable live" and "this specific historical pipeline can act on it"
     are different facts.

Gate:
    python3 -m pytest tests/unit/test_available_at.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.temporal.available_at import BATCH_PUBLICATION_LAG, add_temporal_columns, assert_causal


def test_kline_market_available_at_equals_close_time_not_open_time():
    open_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"open_time": [open_time]})
    out = add_temporal_columns(
        df, event_time_col="open_time", source_kind="binance_vision_monthly",
        bar_seconds=300, provably_live_observable=True,
    )
    expected_close = open_time + pd.Timedelta(seconds=300)
    assert out["market_available_at"].iloc[0] == expected_close
    assert out["market_available_at"].iloc[0] > open_time  # never at open time


def test_kline_research_available_at_never_before_close_time():
    open_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"open_time": [open_time]})
    out = add_temporal_columns(
        df, event_time_col="open_time", source_kind="binance_vision_monthly",
        bar_seconds=300, provably_live_observable=True,
    )
    close_time = open_time + pd.Timedelta(seconds=300)
    assert (out["research_available_at"] >= close_time).all()


def test_aggtrade_market_available_at_not_artificially_delayed_by_archive_lag():
    # aggTrades are point events (bar_seconds=0): market_available_at must
    # equal the raw event time exactly, regardless of source_kind -- the
    # archive's J+1-ish publication lag must show up ONLY on
    # archive_published_at, never contaminate market_available_at.
    event_time = pd.Timestamp("2024-06-15 10:00:00.123", tz="UTC")
    df = pd.DataFrame({"transact_time": [event_time]})
    out = add_temporal_columns(
        df, event_time_col="transact_time", source_kind="binance_vision_daily",
        bar_seconds=0, provably_live_observable=True,
    )

    assert out["market_available_at"].iloc[0] == event_time  # unshifted
    expected_lag = BATCH_PUBLICATION_LAG["binance_vision_daily"]
    assert out["archive_published_at"].iloc[0] == event_time + expected_lag
    assert out["archive_published_at"].iloc[0] > out["market_available_at"].iloc[0]


def test_research_available_at_tracks_market_not_archive_for_live_observable_source():
    """The fix from review: a historical aggTrade genuinely broadcast live
    on Binance's public websocket must NOT wait until J+1 (the Vision
    archive's own publication lag) to become usable in a backtest."""
    event_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"transact_time": [event_time]})
    out = add_temporal_columns(
        df, event_time_col="transact_time", source_kind="binance_vision_daily",
        bar_seconds=0, provably_live_observable=True, ingestion_margin=pd.Timedelta(seconds=5),
    )
    research_at = out["research_available_at"].iloc[0]
    archive_at = out["archive_published_at"].iloc[0]

    assert research_at < archive_at  # must NOT inherit the J+1 archive lag
    assert research_at == event_time + pd.Timedelta(seconds=5)  # ~market time + tiny observation margin
    assert (research_at - event_time) < pd.Timedelta(hours=1)  # nowhere near J+1


def test_research_available_at_falls_back_to_archive_when_not_provably_live():
    event_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"transact_time": [event_time]})
    out = add_temporal_columns(
        df, event_time_col="transact_time", source_kind="binance_vision_daily",
        bar_seconds=0, provably_live_observable=False,
    )
    assert out["research_available_at"].iloc[0] == out["archive_published_at"].iloc[0] + pd.Timedelta(seconds=5)


def test_execution_available_at_is_slower_than_research_available_at_for_batch_source():
    """provably observable live != this historical pipeline can act on it --
    execution_available_at (receipt + processing latency) must stay on the
    archive's real timeline even when research_available_at fast-forwards
    to market time."""
    event_time = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")
    df = pd.DataFrame({"transact_time": [event_time]})
    out = add_temporal_columns(
        df, event_time_col="transact_time", source_kind="binance_vision_daily",
        bar_seconds=0, provably_live_observable=True,
    )
    assert out["execution_available_at"].iloc[0] > out["research_available_at"].iloc[0]
    assert out["execution_available_at"].iloc[0] == out["archive_published_at"].iloc[0] + pd.Timedelta(milliseconds=150)


def test_live_stream_execution_available_at_uses_real_recv_time_plus_latency():
    event_time = pd.Timestamp("2024-06-15 10:00:00.000", tz="UTC")
    recv_time = pd.Timestamp("2024-06-15 10:00:00.150", tz="UTC")  # 150ms real network latency
    df = pd.DataFrame({"timestamp": [event_time], "recv_time": [recv_time]})
    out = add_temporal_columns(
        df, event_time_col="timestamp", source_kind="live_stream", bar_seconds=0,
        provably_live_observable=True, received_at_col="recv_time",
    )
    assert out["archive_published_at"].iloc[0] == recv_time
    assert out["execution_available_at"].iloc[0] == recv_time + pd.Timedelta(milliseconds=150)
    assert out["research_available_at"].iloc[0] == event_time + pd.Timedelta(seconds=5)


def test_provably_live_observable_has_no_default_must_be_explicit():
    df = pd.DataFrame({"open_time": [pd.Timestamp("2024-06-15", tz="UTC")]})
    with pytest.raises(TypeError):
        add_temporal_columns(df, event_time_col="open_time", source_kind="binance_vision_monthly", bar_seconds=300)


def test_assert_causal_defaults_to_research_available_at():
    df = pd.DataFrame({
        "feature_ts": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
        "research_available_at": [pd.Timestamp("2024-01-02 00:00", tz="UTC")],  # after feature_ts: leakage
        "execution_available_at": [pd.Timestamp("2023-12-31 00:00", tz="UTC")],  # before: fine
    })
    with pytest.raises(ValueError):
        assert_causal(df, as_of_col="feature_ts")
    assert_causal(df, as_of_col="feature_ts", available_at_col="execution_available_at")  # must not raise
