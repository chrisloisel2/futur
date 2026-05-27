#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import logging
import math
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

warnings.filterwarnings("ignore", message="Python 3.8 is no longer supported.*")

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning
from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipeline.enriched_ohlcv_features import (  # noqa: E402
    DEFAULT_HORIZONS,
    DEFAULT_TIMEFRAMES,
    compute_enriched_ohlcv_features,
)
from data_pipeline.mongo_training import (  # noqa: E402
    DEFAULT_FEATURE_COLLECTION,
    DEFAULT_MONGO_DB,
    DEFAULT_MONGO_URI,
    normalize_symbol_variants,
)

try:
    from tqdm import tqdm as _tqdm
    HAS_TQDM = True
except ImportError:
    _tqdm = None
    HAS_TQDM = False

DEFAULT_SOURCE_COLLECTION = os.getenv(
    "FUTUR_MONGO_SOURCE_COLLECTION",
    os.getenv("MONGODB_SOURCE_COLLECTION", "historical_ohlcv"),
)

LOG = logging.getLogger("enriched_mongo")
warnings.simplefilter("ignore", PerformanceWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning, message="divide by zero")
warnings.filterwarnings("ignore", category=RuntimeWarning, message="invalid value")


class _SimpleProgress:
    def __init__(self, total: int, unit: str, desc: str, leave: bool = True) -> None:
        self.total = max(1, int(total or 1))
        self.unit = unit
        self.desc = desc
        self.leave = leave
        self.current = 0
        self.postfix = ""
        self._render()

    def set_postfix_str(self, value: str) -> None:
        self.postfix = value
        self._render()

    def set_postfix(self, **kwargs: Any) -> None:
        self.postfix = " ".join("%s=%s" % (key, value) for key, value in kwargs.items())
        self._render()

    def update(self, value: int = 1) -> None:
        self.current = min(self.total, self.current + int(value))
        self._render()

    def close(self) -> None:
        if self.current < self.total:
            self.current = self.total
            self._render()
        print()

    def _render(self) -> None:
        width = 28
        ratio = min(1.0, self.current / self.total)
        filled = int(width * ratio)
        bar = "#" * filled + "-" * (width - filled)
        suffix = (" | " + self.postfix) if self.postfix else ""
        print(
            "\r%s |%s| %d/%d %s%s" % (self.desc, bar, self.current, self.total, self.unit, suffix),
            end="",
            flush=True,
        )


