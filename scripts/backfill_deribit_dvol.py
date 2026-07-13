#!/usr/bin/env python3
"""
scripts/backfill_deribit_dvol.py
─────────────────────────────────────────────────────────────────────────────
Backfill DVOL (indice Deribit de volatilité implicite 30j, annualisée, en %)
BTC + ETH, résolution 1D, depuis le lancement (2021-03) jusqu'à maintenant.

Source : API publique documentée Deribit `public/get_volatility_index_data`
(gratuite, sans auth, reproductible/hashable — conforme à la règle projet
« APIs publiques documentées, jamais de scraping anti-bot »).

Sortie : data/options_backfill/deribit/DVOL_{CCY}_1d.parquet
         (colonnes ts/open/high/low/close, close = DVOL fin de jour, en %)
Idempotent : re-run = réécriture complète (volume minuscule, ~2k lignes).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "options_backfill" / "deribit"
START_MS = 1_616_500_000_000          # 2021-03-23 (lancement DVOL)
CHUNK_D = 900


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "futur-research/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def fetch_dvol(ccy: str) -> pd.DataFrame:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows, t0 = [], START_MS
    while t0 < now_ms:
        t1 = min(t0 + CHUNK_D * 86_400_000, now_ms)
        u = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
             f"?currency={ccy}&start_timestamp={t0}&end_timestamp={t1}&resolution=1D")
        data = _get(u)["result"]["data"]
        rows.extend(data)
        t0 = t1 + 1
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for ccy in ("BTC", "ETH"):
        df = fetch_dvol(ccy)
        p = OUT / f"DVOL_{ccy}_1d.parquet"
        df.to_parquet(p, index=False)
        print(f"{ccy}: {len(df)} jours DVOL "
              f"({df['ts'].iloc[0].date()} → {df['ts'].iloc[-1].date()}), "
              f"close actuel {df['close'].iloc[-1]:.1f}% → {p}")


if __name__ == "__main__":
    main()
