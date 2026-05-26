from pathlib import Path
import py_compile
script = r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import os
import re
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne
from pymongo.errors import BulkWriteError

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

LOG = logging.getLogger("institutional_ohlcv_builder")

DEFAULT_MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
DEFAULT_MONGO_DB = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trading"))
DEFAULT_SOURCE_COLLECTION = os.getenv("FUTUR_MONGO_SOURCE_COLLECTION", os.getenv("MONGODB_SOURCE_COLLECTION", "historical_ohlcv"))
DEFAULT_TARGET_COLLECTION = os.getenv("FUTUR_MONGO_FEATURE_COLLECTION", "ohlcv_institutional_features")

DEFAULT_HORIZONS = (1, 2, 3, 5, 8, 10, 13, 20, 30, 50, 100, 200)
DEFAULT_LABEL_HORIZONS = (3, 5, 10, 20, 50)
DEFAULT_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")

FEATURE_VERSION = "institutional_ohlcv_v1.0.0"

INTERVAL_SECONDS: Dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}

PANDAS_FREQ: Dict[str, str] = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1D",
    "1w": "1W",
}


@dataclass(frozen=True)
class GroupSpec:
    kind: str
    symbol: str
    interval: str
    count: int
    source_origin: str
    paths: Tuple[Path, ...] = ()
    raw_symbol: Optional[str] = None


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Institutional OHLCV feature builder for MongoDB training datasets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI)
    parser.add_argument("--mongo-db", default=DEFAULT_MONGO_DB)
    parser.add_argument("--source-collection", default=DEFAULT_SOURCE_COLLECTION)
    parser.add_argument("--target-collection", default=DEFAULT_TARGET_COLLECTION)
    parser.add_argument("--data-dir", default=str(Path.cwd() / "data"))
    parser.add_argument("--source-mode", choices=("mongo", "local", "both"), default="both")
    parser.add_argument("--collection-mode", choices=("combined", "per-symbol", "both"), default="per-symbol")

    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--intervals", nargs="*", default=None)
    parser.add_argument("--exclude-intervals", nargs="*", default=None)

    parser.add_argument("--horizons", default=",".join(str(x) for x in DEFAULT_HORIZONS))
    parser.add_argument("--label-horizons", default=",".join(str(x) for x in DEFAULT_LABEL_HORIZONS))
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))

    parser.add_argument("--batch-size", type=int, default=int(os.getenv("FUTUR_MONGO_BATCH_SIZE", "500")))
    parser.add_argument("--chunk-rows", type=int, default=int(os.getenv("FUTUR_MONGO_CHUNK_ROWS", "25000")))
    parser.add_argument("--context-rows", type=int, default=None)
    parser.add_argument("--max-rows-per-group", type=int, default=None)
    parser.add_argument("--min-rows", type=int, default=500)

    parser.add_argument("--start", default=None, help="Inclusive timestamp filter, e.g. 2022-01-01")
    parser.add_argument("--end", default=None, help="Inclusive timestamp filter, e.g. 2025-12-31")

    parser.add_argument("--fee-bps", type=float, default=6.0, help="Round-trip fee estimate in basis points")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="Round-trip slippage estimate in basis points")
    parser.add_argument("--triple-target-atr", type=float, default=1.5)
    parser.add_argument("--triple-stop-atr", type=float, default=1.0)

    parser.add_argument("--drop-target", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument("--no-mtf", action="store_true")
    parser.add_argument("--strict-quality", action="store_true")
    parser.add_argument("--min-valid-ratio", type=float, default=0.55)
    parser.add_argument("--fail-on-zero-labels", action="store_true")

    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    horizons = parse_int_list(args.horizons)
    label_horizons = parse_int_list(args.label_horizons)
    timeframes = [canonical_interval(x) for x in str(args.timeframes).split(",") if x.strip()]
    symbol_filter = build_symbol_filter(args.symbols)
    interval_filter = {canonical_interval(x) for x in args.intervals} if args.intervals else None
    exclude_intervals = {canonical_interval(x) for x in args.exclude_intervals} if args.exclude_intervals else set()

    start = parse_user_timestamp(args.start)
    end = parse_user_timestamp(args.end)

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=8000, maxPoolSize=8)
    try:
        client.admin.command("ping")
        db = client[args.mongo_db]
        source = db[args.source_collection]

        if args.drop_target and not args.dry_run and not args.audit_only:
            drop_target_collections(db, args.target_collection, args.collection_mode)
            LOG.warning("dropped target collection set target=%s mode=%s", args.target_collection, args.collection_mode)

        specs = discover_group_specs(
            source=source,
            data_dir=Path(args.data_dir),
            source_mode=args.source_mode,
            symbol_filter=symbol_filter,
            interval_filter=interval_filter,
            exclude_intervals=exclude_intervals,
        )

        integrity = audit_group_coverage(specs, timeframes)
        print_coverage_report(integrity)

        if not specs:
            LOG.error("no OHLCV group found")
            return 2

        totals: Dict[str, int] = {
            "groups": 0,
            "skipped": 0,
            "raw_rows": 0,
            "clean_rows": 0,
            "written_rows": 0,
            "collection_writes": 0,
            "upserted": 0,
            "modified": 0,
            "matched": 0,
        }

        build_started = datetime.now(timezone.utc)
        t0 = time.monotonic()

        for i, spec in enumerate(specs, start=1):
            LOG.info("[%d/%d] processing %s %s source=%s", i, len(specs), spec.symbol, spec.interval, spec.source_origin)
            try:
                stats = process_group(
                    db=db,
                    source_collection=source,
                    spec=spec,
                    target_collection=args.target_collection,
                    collection_mode=args.collection_mode,
                    horizons=horizons,
                    label_horizons=label_horizons,
                    timeframes=timeframes,
                    include_labels=not args.no_labels,
                    include_mtf=not args.no_mtf,
                    batch_size=args.batch_size,
                    chunk_rows=args.chunk_rows,
                    context_rows=args.context_rows,
                    min_rows=args.min_rows,
                    max_rows_per_group=args.max_rows_per_group,
                    start=start,
                    end=end,
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                    triple_target_atr=args.triple_target_atr,
                    triple_stop_atr=args.triple_stop_atr,
                    dry_run=args.dry_run or args.audit_only,
                    strict_quality=args.strict_quality,
                    min_valid_ratio=args.min_valid_ratio,
                    fail_on_zero_labels=args.fail_on_zero_labels,
                )
            except Exception as exc:
                LOG.exception("FAILED group=%s %s: %s", spec.symbol, spec.interval, exc)
                stats = {"skipped": 1}

            accumulate(totals, stats)
            LOG.info(
                "[%d/%d] cumulative groups=%d skipped=%d clean_rows=%d written_rows=%d elapsed=%.1fs",
                i, len(specs), totals["groups"], totals["skipped"], totals["clean_rows"], totals["written_rows"],
                time.monotonic() - t0,
            )

        metadata = {
            "feature_version": FEATURE_VERSION,
            "target_collection": args.target_collection,
            "collection_mode": args.collection_mode,
            "source_collection": args.source_collection,
            "source_mode": args.source_mode,
            "horizons": horizons,
            "label_horizons": label_horizons,
            "timeframes": timeframes,
            "include_labels": not args.no_labels,
            "include_mtf": not args.no_mtf,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "triple_target_atr": args.triple_target_atr,
            "triple_stop_atr": args.triple_stop_atr,
            "start": start,
            "end": end,
            "dry_run": bool(args.dry_run),
            "audit_only": bool(args.audit_only),
            "totals": totals,
            "coverage": integrity,
            "started_at": build_started,
            "finished_at": datetime.now(timezone.utc),
            "elapsed_seconds": float(time.monotonic() - t0),
        }

        if not args.dry_run and not args.audit_only:
            db["ohlcv_feature_build_metadata"].insert_one(mongo_clean(metadata))

        LOG.info("DONE totals=%s", json.dumps(totals, default=str))
        return 0 if totals["groups"] > 0 else 2
    finally:
        client.close()


