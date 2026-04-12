#!/usr/bin/env python3
"""
S3 PARQUET COLUMN NORMALIZER (BTC ONLY, IN-PLACE)

Scope:
- ONLY symbol=BTCUSDT under your existing partitioned layout
- All years / quotes / intervals under that symbol (whatever exists)
- Rewrite parquet files in place (same S3 keys)
- Do NOT change folder structure

Requirements:
    pip install pyarrow s3fs boto3

Usage:
    python normalize_s3_parquets_btc.py \
      --bucket qbia \
      --prefix bourse/processed/market \
      --symbol BTCUSDT \
      --dry-run 1 \
      --workers 8

Then:
    --dry-run 0 to actually rewrite.
"""

import re
import sys
import json
import time
import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import pyarrow.parquet as pq
import s3fs


# ======================================================================================
# COLUMN NORMALIZATION
# ======================================================================================

EXPLICIT_RENAMES = {
    # Time columns
    "Open_Time": "open_time",
    "openTime": "open_time",
    "open_time": "open_time",
    "Close_Time": "close_time",
    "closeTime": "close_time",
    "close_time": "close_time",

    # OHLCV
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Quote_Volume": "quote_volume",
    "Taker_Buy_Base": "taker_buy_base",
    "Taker_Buy_Quote": "taker_buy_quote",
    "Trades": "trades",

    # Common metadata
    "Datetime": "datetime",
    "DateTime": "datetime",
    "Timestamp": "timestamp",
    "Event_Time": "event_time",
    "Symbol": "symbol",
    "Quote": "quote",
    "Interval": "interval",
    "Year": "year",
}

EXPLICIT_CANONICAL = set(EXPLICIT_RENAMES.values())


def _to_snake_case(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace(" ", "_").replace("-", "_")
    s = re.sub(r"__+", "_", s)
    return s.lower()


def normalize_column_name(col: str) -> str:
    if col in EXPLICIT_RENAMES:
        return EXPLICIT_RENAMES[col]
    if col in EXPLICIT_CANONICAL:
        return col

    out = _to_snake_case(col)
    out = re.sub(r"__+", "_", out).strip("_")

    if out in ("open_time_ms", "open_time_millis"):
        out = "open_time"
    if out in ("close_time_ms", "close_time_millis"):
        out = "close_time"

    return out


def build_rename_map(columns: List[str]) -> Dict[str, str]:
    mapping = {}
    used = {}
    for c in columns:
        new_c = normalize_column_name(c)

        # avoid collisions
        if new_c in used and used[new_c] != c:
            i = 1
            candidate = f"{new_c}__dup{i}"
            while candidate in used:
                i += 1
                candidate = f"{new_c}__dup{i}"
            new_c = candidate

        mapping[c] = new_c
        used[new_c] = c

    return mapping


# ======================================================================================
# S3 HELPERS
# ======================================================================================

@dataclass
class JobResult:
    key: str
    changed: bool
    ok: bool
    reason: Optional[str] = None
    old_cols: Optional[List[str]] = None
    new_cols: Optional[List[str]] = None


def list_btc_parquet_keys(bucket: str, prefix: str, symbol: str) -> List[str]:
    """
    Lists every parquet under:
        s3://bucket/prefix/**/symbol=<symbol>/**.parquet
    Works even if you have multiple partition layouts.
    """
    s3 = boto3.client("s3")
    keys: List[str] = []

    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token

        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            k = obj["Key"]
            if not k.endswith(".parquet"):
                continue
            # strict BTC filter
            if f"symbol={symbol}/" not in k:
                continue
            keys.append(k)

        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break

    return keys


def copy_and_delete_temp(bucket: str, temp_key: str, final_key: str):
    s3 = boto3.client("s3")
    s3.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": temp_key},
        Key=final_key,
    )
    s3.delete_object(Bucket=bucket, Key=temp_key)


def get_parquet_compression(path: str, fs: s3fs.S3FileSystem) -> str:
    try:
        with fs.open(path, "rb") as f:
            pf = pq.ParquetFile(f)
            md = pf.metadata
            if md is None:
                return "ZSTD"
            rg = md.row_group(0)
            col = rg.column(0)
            comp = col.compression
            if isinstance(comp, str):
                return comp.upper()
            return str(comp).upper()
    except Exception:
        return "ZSTD"


# ======================================================================================
# NORMALIZE ONE FILE
# ======================================================================================

