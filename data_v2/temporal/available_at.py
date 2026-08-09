"""
data_v2/temporal/available_at.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 12: canonical temporal columns on every normalized observation,
and the one absolute rule of this dataset -- a feature computed "as of" t may
only read rows whose available_at <= t.

Five columns, always in this order of increasing lateness:
  event_time     -- when the thing actually happened, exchange-side
                     (already called `timestamp` in the live raw store,
                     e.g. scripts/validate_derivatives_store.py; kept as an
                     alias input, not renamed away).
  exchange_time  -- exchange-reported time for the event, when distinct from
                     event_time (e.g. a kline's open_time vs its close_time
                     publication instant); defaults to event_time when the
                     source doesn't distinguish them.
  received_at    -- when OUR infrastructure first saw the bytes. For live
                     streams this is the existing `recv_time` field
                     (scripts/validate_derivatives_store.py already checks
                     recv_time >= event_time). For batch/archival sources
                     (Binance Vision daily/monthly zips) there is no
                     socket-receipt instant -- received_at is set to the
                     archive's own publication lag (see BATCH_PUBLICATION_LAG).
  available_at   -- the causal cutoff: the earliest instant a feature is
                     allowed to use this row. received_at plus a small
                     processing/ingestion margin. THIS is the column every
                     feature builder must compare against, not event_time.
  ingested_at    -- when THIS pipeline run wrote the row (wall clock at
                     normalization time) -- an audit/debug column, never
                     used for causality.

Batch sources (Vision zips) publish with a real lag after the data period
they cover -- daily files typically land the following UTC day, monthly
files a few days into the next month. Treating a Vision row's available_at
as equal to its event_time would be leakage: a backtest could "see" a
2024-06-15 5m bar's OI/kline data at 2024-06-15 00:05 when it did not
actually exist publicly until 2024-06-16+.
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
    exchange_time_col: Optional[str] = None,
    received_at_col: Optional[str] = None,
    ingestion_margin: pd.Timedelta = pd.Timedelta(seconds=5),
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Return a copy of df with event_time/exchange_time/received_at/
    available_at/ingested_at added, derived per source_kind."""
    out = df.copy()
    event_time = pd.to_datetime(out[event_time_col], utc=True)
    out["event_time"] = event_time
    out["exchange_time"] = (
        pd.to_datetime(out[exchange_time_col], utc=True) if exchange_time_col else event_time
    )

    if source_kind == "live_stream":
        if received_at_col is None:
            raise ValueError("live_stream source requires received_at_col (e.g. the existing recv_time field)")
        received_at = pd.to_datetime(out[received_at_col], utc=True)
    else:
        lag = BATCH_PUBLICATION_LAG[source_kind]
        received_at = event_time + lag

    out["received_at"] = received_at
    out["available_at"] = received_at + ingestion_margin
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
