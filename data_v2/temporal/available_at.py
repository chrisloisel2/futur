"""
data_v2/temporal/available_at.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 12: canonical temporal columns on every normalized observation.

Six columns, plus two DERIVED causal cutoffs for two DIFFERENT questions --
this split is the point of this module, not a stylistic choice:

  event_time            -- when the thing actually happened, exchange-side.
  exchange_time         -- exchange-reported time for the event, when
                            distinct from event_time; defaults to event_time.
  market_available_at   -- the earliest instant the fact is knowable IN
                            PRINCIPLE, independent of how WE happened to
                            acquire it. For a bar (kline/OI/premium bucket),
                            that is its CLOSE time, not its open/label time.
                            For a point event (an aggTrade), the event
                            itself (bar_seconds=0).
  archive_published_at  -- when THIS specific ingestion pathway (a Binance
                            Vision zip, or a live socket) actually had the
                            bytes. For a live stream this is the real
                            network receipt time. For a Vision batch
                            archive it is market_available_at plus the
                            archive's own real publication lag.
  ingested_at            -- when THIS pipeline run wrote the row -- audit/
                            debug only, never used for causality.

  research_available_at -- the causal cutoff for BACKTESTING/RESEARCH:
                            "what could a trader connected to Binance have
                            known at this instant?" -- NOT "what date did
                            WE happen to download the historical zip?". For
                            a source you can justify was genuinely, publicly
                            observable in real time (Binance broadcasts live
                            aggTrade/kline/bookTicker websockets, and OI via
                            REST poll, for essentially this entire dataset's
                            history -- Vision archives are a same-data
                            convenience republish, not a new disclosure),
                            research_available_at = market_available_at
                            (+ a small, fixed observation margin -- even an
                            idealized live trader has some reaction lag).
                            For a source you canNOT justify this for, it
                            falls back to archive_published_at -- do not
                            claim live-observability you can't defend.
                            THIS is the column research/backtest feature
                            builders must compare against.
  execution_available_at -- the causal cutoff for LIVE EXECUTION:
                            received_at (the real, source-specific receipt
                            time -- archive_published_at under the hood)
                            plus a live processing_latency. THIS is the
                            column a live/paper execution engine replay
                            must compare against -- it is deliberately
                            LATER than research_available_at for a batch
                            source, because "provably observable live" and
                            "this specific historical pipeline can act on
                            it" are different facts.

An earlier version of this module used a single available_at =
max(market_available_at, archive_published_at) + margin as THE causal
cutoff for everything. That silently forced every batch-sourced row (i.e.
this entire Data V2 corpus so far: OI/perp/spot/aggTrades all come from
Vision archives) onto its ~J+1 archive-publication lag even for research
use -- a historical aggTrade broadcast live on Binance's public websocket
at 10:00 would only "become available" at J+1 in a backtest, which
understates how much a real trader could have known and is not the
causality question research actually needs answered.

Two leakage modes this module exists to prevent, both concrete and both
caught by tests/unit/test_available_at.py:
  1. Treating a Vision-only row's research_available_at as its event_time
     when the source has NO justified live equivalent: still leakage.
  2. Treating a bar's OPEN time as when it becomes knowable: a 5m bar
     labelled 10:00 covers [10:00, 10:05) -- its close/high/low/volume are
     not known until 10:05, regardless of source or lag on top of that.
"""
from __future__ import annotations

from typing import Literal, Optional

import pandas as pd

BATCH_PUBLICATION_LAG = {
    "binance_vision_daily": pd.Timedelta(days=1, hours=6),
    "binance_vision_monthly": pd.Timedelta(days=5),
    "binance_rest_snapshot": pd.Timedelta(minutes=1),
}

DEFAULT_PROCESSING_LATENCY = pd.Timedelta(milliseconds=150)

SourceKind = Literal["live_stream", "binance_vision_daily", "binance_vision_monthly", "binance_rest_snapshot"]