def process_group(
    *,
    db: Any,
    source_collection: Any,
    spec: GroupSpec,
    target_collection: str,
    collection_mode: str,
    horizons: Sequence[int],
    label_horizons: Sequence[int],
    timeframes: Sequence[str],
    include_labels: bool,
    include_mtf: bool,
    batch_size: int,
    chunk_rows: int,
    context_rows: Optional[int],
    min_rows: int,
    max_rows_per_group: Optional[int],
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
    fee_bps: float,
    slippage_bps: float,
    triple_target_atr: float,
    triple_stop_atr: float,
    dry_run: bool,
    strict_quality: bool,
    min_valid_ratio: float,
    fail_on_zero_labels: bool,
) -> Dict[str, int]:
    raw = load_group_frame(source_collection, spec, max_rows=max_rows_per_group, start=start, end=end)
    if raw is None or raw.empty:
        LOG.warning("skip empty group %s %s", spec.symbol, spec.interval)
        return {"skipped": 1}

    raw_rows = len(raw)
    clean = canonicalize_ohlcv_frame(raw, symbol=spec.symbol, interval=spec.interval, start=start, end=end)
    del raw
    gc.collect()

    if len(clean) < min_rows:
        LOG.warning("skip %s %s: only %d clean rows, min_rows=%d", spec.symbol, spec.interval, len(clean), min_rows)
        return {"skipped": 1, "raw_rows": raw_rows, "clean_rows": len(clean)}

    collections = target_collections_for_symbol(
        db=db,
        target_collection=target_collection,
        collection_mode=collection_mode,
        symbol=spec.symbol,
        dry_run=dry_run,
    )

    if chunk_rows and len(clean) > int(chunk_rows):
        stats = enrich_write_chunked(
            clean=clean,
            collections=collections,
            spec=spec,
            horizons=horizons,
            label_horizons=label_horizons,
            timeframes=timeframes,
            include_labels=include_labels,
            include_mtf=include_mtf,
            batch_size=batch_size,
            chunk_rows=int(chunk_rows),
            context_rows=context_rows,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            triple_target_atr=triple_target_atr,
            triple_stop_atr=triple_stop_atr,
            dry_run=dry_run,
            strict_quality=strict_quality,
            min_valid_ratio=min_valid_ratio,
            fail_on_zero_labels=fail_on_zero_labels,
        )
    else:
        features = build_feature_matrix(
            clean,
            symbol=spec.symbol,
            interval=spec.interval,
            source_origin=spec.source_origin,
            horizons=horizons,
            label_horizons=label_horizons,
            timeframes=timeframes,
            include_labels=include_labels,
            include_mtf=include_mtf,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            triple_target_atr=triple_target_atr,
            triple_stop_atr=triple_stop_atr,
        )
        stats = validate_and_write_features(
            features=features,
            collections=collections,
            symbol=spec.symbol,
            interval=spec.interval,
            source_origin=spec.source_origin,
            batch_size=batch_size,
            dry_run=dry_run,
            strict_quality=strict_quality,
            min_valid_ratio=min_valid_ratio,
            fail_on_zero_labels=fail_on_zero_labels,
        )
        del features

    stats["groups"] = int(stats.get("groups", 0)) or 1
    stats["raw_rows"] = raw_rows
    stats["clean_rows"] = len(clean)
    del clean
    gc.collect()
    return stats


def enrich_write_chunked(
    *,
    clean: pd.DataFrame,
    collections: List[Any],
    spec: GroupSpec,
    horizons: Sequence[int],
    label_horizons: Sequence[int],
    timeframes: Sequence[str],
    include_labels: bool,
    include_mtf: bool,
    batch_size: int,
    chunk_rows: int,
    context_rows: Optional[int],
    fee_bps: float,
    slippage_bps: float,
    triple_target_atr: float,
    triple_stop_atr: float,
    dry_run: bool,
    strict_quality: bool,
    min_valid_ratio: float,
    fail_on_zero_labels: bool,
) -> Dict[str, int]:
    max_feature_h = max([int(x) for x in horizons] or [1])
    max_label_h = max([int(x) for x in label_horizons] or [1]) if include_labels else 0
    context_before = int(context_rows) if context_rows is not None else max(1000, max_feature_h * 5)
    context_after = max_label_h
    n = len(clean)
    totals: Dict[str, int] = {
        "groups": 1,
        "skipped": 0,
        "written_rows": 0,
        "collection_writes": 0,
        "upserted": 0,
        "modified": 0,
        "matched": 0,
    }

    LOG.info(
        "chunking %s %s rows=%d chunk_rows=%d context_before=%d context_after=%d",
        spec.symbol, spec.interval, n, chunk_rows, context_before, context_after,
    )

    for chunk_id, core_start in enumerate(range(0, n, chunk_rows), start=1):
        core_end = min(n, core_start + chunk_rows)
        ext_start = max(0, core_start - context_before)
        ext_end = min(n, core_end + context_after)
        write_index = clean.index[core_start:core_end]

        features = build_feature_matrix(
            clean.iloc[ext_start:ext_end].copy(),
            symbol=spec.symbol,
            interval=spec.interval,
            source_origin=spec.source_origin,
            horizons=horizons,
            label_horizons=label_horizons,
            timeframes=timeframes,
            include_labels=include_labels,
            include_mtf=include_mtf,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            triple_target_atr=triple_target_atr,
            triple_stop_atr=triple_stop_atr,
        )

        features = features.loc[features.index.isin(write_index)].copy()
        if features.empty:
            totals["skipped"] += 1
            continue

        LOG.info(
            "chunk %d %s %s core_rows=%d feature_cols=%d",
            chunk_id, spec.symbol, spec.interval, len(features), len(features.columns),
        )

        stats = validate_and_write_features(
            features=features,
            collections=collections,
            symbol=spec.symbol,
            interval=spec.interval,
            source_origin=spec.source_origin,
            batch_size=batch_size,
            dry_run=dry_run,
            strict_quality=strict_quality,
            min_valid_ratio=min_valid_ratio,
            fail_on_zero_labels=fail_on_zero_labels,
        )

        for k in ("skipped", "written_rows", "collection_writes", "upserted", "modified", "matched"):
            totals[k] = int(totals.get(k, 0)) + int(stats.get(k, 0))
        del features
        gc.collect()

    return totals


