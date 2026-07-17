#!/usr/bin/env python3
"""
scripts/backfill_binance_liquidation_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill HISTORIQUE des snapshots de liquidation Binance Futures (Vision).

Source : https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot/
         {SYM}/{SYM}-liquidationSnapshot-{YYYY-MM-DD}.zip

Découverte 2026-07-17 : liquidationSnapshot n'existe QUE pour les futures
COIN-M (cm). Le dossier USD-M (um) a été retiré de Binance Vision (l'ancien
flux um s'arrêtait en 2023-04 quand forceOrder a été throttlé à 1 ordre/s).
Fenêtre cm constatée : 2023-06-25 → 2026-01-01 (rien après, sondé le
2026-07-17). 45 contrats PERP + trimestriels.

Usage prévu : validation du proxy cascade 5-min (metrics um) contre des
liquidations réelles, et features d'épuisement de cascade (expérience
séparée du pipeline CTREND — voir reports/liq_cascade/).

Idempotent : manifest par symbole (jours faits / 404). Sortie :
  data/derivatives_backfill/binance_vision_liquidation/{SYM}_liq.parquet

Usage :
  python3 scripts/backfill_binance_liquidation_vision.py                  # PERPs cœur
  python3 scripts/backfill_binance_liquidation_vision.py --symbols BTCUSD_PERP
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
OUT_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_liquidation"
BASE = "https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot"

# Tous les PERP cm disposant d'un dossier liquidationSnapshot (sondé 2026-07-17)
ALL_PERPS = [
    "AAVEUSD_PERP", "ADAUSD_PERP", "ALGOUSD_PERP", "APEUSD_PERP", "APTUSD_PERP",
    "ATOMUSD_PERP", "AVAXUSD_PERP", "AXSUSD_PERP", "BCHUSD_PERP", "BNBUSD_PERP",
    "BTCUSD_PERP", "CHZUSD_PERP", "DOGEUSD_PERP", "DOGSUSD_PERP", "DOTUSD_PERP",
    "EGLDUSD_PERP", "ENSUSD_PERP", "EOSUSD_PERP", "ETCUSD_PERP", "ETHUSD_PERP",
    "FILUSD_PERP", "FTMUSD_PERP", "GMTUSD_PERP", "ICXUSD_PERP", "KNCUSD_PERP",
    "LINKUSD_PERP", "LTCUSD_PERP", "MANAUSD_PERP", "MATICUSD_PERP", "NEARUSD_PERP",
    "OPUSD_PERP", "ROSEUSD_PERP", "RUNEUSD_PERP", "SANDUSD_PERP", "SOLUSD_PERP",
    "SUIUSD_PERP", "THETAUSD_PERP", "TRXUSD_PERP", "UNIUSD_PERP", "WIFUSD_PERP",
    "WLDUSD_PERP", "XLMUSD_PERP", "XMRUSD_PERP", "XRPUSD_PERP", "XTZUSD_PERP",
]

WINDOW_START = date(2023, 6, 25)   # premier jour publié (constaté)
WINDOW_END = date(2026, 1, 1)      # dernier jour publié (constaté)


def _fetch_day(sym: str, d: date):
    url = f"{BASE}/{sym}/{sym}-liquidationSnapshot-{d.isoformat()}.zip"
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
    pq = OUT_DIR / f"{sym}_liq.parquet"
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
                nerr += 1  # transitoire : retenté au prochain run

    if frames:
        new = pd.concat(frames, ignore_index=True)
        if "time" in new.columns:
            t = pd.to_numeric(new["time"], errors="coerce")
            if t.notna().mean() > 0.5:
                new["time"] = pd.to_datetime(t, unit="ms", utc=True)
            else:
                new["time"] = pd.to_datetime(new["time"], utc=True, format="mixed")
        for c in ("original_quantity", "price", "average_price",
                  "last_fill_quantity", "accumulated_fill_quantity"):
            if c in new.columns:
                new[c] = pd.to_numeric(new[c], errors="coerce")
        if pq.exists():
            old = pd.read_parquet(pq)
            new = pd.concat([old, new], ignore_index=True)
        new = (new.drop_duplicates()
                  .sort_values("time").reset_index(drop=True))
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
    ap.add_argument("--symbols", default=",".join(ALL_PERPS))
    ap.add_argument("--start", default=WINDOW_START.isoformat())
    ap.add_argument("--end", default=WINDOW_END.isoformat())
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print(f"Backfill liquidationSnapshot cm : {len(syms)} symboles, {start} → {end}",
          flush=True)
    t0 = time.time()
    reg = {}
    for sym in syms:
        r = backfill_symbol(sym, start, end, workers=args.workers)
        reg[sym] = r
        print(f"  {sym:14} new={r.get('new',0):5}  404={r.get('n404',0):4}  "
              f"err={r.get('errors',0):3}  rows={r.get('rows_total','-')}", flush=True)

    (OUT_DIR / "registry.json").write_text(json.dumps(
        {"generated_at": pd.Timestamp.utcnow().isoformat(),
         "window": [str(start), str(end)], "symbols": reg}, indent=2))
    print(f"\nTerminé en {time.time()-t0:.0f}s → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
