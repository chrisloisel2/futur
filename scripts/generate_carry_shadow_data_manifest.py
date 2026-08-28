#!/usr/bin/env python3
"""
scripts/generate_carry_shadow_data_manifest.py
─────────────────────────────────────────────────────────────────────────────
Phase 4D commit 7: generates the data provenance manifest for the real
market data used to shadow CarryBasisAdapter. Read-only inspection of
already-fetched files -- does not fetch, transform, or touch any feature/
strategy code itself.

For each of BTCUSDT/ETHUSDT records: source endpoint(s), venue/market,
symbol, requested vs. obtained period, retrieval date, row count, first/
last timestamp, gaps, duplicates, and SHA-256 of every raw and enriched
file involved. Writes data/manifests/carry_shadow_data_manifest.json
(committed -- small, no market data values, just provenance).

Two raw-data sources per symbol:
  1. data/raw/binance_funding_rate/{SYM}_funding_rate.parquet -- collected
     by this Phase's own scripts/collect_funding_rate_binance.py, hashed
     directly (SHA-256 of the file as fetched, verbatim).
  2. The klines scripts/backfill_enriched_from_binance.py fetched live via
     its own network calls to build the enriched parquet -- NOT persisted
     as a separate raw file by that (unmodified, canonical) script, so
     there is no raw-klines file of THAT exact fetch to hash. As an
     independent real-data cross-check, this repo already has raw BTCUSDT/
     ETHUSDT USD-M klines on disk (data/raw/binance_um_klines/, ingested
     separately, earlier, by a different pipeline) covering materially the
     same window -- its SHA-256 and a row-count/date-range/spot-price
     cross-check against the enriched file's own `close` column are
     recorded below as a genuine, documented substitute, not a claim of
     byte-identical provenance to backfill_enriched_from_binance.py's own
     live fetch.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ENRICHED_DIR = ROOT / "data" / "enriched"
FUNDING_RAW_DIR = ROOT / "data" / "raw" / "binance_funding_rate"
KLINES_RAW_DIR = ROOT / "data" / "raw" / "binance_um_klines" / "futures_um"
MANIFEST_DIR = ROOT / "data" / "manifests"

SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_files_concat(paths: list[Path]) -> str:
    """Hashes the SORTED list of individual file hashes (not the raw bytes
    concatenated) -- order-independent of how the files happen to be laid
    out on disk, deterministic given the same set of files."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(_sha256_file(p).encode("ascii"))
    return h.hexdigest()


def _gaps_and_dupes(ts: pd.Series, expected_freq: pd.Timedelta,
                    tolerance: pd.Timedelta = pd.Timedelta(0)) -> dict:
    """A "gap" is any consecutive spacing that differs from `expected_freq`
    by more than `tolerance`. Real Binance funding timestamps carry a few
    ms of jitter around the true 8h boundary (see
    scripts/merge_funding_into_enriched.py's own docstring) -- without a
    tolerance that jitter would show up as thousands of false-positive
    "gaps" here, hiding any REAL missing funding period among the noise."""
    ts = ts.sort_values().reset_index(drop=True)
    dupes = int(ts.duplicated().sum())
    ts_unique = ts.drop_duplicates()
    diffs = ts_unique.diff().dropna()
    gap_mask = (diffs - expected_freq).abs() > tolerance
    gaps = []
    for idx in diffs[gap_mask].index:
        gaps.append({
            "after": str(ts_unique.iloc[ts_unique.index.get_loc(idx) - 1]),
            "before": str(ts_unique.loc[idx]),
            "gap": str(diffs.loc[idx]),
        })
    return {"duplicate_timestamps": dupes, "gaps": gaps, "n_gaps": len(gaps)}


def _funding_provenance(symbol: str) -> dict:
    path = FUNDING_RAW_DIR / f"{symbol}_funding_rate.parquet"
    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    gd = _gaps_and_dupes(df["datetime"], pd.Timedelta(hours=8), tolerance=pd.Timedelta(minutes=5))
    return {
        "source": "Binance USD-M futures funding rate history",
        "endpoint": "https://fapi.binance.com/fapi/v1/fundingRate",
        "venue": "binance_usdm", "market": "USD-M perpetual futures",
        "symbol": symbol,
        "collector": "scripts/collect_funding_rate_binance.py",
        "n_rows": len(df),
        "first_timestamp": str(df["datetime"].min()),
        "last_timestamp": str(df["datetime"].max()),
        "expected_frequency": "8h (3 funding events/day)",
        **gd,
        "sha256": _sha256_file(path),
        "file": str(path.relative_to(ROOT)),
    }