def build_feature_matrix(
    raw: pd.DataFrame,
    *,
    symbol: str,
    interval: str,
    source_origin: str,
    horizons: Sequence[int],
    label_horizons: Sequence[int],
    timeframes: Sequence[str],
    include_labels: bool,
    include_mtf: bool,
    fee_bps: float,
    slippage_bps: float,
    triple_target_atr: float,
    triple_stop_atr: float,
) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("empty clean OHLCV frame")

    raw = raw.sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    idx = raw.index
    interval = canonical_interval(interval)

    open_ = raw["open"].astype("float64")
    high = raw["high"].astype("float64")
    low = raw["low"].astype("float64")
    close = raw["close"].astype("float64")
    volume = raw["volume"].astype("float64")
    prev_close = close.shift(1)

    out = pd.DataFrame(index=idx)
    out.index.name = "timestamp"
    out["symbol"] = symbol
    out["symbol_compact"] = compact_symbol(symbol)
    out["interval"] = interval
    out["feature_version"] = FEATURE_VERSION
    out["source_origin"] = source_origin

    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["close"] = close
    out["volume"] = volume
    out["dollar_volume"] = close * volume
    out["log_close"] = safe_log(close)
    out["log_volume"] = safe_log1p(volume)

    interval_sec = INTERVAL_SECONDS.get(interval)
    if interval_sec:
        gap_seconds = idx.to_series().diff().dt.total_seconds().astype("float64")
        out["dq_gap_seconds"] = gap_seconds.values
        out["dq_gap_ratio"] = gap_seconds.values / float(interval_sec)
        out["dq_gap_flag"] = boolean_feature(gap_seconds > interval_sec * 1.5, valid=gap_seconds.notna())
    else:
        out["dq_gap_seconds"] = np.nan
        out["dq_gap_ratio"] = np.nan
        out["dq_gap_flag"] = np.nan

    out["dq_zero_volume"] = boolean_feature(volume <= 0, valid=volume.notna())

    hl_range = high - low
    body = close - open_
    abs_body = body.abs()
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low

    out["return_1"] = close.pct_change(1)
    out["log_return_1"] = safe_log(close / prev_close)
    out["open_to_close_return"] = safe_div(close - open_, open_)
    out["close_to_open_gap"] = safe_div(open_ - prev_close, prev_close)
    out["high_low_range_pct"] = safe_div(hl_range, prev_close)
    out["candle_body_pct"] = safe_div(body, open_)
    out["candle_abs_body_pct"] = safe_div(abs_body, open_)
    out["upper_wick_pct"] = safe_div(upper_wick, open_)
    out["lower_wick_pct"] = safe_div(lower_wick, open_)
    out["body_to_range"] = safe_div(abs_body, hl_range)
    out["upper_wick_to_range"] = safe_div(upper_wick, hl_range)
    out["lower_wick_to_range"] = safe_div(lower_wick, hl_range)
    out["close_position_in_range"] = safe_div(close - low, hl_range)
    out["bullish_candle"] = boolean_feature(close > open_, valid=close.notna() & open_.notna())
    out["bearish_candle"] = boolean_feature(close < open_, valid=close.notna() & open_.notna())
    out["doji_score"] = 1.0 - safe_div(abs_body, hl_range).clip(lower=0, upper=1)
    out["hammer_score"] = score_hammer(open_, high, low, close)
    out["shooting_star_score"] = score_shooting_star(open_, high, low, close)

    true_range = compute_true_range(high, low, close)
    log_ret = out["log_return_1"]

    for h in horizons:
        h = int(h)
        if h < 1:
            continue
        minp = min_periods(h)

        rolling_close_mean = close.rolling(h, min_periods=minp).mean()
        rolling_close_std = close.rolling(h, min_periods=minp).std()
        rolling_vol_mean = volume.rolling(h, min_periods=minp).mean()
        rolling_vol_std = volume.rolling(h, min_periods=minp).std()
        rolling_high = high.rolling(h, min_periods=minp).max()
        rolling_low = low.rolling(h, min_periods=minp).min()
        rolling_range = rolling_high - rolling_low

        out[f"return_{h}"] = close.pct_change(h)
        out[f"log_return_{h}"] = safe_log(close / close.shift(h))
        out[f"return_accel_{h}"] = out[f"return_{h}"] - out[f"return_{h}"].shift(h)
        out[f"realized_vol_{h}"] = log_ret.rolling(h, min_periods=minp).std()
        out[f"downside_vol_{h}"] = log_ret.where(log_ret < 0).rolling(h, min_periods=minp).std()
        out[f"upside_vol_{h}"] = log_ret.where(log_ret > 0).rolling(h, min_periods=minp).std()
        out[f"range_pct_{h}"] = safe_div(rolling_range, close)
        out[f"close_zscore_{h}"] = safe_div(close - rolling_close_mean, rolling_close_std)
        out[f"return_zscore_{h}"] = safe_div(log_ret - log_ret.rolling(h, min_periods=minp).mean(), log_ret.rolling(h, min_periods=minp).std())

        out[f"volume_mean_{h}"] = rolling_vol_mean
        out[f"volume_ratio_{h}"] = safe_div(volume, rolling_vol_mean)
        out[f"volume_zscore_{h}"] = safe_div(volume - rolling_vol_mean, rolling_vol_std)
        out[f"dollar_volume_ratio_{h}"] = safe_div(out["dollar_volume"], out["dollar_volume"].rolling(h, min_periods=minp).mean())

        out[f"sma_{h}"] = rolling_close_mean
        out[f"ema_{h}"] = ema(close, h)
        out[f"distance_sma_{h}"] = safe_div(close - out[f"sma_{h}"], close)
        out[f"distance_ema_{h}"] = safe_div(close - out[f"ema_{h}"], close)
        out[f"ema_slope_{h}"] = safe_div(out[f"ema_{h}"] - out[f"ema_{h}"].shift(h), close.shift(h))
        out[f"sma_slope_{h}"] = safe_div(out[f"sma_{h}"] - out[f"sma_{h}"].shift(h), close.shift(h))

        out[f"rolling_high_{h}"] = rolling_high
        out[f"rolling_low_{h}"] = rolling_low
        out[f"distance_high_{h}"] = safe_div(close - rolling_high, close)
        out[f"distance_low_{h}"] = safe_div(close - rolling_low, close)
        out[f"donchian_position_{h}"] = safe_div(close - rolling_low, rolling_range)
        out[f"breakout_high_{h}"] = boolean_feature(close > rolling_high.shift(1), valid=rolling_high.shift(1).notna())
        out[f"breakdown_low_{h}"] = boolean_feature(close < rolling_low.shift(1), valid=rolling_low.shift(1).notna())

        atr_h = atr_from_tr(true_range, h)
        out[f"atr_{h}"] = atr_h
        out[f"atr_pct_{h}"] = safe_div(atr_h, close)
        out[f"atr_expansion_{h}"] = safe_div(atr_h, atr_h.rolling(h, min_periods=minp).mean())

        bb_mid = rolling_close_mean
        bb_std = rolling_close_std
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std
        out[f"bb_width_{h}"] = safe_div(bb_upper - bb_lower, bb_mid)
        out[f"bb_percent_b_{h}"] = safe_div(close - bb_lower, bb_upper - bb_lower)
        out[f"distance_bb_upper_{h}"] = safe_div(close - bb_upper, close)
        out[f"distance_bb_lower_{h}"] = safe_div(close - bb_lower, close)

        out[f"rsi_{h}"] = rsi(close, h)
        out[f"stoch_k_{h}"] = 100.0 * safe_div(close - rolling_low, rolling_range)
        out[f"williams_r_{h}"] = -100.0 * safe_div(rolling_high - close, rolling_range)
        out[f"roc_{h}"] = safe_div(close - close.shift(h), close.shift(h))
        out[f"cci_{h}"] = cci(high, low, close, h)
        out[f"mfi_{h}"] = mfi(high, low, close, volume, h)
        out[f"cmf_{h}"] = cmf(high, low, close, volume, h)
        out[f"efficiency_ratio_{h}"] = efficiency_ratio(close, h)
        out[f"choppiness_{h}"] = choppiness_index(high, low, close, h)

        plus_di, minus_di, adx = adx_indicators(high, low, close, h)
        out[f"di_plus_{h}"] = plus_di
        out[f"di_minus_{h}"] = minus_di
        out[f"di_spread_{h}"] = plus_di - minus_di
        out[f"adx_{h}"] = adx

        out[f"rolling_vwap_{h}"] = rolling_vwap(high, low, close, volume, h)
        out[f"distance_vwap_{h}"] = safe_div(close - out[f"rolling_vwap_{h}"], close)

        out[f"liquidity_volume_per_range_{h}"] = safe_div(volume.rolling(h, min_periods=minp).sum(), rolling_range)
        out[f"amihud_illiq_{h}"] = safe_div(out[f"return_{h}"].abs(), out["dollar_volume"].rolling(h, min_periods=minp).mean())
        out[f"absorption_proxy_{h}"] = out[f"volume_zscore_{h}"] - out[f"range_pct_{h}"].rank(pct=True)

        if h in {10, 20, 30, 50, 100, 200}:
            out[f"regression_slope_{h}"] = rolling_slope(safe_log(close), h)
            out[f"regression_r2_{h}"] = rolling_r2(safe_log(close), h)
            out[f"hurst_proxy_{h}"] = hurst_proxy(close, h)

    out["obv"] = obv(close, volume)
    out["obv_slope_20"] = safe_div(out["obv"] - out["obv"].shift(20), out["dollar_volume"].rolling(20, min_periods=10).mean())
    out["vpt"] = (volume * close.pct_change()).replace([np.inf, -np.inf], np.nan).fillna(0.0).cumsum()
    out["force_index_13"] = ema(close.diff() * volume, 13)

    macd_line, macd_signal, macd_hist = macd(close)
    out["macd"] = macd_line
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd_hist
    out["macd_hist_slope"] = macd_hist.diff()

    out["ema_9_21_spread"] = safe_div(ema(close, 9) - ema(close, 21), close)
    out["ema_21_50_spread"] = safe_div(ema(close, 21) - ema(close, 50), close)
    out["ema_50_200_spread"] = safe_div(ema(close, 50) - ema(close, 200), close)

    out["consecutive_up_closes"] = consecutive_count(close.diff() > 0)
    out["consecutive_down_closes"] = consecutive_count(close.diff() < 0)
    out["consecutive_volume_up"] = consecutive_count(volume.diff() > 0)

    out["current_drawdown_50"] = safe_div(close, close.rolling(50, min_periods=20).max()) - 1.0
    out["current_runup_50"] = safe_div(close, close.rolling(50, min_periods=20).min()) - 1.0
    out["current_drawdown_200"] = safe_div(close, close.rolling(200, min_periods=50).max()) - 1.0
    out["noise_to_signal_20"] = safe_div(close.diff().abs().rolling(20, min_periods=10).sum(), (close - close.shift(20)).abs())

    add_time_features(out)

    out["trend_score"] = composite_trend_score(out)
    out["momentum_score"] = composite_momentum_score(out)
    out["volatility_score"] = composite_volatility_score(out)
    out["breakout_score"] = composite_breakout_score(out)
    out["reversal_score"] = composite_reversal_score(out)
    out["liquidity_score"] = composite_liquidity_score(out)
    out["market_quality_score"] = composite_market_quality(out)

    if include_mtf:
        mtf = build_multi_timeframe_features(raw, base_interval=interval, symbol=symbol, timeframes=timeframes)
        if not mtf.empty:
            out = pd.concat([out, mtf], axis=1)

    if include_labels:
        label_frame = build_labels(
            close=close,
            high=high,
            low=low,
            atr_pct=out.get("atr_pct_14", out.get("atr_pct_13", out.get("atr_pct_20"))),
            label_horizons=label_horizons,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            triple_target_atr=triple_target_atr,
            triple_stop_atr=triple_stop_atr,
        )
        out = pd.concat([out, label_frame], axis=1)

    out["enriched_at"] = datetime.now(timezone.utc)

    out = out.replace([np.inf, -np.inf], np.nan)
    out = downcast_numeric(out)
    return out


