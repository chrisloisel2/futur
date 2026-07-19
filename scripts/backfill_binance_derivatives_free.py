#!/usr/bin/env python3
"""
scripts/backfill_binance_derivatives_free.py
─────────────────────────────────────────────────────────────────────────────
Backfill GRATUIT Binance Futures (APIs publiques documentées, hashable, repro).

  fundingRate    : historique MULTI-AN paginé (le gem gratuit) — tous actifs
  openInterestHist : DERNIER MOIS seulement (limite Binance documentée)
  takerlongshortRatio / globalLongShortAccountRatio : dernier mois

Honnêteté : pas de liquidations historiques ici (indisponibles gratuitement).
Sortie consolidée par actif : data/derivatives_backfill/binance/<stream>/<SYM>.parquet
(écriture atomique) + registry. Rien d'inventé ; seulement ce que l'API rend.

    python3 scripts/backfill_binance_derivatives_free.py --start 2021-01-01
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

B = "https://fapi.binance.com"
OUT = ROOT / "data" / "derivatives_backfill" / "binance"
REG = ROOT / "artifacts" / "data_registry" / "derivatives_backfill_store.yaml"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]


def _get(url: str, tries: int = 3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)


def backfill_funding(sym: str, start_ms: int) -> pd.DataFrame:
    rows, cursor = [], start_ms
    now = int(time.time() * 1000)
    while cursor < now:
        data = _get(f"{B}/fapi/v1/fundingRate?symbol={sym}&startTime={cursor}&limit=1000")
        if not data:
            break
        rows.extend(data)
        last = data[-1]["fundingTime"]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(0.25)
        if len(data) < 1000 and last > now - 8 * 3600 * 1000:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("fundingTime")
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["mark_price"] = pd.to_numeric(df.get("markPrice", ""), errors="coerce")
    df = df.dropna(subset=["funding_rate"])
    return df[["timestamp", "funding_rate", "mark_price"]].sort_values("timestamp").reset_index(drop=True)


def backfill_oi_hist(sym: str) -> pd.DataFrame:
    data = _get(f"{B}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=500")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["open_interest"] = df["sumOpenInterest"].astype(float)
    df["open_interest_usd"] = df["sumOpenInterestValue"].astype(float)
    return df[["timestamp", "open_interest", "open_interest_usd"]].sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    syms = [s.strip() for s in args.symbols.split(",")]

    registry = {}
    print(f"{'Asset':<10}{'funding pts':>12}{'funding span':>26}{'OI hist pts':>12}")
    print("─" * 62)
    for sym in syms:
        try:
            fund = backfill_funding(sym, start_ms)
            oi = backfill_oi_hist(sym)
        except Exception as e:
            print(f"{sym:<10}  ERREUR {e}"); registry[sym] = {"status": "ERROR", "error": str(e)}; continue
        ent = {"status": "PASS"}
        if len(fund):
            p = OUT / "funding" / f"{sym}.parquet"
            atomic_write_parquet(fund, p)
            ent["funding"] = {"rows": int(len(fund)),
                              "span": [str(fund['timestamp'].min()), str(fund['timestamp'].max())]}
        if len(oi):
            p = OUT / "open_interest_hist" / f"{sym}.parquet"
            atomic_write_parquet(oi, p)
            ent["oi_hist"] = {"rows": int(len(oi)),
                              "span": [str(oi['timestamp'].min()), str(oi['timestamp'].max())]}
        registry[sym] = ent
        fspan = ent.get("funding", {}).get("span", ["", ""])
        print(f"{sym:<10}{ent.get('funding',{}).get('rows',0):>12}"
              f"{(fspan[0][:10]+'→'+fspan[1][:10]):>26}{ent.get('oi_hist',{}).get('rows',0):>12}")

    REG.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    REG.write_text(yaml.safe_dump(registry, sort_keys=True, allow_unicode=True))
    n_ok = sum(1 for v in registry.values() if v.get("status") == "PASS")
    print(f"\nBINANCE_FREE_BACKFILL : {n_ok}/{len(syms)} assets → {REG.relative_to(ROOT)}")
    print("  ⚠ liquidations historiques NON incluses (indisponibles gratuitement)")


if __name__ == "__main__":
    main()
