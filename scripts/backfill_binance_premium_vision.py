#!/usr/bin/env python3
"""
scripts/backfill_binance_premium_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill premiumIndexKlines 5m (Binance Vision, fichiers MENSUELS) — le
premium perp vs index, la donnée du moteur PREMIUM_DISLOCATION.

Sortie : data/derivatives_backfill/binance_vision_premium/{SYM}_premium_5m.parquet
Idempotent (manifest par symbole, mois faits/404).
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
OUT_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_premium"
BASE = "https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def _months(start: str, end: str):
    cur = date.fromisoformat(start + "-01")
    stop = date.fromisoformat(end + "-01")
    while cur <= stop:
        yield f"{cur.year}-{cur.month:02d}"
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)


def _fetch_month(sym: str, ym: str):
    url = f"{BASE}/{sym}/5m/{sym}-5m-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return ym, ("404" if e.code == 404 else f"http_{e.code}"), None
    except Exception as e:
        return ym, f"err_{type(e).__name__}", None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS,
                             skiprows=1)
            # certains dumps ont un header, d'autres non → détecte
            if isinstance(df["open_time"].iloc[0], str) and not str(
                    df["open_time"].iloc[0]).isdigit():
                df = df.iloc[1:]
        df = df[["open_time", "open", "high", "low", "close"]].apply(
            pd.to_numeric, errors="coerce").dropna(subset=["open_time", "close"])
        return ym, "ok", df
    except Exception as e:
        return ym, f"parse_{type(e).__name__}", None


def backfill(sym: str, start: str, end: str, workers: int) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq = OUT_DIR / f"{sym}_premium_5m.parquet"
    mf = OUT_DIR / f"{sym}_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {"done": [], "missing": []}
    seen = set(manifest["done"]) | set(manifest["missing"])
    todo = [m for m in _months(start, end) if m not in seen]
    if not todo:
        return {"symbol": sym, "new": 0}
    frames, n404 = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed({ex.submit(_fetch_month, sym, m): m for m in todo}):
            ym, status, df = fut.result()
            if status == "ok":
                frames.append(df); manifest["done"].append(ym)
            elif status == "404":
                n404 += 1; manifest["missing"].append(ym)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        new["ts"] = pd.to_datetime(new["open_time"], unit="ms", utc=True)
        new = new.rename(columns={"close": "premium", "high": "premium_high",
                                  "low": "premium_low", "open": "premium_open"})
        if pq.exists():
            new = pd.concat([pd.read_parquet(pq), new], ignore_index=True)
        new = (new.drop_duplicates(subset=["open_time"])
                  .sort_values("open_time").reset_index(drop=True))
        tmp = pq.with_suffix(".tmp.parquet"); new.to_parquet(tmp, index=False)
        tmp.replace(pq)
    manifest["done"] = sorted(set(manifest["done"]))
    manifest["missing"] = sorted(set(manifest["missing"]))
    mf.write_text(json.dumps(manifest))
    return {"symbol": sym, "new": len(frames), "n404": n404}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--end", default="2026-06")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    if args.symbols:
        syms = args.symbols.split(",")
    else:
        import yaml
        syms = yaml.safe_load((ROOT / "configs/portfolio_v1_1_parallel_50.yaml"
                               ).read_text())["universe"]
    t0 = time.time()
    for sym in syms:
        r = backfill(sym, args.start, args.end, args.workers)
        print(f"  {sym:12} new={r.get('new',0):3} 404={r.get('n404',0):3}", flush=True)
    print(f"Terminé {time.time()-t0:.0f}s → {OUT_DIR}")


if __name__ == "__main__":
    main()
