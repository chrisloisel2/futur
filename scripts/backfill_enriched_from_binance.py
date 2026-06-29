#!/usr/bin/env python3
"""
scripts/backfill_enriched_from_binance.py
─────────────────────────────────────────────────────────────────────────────
Remplit data/enriched/{SYM}_1h_enriched.parquet pour les actifs manquants depuis
les klines 1h Binance Futures (officiel, gratuit) → features enriched canoniques
(compute_enriched_ohlcv_features, MTF+sequence ON, comme le rebuild offline).

Idempotent / RÉSUMABLE : saute un actif déjà présent + valide. Gère le préfixe
"1000" (ex. PEPE → 1000PEPEUSDT). Skip propre si Binance ne liste pas le symbole
(jamais de données inventées). Macro/funding/OI absents pour ces alts (documenté) ;
les features prix (returns/EMA/RSI/vol/momentum) suffisent au long/carry book.

    python3 scripts/backfill_enriched_from_binance.py --start 2021-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.settings import configure_project_imports
configure_project_imports()

from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from ai.level_0.labels import compute_label_columns
from scripts.assemble_enriched_from_dataout import _apply_feature_aliases
from scripts.validate_parquet_store import validate_file
from src.institutional.data.atomic_parquet import atomic_write_parquet

B = "https://fapi.binance.com"
ENRICHED = ROOT / "data" / "enriched"
UNIVERSE_CFG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"


def _get(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None            # symbole non listé sous ce nom
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def _fetch_klines(binance_sym, start_ms):
    rows, cur, now = [], start_ms, int(time.time() * 1000)
    while cur < now:
        data = _get(f"{B}/fapi/v1/klines?symbol={binance_sym}&interval=1h&startTime={cur}&limit=1500")
        if data is None:
            return None
        if not data:
            break
        rows.extend(data)
        last = data[-1][0]
        if last <= cur:
            break
        cur = last + 3_600_000
        time.sleep(0.15)
    return rows


def fetch_ohlcv(symbol, start_ms):
    """Retourne (df_ohlcv, binance_sym_utilisé) ou (None, None) si indisponible."""
    for cand in (symbol, "1000" + symbol):       # fallback préfixe 1000 (PEPE…)
        rows = _fetch_klines(cand, start_ms)
        if rows:
            df = pd.DataFrame(rows, columns=["ot", "open", "high", "low", "close", "volume",
                                             "ct", "qv", "n", "tbb", "tbq", "ig"])
            df["datetime"] = pd.to_datetime(df["ot"].astype("int64"), unit="ms", utc=True)
            for c in ("open", "high", "low", "close", "volume"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df[["datetime", "open", "high", "low", "close", "volume"]].dropna()
            df = df.drop_duplicates("datetime").set_index("datetime").sort_index()
            return df, cand
    return None, None


def build_one(symbol, start_ms, force=False):
    out = ENRICHED / f"{symbol}_1h_enriched.parquet"
    if out.exists() and not force:
        rep = validate_file(out)
        if rep["ok"]:
            return "SKIP_VALID"
    df_in, bsym = fetch_ohlcv(symbol, start_ms)
    if df_in is None or len(df_in) < 2000:
        return "UNAVAILABLE"
    df_in.index.name = "datetime"
    df_enr = compute_enriched_ohlcv_features(
        df_in, interval="1h", include_labels=False,
        include_multi_timeframe=True, include_sequence_features=True)
    df_enr = _apply_feature_aliases(df_enr)
    df_enr.index.name = "datetime"
    df_enr = df_enr.reset_index()
    df_enr["datetime"] = pd.to_datetime(df_enr["datetime"], utc=True)
    try:
        df_enr = compute_label_columns(df_enr)
    except Exception:
        pass
    atomic_write_parquet(df_enr, out)
    return f"OK {len(df_enr)}x{len(df_enr.columns)} (src={bsym})"


def main():
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--symbols", default=None, help="défaut = univers 50 moins déjà-valides")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",")]
    else:
        syms = yaml.safe_load(UNIVERSE_CFG.read_text())["universe"]

    print(f"Backfill enriched (klines Binance) — {len(syms)} symboles, start {args.start}")
    summary = {}
    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        try:
            res = build_one(sym, start_ms, force=args.force)
        except Exception as e:
            res = f"ERROR {repr(e)[:80]}"
        summary[sym] = res
        print(f"  [{i}/{len(syms)}] {sym:<12} {res}  ({time.time()-t0:.0f}s)", flush=True)
    (ROOT / "reports" / "parallel_50").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "parallel_50" / "enriched_backfill_status.json").write_text(
        json.dumps(summary, indent=2))
    ok = sum(1 for v in summary.values() if v.startswith("OK") or v == "SKIP_VALID")
    print(f"\nDONE : {ok}/{len(syms)} enriched disponibles")


if __name__ == "__main__":
    main()
