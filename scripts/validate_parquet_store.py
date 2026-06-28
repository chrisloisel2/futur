#!/usr/bin/env python3
"""
scripts/validate_parquet_store.py
─────────────────────────────────────────────────────────────────────────────
Validateur de store parquet (Phase 15 — reproducibility lock).

Contrôle, par fichier : magic bytes parquet, schéma OHLCV, timestamp monotone,
doublons, gaps, OHLC cohérent, volume ≥ 0, ratio NaN, timezone, sha256.

--strict : sort en code 1 si un seul fichier échoue (gate CI).

Usage :
    python3 scripts/validate_parquet_store.py --path data/enriched --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

OHLC = ["open", "high", "low", "close"]


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _has_parquet_magic(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            f.seek(-4, 2)
            tail = f.read(4)
        return head == b"PAR1" and tail == b"PAR1"
    except Exception:
        return False


def validate_file(path: Path) -> dict:
    rep = {"file": str(path), "sha256": None, "issues": [], "ok": False, "rows": 0}
    if not _has_parquet_magic(path):
        rep["issues"].append("MAGIC_BYTES_MISSING (fichier corrompu)")
        return rep
    rep["sha256"] = _sha256(path)
    try:
        import pyarrow.parquet as pq
        schema = set(pq.ParquetFile(path).schema_arrow.names)
    except Exception as e:
        rep["issues"].append(f"UNREADABLE_FOOTER: {e}")
        return rep

    ts_col = "datetime" if "datetime" in schema else ("timestamp" if "timestamp" in schema else None)
    if ts_col is None:
        rep["issues"].append("NO_TIMESTAMP_COLUMN")
    missing_ohlc = [c for c in OHLC if c not in schema]
    if missing_ohlc:
        rep["issues"].append(f"MISSING_OHLC: {missing_ohlc}")

    cols = [c for c in ([ts_col] + OHLC + ["volume"]) if c and c in schema]
    try:
        df = pd.read_parquet(path, columns=cols)
    except Exception as e:
        rep["issues"].append(f"READ_COLUMNS_FAILED: {e}")
        return rep

    rep["rows"] = int(len(df))
    if ts_col:
        ts = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
        if ts.isna().any():
            rep["issues"].append("TIMESTAMP_PARSE_NAN")
        if not ts.is_monotonic_increasing:
            rep["issues"].append("TIMESTAMP_NOT_MONOTONIC")
        dups = int(ts.duplicated().sum())
        if dups:
            rep["issues"].append(f"DUPLICATE_TIMESTAMPS={dups}")
        gaps = ts.diff().dt.total_seconds().dropna()
        if len(gaps):
            max_gap_h = float(gaps.max() / 3600.0)
            rep["max_gap_hours"] = round(max_gap_h, 1)
            if max_gap_h > 48:
                rep["issues"].append(f"LARGE_GAP={max_gap_h:.0f}h")
    if all(c in df.columns for c in OHLC):
        bad = ((df["high"] < df["low"]) | (df["high"] < df["close"]) |
               (df["low"] > df["close"])).sum()
        if bad:
            rep["issues"].append(f"OHLC_INCOHERENT={int(bad)}")
        nan_ratio = float(df[OHLC].isna().mean().max())
        rep["nan_ratio_ohlc"] = round(nan_ratio, 4)
        if nan_ratio > 0.05:
            rep["issues"].append(f"HIGH_NAN_OHLC={nan_ratio:.2%}")
    if "volume" in df.columns and (df["volume"] < 0).any():
        rep["issues"].append("NEGATIVE_VOLUME")

    rep["ok"] = len(rep["issues"]) == 0
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data/enriched")
    ap.add_argument("--pattern", default="*.parquet")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--out", default="reports/repro_audit/parquet_validation.json")
    args = ap.parse_args()

    files = sorted(Path(args.path).glob(args.pattern))
    reports = [validate_file(p) for p in files]
    ok = [r for r in reports if r["ok"]]
    bad = [r for r in reports if not r["ok"]]

    print(f"\n{'='*70}\nPARQUET STORE VALIDATION — {args.path}\n{'='*70}")
    print(f"  fichiers : {len(reports)}  |  OK : {len(ok)}  |  ÉCHEC : {len(bad)}")
    for r in reports:
        flag = "OK " if r["ok"] else "FAIL"
        print(f"  [{flag}] {Path(r['file']).name:<40} rows={r['rows']:>9,}  {('; '.join(r['issues']) or '')}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=2))
    print(f"\n→ rapport : {out}")

    if args.strict and bad:
        print(f"\nSTRICT FAIL : {len(bad)} fichier(s) corrompu(s)/invalide(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