def _make_progress(total: int, unit: str, desc: str, leave: bool = True) -> Optional[Any]:
    if not int(os.getenv("FUTUR_MONGO_PROGRESS", "1")):
        return None
    if HAS_TQDM and _tqdm is not None:
        return _tqdm(total=total, unit=unit, desc=desc, leave=leave)
    return _SimpleProgress(total=total, unit=unit, desc=desc, leave=leave)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build a new enriched MongoDB OHLCV collection from ./data and the "
            "current MongoDB historical collection."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI)
    p.add_argument("--mongo-db", default=DEFAULT_MONGO_DB)
    p.add_argument("--source-collection", default=DEFAULT_SOURCE_COLLECTION)
    p.add_argument("--target-collection", default=DEFAULT_FEATURE_COLLECTION)
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--symbols", nargs="*", default=None, help="Symbol subset, e.g. BTCUSDT ETHUSDT")
    p.add_argument("--intervals", nargs="*", default=None, help="Interval subset, e.g. 1h 4h")
    p.add_argument("--horizons", default=",".join(str(x) for x in DEFAULT_HORIZONS))
    p.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    p.add_argument("--batch-size", type=int, default=2000, help="MongoDB bulk_write batch size")
    p.add_argument(
        "--wide-batch-size",
        type=int,
        default=int(os.getenv("FUTUR_MONGO_WIDE_BATCH_SIZE", "500")),
        help="MongoDB batch size cap when documents have thousands of feature columns",
    )
    p.add_argument(
        "--chunk-rows",
        type=int,
        default=int(os.getenv("FUTUR_MONGO_CHUNK_ROWS", "10000")),
        help="Maximum core rows enriched at once per crypto/timeframe; 0 disables chunking",
    )
    p.add_argument(
        "--context-rows",
        type=int,
        default=None,
        help="Past context rows prepended to each chunk for rolling indicators; default=max(1000, 5*max horizon)",
    )
    p.add_argument("--workers", type=int, default=1, help="Parallel worker threads; 1 gives one visible progress bar per crypto")
    p.add_argument(
        "--allow-parallel-enrichment",
        action="store_true",
        help="Allow multiple full feature matrices in RAM at once; unsafe on machines with limited memory",
    )
    p.add_argument(
        "--collection-mode",
        choices=["both", "combined", "per-symbol"],
        default="per-symbol",
        help="Write a combined collection, dedicated per-crypto collections, or both",
    )
    p.add_argument("--exclude-intervals", nargs="*", default=None, help="Intervals to skip, e.g. 1m")
    p.add_argument("--strict-integrity", action="store_true", help="Abort if selected timeframes are missing and not derivable")
    p.add_argument("--limit-per-group", type=int, default=None)
    p.add_argument("--drop-target", action="store_true", help="Drop the target collection before writing")
    p.add_argument("--skip-data-dir", action="store_true")
    p.add_argument("--skip-mongo-source", action="store_true")
    p.add_argument("--no-labels", action="store_true")
    p.add_argument("--no-sequence-features", action="store_true")
    p.add_argument("--no-mtf", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    horizons = _parse_int_list(args.horizons)
    timeframes = [x.strip() for x in str(args.timeframes).split(",") if x.strip()]
    symbol_filter = _build_symbol_filter(args.symbols)
    interval_filter = {_canonical_interval(x) for x in args.intervals} if args.intervals else None
    exclude_intervals = {_canonical_interval(x) for x in args.exclude_intervals} if args.exclude_intervals else set()
    workers = max(1, int(args.workers or 1))

    compute_kwargs: Dict[str, Any] = dict(
        horizons=horizons,
        timeframes=timeframes,
        include_labels=not args.no_labels,
        include_sequence_features=not args.no_sequence_features,
        include_multi_timeframe=not args.no_mtf,
        limit=args.limit_per_group,
        batch_size=args.batch_size,
        wide_batch_size=args.wide_batch_size,
        chunk_rows=args.chunk_rows,
        context_rows=args.context_rows,
        dry_run=args.dry_run,
    )

    client = MongoClient(
        args.mongo_uri,
        serverSelectionTimeoutMS=5000,
        maxPoolSize=workers + 4,
    )
    try:
        client.admin.command("ping")
        db = client[args.mongo_db]
        source = db[args.source_collection]

        if args.drop_target:
            if args.source_collection == args.target_collection:
                raise RuntimeError("--drop-target refused: source and target collections are identical")
            if not args.dry_run:
                drop_target_collections(db, args.target_collection, args.collection_mode)
            LOG.info(
                "dropped target collection set: %s.%s mode=%s",
                args.mongo_db,
                args.target_collection,
                args.collection_mode,
            )

        totals: Dict[str, int] = {
            "groups": 0, "rows_read": 0, "rows_written": 0,
            "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 0,
        }
        t0 = time.monotonic()

        group_specs: List[Dict[str, Any]] = []
        if not args.skip_data_dir:
            for paths, symbol, interval in iter_local_groups(Path(args.data_dir), symbol_filter, interval_filter, exclude_intervals):
                group_specs.append({
                    "kind": "local",
                    "paths": paths,
                    "symbol": symbol,
                    "interval": interval,
                    "count": len(paths),
                    "source_origin": "data:%s" % _display_path(paths[0]),
                })

        if not args.skip_mongo_source:
            for symbol, interval, count in iter_mongo_groups(source, symbol_filter, interval_filter, exclude_intervals):
                group_specs.append({
                    "kind": "mongo",
                    "symbol": normalize_storage_symbol(symbol),
                    "raw_symbol": symbol,
                    "interval": interval,
                    "count": count,
                    "source_origin": "mongo:%s.%s" % (args.mongo_db, args.source_collection),
                })

        integrity = audit_timeframe_integrity(group_specs, timeframes)
        print_integrity_report(integrity, timeframes)
        if args.strict_integrity and integrity["missing"]:
            LOG.error("strict integrity failed: missing selected timeframes")
            return 2

        if args.collection_mode in {"combined", "both"} and not args.dry_run:
            ensure_indexes(db[args.target_collection])

        if workers > 1 and not args.allow_parallel_enrichment and not args.limit_per_group and not args.dry_run:
            if not args.chunk_rows:
                LOG.warning(
                    "--workers=%d requested without chunking; forcing workers=1 to avoid Linux OOM 'Killed'. "
                    "Use --allow-parallel-enrichment only on a high-RAM machine.",
                    workers,
                )
                workers = 1
            elif workers > 2:
                LOG.warning(
                    "--workers=%d requested with wide chunked features; capping workers to 2. "
                    "Use --allow-parallel-enrichment to keep the requested worker count.",
                    workers,
                )
                workers = 2

        if workers == 1:
            total = len(group_specs)
            LOG.info("processing %d groups sequentially", total)
            for idx, spec in enumerate(group_specs, start=1):
                stats = process_group_spec(db, source, args.target_collection, args.collection_mode, spec, compute_kwargs)
                _accumulate(totals, stats)
                elapsed = time.monotonic() - t0
                eta = (elapsed / idx) * (total - idx) if idx else 0
                LOG.info(
                    "[%d/%d] %s %s | rows=%d collection_writes=%d elapsed=%.1fs ETA=%.0fs",
                    idx, total, spec["symbol"], spec["interval"],
                    totals["rows_written"], totals["collection_writes"], elapsed, eta,
                )
        else:
            futures: Dict[Any, Tuple[str, str]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for spec in group_specs:
                    f = pool.submit(process_group_spec, db, source, args.target_collection, args.collection_mode, spec, compute_kwargs)
                    futures[f] = (spec["symbol"], spec["interval"])
                total = len(futures)
                LOG.info("submitted %d groups to %d workers", total, workers)
                pbar = _make_progress(total=total, unit="group", desc="all crypto/timeframes")
                completed = 0
                for future in as_completed(futures):
                    sym, ivl = futures[future]
                    try:
                        stats = future.result()
                        _accumulate(totals, stats)
                    except Exception as exc:
                        LOG.error("FAILED %s %s: %s", sym, ivl, exc)
                        totals["skipped"] += 1
                    completed += 1
                    if pbar:
                        pbar.set_postfix(rows=totals["rows_written"], writes=totals["collection_writes"])
                        pbar.update(1)
                if pbar:
                    pbar.close()

        metadata = {
            "target_collection": args.target_collection,
            "collection_mode": args.collection_mode,
            "per_symbol_collection_prefix": "%s_" % args.target_collection,
            "source_collection": args.source_collection,
            "data_dir": str(Path(args.data_dir).resolve()),
            "horizons": horizons,
            "timeframes": timeframes,
            "include_labels": not args.no_labels,
            "include_sequence_features": not args.no_sequence_features,
            "include_multi_timeframe": not args.no_mtf,
            "dry_run": args.dry_run,
            "totals": totals,
            "integrity": integrity,
            "created_at": datetime.now(timezone.utc),
        }
        if not args.dry_run:
            db["enriched_dataset_metadata"].insert_one(metadata)
        LOG.info("completed in %.1fs: %s", time.monotonic() - t0, totals)
        return 0
    finally:
        client.close()


def ensure_indexes(collection: Any) -> None:
    collection.create_index(
        [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
        unique=True,
        name="uniq_symbol_interval_timestamp",
    )
    collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
    collection.create_index([("symbol", ASCENDING), ("interval", ASCENDING)], name="symbol_interval")
    collection.create_index([("feature_version", ASCENDING)], name="feature_version")
    collection.create_index([("source_origin", ASCENDING)], name="source_origin")


def drop_target_collections(db: Any, target_collection: str, mode: str) -> None:
    names = set()
    if mode in {"combined", "both", "per-symbol"}:
        names.add(target_collection)
    if mode in {"per-symbol", "both"}:
        prefix = "%s_" % target_collection
        names.update(name for name in db.list_collection_names() if name.startswith(prefix))
    for name in sorted(names):
        db.drop_collection(name)


def dedicated_collection_name(target_collection: str, symbol: str) -> str:
    suffix = symbol.lower().replace("/", "").replace("-", "").replace("_", "")
    return "%s_%s" % (target_collection, suffix)


def target_collections_for_symbol(
    db: Any,
    target_collection: str,
    mode: str,
    symbol: str,
    *,
    dry_run: bool,
) -> List[Any]:
    names: List[str] = []
    if mode in {"combined", "both"}:
        names.append(target_collection)
    if mode in {"per-symbol", "both"}:
        names.append(dedicated_collection_name(target_collection, symbol))
    collections = [db[name] for name in dict.fromkeys(names)]
    if not dry_run:
        for collection in collections:
            ensure_indexes(collection)
    return collections


def process_group_spec(
    db: Any,
    source_collection: Any,
    target_collection: str,
    collection_mode: str,
    spec: Dict[str, Any],
    compute_kwargs: Dict[str, Any],
) -> Dict[str, int]:
    collections = target_collections_for_symbol(
        db,
        target_collection,
        collection_mode,
        spec["symbol"],
        dry_run=bool(compute_kwargs.get("dry_run")),
    )
    if spec["kind"] == "local":
        return load_enrich_and_write(
            collections,
            spec["paths"],
            symbol=spec["symbol"],
            interval=spec["interval"],
            source_origin=spec["source_origin"],
            **compute_kwargs,
        )

    raw_symbol = spec.get("raw_symbol", spec["symbol"])
    interval = spec["interval"]
    LOG.info("fetching mongo group %s %s (%d docs)", raw_symbol, interval, int(spec.get("count", 0)))
    cursor = (
        source_collection
        .find({"symbol": raw_symbol, "interval": interval})
        .sort("timestamp", ASCENDING)
        .batch_size(10_000)
    )
    if compute_kwargs.get("limit"):
        cursor = cursor.limit(int(compute_kwargs["limit"]))
    docs = list(cursor)
    if not docs:
        return {"groups": 0, "rows_read": 0, "rows_written": 0, "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 1}
    frame = pd.DataFrame(docs).drop(columns=["_id"], errors="ignore")
    return enrich_and_write(
        collections,
        frame,
        symbol=spec["symbol"],
        interval=interval or infer_interval_from_frame(frame),
        source_origin=spec["source_origin"],
        **compute_kwargs,
    )


def audit_timeframe_integrity(group_specs: Sequence[Dict[str, Any]], selected_timeframes: Sequence[str]) -> Dict[str, Any]:
    by_symbol: Dict[str, Set[str]] = {}
    rows_by_group: Dict[str, Dict[str, int]] = {}
    for spec in group_specs:
        symbol = str(spec["symbol"])
        interval = str(spec["interval"]).lower()
        by_symbol.setdefault(symbol, set()).add(interval)
        rows_by_group.setdefault(symbol, {})[interval] = int(spec.get("count", 0) or len(spec.get("paths", [])))

    selected = [_canonical_interval(tf) for tf in selected_timeframes]
    missing: Dict[str, List[str]] = {}
    derivable: Dict[str, List[str]] = {}
    ok: Dict[str, List[str]] = {}
    for symbol, intervals in sorted(by_symbol.items()):
        for tf in selected:
            if tf in intervals:
                ok.setdefault(symbol, []).append(tf)
            elif can_derive_timeframe(tf, intervals):
                derivable.setdefault(symbol, []).append(tf)
            else:
                missing.setdefault(symbol, []).append(tf)
    return {
        "symbols": sorted(by_symbol),
        "selected_timeframes": selected,
        "available": {sym: sorted(vals) for sym, vals in by_symbol.items()},
        "ok": ok,
        "derivable": derivable,
        "missing": missing,
        "rows_by_group": rows_by_group,
    }


def print_integrity_report(integrity: Dict[str, Any], selected_timeframes: Sequence[str]) -> None:
    print("\n=== DATA INTEGRITY / TIMEFRAME COVERAGE ===", flush=True)
    print("Selected timeframes:", ", ".join(_canonical_interval(x) for x in selected_timeframes), flush=True)
    if not integrity["symbols"]:
        print("NO DATASET GROUP FOUND", flush=True)
        return
    for symbol in integrity["symbols"]:
        counts = integrity.get("rows_by_group", {}).get(symbol, {})
        available = ", ".join(
            "%s:%s" % (tf, counts.get(tf, "?"))
            for tf in integrity["available"].get(symbol, [])
        ) or "-"
        derived = ", ".join(integrity["derivable"].get(symbol, [])) or "-"
        missing = ", ".join(integrity["missing"].get(symbol, [])) or "-"
        status = "OK" if missing == "-" else "MISSING"
        print(f"{status:7} {symbol:12} sources=[{available}] derived=[{derived}] missing=[{missing}]", flush=True)
    print("===========================================\n", flush=True)


def _canonical_interval(value: str) -> str:
    raw = str(value).strip().lower()
    return {
        "daily": "1d",
        "day": "1d",
        "weekly": "1w",
        "week": "1w",
        "60m": "1h",
        "1min": "1m",
    }.get(raw, raw)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def can_derive_timeframe(target: str, available: Set[str]) -> bool:
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
    target_s = seconds.get(_canonical_interval(target))
    if target_s is None:
        return False
    for interval in available:
        source_s = seconds.get(_canonical_interval(interval))
        if source_s and source_s <= target_s and target_s % source_s == 0:
            return True
    return False


def iter_local_groups(
    data_dir: Path,
    symbol_filter: Optional[set],
    interval_filter: Optional[set],
    exclude_intervals: Optional[Set[str]] = None,
) -> Iterator[Tuple[List[Path], str, str]]:
    """Yield (paths, symbol, interval) without loading DataFrames into memory."""
    if not data_dir.exists():
        LOG.warning("data dir not found: %s", data_dir)
        return

    candidates = sorted(
        path for path in data_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".parquet"}
    )
    LOG.info("scanning %d local data files under %s", len(candidates), data_dir)

    grouped: Dict[Tuple[str, str], List[Path]] = {}
    for path in candidates:
        if "_checkpoints" in path.parts:
            continue
        path_symbol = infer_symbol_from_path(path)
        path_interval = infer_interval_from_path(path)
        if path_symbol is None or path_interval is None:
            continue
        symbol = normalize_storage_symbol(path_symbol)
        interval = path_interval.lower()
        if symbol_filter and not symbol_matches(symbol, symbol_filter):
            continue
        if interval_filter and interval not in interval_filter:
            continue
        if exclude_intervals and interval in exclude_intervals:
            continue
        grouped.setdefault((symbol, interval), []).append(path)

    for (symbol, interval), paths in sorted(grouped.items()):
        yield sorted(paths, key=local_path_priority), symbol, interval


def load_local_frame(paths: List[Path]) -> Optional[pd.DataFrame]:
    frames = []
    for path in paths:
        try:
            if path.suffix.lower() == ".parquet":
                frame = pd.read_parquet(path)
            else:
                try:
                    frame = pd.read_csv(path, low_memory=False)
                except Exception:
                    frame = pd.read_csv(path, engine="python", on_bad_lines="skip")
        except Exception as exc:
            LOG.warning("skip unreadable file %s: %s", path, exc)
            continue
        if not has_ohlcv(frame):
            LOG.debug("skip non-OHLCV file: %s", path)
            continue
        frames.append(frame)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True, sort=False)


def load_enrich_and_write(
    collections: List[Any],
    paths: List[Path],
    *,
    symbol: str,
    interval: str,
    source_origin: str,
    **kwargs: Any,
) -> Dict[str, int]:
    frame = load_local_frame(paths)
    if frame is None:
        return {"groups": 0, "rows_read": 0, "rows_written": 0, "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 1}
    LOG.info("local group %s %s: %d files, %d rows", symbol, interval, len(paths), len(frame))
    return enrich_and_write(collections, frame, symbol=symbol, interval=interval, source_origin=source_origin, **kwargs)


def iter_local_frames(
    data_dir: Path,
    symbol_filter: Optional[set],
    interval_filter: Optional[set],
) -> Iterator[Tuple[Path, str, str, pd.DataFrame]]:
    """Legacy eager loader kept for external callers; prefer iter_local_groups."""
    for paths, symbol, interval in iter_local_groups(data_dir, symbol_filter, interval_filter):
        frame = load_local_frame(paths)
        if frame is None:
            continue
        LOG.info("local group %s %s: %d files, %d rows", symbol, interval, len(paths), len(frame))
        yield paths[0], symbol, interval, frame


def iter_mongo_groups(
    source_collection: Any,
    symbol_filter: Optional[set],
    interval_filter: Optional[set],
    exclude_intervals: Optional[Set[str]] = None,
) -> Iterator[Tuple[str, str, int]]:
    pipeline = [
        {"$group": {"_id": {"symbol": "$symbol", "interval": "$interval"}, "count": {"$sum": 1}}},
        {"$sort": {"_id.symbol": 1, "_id.interval": 1}},
    ]
    for item in source_collection.aggregate(pipeline, allowDiskUse=True):
        raw_symbol = item.get("_id", {}).get("symbol")
        interval = (item.get("_id", {}).get("interval") or "1h").lower()
        if not raw_symbol:
            continue
        if symbol_filter and not symbol_matches(str(raw_symbol), symbol_filter):
            continue
        if interval_filter and interval not in interval_filter:
            continue
        if exclude_intervals and interval in exclude_intervals:
            continue
        yield str(raw_symbol), interval, int(item.get("count", 0))


def enrich_and_write(
    collections: List[Any],
    frame: pd.DataFrame,
    *,
    symbol: str,
    interval: str,
    source_origin: str,
    horizons: Sequence[int],
    timeframes: Sequence[str],
    include_labels: bool,
    include_sequence_features: bool,
    include_multi_timeframe: bool,
    batch_size: int,
    wide_batch_size: int,
    chunk_rows: int,
    context_rows: Optional[int],
    limit: Optional[int],
    dry_run: bool,
    write_start: Optional[pd.Timestamp] = None,
    write_end: Optional[pd.Timestamp] = None,
    show_progress: bool = True,
) -> Dict[str, int]:
    if limit:
        ts_col = next((c for c in ["timestamp", "datetime", "open_time"] if c in frame.columns), frame.columns[0])
        frame = frame.sort_values(ts_col).tail(int(limit))
    if frame.empty:
        return {"groups": 0, "rows_read": 0, "rows_written": 0, "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 1}

    if chunk_rows and len(frame) > int(chunk_rows) and write_start is None and write_end is None:
        return enrich_and_write_chunked(
            collections,
            frame,
            symbol=symbol,
            interval=interval,
            source_origin=source_origin,
            horizons=horizons,
            timeframes=timeframes,
            include_labels=include_labels,
            include_sequence_features=include_sequence_features,
            include_multi_timeframe=include_multi_timeframe,
            batch_size=batch_size,
            wide_batch_size=wide_batch_size,
            chunk_rows=int(chunk_rows),
            context_rows=context_rows,
            dry_run=dry_run,
        )

    rows_read = len(frame)
    pbar = _make_progress(total=4, unit="step", desc="%s %s" % (symbol, interval), leave=True) if show_progress else None

    try:
        if pbar:
            pbar.set_postfix_str("features")
        enriched = compute_enriched_ohlcv_features(
            frame,
            symbol=symbol,
            interval=interval,
            horizons=horizons,
            label_horizons=horizons,
            include_labels=include_labels,
            include_sequence_features=include_sequence_features,
            include_multi_timeframe=include_multi_timeframe,
            timeframes=timeframes,
            source_coverage={source_origin: int(rows_read)},
        )
    except Exception as exc:
        LOG.exception("failed to enrich %s %s from %s: %s", symbol, interval, source_origin, exc)
        if pbar:
            pbar.close()
        return {"groups": 0, "rows_read": rows_read, "rows_written": 0, "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 1}
    del frame
    gc.collect()
    if pbar:
        pbar.update(1)

    if write_start is not None or write_end is not None:
        idx = pd.DatetimeIndex(pd.to_datetime(enriched.index, utc=True, errors="coerce"))
        mask = np.ones(len(enriched), dtype=bool)
        if write_start is not None:
            mask &= idx >= pd.Timestamp(write_start)
        if write_end is not None:
            mask &= idx <= pd.Timestamp(write_end)
        enriched = enriched.loc[mask].copy()
        if enriched.empty:
            if pbar:
                pbar.close()
            gc.collect()
            return {"groups": 0, "rows_read": rows_read, "rows_written": 0, "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 1}

    enriched["source_origin"] = source_origin
    enriched["enriched_at"] = datetime.now(timezone.utc)
    enriched["symbol_compact"] = symbol.replace("/", "")
    downcasted = downcast_float_columns(enriched)
    if downcasted:
        LOG.info("downcasted %d float64 feature columns to float32 before Mongo serialization", downcasted)

    LOG.info(
        "writing %s %s [%s]: %d rows × %d cols",
        symbol, interval, source_origin, len(enriched), len(enriched.columns),
    )

    write_batch_size = effective_write_batch_size(batch_size, wide_batch_size, len(enriched.columns))
    if write_batch_size != batch_size:
        LOG.info(
            "wide documents detected (%d cols); Mongo write batch capped from %d to %d rows",
            len(enriched.columns), batch_size, write_batch_size,
        )

    stats: Dict[str, int] = {
        "groups": 1, "rows_read": rows_read, "rows_written": 0,
        "collection_writes": 0, "upserted": 0, "modified": 0, "matched": 0, "skipped": 0,
    }
    if pbar:
        pbar.set_postfix_str("prepare")
        pbar.update(1)
    if pbar:
        pbar.set_postfix_str("mongo")
    if dry_run:
        stats["rows_written"] = int(len(enriched))
        stats["collection_writes"] = int(len(enriched)) * max(1, len(collections))
        if pbar:
            pbar.update(2)
            pbar.close()
        del enriched
        gc.collect()
        return stats

    for records in iter_dataframe_record_batches(enriched, write_batch_size):
        if not records:
            continue
        operations = [
            ReplaceOne(
                {
                    "symbol": record["symbol"],
                    "interval": record["interval"],
                    "timestamp": record["timestamp"],
                },
                record,
                upsert=True,
            )
            for record in records
        ]
        stats["rows_written"] += len(operations)
        for collection in collections:
            result = collection.bulk_write(operations, ordered=False)
            stats["collection_writes"] += len(operations)
            stats["upserted"] += result.upserted_count or 0
            stats["modified"] += result.modified_count or 0
            stats["matched"] += result.matched_count or 0
        del operations
        del records
    if pbar:
        pbar.update(2)
        pbar.close()
    del enriched
    gc.collect()
    return stats


def enrich_and_write_chunked(
    collections: List[Any],
    frame: pd.DataFrame,
    *,
    symbol: str,
    interval: str,
    source_origin: str,
    horizons: Sequence[int],
    timeframes: Sequence[str],
    include_labels: bool,
    include_sequence_features: bool,
    include_multi_timeframe: bool,
    batch_size: int,
    wide_batch_size: int,
    chunk_rows: int,
    context_rows: Optional[int],
    dry_run: bool,
) -> Dict[str, int]:
    ts_col = next((c for c in ["timestamp", "datetime", "open_time"] if c in frame.columns), frame.columns[0])
    frame = frame.sort_values(ts_col).reset_index(drop=True)
    total_rows = len(frame)
    chunk_rows = max(1, int(chunk_rows))
    context_before = int(context_rows) if context_rows is not None else max(1000, max(int(h) for h in horizons) * 5)
    context_after = max(int(h) for h in horizons) if include_labels and horizons else 0
    total_chunks = int(math.ceil(total_rows / chunk_rows))

    LOG.info(
        "chunking %s %s: %d rows into %d chunks (chunk_rows=%d, context_before=%d, context_after=%d)",
        symbol, interval, total_rows, total_chunks, chunk_rows, context_before, context_after,
    )
    pbar = _make_progress(total=total_chunks, unit="chunk", desc="%s %s chunks" % (symbol, interval))
    totals: Dict[str, int] = {
        "groups": 1,
        "rows_read": total_rows,
        "rows_written": 0,
        "collection_writes": 0,
        "upserted": 0,
        "modified": 0,
        "matched": 0,
        "skipped": 0,
    }

    for chunk_index, core_start in enumerate(range(0, total_rows, chunk_rows), start=1):
        core_end = min(total_rows, core_start + chunk_rows)
        extended_start = max(0, core_start - context_before)
        extended_end = min(total_rows, core_end + context_after)
        core = frame.iloc[core_start:core_end]
        core_ts = pd.to_datetime(core[ts_col], utc=True, errors="coerce").dropna()
        if core_ts.empty:
            totals["skipped"] += 1
            if pbar:
                pbar.update(1)
            continue
        write_start = core_ts.iloc[0]
        write_end = core_ts.iloc[-1]
        LOG.info(
            "chunk %d/%d %s %s: core=%d rows context=%d rows",
            chunk_index, total_chunks, symbol, interval, core_end - core_start, extended_end - extended_start,
        )
        stats = enrich_and_write(
            collections,
            frame.iloc[extended_start:extended_end].copy(),
            symbol=symbol,
            interval=interval,
            source_origin=source_origin,
            horizons=horizons,
            timeframes=timeframes,
            include_labels=include_labels,
            include_sequence_features=include_sequence_features,
            include_multi_timeframe=include_multi_timeframe,
            batch_size=batch_size,
            wide_batch_size=wide_batch_size,
            chunk_rows=0,
            context_rows=context_rows,
            limit=None,
            dry_run=dry_run,
            write_start=write_start,
            write_end=write_end,
            show_progress=False,
        )
        for key in ("rows_written", "collection_writes", "upserted", "modified", "matched", "skipped"):
            totals[key] += int(stats.get(key, 0))
        if pbar:
            pbar.set_postfix(rows=totals["rows_written"], writes=totals["collection_writes"])
            pbar.update(1)
        gc.collect()

    if pbar:
        pbar.close()
    return totals


def effective_write_batch_size(requested: int, wide_cap: int, column_count: int) -> int:
    requested = max(1, int(requested or 1))
    wide_cap = max(1, int(wide_cap or 1))
    if column_count >= 3000:
        return min(requested, wide_cap)
    if column_count >= 1500:
        return min(requested, max(wide_cap, 500))
    if column_count >= 750:
        return min(requested, max(wide_cap, 1000))
    return requested


def downcast_float_columns(df: pd.DataFrame) -> int:
    if str(os.getenv("FUTUR_MONGO_KEEP_FLOAT64", "0")).lower() in {"1", "true", "yes"}:
        return 0
    float_cols = list(df.select_dtypes(include=["float64"]).columns)
    for col in float_cols:
        df[col] = df[col].astype("float32", copy=False)
    return len(float_cols)


def iter_dataframe_record_batches(df: pd.DataFrame, batch_size: int) -> Iterator[List[Dict[str, Any]]]:
    batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(df), batch_size):
        batch = dataframe_records(df.iloc[start:start + batch_size])
        if batch:
            yield batch


def dataframe_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    frame = df.reset_index()
    if "index" in frame.columns and "timestamp" not in frame.columns:
        frame = frame.rename(columns={"index": "timestamp"})
    if "timestamp" not in frame.columns and "datetime" in frame.columns:
        frame = frame.rename(columns={"datetime": "timestamp"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame[frame["timestamp"].notna()].copy()
    if frame.empty:
        return []

    # Vectorized: replace ±inf → NaN in all float columns (stays float64)
    float_cols = frame.select_dtypes(include=["float"]).columns
    if len(float_cols):
        frame[float_cols] = frame[float_cols].replace([np.inf, -np.inf], np.nan)

    # Vectorized: convert datetime64 columns → Python datetime objects in-place
    for col in frame.select_dtypes(include=["datetimetz", "datetime64"]).columns:
        s = frame[col]
        if getattr(s.dtype, "tz", None) is not None:
            s = s.dt.tz_convert("UTC").dt.tz_localize(None)
        frame[col] = s.dt.to_pydatetime()

    # Cython to_dict for the actual record construction, then flat scalar cleanup
    return [
        {k: _clean_scalar(v) for k, v in rec.items()}
        for rec in frame.to_dict("records")
    ]


def _clean_scalar(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, bytes, datetime)):
        return v
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, int):
        return v
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, pd.Timestamp):
        if v.tzinfo is not None:
            v = v.tz_convert("UTC").tz_localize(None)
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        return _clean_scalar(v.item())
    if isinstance(v, dict):
        return {str(k): _clean_scalar(vv) for k, vv in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean_scalar(vv) for vv in v]
    return v


def infer_symbol_from_path(path: Path) -> Optional[str]:
    stem = path.stem
    if stem.endswith("_1h_features"):
        return stem.replace("_1h_features", "")
    if stem.endswith("_1h_alpha"):
        return stem.replace("_1h_alpha", "")
    candidates = [stem.split("_")[0]]
    candidates.extend(reversed(path.parts))
    for candidate in candidates:
        token = str(candidate).split(".")[0].split("=")[-1]
        compact = token.upper().replace("_", "").replace("-", "").replace("/", "")
        if _looks_like_symbol_token(compact):
            return compact
    return None


def infer_interval_from_path(path: Path) -> Optional[str]:
    text = "/".join(path.parts).lower()
    stem = path.stem.lower()
    parts = {str(part).lower() for part in path.parts}
    aliases = {
        "1m": {"1m", "1min", "1minute"},
        "5m": {"5m", "5min", "5minute"},
        "15m": {"15m", "15min", "15minute"},
        "1h": {"1h", "60m", "1hour"},
        "4h": {"4h", "240m", "4hour"},
        "1d": {"1d", "daily", "day"},
        "1w": {"1w", "weekly", "week"},
    }
    for interval, names in aliases.items():
        if stem.endswith("_%s_features" % interval) or stem.endswith("_%s_alpha" % interval):
            return interval
        if parts & names:
            return interval
        if ("ohlcv_%s" % interval) in text:
            return interval
    return None


def _looks_like_symbol_token(token: str) -> bool:
    if not token or not token.isalnum() or len(token) < 5:
        return False
    if token in {"SPOT", "FUTURES", "KLINES", "DATA", "RAW"}:
        return False
    return any(token.endswith(q) and len(token) > len(q) for q in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR"))


def infer_interval_from_frame(frame: pd.DataFrame) -> str:
    ts_col = next((c for c in ["timestamp", "datetime", "open_time"] if c in frame.columns), None)
    if ts_col is None:
        return "1h"
    ts = pd.to_datetime(frame[ts_col], utc=True, errors="coerce").dropna().sort_values()
    if len(ts) < 3:
        return "1h"
    seconds = int(ts.diff().dropna().median().total_seconds())
    mapping = [(60, "1m"), (300, "5m"), (900, "15m"), (3600, "1h"), (14400, "4h"), (86400, "1d"), (604800, "1w")]
    return min(mapping, key=lambda item: abs(item[0] - seconds))[1]


def has_ohlcv(frame: pd.DataFrame) -> bool:
    cols = {str(c).lower() for c in frame.columns}
    return {"open", "high", "low", "close", "volume"}.issubset(cols)


def local_path_priority(path: Path) -> int:
    text = "/".join(path.parts).lower()
    name = path.name.lower()
    if "/raw/" in text or "binance_vision" in text:
        return 0
    if "ohlcv" in text:
        return 1
    if "alpha" in name:
        return 2
    if "features" in name:
        return 3
    return 1


def normalize_storage_symbol(symbol: str) -> str:
    compact = str(symbol or "").strip().upper().replace("_", "").replace("-", "").replace("/", "")
    if compact == "BTCUSD":
        compact = "BTCUSDT"
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return "%s/%s" % (compact[: -len(quote)], quote)
    return compact


def _build_symbol_filter(symbols: Optional[Sequence[str]]) -> Optional[set]:
    if not symbols:
        return None
    out: set = set()
    for symbol in symbols:
        out.update(normalize_symbol_variants(symbol))
        out.add(normalize_storage_symbol(symbol))
    return {x.upper() for x in out}


def symbol_matches(symbol: str, symbol_filter: set) -> bool:
    variants = {x.upper() for x in normalize_symbol_variants(symbol)}
    variants.add(normalize_storage_symbol(symbol).upper())
    variants.add(str(symbol).upper())
    return bool(variants & symbol_filter)


def _parse_int_list(value: str) -> List[int]:
    return sorted({int(p.strip()) for p in str(value).split(",") if p.strip()})


def _accumulate(total: Dict[str, int], delta: Dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = int(total.get(key, 0)) + int(value)


if __name__ == "__main__":
    raise SystemExit(main())
