#!/usr/bin/env python3
"""
scripts/backfill_hyperliquid_funding_premium.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT Hyperliquid `fundingHistory` (horaire) : funding_rate + premium
(mark vs oracle = pression d'ordre-flow LOCALE à HL).

Rôle : côté HL du futur moteur CEX-DEX/lead-lag. Le tick fin historique est
requester-pays (S3 hyperliquid-archive, 403 anonyme) ; ce backfill horaire est
le premier scan gratuit — un premium HL persistant qui précède les retours
Binance = trace de price discovery fragmentée.

Sortie : data/derivatives_backfill/hyperliquid/funding/<COIN>.parquet

    .venv/bin/python scripts/backfill_hyperliquid_funding_premium.py --start 2024-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.atomic_parquet import atomic_write_parquet

API = "https://api.hyperliquid.xyz/info"
OUT = ROOT / "data" / "derivatives_backfill" / "hyperliquid" / "funding"
COINS = ["BTC", "ETH", "SOL"]


def _post(payload: dict, tries: int = 4):
    body = json.dumps(payload).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(API, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2.0 + k)


def backfill(coin: str, start_ms: int) -> pd.DataFrame:
    rows, cursor = [], start_ms
    now = int(time.time() * 1000)
    while cursor < now:
        data = _post({"type": "fundingHistory", "coin": coin,
                      "startTime": cursor, "endTime": now})
        if not data:
            break
        rows.extend(data)
        last = data[-1]["time"]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(0.25)
        if len(data) < 400:      # dernière page
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("time")
    df["timestamp"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["premium"] = pd.to_numeric(df["premium"], errors="coerce")
    return df.dropna(subset=["funding_rate"])[["timestamp", "funding_rate", "premium"]] \
             .sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--coins", default=",".join(COINS))
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    for coin in [c.strip() for c in args.coins.split(",")]:
        df = backfill(coin, start_ms)
        if not len(df):
            print(f"{coin}: rien")
            continue
        atomic_write_parquet(df, OUT / f"{coin}.parquet")
        print(f"{coin}: {len(df):,} pts horaires "
              f"{df['timestamp'].min().date()} → {df['timestamp'].max().date()}, "
              f"premium médian {df['premium'].median()*1e4:.2f} bps")
    print(f"→ {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
