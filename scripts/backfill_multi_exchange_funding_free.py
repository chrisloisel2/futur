#!/usr/bin/env python3
"""
scripts/backfill_multi_exchange_funding_free.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT funding history Bybit + OKX (APIs publiques v5), normalisé sur
les symboles Binance → permet les NÉO-SIGNAUX CROSS-EXCHANGE (funding spread /
divergence), l'edge gratuit le plus sérieux sans liquidations historiques.

Sortie : data/derivatives_backfill/{bybit,okx}/funding/<SYM>.parquet (atomique).
Normalisation : OKX BTC-USDT-SWAP → BTCUSDT ; timestamps UTC ; funding 8h.

    python3 scripts/backfill_multi_exchange_funding_free.py --start 2023-01-01
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

OUT = ROOT / "data" / "derivatives_backfill"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]


def _get(url: str, tries: int = 3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def bybit_funding(sym: str, start_ms: int) -> pd.DataFrame:
    rows, end = [], int(time.time() * 1000)
    while end > start_ms:
        d = _get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&endTime={end}&limit=200")
        lst = d.get("result", {}).get("list", [])
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(x["fundingRateTimestamp"]) for x in lst)
        if oldest >= end:
            break
        end = oldest - 1
        time.sleep(0.2)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("fundingRateTimestamp")
    df["timestamp"] = pd.to_datetime(df["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["timestamp", "funding_rate"]].dropna().sort_values("timestamp").reset_index(drop=True)


def okx_funding(sym: str, start_ms: int) -> pd.DataFrame:
    inst = sym.replace("USDT", "-USDT-SWAP")
    rows, after = [], int(time.time() * 1000)
    while after > start_ms:
        d = _get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&after={after}&limit=100")
        lst = d.get("data", [])
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(x["fundingTime"]) for x in lst)
        if oldest >= after:
            break
        after = oldest - 1
        time.sleep(0.2)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("fundingTime")
    df["timestamp"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["timestamp", "funding_rate"]].dropna().sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    syms = [s.strip() for s in args.symbols.split(",")]

    print(f"{'Asset':<10}{'Bybit pts':>12}{'OKX pts':>10}")
    print("─" * 34)
    for sym in syms:
        nb = nk = 0
        try:
            by = bybit_funding(sym, start_ms)
            if len(by):
                atomic_write_parquet(by, OUT / "bybit" / "funding" / f"{sym}.parquet"); nb = len(by)
        except Exception as e:
            print(f"  bybit {sym}: {repr(e)[:60]}")
        try:
            ok = okx_funding(sym, start_ms)
            if len(ok):
                atomic_write_parquet(ok, OUT / "okx" / "funding" / f"{sym}.parquet"); nk = len(ok)
        except Exception as e:
            print(f"  okx {sym}: {repr(e)[:60]}")
        print(f"{sym:<10}{nb:>12}{nk:>10}")
    print("\nMULTI_EXCHANGE_FUNDING_BACKFILL done (bybit+okx, normalisé Binance)")


if __name__ == "__main__":
    main()
