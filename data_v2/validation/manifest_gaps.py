#!/usr/bin/env python3
"""
data_v2/validation/manifest_gaps.py
─────────────────────────────────────────────────────────────────────────────
Shared foundation for "is this gap in a P0 dataset actually fixable, or
confirmed absent at the source" -- used by both scripts/
build_backfill_bound_repair.py (decide whether to flag a repair) and
scripts/build_data_v2_readiness.py (exclude a confirmed-unavailable period
from the coverage denominator, so a symbol whose OI genuinely has no data
before some source-confirmed floor -- e.g. ADAUSDT/ZRXUSDT, both 404 for
every single day 2020-09-01..2021-11-30 -- can still reach 100% coverage
of what's actually obtainable, instead of being permanently capped below
target no matter how complete the backfill is).

Factored out into its own module specifically to avoid a circular import:
build_backfill_bound_repair.py originally imported its loaders from
build_data_v2_readiness.py; once readiness also needed the missing-manifest
logic (to exclude confirmed-unavailable periods from its own coverage
denominator), that import direction became circular.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

# Binance Vision futures metrics' DOCUMENTED history floor. The TRUE
# per-symbol floor is frequently later than this (see build_instrument_
# master.py's empirical source-floor detection: ~130/312 symbols 404 for
# every day between this floor and 2021-12-01) -- this constant is only a
# starting point for the expected-coverage window, never assumed to be
# what any given symbol actually achieves.
VISION_OI_FLOOR = pd.Timestamp("2020-09-01", tz="UTC")


def load_year_partitioned(base_dir: Path, symbol: str, filename: str) -> Optional[pd.DataFrame]:
    parts = sorted((base_dir / f"symbol={symbol}").glob(f"year=*/{filename}"))
    if not parts:
        return None
    frames = [pd.read_parquet(p) for p in parts]
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="timestamp").sort_values("timestamp")
    return df


def load_oi(symbol: str) -> Optional[pd.DataFrame]:
    path = ROOT / f"data/derivatives_backfill/binance_vision_metrics/{symbol}_metrics_5m.parquet"
    return pd.read_parquet(path) if path.exists() else None


def load_funding(symbol: str) -> Optional[pd.DataFrame]:
    path = ROOT / f"data/derivatives_backfill/binance/funding/{symbol}.parquet"
    return pd.read_parquet(path) if path.exists() else None


def _missing_days(manifest_path: Path, key: str = "missing") -> set:
    if not manifest_path.exists():
        return set()
    return set(json.loads(manifest_path.read_text()).get(key, []))


def _missing_months(manifest_path: Path) -> set:
    if not manifest_path.exists():
        return set()
    return set(json.loads(manifest_path.read_text()).get("missing_months", []))


def _done_months(manifest_path: Path) -> set:
    if not manifest_path.exists():
        return set()
    return set(json.loads(manifest_path.read_text()).get("done_months", []))


def _done_days(manifest_path: Path, key: str = "done_days") -> set:
    if not manifest_path.exists():
        return set()
    return set(json.loads(manifest_path.read_text()).get(key, []))


# Per P0 dataset: how to load it, the granularity its backfiller tracks
# confirmed-missing/done periods at (day or month), and how to read that
# backfiller's own manifest for each. agg_trades_flow_1m is not a separate
# key -- the 1m and 5m builders share one manifest (data_v2/normalized/
# agg_trades/build_agg_trades_flow.py writes only under OUT_1M's
# manifest.json), so callers evaluating agg_trades_flow_1m reuse the
# agg_trades_flow_5m entry.
#
# done_fn (2026-08-11, LENDUSDT fix): the missing-period check alone can
# only prove a gap unfillable when the manifest recorded an explicit 404
# for every period in it -- but a MONTHLY-cadence source can have a real
# gap INSIDE a month that was never missing at all, just started partway
# through (real case: LENDUSDT's perp_5m July-2020 file fetched fine and
# is in done_months, but its own first row is 2020-07-23, not the 21st its
# canonical_listing_ts implied -- missing_months is empty for that month,
# so the old missing-only check could never classify this as unfillable
# and left it "actionable" forever, even though re-fetching an
# already-done month can only ever reproduce the identical file). done_fn
# closes that: if the WHOLE gap falls inside one period already recorded
# DONE, the source's own real data has already been fetched and provably
# starts where it starts -- see gap_confirmed_unfillable's intra-period
# branch. Generic by construction (no symbol-specific logic): applies to
# any archive with a similar intra-period start.
DATASET_MANIFEST_SPECS = {
    "oi_vision_5m": dict(
        loader=load_oi, ts_col="create_time", source_available_from=VISION_OI_FLOOR, granularity="day",
        missing_fn=lambda s: _missing_days(ROOT / f"data/derivatives_backfill/binance_vision_metrics/{s}_manifest.json"),
        done_fn=lambda s: _done_days(ROOT / f"data/derivatives_backfill/binance_vision_metrics/{s}_manifest.json", key="done"),
    ),
    "perp_5m": dict(
        loader=lambda s: load_year_partitioned(ROOT / "data_v2/normalized/perp_ohlcv/venue=binance", s, "perp_5m.parquet"),
        ts_col="timestamp", source_available_from=None, granularity="month",
        missing_fn=lambda s: _missing_months(ROOT / f"data_v2/normalized/perp_ohlcv/venue=binance/symbol={s}/manifest.json"),
        done_fn=lambda s: _done_months(ROOT / f"data_v2/normalized/perp_ohlcv/venue=binance/symbol={s}/manifest.json"),
    ),
    "spot_5m": dict(
        loader=lambda s: load_year_partitioned(ROOT / "data_v2/normalized/spot_ohlcv/venue=binance", s, "spot_5m.parquet"),
        ts_col="timestamp", source_available_from=None, granularity="month",
        missing_fn=lambda s: _missing_months(ROOT / f"data_v2/normalized/spot_ohlcv/venue=binance/symbol={s}/manifest.json"),
        done_fn=lambda s: _done_months(ROOT / f"data_v2/normalized/spot_ohlcv/venue=binance/symbol={s}/manifest.json"),
    ),
    "agg_trades_flow_5m": dict(
        loader=lambda s: load_year_partitioned(ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance", s, "flow.parquet"),
        ts_col="timestamp", source_available_from=None, granularity="day",
        missing_fn=lambda s: _missing_days(
            ROOT / f"data_v2/normalized/agg_trades_flow/1m/venue=binance/symbol={s}/manifest.json", key="missing_days"
        ),
        done_fn=lambda s: _done_days(
            ROOT / f"data_v2/normalized/agg_trades_flow/1m/venue=binance/symbol={s}/manifest.json", key="done_days"
        ),
    ),
}


def gap_confirmed_unfillable(
    start: pd.Timestamp, end: pd.Timestamp, missing: set, granularity: str, done: Optional[set] = None
) -> bool:
    """True iff the gap [start, end) is provably unfillable, by either of
    two independent proofs:
      1. EVERY period in [start, end) is already recorded as confirmed-
         missing (404'd) in the relevant backfiller's own manifest -- the
         whole gap has already been attempted and there is nothing left to
         fetch. `end`'s own period is excluded (it already has real data
         by construction: it's the data's own observed start).
      2. The ENTIRE gap falls inside a single period already recorded DONE
         (successfully fetched, never missing) -- a monthly/daily archive
         that was fetched and processed but genuinely starts partway
         through that period. Re-fetching an already-done period can only
         reproduce the identical file, so this is just as unfillable as
         case 1, and `missing` alone can never see it (the period was
         never missing). Generic: works for any granularity/source, no
         symbol-specific logic.
    """
    if done and granularity == "month" and (start.year, start.month) == (end.year, end.month):
        if f"{start.year:04d}-{start.month:02d}" in done:
            return True
    if done and granularity == "day" and start.date() == end.date():
        if start.date().isoformat() in done:
            return True
    if not missing:
        return False
    if granularity == "day":
        d = start.date()
        while d < end.date():
            if d.isoformat() not in missing:
                return False
            d += timedelta(days=1)
        return True
    if granularity == "month":
        y, m = start.year, start.month
        while (y, m) < (end.year, end.month):
            if f"{y:04d}-{m:02d}" not in missing:
                return False
            m += 1
            if m > 12:
                m = 1
                y += 1
        return True
    raise ValueError(f"unknown granularity: {granularity}")