def build_multi_timeframe_features(
    raw: pd.DataFrame,
    *,
    base_interval: str,
    symbol: str,
    timeframes: Sequence[str],
) -> pd.DataFrame:
    base_interval = canonical_interval(base_interval)
    base_seconds = INTERVAL_SECONDS.get(base_interval)
    if not base_seconds:
        return pd.DataFrame(index=raw.index)

    pieces: List[pd.DataFrame] = []
    decision_time = pd.DataFrame(
        {"decision_time": raw.index + pd.to_timedelta(base_seconds, unit="s")},
        index=raw.index,
    ).sort_values("decision_time")

    for tf in timeframes:
        tf = canonical_interval(tf)
        tf_seconds = INTERVAL_SECONDS.get(tf)
        rule = PANDAS_FREQ.get(tf)
        if not tf_seconds or not rule:
            continue
        if tf_seconds <= base_seconds:
            continue
        if tf_seconds % base_seconds != 0:
            continue

        resampled = (
            raw[["open", "high", "low", "close", "volume"]]
            .resample(rule, label="right", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"])
        )
        if len(resampled) < 50:
            continue

        lite = build_light_indicator_set(resampled)
        lite = lite.add_prefix(f"mtf_{tf}_")
        right = lite.reset_index().rename(columns={lite.index.name or "index": "mtf_time"})
        if "timestamp" in right.columns:
            right = right.rename(columns={"timestamp": "mtf_time"})
        right = right.sort_values("mtf_time")

        merged = pd.merge_asof(
            decision_time.reset_index().sort_values("decision_time"),
            right,
            left_on="decision_time",
            right_on="mtf_time",
            direction="backward",
            allow_exact_matches=True,
        )
        merged = merged.set_index("timestamp").drop(columns=["decision_time", "mtf_time"], errors="ignore")
        merged = merged.reindex(raw.index)
        pieces.append(merged)

    if not pieces:
        return pd.DataFrame(index=raw.index)
    return pd.concat(pieces, axis=1)


def build_light_indicator_set(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["close"].astype("float64")
    high = frame["high"].astype("float64")
    low = frame["low"].astype("float64")
    volume = frame["volume"].astype("float64")
    tr = compute_true_range(high, low, close)
    out = pd.DataFrame(index=frame.index)
    out.index.name = "timestamp"

    for h in (5, 10, 20, 50, 100):
        minp = min_periods(h)
        ema_h = ema(close, h)
        out[f"return_{h}"] = close.pct_change(h)
        out[f"realized_vol_{h}"] = safe_log(close / close.shift(1)).rolling(h, min_periods=minp).std()
        out[f"ema_distance_{h}"] = safe_div(close - ema_h, close)
        out[f"ema_slope_{h}"] = safe_div(ema_h - ema_h.shift(h), close.shift(h))
        out[f"rsi_{h}"] = rsi(close, h)
        out[f"atr_pct_{h}"] = safe_div(atr_from_tr(tr, h), close)
        high_n = high.rolling(h, min_periods=minp).max()
        low_n = low.rolling(h, min_periods=minp).min()
        out[f"donchian_position_{h}"] = safe_div(close - low_n, high_n - low_n)
        out[f"volume_zscore_{h}"] = safe_div(volume - volume.rolling(h, min_periods=minp).mean(), volume.rolling(h, min_periods=minp).std())
        out[f"choppiness_{h}"] = choppiness_index(high, low, close, h)
        _, _, adx = adx_indicators(high, low, close, h)
        out[f"adx_{h}"] = adx

    out["trend_score"] = composite_trend_score(out)
    out["momentum_score"] = composite_momentum_score(out)
    out["volatility_score"] = composite_volatility_score(out)
    return out.replace([np.inf, -np.inf], np.nan)


def build_labels(
    *,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_pct: Optional[pd.Series],
    label_horizons: Sequence[int],
    fee_bps: float,
    slippage_bps: float,
    triple_target_atr: float,
    triple_stop_atr: float,
) -> pd.DataFrame:
    out = pd.DataFrame(index=close.index)
    if atr_pct is None:
        atr_pct = safe_log(close / close.shift(1)).rolling(20, min_periods=10).std()

    round_trip_cost = (float(fee_bps) + float(slippage_bps)) / 10000.0

    for h in label_horizons:
        h = int(h)
        if h < 1:
            continue

        future_close = close.shift(-h)
        future_high = forward_rolling(high, h, "max")
        future_low = forward_rolling(low, h, "min")

        future_return = safe_div(future_close - close, close)
        mfe = safe_div(future_high - close, close)
        mae = safe_div(future_low - close, close)

        out[f"label_future_return_{h}"] = future_return
        out[f"label_future_log_return_{h}"] = safe_log(future_close / close)
        out[f"label_mfe_{h}"] = mfe
        out[f"label_mae_{h}"] = mae
        out[f"label_net_return_long_{h}"] = future_return - round_trip_cost
        out[f"label_net_return_short_{h}"] = -future_return - round_trip_cost

        direction = pd.Series(np.nan, index=close.index, dtype="float64")
        direction[future_return > round_trip_cost] = 1.0
        direction[future_return < -round_trip_cost] = -1.0
        direction[(future_return <= round_trip_cost) & (future_return >= -round_trip_cost)] = 0.0
        out[f"label_direction_{h}"] = direction

        rr = safe_div(mfe - round_trip_cost, (mae.abs() + round_trip_cost))
        out[f"label_long_reward_risk_{h}"] = rr

        triple, tte = triple_barrier(
            close=close,
            high=high,
            low=low,
            atr_pct=atr_pct,
            horizon=h,
            target_mult=triple_target_atr,
            stop_mult=triple_stop_atr,
            cost_rate=round_trip_cost,
        )
        out[f"label_triple_barrier_{h}"] = triple
        out[f"label_time_to_barrier_{h}"] = tte

    return out


def triple_barrier(
    *,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_pct: pd.Series,
    horizon: int,
    target_mult: float,
    stop_mult: float,
    cost_rate: float,
) -> Tuple[pd.Series, pd.Series]:
    c = close.to_numpy(dtype="float64")
    h = high.to_numpy(dtype="float64")
    l = low.to_numpy(dtype="float64")
    a = atr_pct.to_numpy(dtype="float64")
    n = len(c)
    label = np.full(n, np.nan, dtype="float64")
    tte = np.full(n, np.nan, dtype="float64")

    for i in range(0, max(0, n - horizon)):
        if not np.isfinite(c[i]) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        upper = c[i] * (1.0 + target_mult * a[i] + cost_rate)
        lower = c[i] * (1.0 - stop_mult * a[i] - cost_rate)
        event = 0.0
        event_t = float(horizon)

        for j in range(i + 1, i + horizon + 1):
            hit_up = np.isfinite(h[j]) and h[j] >= upper
            hit_down = np.isfinite(l[j]) and l[j] <= lower
            if hit_up and hit_down:
                event = 0.0
                event_t = float(j - i)
                break
            if hit_up:
                event = 1.0
                event_t = float(j - i)
                break
            if hit_down:
                event = -1.0
                event_t = float(j - i)
                break

        if event == 0.0 and np.isfinite(c[i + horizon]):
            r = (c[i + horizon] - c[i]) / c[i]
            if r > cost_rate:
                event = 1.0
            elif r < -cost_rate:
                event = -1.0
            else:
                event = 0.0

        label[i] = event
        tte[i] = event_t

    return pd.Series(label, index=close.index), pd.Series(tte, index=close.index)


def validate_and_write_features(
    *,
    features: pd.DataFrame,
    collections: List[Any],
    symbol: str,
    interval: str,
    source_origin: str,
    batch_size: int,
    dry_run: bool,
    strict_quality: bool,
    min_valid_ratio: float,
    fail_on_zero_labels: bool,
) -> Dict[str, int]:
    audit = audit_feature_matrix(features)

    LOG.info(
        "audit %s %s rows=%d cols=%d numeric_valid=%.3f all_null_cols=%d all_zero_cols=%d constant_cols=%d label_cols=%d",
        symbol,
        interval,
        audit["rows"],
        audit["cols"],
        audit["numeric_valid_ratio"],
        len(audit["all_null_cols"]),
        len(audit["all_zero_cols"]),
        len(audit["constant_cols"]),
        len(audit["label_cols"]),
    )

    if audit["all_zero_cols"]:
        LOG.warning("all-zero columns sample %s %s: %s", symbol, interval, audit["all_zero_cols"][:20])
    if audit["all_null_cols"]:
        LOG.warning("all-null columns sample %s %s: %s", symbol, interval, audit["all_null_cols"][:20])
    if audit["label_distribution"]:
        LOG.info("label distribution sample %s %s: %s", symbol, interval, json.dumps(audit["label_distribution"], default=str)[:1000])

    if strict_quality and audit["numeric_valid_ratio"] < float(min_valid_ratio):
        raise RuntimeError(
            "quality gate failed: numeric_valid_ratio=%.4f < %.4f for %s %s"
            % (audit["numeric_valid_ratio"], min_valid_ratio, symbol, interval)
        )

    if fail_on_zero_labels and audit["label_cols"]:
        bad_labels = [
            c for c in audit["label_cols"]
            if c.startswith("label_direction_") and features[c].dropna().nunique() <= 1
        ]
        if bad_labels:
            raise RuntimeError("quality gate failed: degenerate label columns %s" % bad_labels[:10])

    stats: Dict[str, int] = {
        "groups": 1,
        "skipped": 0,
        "written_rows": 0,
        "collection_writes": 0,
        "upserted": 0,
        "modified": 0,
        "matched": 0,
    }

    if dry_run:
        stats["written_rows"] = len(features)
        stats["collection_writes"] = len(features) * max(1, len(collections))
        return stats

    batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(features), batch_size):
        batch_df = features.iloc[start:start + batch_size]
        records = dataframe_to_mongo_records(batch_df)
        if not records:
            continue

        ops = [
            ReplaceOne(
                {"symbol": r["symbol"], "interval": r["interval"], "timestamp": r["timestamp"]},
                r,
                upsert=True,
            )
            for r in records
        ]

        for collection in collections:
            try:
                result = collection.bulk_write(ops, ordered=False)
            except BulkWriteError as exc:
                LOG.error("bulk write error collection=%s details=%s", collection.name, str(exc.details)[:2000])
                raise
            stats["collection_writes"] += len(ops)
            stats["upserted"] += int(result.upserted_count or 0)
            stats["modified"] += int(result.modified_count or 0)
            stats["matched"] += int(result.matched_count or 0)

        stats["written_rows"] += len(records)

    return stats


def audit_feature_matrix(df: pd.DataFrame) -> Dict[str, Any]:
    numeric = df.select_dtypes(include=[np.number])
    numeric_valid_ratio = float(numeric.notna().mean().mean()) if numeric.shape[1] else 0.0

    all_null_cols = [c for c in df.columns if df[c].isna().all()]
    all_zero_cols: List[str] = []
    constant_cols: List[str] = []

    for c in numeric.columns:
        s = numeric[c].dropna()
        if s.empty:
            continue
        if bool((s == 0).all()):
            all_zero_cols.append(str(c))
        if s.nunique(dropna=True) <= 1:
            constant_cols.append(str(c))

    label_cols = [str(c) for c in df.columns if str(c).startswith("label_")]
    label_distribution: Dict[str, Dict[str, int]] = {}
    for c in label_cols:
        if "direction" in c or "triple_barrier" in c:
            vc = df[c].value_counts(dropna=False).head(10)
            label_distribution[c] = {str(k): int(v) for k, v in vc.items()}

    return {
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "numeric_cols": int(len(numeric.columns)),
        "numeric_valid_ratio": numeric_valid_ratio,
        "all_null_cols": all_null_cols,
        "all_zero_cols": all_zero_cols,
        "constant_cols": constant_cols,
        "label_cols": label_cols,
        "label_distribution": label_distribution,
    }


def dataframe_to_mongo_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    frame = df.reset_index()
    if "index" in frame.columns and "timestamp" not in frame.columns:
        frame = frame.rename(columns={"index": "timestamp"})
    if "timestamp" not in frame.columns:
        raise ValueError("feature frame must have timestamp index or timestamp column")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame[frame["timestamp"].notna()].copy()
    if frame.empty:
        return []

    for col in frame.select_dtypes(include=["datetimetz", "datetime64[ns]", "datetime64[ns, UTC]"]).columns:
        values = pd.to_datetime(frame[col], utc=True, errors="coerce")
        frame[col] = values.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_pydatetime()

    float_cols = frame.select_dtypes(include=["float", "float32", "float64"]).columns
    if len(float_cols):
        frame[float_cols] = frame[float_cols].replace([np.inf, -np.inf], np.nan)

    records = []
    for rec in frame.to_dict("records"):
        records.append(mongo_clean(rec))
    return records


def mongo_clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): mongo_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mongo_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            value = value.tz_convert("UTC").tz_localize(None)
        return value.to_pydatetime()
    if isinstance(value, np.datetime64):
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return ts.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return None if not math.isfinite(f) else f
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (str, bool, int, bytes)):
        return value
    if pd.isna(value):
        return None
    return value


