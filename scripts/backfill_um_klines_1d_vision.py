#!/usr/bin/env python3
"""
scripts/backfill_um_klines_1d_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill klines 1d de TOUS les perps USDT-M Binance (Vision, mensuel),
CONTRATS DÉLISTÉS INCLUS — base de l'univers point-in-time CTREND v1.

Source : https://data.binance.vision/data/futures/um/monthly/klines/
         {SYM}/1d/{SYM}-1d-{YYYY-MM}.zip

L'énumération S3 du 2026-07-17 donne 934 dossiers symboles (dont délistés) ;
on garde les *USDT. Les dates de listing/delisting sont déduites des bornes
des klines elles-mêmes (aucune donnée avant listing / après delisting).

Idempotent : manifest par symbole (mois faits / 404). Sortie :
  data/derivatives_backfill/um_klines_1d/{SYM}_1d.parquet

Usage :
  python3 scripts/backfill_um_klines_1d_vision.py --symbols-file all_usdt.txt
  python3 scripts/backfill_um_klines_1d_vision.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "derivatives_backfill" / "um_klines_1d"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"

KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume",
         "ignore"]


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _fetch_month(sym: str, ym: str):
    url = f"{BASE}/{sym}/1d/{sym}-1d-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return ym, ("404" if e.code == 404 else f"http_{e.code}"), None
    except Exception as e:
        return ym, f"err_{type(e).__name__}", None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None)
        # certains dumps ont une ligne d'en-tête, d'autres non
        if isinstance(df.iloc[0, 0], str) and not str(df.iloc[0, 0]).isdigit():
            df = df.iloc[1:].reset_index(drop=True)
        df.columns = KCOLS[: len(df.columns)]
        return ym, "ok", df
    except Exception as e:
        return ym, f"parse_{type(e).__name__}", None


def backfill_symbol(sym: str, start: date, end: date, workers: int = 6) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq = OUT_DIR / f"{sym}_1d.parquet"
    mf = OUT_DIR / f"{sym}_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {"done": [], "missing": []}
    skip = set(manifest["done"]) | set(manifest["missing"])
    todo = [ym for ym in _months(start, end) if ym not in skip]
    if not todo:
        return {"symbol": sym, "new": 0, "status": "up_to_date"}

    frames, n404, nerr = [], 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_month, sym, ym): ym for ym in todo}
        for fut in as_completed(futs):
            ym, status, df = fut.result()
            if status == "ok":
                frames.append(df)
                manifest["done"].append(ym)
            elif status == "404":
                n404 += 1
                manifest["missing"].append(ym)
            else:
                nerr += 1

    if frames:
        new = pd.concat(frames, ignore_index=True)
        for c in new.columns:
            if c != "ignore":
                new[c] = pd.to_numeric(new[c], errors="coerce")
        unit = "us" if new["open_time"].iloc[-1] > 1e14 else "ms"
        new["open_time"] = pd.to_datetime(new["open_time"], unit=unit, utc=True)
        new = new.drop(columns=["close_time", "ignore"], errors="ignore")
        if pq.exists():
            old = pd.read_parquet(pq)
            new = pd.concat([old, new], ignore_index=True)
        new = (new.drop_duplicates(subset=["open_time"])
                  .sort_values("open_time").reset_index(drop=True))
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
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument("--start", default="2019-09-01")   # lancement um futures
    ap.add_argument("--end", default=None, help="défaut : mois courant")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--par-symbols", type=int, default=4,
                    help="symboles traités en parallèle")
    args = ap.parse_args()

    if args.symbols_file:
        syms = [s.strip() for s in Path(args.symbols_file).read_text().split()
                if s.strip()]
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        raise SystemExit("--symbols ou --symbols-file requis")

    start = date.fromisoformat(args.start + ("-01" if len(args.start) == 7 else ""))
    today = date.today()
    end = (date.fromisoformat(args.end + ("-01" if len(args.end) == 7 else ""))
           if args.end else date(today.year, today.month, 1))

    print(f"Backfill klines 1d um : {len(syms)} symboles, {start} → {end}", flush=True)
    t0 = time.time()
    reg = {}
    with ThreadPoolExecutor(max_workers=args.par_symbols) as ex:
        futs = {ex.submit(backfill_symbol, s, start, end, args.workers): s
                for s in syms}
        for i, fut in enumerate(as_completed(futs)):
            r = fut.result()
            reg[r["symbol"]] = r
            if r.get("new", 0) or (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(syms)}] {r['symbol']:18} new={r.get('new',0):3} "
                      f"404={r.get('n404',0):3} rows={r.get('rows_total','-')}",
                      flush=True)

    (OUT_DIR / "registry.json").write_text(json.dumps(
        {"generated_at": pd.Timestamp.utcnow().isoformat(),
         "window": [str(start), str(end)], "symbols": reg}, indent=2))
    print(f"\nTerminé en {time.time()-t0:.0f}s → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
