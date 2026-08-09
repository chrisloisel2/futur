"""
data_v2/temporal/available_at.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 12: canonical temporal columns on every normalized observation,
and the one absolute rule of this dataset -- a feature computed "as of" t may
only read rows whose available_at <= t.

Seven columns, always in this order of increasing lateness:
  event_time          -- when the thing actually happened, exchange-side
                          (already called `timestamp` in the live raw store,
                          e.g. scripts/validate_derivatives_store.py; kept
                          as an alias input, not renamed away).
  exchange_time       -- exchange-reported time for the event, when distinct
                          from event_time; defaults to event_time.
  market_available_at -- the earliest instant the fact is knowable IN
                          PRINCIPLE, independent of our ingestion pathway.
                          For a bar (kline/OI/premium bucket), that is its
                          CLOSE time, not its open/label time -- you cannot
                          know a 5m bar's high/low/close/volume before the
                          bar closes (event_time + bar_seconds). For a point
                          event (an aggTrade), the event itself is the
                          instant of availability (bar_seconds=0).
  archive_published_at -- when THIS specific ingestion pathway could
                          actually have the bytes. For a live stream this is
                          the real network receipt time (the existing
                          `recv_time` field; scripts/validate_derivatives_
                          store.py already checks recv_time >= event_time).
                          For a Binance Vision batch archive there is no
                          socket-receipt instant -- it is market_available_at
                          plus the archive's own real publication lag (see
                          BATCH_PUBLICATION_LAG). Kept SEPARATE from
                          market_available_at on purpose: conflating them
                          would let a J+1 archive-publication lag silently
                          leak into what should be a pure "the bar closed"
                          fact, or conversely let a pure market fact silently
                          stand in for archive availability it doesn't have.
  received_at          -- alias of archive_published_at, kept for backward
                          compatibility with earlier Data V2 code/tests that
                          only knew this name.
  available_at         -- the causal cutoff: max(market_available_at,
                          archive_published_at) plus a small ingestion
                          margin. THIS is the column every feature builder
                          must compare against, never event_time.
  ingested_at           -- when THIS pipeline run wrote the row (wall clock
                          at normalization time) -- audit/debug only, never
                          used for causality.

Two leakage modes this module exists to prevent, both concrete and both
caught by tests/unit/test_available_at.py:
  1. Treating a Vision row's available_at as its event_time: a backtest
     could "see" a 2024-06-15 5m bar's OI/kline data at 2024-06-15 00:05,
     when the archive containing it did not exist publicly until
     2024-06-16+.
  2. Treating a bar's OPEN time as when it becomes knowable: a 5m bar
     labelled 10:00 covers [10:00, 10:05) -- its close/high/low/volume are
     not known until 10:05, regardless of archive lag on top of that.
"""
from __future__ import annotations

from typing import Literal, Optional

import pandas as pd

BATCH_PUBLICATION_LAG = {
    "binance_vision_daily": pd.Timedelta(days=1, hours=6),
    "binance_vision_monthly": pd.Timedelta(days=5),
    "binance_rest_snapshot": pd.Timedelta(minutes=1),
}

SourceKind = Literal["live_stream", "binance_vision_daily", "binance_vision_monthly", "binance_rest_snapshot"]


def add_temporal_columns(
    df: pd.DataFrame,
    *,
    event_time_col: str,
    source_kind: SourceKind,
    bar_seconds: int = 0,
    exchange_time_col: Optional[str] = None,
    received_at_col: Optional[str] = None,
    ingestion_margin: pd.Timedelta = pd.Timedelta(seconds=5),
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Return a copy of df with event_time/exchange_time/market_available_at/
    archive_published_at/received_at/available_at/ingested_at added.

    bar_seconds: 0 for a point event (an aggTrade -- knowable the instant it
    happens). For a bar/bucket (kline, OI 5m bucket, premium 5m bucket) pass
    the bar's duration in seconds (300 for 5m) so market_available_at is
    computed as the bar's CLOSE time, never its open/label time.
    """
    out = df.copy()
    event_time = pd.to_datetime(out[event_time_col], utc=True)
    out["event_time"] = event_time
    out["exchange_time"] = (
        pd.to_datetime(out[exchange_time_col], utc=True) if exchange_time_col else event_time
    )

    market_available_at = event_time + pd.Timedelta(seconds=bar_seconds)
    out["market_available_at"] = market_available_at

    if source_kind == "live_stream":
        if received_at_col is None:
            raise ValueError("live_stream source requires received_at_col (e.g. the existing recv_time field)")
        archive_published_at = pd.to_datetime(out[received_at_col], utc=True)
    else:
        lag = BATCH_PUBLICATION_LAG[source_kind]
        archive_published_at = market_available_at + lag

    out["archive_published_at"] = archive_published_at
    out["received_at"] = archive_published_at  # backward-compat alias
    out["available_at"] = pd.concat([market_available_at, archive_published_at], axis=1).max(axis=1) + ingestion_margin
    out["ingested_at"] = now if now is not None else pd.Timestamp.now(tz="UTC")
    return out


def assert_causal(
    df: pd.DataFrame,
    *,
    as_of_col: str,
    available_at_col: str = "available_at",
) -> None:
    """Raise if any row's available_at is later than the timestamp a
    feature built from it claims to be "as of" -- the absolute leakage
    guard the plan requires. Call this right before a feature/label join,
    not just at ingestion time, since the violation that matters is at the
    point of use."""
    as_of = pd.to_datetime(df[as_of_col], utc=True)
    available_at = pd.to_datetime(df[available_at_col], utc=True)
    violations = available_at > as_of
    if violations.any():
        n = int(violations.sum())
        first_idx = violations.idxmax()
        raise ValueError(
            f"Causality violation: {n} row(s) have available_at > {as_of_col} "
            f"(e.g. row {first_idx}: available_at={available_at.loc[first_idx]} > "
            f"{as_of_col}={as_of.loc[first_idx]}). A feature at t may only depend "
            f"on rows with available_at <= t."
        )