def canonicalize_ohlcv_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    interval: str,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    columns = {normalize_col(c): c for c in frame.columns}

    ts_col = find_col(columns, ["timestamp", "datetime", "date", "time", "open_time", "opentime", "start_time", "kline_open_time"])
    open_col = find_col(columns, ["open", "o"])
    high_col = find_col(columns, ["high", "h"])
    low_col = find_col(columns, ["low", "l"])
    close_col = find_col(columns, ["close", "c", "last"])
    volume_col = find_col(columns, ["volume", "vol", "base_volume", "v"])

    missing = [name for name, col in {
        "timestamp": ts_col, "open": open_col, "high": high_col, "low": low_col, "close": close_col, "volume": volume_col
    }.items() if col is None]
    if missing:
        raise ValueError("missing required OHLCV columns %s; columns=%s" % (missing, list(frame.columns)[:50]))

    out = pd.DataFrame()
    out["timestamp"] = parse_timestamp_series(frame[ts_col])
    out["open"] = pd.to_numeric(frame[open_col], errors="coerce")
    out["high"] = pd.to_numeric(frame[high_col], errors="coerce")
    out["low"] = pd.to_numeric(frame[low_col], errors="coerce")
    out["close"] = pd.to_numeric(frame[close_col], errors="coerce")
    out["volume"] = pd.to_numeric(frame[volume_col], errors="coerce")

    out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if out.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = out.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    out = out[(out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0) & (out["close"] > 0) & (out["volume"] >= 0)]

    if out.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    repaired_high = out[["open", "high", "low", "close"]].max(axis=1)
    repaired_low = out[["open", "high", "low", "close"]].min(axis=1)
    repaired_rows = int(((out["high"] != repaired_high) | (out["low"] != repaired_low)).sum())
    if repaired_rows:
        LOG.warning("repaired high/low inconsistencies symbol=%s interval=%s rows=%d", symbol, interval, repaired_rows)
    out["high"] = repaired_high
    out["low"] = repaired_low

    if start is not None:
        out = out[out["timestamp"] >= start]
    if end is not None:
        out = out[out["timestamp"] <= end]

    out = out.set_index("timestamp").sort_index()
    out.index.name = "timestamp"
    out = out[~out.index.duplicated(keep="last")]
    return out[["open", "high", "low", "close", "volume"]].astype("float64")