def normalize_one_parquet(bucket: str, key: str, fs: s3fs.S3FileSystem, dry_run: bool) -> JobResult:
    s3_path = f"s3://{bucket}/{key}"

    try:
        with fs.open(s3_path, "rb") as f:
            table = pq.read_table(f)

        old_cols = table.column_names
        rename_map = build_rename_map(old_cols)
        new_cols = [rename_map[c] for c in old_cols]

        changed = old_cols != new_cols
        if not changed:
            return JobResult(key=key, changed=False, ok=True, old_cols=old_cols, new_cols=new_cols)

        table2 = table.rename_columns(new_cols)

        temp_key = key + ".__tmp_normalize_columns__"
        temp_path = f"s3://{bucket}/{temp_key}"

        if dry_run:
            return JobResult(key=key, changed=True, ok=True, reason="dry-run", old_cols=old_cols, new_cols=new_cols)

        comp = get_parquet_compression(s3_path, fs)
        if comp not in {"SNAPPY", "GZIP", "BROTLI", "ZSTD", "LZ4_RAW", "LZ4", "NONE"}:
            comp = "ZSTD"

        with fs.open(temp_path, "wb") as out_f:
            pq.write_table(
                table2,
                out_f,
                compression=comp if comp != "NONE" else None,
                use_dictionary=True,
                write_statistics=True,
            )

        copy_and_delete_temp(bucket=bucket, temp_key=temp_key, final_key=key)

        return JobResult(key=key, changed=True, ok=True, old_cols=old_cols, new_cols=new_cols)

    except Exception as e:
        return JobResult(key=key, changed=False, ok=False, reason=f"{type(e).__name__}: {e}")


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    parser = argparse.ArgumentParser(description="Normalize BTC parquet column names across S3 (in place).")
    parser.add_argument("--bucket", required=True, type=str)
    parser.add_argument("--prefix", required=True, type=str)
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--dry-run", type=int, default=1)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--report", type=str, default="normalize_btc_report.json")
    args = parser.parse_args()

    dry_run = bool(args.dry_run)

    print("=" * 100)
    print("S3 PARQUET COLUMN NORMALIZER (BTC ONLY)")
    print("=" * 100)
    print(f"Bucket:   {args.bucket}")
    print(f"Prefix:   {args.prefix}")
    print(f"Symbol:   {args.symbol}")
    print(f"Dry-run:  {dry_run}")
    print(f"Workers:  {args.workers}")
    print(f"MaxFiles: {args.max_files if args.max_files else 'ALL'}")
    print("=" * 100)

    keys = list_btc_parquet_keys(args.bucket, args.prefix, args.symbol)
    keys.sort()

    if args.max_files and args.max_files > 0:
        keys = keys[: args.max_files]

    print(f"Found {len(keys)} parquet files for symbol={args.symbol}.")

    if not keys:
        print("Nothing to do.")
        sys.exit(0)

    fs = s3fs.S3FileSystem()

    t0 = time.time()
    results: List[JobResult] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(normalize_one_parquet, args.bucket, k, fs, dry_run) for k in keys]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)

            if not r.ok:
                print(f"[{i}/{len(keys)}] FAIL  {r.key}  -> {r.reason}")
            elif r.changed:
                print(f"[{i}/{len(keys)}] OK*   {r.key}  (renamed columns)")
            else:
                print(f"[{i}/{len(keys)}] OK    {r.key}  (no change)")

    dt = time.time() - t0

    ok = sum(1 for r in results if r.ok)
    fail = sum(1 for r in results if not r.ok)
    changed = sum(1 for r in results if r.ok and r.changed)
    unchanged = sum(1 for r in results if r.ok and not r.changed)

    report = {
        "bucket": args.bucket,
        "prefix": args.prefix,
        "symbol": args.symbol,
        "dry_run": dry_run,
        "workers": args.workers,
        "n_files": len(keys),
        "ok": ok,
        "fail": fail,
        "changed": changed,
        "unchanged": unchanged,
        "duration_sec": dt,
        "results": [
            {
                "key": r.key,
                "ok": r.ok,
                "changed": r.changed,
                "reason": r.reason,
                "old_cols": r.old_cols,
                "new_cols": r.new_cols,
            }
            for r in results
        ],
    }

    with open(args.report, "w") as f:
        json.dump(report, f, indent=2)

    print("=" * 100)
    print("DONE")
    print("=" * 100)
    print(f"OK:        {ok}")
    print(f"FAIL:      {fail}")
    print(f"CHANGED:   {changed}")
    print(f"UNCHANGED: {unchanged}")
    print(f"Time:      {dt:.1f}s")
    print(f"Report:    {args.report}")
    print("=" * 100)

    if fail > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
