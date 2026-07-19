#!/usr/bin/env python3
"""
scripts/backfill_binance_metrics_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill HISTORIQUE des métriques dérivées Binance Futures (Binance Vision).

Source : https://data.binance.vision/data/futures/um/daily/metrics/{SYM}/
         {SYM}-metrics-{YYYY-MM-DD}.zip  (CSV 5-min : sum_open_interest,
         sum_open_interest_value, toptrader ratios, long_short_ratio,
         taker_long_short_vol_ratio) — dispo 2020-09 → J-2, TOUS les actifs.

Découverte 2026-07-06 : comble le gap déclaré dans SCALE_ASSESSMENT
("OI BTC-only, pas d'OI 2026, alts sans OI/taker") — l'OI 5-min multi-actifs
multi-années EST disponible gratuitement. C'est le carburant du moteur
événementiel LIQ_CASCADE (cascades de deleveraging à 5 min).

Idempotent : manifest par symbole (jours faits / 404). Sortie :
  data/derivatives_backfill/binance_vision_metrics/{SYM}_metrics_5m.parquet

Usage :
  python3 scripts/backfill_binance_metrics_vision.py                    # 12 cœur
  python3 scripts/backfill_binance_metrics_vision.py --symbols BTCUSDT --start 2020-09-01
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_metrics"
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"

CORE_12 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "AVAXUSDT", "LINKUSDT", "BNBUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT"]

NUMERIC_COLS = ["sum_open_interest", "sum_open_interest_value",
                "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
                "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


def _fetch_day(sym: str, d: date):
    url = f"{BASE}/{sym}/{sym}-metrics-{d.isoformat()}.zip"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return d, "404", None
        return d, f"http_{e.code}", None
    except Exception as e:
        return d, f"err_{type(e).__name__}", None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = z.namelist()[0]
            df = pd.read_csv(z.open(name))
        return d, "ok", df
    except Exception as e:
        return d, f"parse_{type(e).__name__}", None


def backfill_symbol(sym: str, start: date, end: date, workers: int = 8) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq = OUT_DIR / f"{sym}_metrics_5m.parquet"
    mf = OUT_DIR / f"{sym}_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {"done": [], "missing": []}
    done = set(manifest["done"]) | set(manifest["missing"])

    days = []
    d = start
    while d <= end:
        if d.isoformat() not in done:
            days.append(d)
        d += timedelta(days=1)

    if not days:
        return {"symbol": sym, "new": 0, "status": "up_to_date"}

    frames, n404, nerr = [], 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_day, sym, d): d for d in days}
        for fut in as_completed(futs):
            d, status, df = fut.result()
            if status == "ok":
                frames.append(df)
                manifest["done"].append(d.isoformat())
            elif status == "404":
                n404 += 1
                manifest["missing"].append(d.isoformat())
            else:
                nerr += 1  # transitoire : sera retenté au prochain run

    if frames:
        new = pd.concat(frames, ignore_index=True)
        # certains dumps mélangent "YYYY-MM-DD HH:MM:SS" et "YYYY-MM-DD" (barre minuit)
        new["create_time"] = pd.to_datetime(new["create_time"], utc=True, format="mixed")
        for c in NUMERIC_COLS:
            if c in new.columns:
                new[c] = pd.to_numeric(new[c], errors="coerce")
        if pq.exists():
            old = pd.read_parquet(pq)
            new = pd.concat([old, new], ignore_index=True)
        new = (new.drop_duplicates(subset=["create_time"])
                  .sort_values("create_time").reset_index(drop=True))
        tmp = pq.with_suffix(".tmp.parquet")
        new.to_parquet(tmp, index=False)
        tmp.replace(pq)
        rows = len(new)
    else:
        rows = 0

    manifest["done"] = sorted(set(manifest["done"]))
    manifest["missing"] = sorted(set(manifest["missing"]))
    mf.write_text(json.dumps(manifest))
    return {"symbol": sym, "new": len(frames), "n404": n404, "errors": nerr,
            "rows_total": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(CORE_12))
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=None, help="défaut : J-2")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=2)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print(f"Backfill metrics Vision : {len(syms)} symboles, {start} → {end}", flush=True)
    t0 = time.time()
    reg = {}
    for sym in syms:
        r = backfill_symbol(sym, start, end, workers=args.workers)
        reg[sym] = r
        print(f"  {sym:10} new={r.get('new',0):5}  404={r.get('n404',0):4}  "
              f"err={r.get('errors',0):3}  rows={r.get('rows_total','-')}", flush=True)

    (OUT_DIR / "registry.json").write_text(json.dumps(
        {"generated_at": pd.Timestamp.utcnow().isoformat(), "window": [str(start), str(end)],
         "symbols": reg}, indent=2))
    print(f"\nTerminé en {time.time()-t0:.0f}s → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