def load_group_frame(
    source_collection: Any,
    spec: GroupSpec,
    *,
    max_rows: Optional[int],
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
) -> Optional[pd.DataFrame]:
    if spec.kind == "local":
        frames: List[pd.DataFrame] = []
        for path in spec.paths:
            try:
                if path.suffix.lower() == ".parquet":
                    df = pd.read_parquet(path)
                else:
                    try:
                        df = pd.read_csv(path, low_memory=False)
                    except Exception:
                        df = pd.read_csv(path, engine="python", on_bad_lines="skip")
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as exc:
                LOG.warning("skip unreadable local file %s: %s", path, exc)
        if not frames:
            return None
        frame = pd.concat(frames, ignore_index=True, sort=False)
        if max_rows:
            frame = frame.tail(int(max_rows))
        return frame

    query: Dict[str, Any] = {"symbol": spec.raw_symbol or spec.symbol, "interval": spec.interval}
    if start is not None or end is not None:
        q: Dict[str, Any] = {}
        if start is not None:
            q["$gte"] = start.to_pydatetime()
        if end is not None:
            q["$lte"] = end.to_pydatetime()
        query["timestamp"] = q

    cursor = source_collection.find(query).sort("timestamp", ASCENDING).batch_size(10000)
    if max_rows:
        cursor = cursor.limit(int(max_rows))
    docs = list(cursor)
    if not docs:
        return None
    return pd.DataFrame(docs).drop(columns=["_id"], errors="ignore")


def discover_group_specs(
    *,
    source: Any,
    data_dir: Path,
    source_mode: str,
    symbol_filter: Optional[Set[str]],
    interval_filter: Optional[Set[str]],
    exclude_intervals: Set[str],
) -> List[GroupSpec]:
    specs: List[GroupSpec] = []

    if source_mode in {"local", "both"}:
        specs.extend(iter_local_group_specs(data_dir, symbol_filter, interval_filter, exclude_intervals))

    if source_mode in {"mongo", "both"}:
        specs.extend(iter_mongo_group_specs(source, symbol_filter, interval_filter, exclude_intervals))

    dedup: Dict[Tuple[str, str, str, str], GroupSpec] = {}
    for spec in specs:
        key = (spec.kind, spec.symbol, spec.interval, spec.source_origin)
        dedup[key] = spec
    return sorted(dedup.values(), key=lambda s: (s.symbol, s.interval, s.kind, s.source_origin))


def iter_local_group_specs(
    data_dir: Path,
    symbol_filter: Optional[Set[str]],
    interval_filter: Optional[Set[str]],
    exclude_intervals: Set[str],
) -> Iterator[GroupSpec]:
    if not data_dir.exists():
        LOG.warning("data dir not found: %s", data_dir)
        return

    candidates = [
        p for p in data_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".csv", ".parquet"} and "_checkpoints" not in p.parts
    ]
    grouped: Dict[Tuple[str, str], List[Path]] = {}
    for path in sorted(candidates):
        symbol = infer_symbol_from_path(path)
        interval = infer_interval_from_path(path)
        if not symbol or not interval:
            continue
        symbol = normalize_storage_symbol(symbol)
        interval = canonical_interval(interval)
        if symbol_filter and not symbol_matches(symbol, symbol_filter):
            continue
        if interval_filter and interval not in interval_filter:
            continue
        if interval in exclude_intervals:
            continue
        grouped.setdefault((symbol, interval), []).append(path)

    for (symbol, interval), paths in sorted(grouped.items()):
        paths = sorted(paths, key=local_path_priority)
        yield GroupSpec(
            kind="local",
            symbol=symbol,
            interval=interval,
            count=len(paths),
            paths=tuple(paths),
            source_origin="local:%s" % str(paths[0]),
        )


def iter_mongo_group_specs(
    source: Any,
    symbol_filter: Optional[Set[str]],
    interval_filter: Optional[Set[str]],
    exclude_intervals: Set[str],
) -> Iterator[GroupSpec]:
    pipeline = [
        {"$group": {"_id": {"symbol": "$symbol", "interval": "$interval"}, "count": {"$sum": 1}}},
        {"$sort": {"_id.symbol": 1, "_id.interval": 1}},
    ]
    for item in source.aggregate(pipeline, allowDiskUse=True):
        raw_symbol = item.get("_id", {}).get("symbol")
        raw_interval = item.get("_id", {}).get("interval")
        if not raw_symbol:
            continue
        interval = canonical_interval(raw_interval or "1h")
        symbol = normalize_storage_symbol(str(raw_symbol))
        if symbol_filter and not symbol_matches(symbol, symbol_filter):
            continue
        if interval_filter and interval not in interval_filter:
            continue
        if interval in exclude_intervals:
            continue
        yield GroupSpec(
            kind="mongo",
            symbol=symbol,
            interval=interval,
            count=int(item.get("count", 0)),
            raw_symbol=str(raw_symbol),
            source_origin="mongo:%s" % source.name,
        )


