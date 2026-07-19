#!/usr/bin/env python3
"""
scripts/backfill_stablecoin_liquidity.py
─────────────────────────────────────────────────────────────────────────────
Couche data du protocole STABLECOIN_REGIME v0 (reports/STABLECOIN_REGIME_PROTOCOL.md).

Télécharge les séries quotidiennes DefiLlama (gratuites, probées 2026-07-18) :
  - supply agrégée par pegType (charts/all)
  - supply cross-chain USDT (id 1), USDC (id 2), DAI (id 5)
  - prix quotidiens (depeg) pour tether / usd-coin / dai

Sorties (idempotent, ré-exécutable) :
  data/stablecoins/raw/<nom>_<YYYY-MM-DD>.json   (snapshot brut daté du fetch)
  data/stablecoins/supply_daily.parquet          (date, usdt, usdc, dai, trio, all_usd)
  data/stablecoins/prices_daily.parquet          (date, p_usdt, p_usdc, p_dai)

Aucune statistique signal→cible n'est calculée ici.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "stablecoins"
RAW = OUT / "raw"
BASE = "https://stablecoins.llama.fi"
ASSETS = {"usdt": 1, "usdc": 2, "dai": 5}
GECKO = {"p_usdt": "tether", "p_usdc": "usd-coin", "p_dai": "dai"}


def fetch(url: str, name: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "futur-research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status != 200:
            raise RuntimeError(f"{name}: HTTP {r.status}")
        data = json.loads(r.read())
    stamp = dt.date.today().isoformat()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{name}_{stamp}.json").write_text(json.dumps(data))
    return data


def series_circulating(rows: list) -> pd.Series:
    idx = pd.to_datetime([int(r["date"]) for r in rows], unit="s", utc=True)
    vals = [r["totalCirculating"].get("peggedUSD") for r in rows]
    return pd.Series(vals, index=idx, dtype=float)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    all_rows = fetch(f"{BASE}/stablecoincharts/all", "charts_all")
    cols = {"all_usd": series_circulating(all_rows)}
    for sym, aid in ASSETS.items():
        rows = fetch(f"{BASE}/stablecoincharts/all?stablecoin={aid}", f"asset_{sym}")
        cols[sym] = series_circulating(rows)
        print(f"{sym}: {len(rows)} pts, {cols[sym].index[0].date()} → "
              f"{cols[sym].index[-1].date()}", flush=True)
    supply = pd.DataFrame(cols).sort_index()
    supply["trio"] = supply[["usdt", "usdc", "dai"]].sum(axis=1, min_count=3)
    supply.rename_axis("date").reset_index().to_parquet(
        OUT / "supply_daily.parquet", index=False)

    price_rows = fetch(f"{BASE}/stablecoinprices", "prices")
    pidx = pd.to_datetime([int(r["date"]) for r in price_rows], unit="s", utc=True)
    prices = pd.DataFrame(
        {c: [r["prices"].get(g) for r in price_rows] for c, g in GECKO.items()},
        index=pidx, dtype=float).sort_index()
    # premier point daté epoch-0 (artefact API observé) : purgé
    prices = prices[prices.index >= "2015-01-01"]
    prices.rename_axis("date").reset_index().to_parquet(
        OUT / "prices_daily.parquet", index=False)
    print(f"prices: {len(prices)} pts, {prices.index[0].date()} → "
          f"{prices.index[-1].date()}", flush=True)

    n_missing = int(supply[["usdt", "usdc", "dai"]].isna().sum().sum())
    print(f"supply_daily: {len(supply)} lignes, NaN usdt/usdc/dai = {n_missing}")


if __name__ == "__main__":
    sys.exit(main())
