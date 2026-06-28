#!/usr/bin/env python3
"""
scripts/inspect_enriched_schema.py
─────────────────────────────────────────────────────────────────────────────
Dump du schéma d'un (ou plusieurs) enriched parquet : colonnes, dtypes, plage
temporelle, fréquence, ratio NaN. Sert de référence (Phase 23.5).

Usage :
    python3 scripts/inspect_enriched_schema.py \
        --files data/enriched/ADAUSDT_1h_enriched.parquet \
        --out reports/enriched_reference_schema.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def inspect(path: str) -> dict:
    df = pd.read_parquet(path)
    ts_col = "datetime" if "datetime" in df.columns else ("timestamp" if "timestamp" in df.columns else None)
    rep = {"file": path, "n_cols": int(df.shape[1]), "n_rows": int(df.shape[0]),
           "columns": list(df.columns)}
    if ts_col:
        ts = pd.to_datetime(df[ts_col], utc=True)
        rep["start"] = str(ts.min()); rep["end"] = str(ts.max())
        gaps = ts.sort_values().diff().dt.total_seconds().dropna()
        rep["median_gap_h"] = float(gaps.median() / 3600) if len(gaps) else None
        rep["max_gap_h"] = float(gaps.max() / 3600) if len(gaps) else None
    nan = df.isna().mean()
    rep["nan_ratio_max"] = float(nan.max())
    rep["cols_high_nan"] = sorted(nan[nan > 0.5].index.tolist())[:50]
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--out", default="reports/enriched_reference_schema.json")
    args = ap.parse_args()
    reps = {Path(f).stem: inspect(f) for f in args.files}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(reps, indent=2))
    for k, r in reps.items():
        print(f"{k}: {r['n_cols']} cols × {r['n_rows']:,} rows  "
              f"{r.get('start','?')[:10]}→{r.get('end','?')[:10]}  nan_max={r['nan_ratio_max']:.2%}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