def ensure_indexes(collection: Any) -> None:
    collection.create_index(
        [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
        unique=True,
        name="uniq_symbol_interval_timestamp",
    )
    collection.create_index([("symbol", ASCENDING), ("interval", ASCENDING)], name="symbol_interval")
    collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
    collection.create_index([("feature_version", ASCENDING)], name="feature_version")
    collection.create_index([("source_origin", ASCENDING)], name="source_origin")


def drop_target_collections(db: Any, target_collection: str, mode: str) -> None:
    names: Set[str] = set()
    if mode in {"combined", "both", "per-symbol"}:
        names.add(target_collection)
    if mode in {"per-symbol", "both"}:
        prefix = target_collection + "_"
        names.update(name for name in db.list_collection_names() if name.startswith(prefix))
    for name in sorted(names):
        db.drop_collection(name)


def target_collections_for_symbol(
    *,
    db: Any,
    target_collection: str,
    collection_mode: str,
    symbol: str,
    dry_run: bool,
) -> List[Any]:
    names: List[str] = []
    if collection_mode in {"combined", "both"}:
        names.append(target_collection)
    if collection_mode in {"per-symbol", "both"}:
        names.append("%s_%s" % (target_collection, compact_symbol(symbol).lower()))
    collections = [db[name] for name in dict.fromkeys(names)]
    if not dry_run:
        for c in collections:
            ensure_indexes(c)
    return collections


def audit_group_coverage(specs: Sequence[GroupSpec], selected_timeframes: Sequence[str]) -> Dict[str, Any]:
    by_symbol: Dict[str, Set[str]] = {}
    counts: Dict[str, Dict[str, int]] = {}
    for spec in specs:
        by_symbol.setdefault(spec.symbol, set()).add(spec.interval)
        counts.setdefault(spec.symbol, {})[spec.interval] = counts.setdefault(spec.symbol, {}).get(spec.interval, 0) + int(spec.count)

    selected = [canonical_interval(x) for x in selected_timeframes]
    missing: Dict[str, List[str]] = {}
    derivable: Dict[str, List[str]] = {}
    ok: Dict[str, List[str]] = {}

    for symbol, intervals in by_symbol.items():
        for tf in selected:
            if tf in intervals:
                ok.setdefault(symbol, []).append(tf)
            elif can_derive_timeframe(tf, intervals):
                derivable.setdefault(symbol, []).append(tf)
            else:
                missing.setdefault(symbol, []).append(tf)

    return {
        "symbols": sorted(by_symbol.keys()),
        "selected_timeframes": selected,
        "available": {k: sorted(v) for k, v in by_symbol.items()},
        "rows_by_group": counts,
        "ok": ok,
        "derivable": derivable,
        "missing": missing,
    }


def print_coverage_report(integrity: Dict[str, Any]) -> None:
    print("\n=== OHLCV COVERAGE AUDIT ===", flush=True)
    print("Selected MTF:", ", ".join(integrity.get("selected_timeframes", [])), flush=True)
    if not integrity.get("symbols"):
        print("NO GROUP FOUND", flush=True)
        print("============================\n", flush=True)
        return

    for symbol in integrity["symbols"]:
        counts = integrity.get("rows_by_group", {}).get(symbol, {})
        available = ", ".join("%s:%s" % (tf, counts.get(tf, "?")) for tf in integrity["available"].get(symbol, [])) or "-"
        derivable = ", ".join(integrity["derivable"].get(symbol, [])) or "-"
        missing = ", ".join(integrity["missing"].get(symbol, [])) or "-"
        status = "OK" if missing == "-" else "MISSING"
        print("%-7s %-14s sources=[%s] derived=[%s] missing=[%s]" % (status, symbol, available, derivable, missing), flush=True)
    print("============================\n", flush=True)


def safe_div(numerator: Any, denominator: Any) -> Any:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        out = numerator / denominator
    if isinstance(out, (pd.Series, pd.DataFrame)):
        return out.replace([np.inf, -np.inf], np.nan)
    return out


def safe_log(x: Any) -> Any:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        out = np.log(x)
    if isinstance(out, (pd.Series, pd.DataFrame)):
        return out.replace([np.inf, -np.inf], np.nan)
    return out


def safe_log1p(x: Any) -> Any:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        out = np.log1p(x)
    if isinstance(out, (pd.Series, pd.DataFrame)):
        return out.replace([np.inf, -np.inf], np.nan)
    return out


def min_periods(h: int) -> int:
    h = int(h)
    if h <= 3:
        return h
    return max(3, int(math.ceil(h * 0.6)))


def boolean_feature(condition: Any, valid: Optional[Any] = None) -> pd.Series:
    condition = pd.Series(condition)
    result = pd.Series(np.nan, index=condition.index, dtype="float64")
    if valid is None:
        valid = condition.notna()
    else:
        valid = pd.Series(valid, index=condition.index).fillna(False)
    result.loc[valid] = condition.loc[valid].astype(float)
    return result


def ema(series: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    return series.ewm(span=n, adjust=False, min_periods=min_periods(n)).mean()


def compute_true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    parts = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1)
    return parts.max(axis=1)


def atr_from_tr(tr: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean()
    rs = safe_div(avg_gain, avg_loss)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def adx_indicators(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    n = max(1, int(n))
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
    tr = compute_true_range(high, low, close)

    atr_n = atr_from_tr(tr, n)
    plus_di = 100.0 * safe_div(plus_dm.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean(), atr_n)
    minus_di = 100.0 * safe_div(minus_dm.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean(), atr_n)
    dx = 100.0 * safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    adx = dx.ewm(alpha=1.0 / n, adjust=False, min_periods=min_periods(n)).mean()
    return plus_di, minus_di, adx


def cci(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    tp = (high + low + close) / 3.0
    ma = tp.rolling(n, min_periods=min_periods(n)).mean()
    mad = tp.rolling(n, min_periods=min_periods(n)).apply(lambda x: np.nanmean(np.abs(x - np.nanmean(x))), raw=True)
    return safe_div(tp - ma, 0.015 * mad)


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    tp = (high + low + close) / 3.0
    money = tp * volume
    direction = tp.diff()
    pos = money.where(direction > 0, 0.0)
    neg = money.where(direction < 0, 0.0)
    pos_sum = pos.rolling(n, min_periods=min_periods(n)).sum()
    neg_sum = neg.rolling(n, min_periods=min_periods(n)).sum()
    ratio = safe_div(pos_sum, neg_sum)
    return 100.0 - 100.0 / (1.0 + ratio)


def cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    mfm = safe_div((close - low) - (high - close), high - low)
    mfv = mfm * volume
    return safe_div(mfv.rolling(n, min_periods=min_periods(n)).sum(), volume.rolling(n, min_periods=min_periods(n)).sum())


def choppiness_index(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    n = max(2, int(n))
    tr_sum = compute_true_range(high, low, close).rolling(n, min_periods=min_periods(n)).sum()
    hh = high.rolling(n, min_periods=min_periods(n)).max()
    ll = low.rolling(n, min_periods=min_periods(n)).min()
    return 100.0 * safe_div(np.log10(safe_div(tr_sum, hh - ll)), math.log10(n))


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    direction = (close - close.shift(n)).abs()
    volatility = close.diff().abs().rolling(n, min_periods=min_periods(n)).sum()
    return safe_div(direction, volatility)


def rolling_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    tp = (high + low + close) / 3.0
    pv = tp * volume
    return safe_div(pv.rolling(n, min_periods=min_periods(n)).sum(), volume.rolling(n, min_periods=min_periods(n)).sum())


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).fillna(0.0).cumsum()


def rolling_slope(series: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n < 2:
        return pd.Series(np.nan, index=series.index)
    x = np.arange(n, dtype="float64")
    x = x - x.mean()
    denom = float(np.dot(x, x))

    def calc(y: np.ndarray) -> float:
        if np.isnan(y).any():
            return np.nan
        yy = y - np.mean(y)
        return float(np.dot(x, yy) / denom)

    return series.rolling(n, min_periods=n).apply(calc, raw=True)


def rolling_r2(series: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n < 2:
        return pd.Series(np.nan, index=series.index)
    x = np.arange(n, dtype="float64")
    x = x - x.mean()
    denom_x = float(np.dot(x, x))

    def calc(y: np.ndarray) -> float:
        if np.isnan(y).any():
            return np.nan
        yy = y - np.mean(y)
        denom_y = float(np.dot(yy, yy))
        if denom_y <= 0:
            return np.nan
        beta = float(np.dot(x, yy) / denom_x)
        yhat = beta * x
        ss_res = float(np.dot(yy - yhat, yy - yhat))
        return max(0.0, min(1.0, 1.0 - ss_res / denom_y))

    return series.rolling(n, min_periods=n).apply(calc, raw=True)


def hurst_proxy(close: pd.Series, n: int) -> pd.Series:
    n = max(10, int(n))
    logp = safe_log(close)
    ret = logp.diff()
    vol_short = ret.rolling(max(2, n // 4), min_periods=max(2, n // 8)).std()
    vol_long = ret.rolling(n, min_periods=min_periods(n)).std()
    return 0.5 + safe_div(np.log(safe_div(vol_short, vol_long)), np.log(0.25)).clip(-0.5, 0.5)


def forward_rolling(series: pd.Series, window: int, op: str) -> pd.Series:
    shifted = series.shift(-1)
    rev = shifted.iloc[::-1]
    if op == "max":
        out = rev.rolling(window, min_periods=window).max().iloc[::-1]
    elif op == "min":
        out = rev.rolling(window, min_periods=window).min().iloc[::-1]
    else:
        raise ValueError("unsupported forward op: %s" % op)
    return out.reindex(series.index)


def consecutive_count(condition: pd.Series) -> pd.Series:
    condition = condition.fillna(False).astype(bool)
    groups = (condition != condition.shift()).cumsum()
    counts = condition.groupby(groups).cumcount() + 1
    return counts.where(condition, 0).astype("float64")


def score_hammer(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    rng = high - low
    body = (close - open_).abs()
    upper = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower = pd.concat([open_, close], axis=1).min(axis=1) - low
    return (
        safe_div(lower, rng).clip(0, 1)
        * (1.0 - safe_div(body, rng).clip(0, 1))
        * (1.0 - safe_div(upper, rng).clip(0, 1))
    )


def score_shooting_star(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    rng = high - low
    body = (close - open_).abs()
    upper = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower = pd.concat([open_, close], axis=1).min(axis=1) - low
    return (
        safe_div(upper, rng).clip(0, 1)
        * (1.0 - safe_div(body, rng).clip(0, 1))
        * (1.0 - safe_div(lower, rng).clip(0, 1))
    )


def add_time_features(out: pd.DataFrame) -> None:
    idx = pd.DatetimeIndex(out.index)
    hour = idx.hour + idx.minute / 60.0
    dow = idx.dayofweek.astype(float)
    month = idx.month.astype(float)

    out["hour_utc"] = hour
    out["day_of_week"] = dow
    out["month"] = month
    out["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
    out["month_sin"] = np.sin(2.0 * np.pi * month / 12.0)
    out["month_cos"] = np.cos(2.0 * np.pi * month / 12.0)

    out["session_asia"] = ((hour >= 0) & (hour < 8)).astype("float64")
    out["session_europe"] = ((hour >= 7) & (hour < 16)).astype("float64")
    out["session_us"] = ((hour >= 13) & (hour < 22)).astype("float64")
    out["session_overlap_eu_us"] = ((hour >= 13) & (hour < 16)).astype("float64")


def composite_trend_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ("distance_ema_20", "distance_ema_50", "ema_21_50_spread", "ema_50_200_spread", "regression_slope_50"):
        if col in out:
            parts.append(np.tanh(out[col].astype("float64") * 20.0))
    if "adx_20" in out:
        parts.append((out["adx_20"].astype("float64") - 20.0) / 30.0)
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_momentum_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ("rsi_14", "rsi_13", "rsi_20"):
        if col in out:
            parts.append((out[col].astype("float64") - 50.0) / 50.0)
            break
    for col in ("macd_hist", "return_5", "return_10", "roc_20"):
        if col in out:
            parts.append(np.tanh(out[col].astype("float64") * 20.0))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_volatility_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ("atr_pct_14", "atr_pct_13", "atr_pct_20", "realized_vol_20", "bb_width_20"):
        if col in out:
            s = out[col].astype("float64")
            parts.append(s.rolling(200, min_periods=50).rank(pct=True))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_breakout_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ("breakout_high_20", "breakout_high_50", "breakout_high_100"):
        if col in out:
            parts.append(out[col].astype("float64"))
    for col in ("volume_zscore_20", "range_pct_20"):
        if col in out:
            parts.append(np.tanh(out[col].astype("float64")))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_reversal_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    if "close_zscore_20" in out:
        parts.append(-np.tanh(out["close_zscore_20"].astype("float64")))
    if "rsi_14" in out:
        r = out["rsi_14"].astype("float64")
        parts.append(((30.0 - r).clip(lower=0) - (r - 70.0).clip(lower=0)) / 30.0)
    elif "rsi_13" in out:
        r = out["rsi_13"].astype("float64")
        parts.append(((30.0 - r).clip(lower=0) - (r - 70.0).clip(lower=0)) / 30.0)
    if "lower_wick_to_range" in out and "upper_wick_to_range" in out:
        parts.append(out["lower_wick_to_range"].astype("float64") - out["upper_wick_to_range"].astype("float64"))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_liquidity_score(out: pd.DataFrame) -> pd.Series:
    parts = []
    if "dollar_volume" in out:
        dv = safe_log1p(out["dollar_volume"].astype("float64"))
        parts.append(dv.rolling(200, min_periods=50).rank(pct=True))
    if "amihud_illiq_20" in out:
        parts.append(1.0 - out["amihud_illiq_20"].astype("float64").rolling(200, min_periods=50).rank(pct=True))
    if "volume_ratio_20" in out:
        parts.append(np.tanh(out["volume_ratio_20"].astype("float64") / 2.0))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def composite_market_quality(out: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ("trend_score", "liquidity_score"):
        if col in out:
            parts.append(out[col].astype("float64"))
    if "choppiness_20" in out:
        parts.append(1.0 - out["choppiness_20"].astype("float64") / 100.0)
    if "dq_gap_flag" in out:
        parts.append(1.0 - out["dq_gap_flag"].astype("float64"))
    if not parts:
        return pd.Series(np.nan, index=out.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def downcast_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = df[col].astype("float32")
    for col in df.select_dtypes(include=["int64"]).columns:
        if col not in {"timestamp"}:
            df[col] = pd.to_numeric(df[col], downcast="integer")
    return df


def normalize_col(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def find_col(columns: Dict[str, Any], aliases: Sequence[str]) -> Optional[Any]:
    for alias in aliases:
        key = normalize_col(alias)
        if key in columns:
            return columns[key]
    return None


def parse_timestamp_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, utc=True, errors="coerce")

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() >= 0.80:
        med = float(numeric.dropna().abs().median())
        if med > 1e17:
            unit = "ns"
        elif med > 1e14:
            unit = "us"
        elif med > 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")

    return pd.to_datetime(series, utc=True, errors="coerce")


def parse_user_timestamp(value: Optional[str]) -> Optional[pd.Timestamp]:
    if not value:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def parse_int_list(value: str) -> List[int]:
    return sorted({int(x.strip()) for x in str(value).split(",") if x.strip()})


def canonical_interval(value: Any) -> str:
    raw = str(value).strip().lower()
    aliases = {
        "60m": "1h",
        "1hour": "1h",
        "hour": "1h",
        "1min": "1m",
        "1minute": "1m",
        "daily": "1d",
        "day": "1d",
        "weekly": "1w",
        "week": "1w",
    }
    return aliases.get(raw, raw)


def can_derive_timeframe(target: str, available: Set[str]) -> bool:
    target_s = INTERVAL_SECONDS.get(canonical_interval(target))
    if not target_s:
        return False
    for interval in available:
        source_s = INTERVAL_SECONDS.get(canonical_interval(interval))
        if source_s and source_s <= target_s and target_s % source_s == 0:
            return True
    return False


def infer_symbol_from_path(path: Path) -> Optional[str]:
    tokens = [path.stem]
    tokens.extend(path.parts)
    for token in reversed(tokens):
        compact = re.sub(r"[^A-Za-z0-9]", "", str(token)).upper()
        if looks_like_symbol(compact):
            return compact
    return None


def infer_interval_from_path(path: Path) -> Optional[str]:
    text = "/".join(str(x).lower() for x in path.parts)
    aliases = {
        "1m": ["1m", "1min", "1minute"],
        "3m": ["3m", "3min"],
        "5m": ["5m", "5min", "5minute"],
        "15m": ["15m", "15min", "15minute"],
        "30m": ["30m", "30min"],
        "1h": ["1h", "60m", "1hour"],
        "4h": ["4h", "240m", "4hour"],
        "1d": ["1d", "daily", "day"],
        "1w": ["1w", "weekly", "week"],
    }
    parts = {str(p).lower() for p in path.parts}
    stem = path.stem.lower()
    for interval, values in aliases.items():
        if stem.endswith("_%s" % interval) or stem.endswith("_%s_ohlcv" % interval) or stem.endswith("_%s_features" % interval):
            return interval
        if parts & set(values):
            return interval
        for value in values:
            if re.search(r"(^|[_/\-=])%s($|[_/\-=])" % re.escape(value), text):
                return interval
    return None


def looks_like_symbol(token: str) -> bool:
    if not token or len(token) < 5 or not token.isalnum():
        return False
    blocked = {"SPOT", "FUTURES", "KLINES", "DATA", "RAW", "OHLCV", "FEATURES", "CRYPTO"}
    if token in blocked:
        return False
    return any(token.endswith(q) and len(token) > len(q) for q in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR"))


def normalize_storage_symbol(symbol: str) -> str:
    compact = compact_symbol(symbol)
    if compact == "BTCUSD":
        compact = "BTCUSDT"
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return "%s/%s" % (compact[:-len(quote)], quote)
    return compact


def compact_symbol(symbol: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(symbol or "")).upper()


def build_symbol_filter(symbols: Optional[Sequence[str]]) -> Optional[Set[str]]:
    if not symbols:
        return None
    out: Set[str] = set()
    for s in symbols:
        out.add(str(s).upper())
        out.add(compact_symbol(s))
        out.add(normalize_storage_symbol(s).upper())
    return out


def symbol_matches(symbol: str, symbol_filter: Set[str]) -> bool:
    variants = {
        str(symbol).upper(),
        compact_symbol(symbol),
        normalize_storage_symbol(symbol).upper(),
    }
    return bool(variants & symbol_filter)


def local_path_priority(path: Path) -> int:
    text = "/".join(str(x).lower() for x in path.parts)
    name = path.name.lower()
    if "/raw/" in text or "binance_vision" in text:
        return 0
    if "ohlcv" in text:
        return 1
    if "alpha" in name:
        return 3
    if "feature" in name:
        return 4
    return 2


def accumulate(total: Dict[str, int], delta: Dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = int(total.get(key, 0)) + int(value)


if __name__ == "__main__":
    raise SystemExit(main())
'''
out = Path("./institutional_ohlcv_mongo_builder.py")
out.write_text(script, encoding="utf-8")
py_compile.compile(str(out), doraise=True)
print(f"Fichier généré et syntaxe validée: {out}")