def daily_publication_watermark(
    now: pd.Timestamp,
    publication_lag: pd.Timedelta,
    bar_seconds: int = 300,
) -> pd.Timestamp:
    """Data V2 Phase 2, section 2: the last bar timestamp a daily-cadence
    Vision archive (oi_vision_5m, agg_trades_flow_1m/5m) can genuinely be
    expected to exist, given `now`. Generic sibling of
    scripts/build_data_v2_readiness.py's _monthly_publication_watermark, at
    daily granularity: a calendar day must be fully CLOSED (we are not
    still inside it) AND past its own publication_lag before Binance has
    actually published that day's archive -- a still-open or
    just-closed-but-not-yet-lagged day is PENDING_PUBLICATION, not a real
    gap. Demanding coverage through "now" regardless would silently cap a
    daily-cadence dataset's pass rate on data the source cannot yet
    provide -- exactly the bug already found and fixed for perp_5m/spot_5m
    (monthly cadence) on 2026-08-14.

    publication_lag is the real, source-specific lag (pass
    BATCH_PUBLICATION_LAG["binance_vision_daily"] for Vision daily
    archives -- see above). bar_seconds is the dataset's own bar grid (300
    for 5m, 60 for 1m) so the returned timestamp is itself a valid grid
    point, not an arbitrary instant.

    Deliberately does NOT apply to funding: funding is acquired from
    Binance's live /fapi/v1/fundingRate REST endpoint (continuous
    accretion, not a lagged daily batch archive) -- a different contract,
    see scripts/backfill_binance_derivatives_free.py."""
    now = pd.Timestamp(now).tz_convert("UTC")  # a non-UTC tz-aware `now` must key off the UTC calendar day, not its own local date
    bar = pd.Timedelta(seconds=bar_seconds)
    day_start = pd.Timestamp(year=now.year, month=now.month, day=now.day, tz="UTC")
    while True:
        # day_start is the close of the day being tested (the day before
        # day_start) -- that day's archive publishes at day_start + lag.
        if day_start + publication_lag <= now:
            return day_start - bar  # last bar of the day before day_start
        day_start -= pd.Timedelta(days=1)


def add_temporal_columns(
    df: pd.DataFrame,
    *,
    event_time_col: str,
    source_kind: SourceKind,
    provably_live_observable: bool,
    bar_seconds: int = 0,
    exchange_time_col: Optional[str] = None,
    received_at_col: Optional[str] = None,
    ingestion_margin: pd.Timedelta = pd.Timedelta(seconds=5),
    processing_latency: pd.Timedelta = DEFAULT_PROCESSING_LATENCY,
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Return a copy of df with event_time/exchange_time/market_available_at/
    archive_published_at/research_available_at/execution_available_at/
    ingested_at added.

    provably_live_observable: REQUIRED, no default -- forces a deliberate,
    justified call at each call site (aggTrades, klines, and OI all have
    genuine Binance live WS/REST equivalents predating this dataset's
    history -> True; a source you cannot make that case for -> False, and
    research_available_at correctly falls back to the slower
    archive_published_at instead of silently claiming live availability).

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
    out["ingested_at"] = now if now is not None else pd.Timestamp.now(tz="UTC")

    out["research_available_at"] = (
        market_available_at + ingestion_margin if provably_live_observable
        else archive_published_at + ingestion_margin
    )
    out["execution_available_at"] = archive_published_at + processing_latency

    return out


def assert_causal(
    df: pd.DataFrame,
    *,
    as_of_col: str,
    available_at_col: str = "research_available_at",
) -> None:
    """Raise if any row's available_at is later than the timestamp a
    feature built from it claims to be "as of" -- the absolute leakage
    guard the plan requires. Call this right before a feature/label join,
    not just at ingestion time, since the violation that matters is at the
    point of use. Defaults to research_available_at (the backtest/research
    causal cutoff) -- pass available_at_col="execution_available_at" when
    checking a live/paper execution replay instead."""
    as_of = pd.to_datetime(df[as_of_col], utc=True)
    available_at = pd.to_datetime(df[available_at_col], utc=True)
    violations = available_at > as_of
    if violations.any():
        n = int(violations.sum())
        first_idx = violations.idxmax()
        raise ValueError(
            f"Causality violation: {n} row(s) have {available_at_col} > {as_of_col} "
            f"(e.g. row {first_idx}: {available_at_col}={available_at.loc[first_idx]} > "
            f"{as_of_col}={as_of.loc[first_idx]}). A feature at t may only depend "
            f"on rows with {available_at_col} <= t."
        )