def _klines_cross_check(symbol: str) -> dict:
    d = KLINES_RAW_DIR / symbol / "1h"
    files = sorted(d.rglob("*.parquet"))
    if not files:
        return {"available": False}
    dfs = [pd.read_parquet(f, columns=["timestamp", "close"]) for f in files]
    df = pd.concat(dfs).drop_duplicates("timestamp").sort_values("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    gd = _gaps_and_dupes(df["timestamp"], pd.Timedelta(hours=1))
    return {
        "available": True,
        "source": "Binance USD-M futures 1h klines (pre-existing local ingest, "
                  "independent of backfill_enriched_from_binance.py's own live fetch)",
        "n_files": len(files), "n_rows": len(df),
        "first_timestamp": str(df["timestamp"].min()),
        "last_timestamp": str(df["timestamp"].max()),
        "expected_frequency": "1h", **gd,
        "sha256_of_sorted_file_hashes": _sha256_files_concat(files),
        "files": [str(f.relative_to(ROOT)) for f in files],
    }


def _raw_klines_df(symbol: str) -> pd.DataFrame | None:
    files = sorted((KLINES_RAW_DIR / symbol / "1h").rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f, columns=["timestamp", "close"]) for f in files]
    df = pd.concat(dfs).drop_duplicates("timestamp").sort_values("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.rename(columns={"timestamp": "datetime", "close": "close_klines_raw"})


def _enriched_provenance(symbol: str, requested_start: str) -> dict:
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    df = pd.read_parquet(path, columns=["datetime", "close", "funding_rate"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    gd = _gaps_and_dupes(df["datetime"], pd.Timedelta(hours=1))
    n_funding_set = int(df["funding_rate"].notna().sum())

    klines_check = _klines_cross_check(symbol)
    cross_check = {"performed": False}
    raw_klines = _raw_klines_df(symbol)
    if raw_klines is not None:
        merged = df[["datetime", "close"]].merge(raw_klines, on="datetime", how="inner")
        diff = (merged["close"] - merged["close_klines_raw"]).abs()
        cross_check = {
            "performed": True,
            "n_overlapping_rows": len(merged),
            "max_abs_close_diff": float(diff.max()) if len(diff) else None,
            "mean_abs_close_diff": float(diff.mean()) if len(diff) else None,
        }

    return {
        "source": "Binance USD-M futures 1h klines, canonical enrichment pipeline",
        "endpoint": "https://fapi.binance.com/fapi/v1/klines (fetched live by "
                   "scripts/backfill_enriched_from_binance.py, not persisted separately)",
        "venue": "binance_usdm", "market": "USD-M perpetual futures", "symbol": symbol,
        "pipeline": "scripts/backfill_enriched_from_binance.py (unmodified) + "
                   "scripts/merge_funding_into_enriched.py (funding_rate merge, "
                   "Phase 4D addition)",
        "period_requested_start": requested_start,
        "n_rows": len(df),
        "n_columns": int(pd.read_parquet(path).shape[1]),
        "first_timestamp": str(df["datetime"].min()),
        "last_timestamp": str(df["datetime"].max()),
        "expected_frequency": "1h", **gd,
        "n_funding_rows_set": n_funding_set,
        "sha256": _sha256_file(path),
        "file": str(path.relative_to(ROOT)),
        "raw_klines_cross_check": klines_check,
        "close_price_cross_check_vs_raw_klines": cross_check,
    }


def build_manifest(requested_start: str) -> dict:
    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/generate_carry_shadow_data_manifest.py",
        "symbols": {},
        "causality_verification": {
            "funding_merge": (
                "scripts/merge_funding_into_enriched.py joins funding_rate by exact "
                "(floored-to-hour) timestamp only -- no forward-fill, no backward-fill, "
                "no interpolation. A funding rate is visible starting exactly at its own "
                "real event timestamp, never before."),
            "ohlcv_features": (
                "compute_enriched_ohlcv_features (data_pipeline.enriched_ohlcv_features) "
                "is pre-existing production code, not introduced by this phase -- already "
                "relied on elsewhere in this repo for causal OOS inference (see "
                "src/institutional/engines/legacy_bridge.py's own documented anti-leakage "
                "boundary). Not re-audited line-by-line here; this phase's own additions "
                "(the funding merge above) are independently verified causal by "
                "construction."),
            "carry_backtest_consumption": (
                "MultiLegBacktester.px(a, t)/fr(a, t) (src/institutional/backtest/"
                "multileg_backtester.py) select the most recent bar at or before `t` via "
                "searchsorted(side='right')-1 -- never a future bar. This shadow's own "
                "MARK sourcing (mapping.py's _price_asof) uses the identical lookup, "
                "verified by test "
                "(test_mark_uses_the_most_recent_bar_at_or_before_the_cycle_timestamp_"
                "no_lookahead)."),
        },
    }
    for sym in SYMBOLS:
        manifest["symbols"][sym] = {
            "funding_raw": _funding_provenance(sym),
            "enriched": _enriched_provenance(sym, requested_start),
        }
    return manifest


def main() -> None:
    requested_start = sys.argv[1] if len(sys.argv) > 1 else "2024-07-01"
    manifest = build_manifest(requested_start)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    out = MANIFEST_DIR / "carry_shadow_data_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")
    for sym, entry in manifest["symbols"].items():
        e = entry["enriched"]
        print(f"  {sym}: {e['n_rows']} rows, {e['first_timestamp']} -> {e['last_timestamp']}, "
             f"gaps={e['n_gaps']}, dupes={e['duplicate_timestamps']}, sha256={e['sha256'][:16]}...")


if __name__ == "__main__":
    main()
