#!/usr/bin/env python3
"""
scripts/build_deribit_positioning_features.py
─────────────────────────────────────────────────────────────────────────────
Features de POSITIONNEMENT options depuis les trades Deribit backfillés
(couche 2 du futur moteur OPTIONS_POSITIONING — ce n'est PAS du VRP).

Par jour UTC (agrégats causaux : uniquement les trades du jour) :
  atm_iv_traded      iv médiane des trades ~ATM (moneyness 0.95-1.05)
  skew_25ish         iv médiane puts OTM (K/S 0.80-0.95) − calls OTM (1.05-1.20)
  pc_volume_ratio    volume premium puts / calls
  net_call_flow_btc  Σ amount signé (buy=+) sur calls ; idem puts
  block_share        part du volume en blocs
  top_strike_share   concentration : part du strike le plus tradé (proxy pinning)
  n_trades, notional_btc

Sortie : data/options_backfill/deribit/features/<CUR>_daily.parquet

    .venv/bin/python scripts/build_deribit_positioning_features.py --currency BTC
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.atomic_parquet import atomic_write_parquet

TRADES = ROOT / "data" / "options_backfill" / "deribit" / "trades"
OUT = ROOT / "data" / "options_backfill" / "deribit" / "features"


def day_features(g: pd.DataFrame) -> pd.Series:
    m = g["strike"] / g["index_price"]
    sgn = np.where(g["direction"] == "buy", 1.0, -1.0)
    is_call, is_put = g["cp"] == "C", g["cp"] == "P"
    atm = g.loc[m.between(0.95, 1.05), "iv"]
    otm_put = g.loc[is_put & m.between(0.80, 0.95), "iv"]
    otm_call = g.loc[is_call & m.between(1.05, 1.20), "iv"]
    prem_put = (g.loc[is_put, "price"] * g.loc[is_put, "amount"]).sum()
    prem_call = (g.loc[is_call, "price"] * g.loc[is_call, "amount"]).sum()
    vol_by_strike = g.groupby("strike")["amount"].sum()
    return pd.Series({
        "atm_iv_traded": atm.median() if len(atm) else np.nan,
        "skew_25ish": (otm_put.median() - otm_call.median())
                      if len(otm_put) and len(otm_call) else np.nan,
        "pc_volume_ratio": prem_put / prem_call if prem_call > 0 else np.nan,
        "net_call_flow_btc": float((sgn * g["amount"])[is_call].sum()),
        "net_put_flow_btc": float((sgn * g["amount"])[is_put].sum()),
        "block_share": float(g["is_block"].mean()),
        "top_strike_share": float(vol_by_strike.max() / vol_by_strike.sum())
                            if vol_by_strike.sum() > 0 else np.nan,
        "n_trades": len(g),
        "notional_btc": float(g["amount"].sum()),
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    args = ap.parse_args()
    files = sorted((TRADES / args.currency).glob("*.parquet"))
    if not files:
        print("Aucun trade backfillé — lancer backfill_deribit_option_trades.py d'abord.")
        return
    # incrémental par fichier mensuel (les jours ne chevauchent jamais deux mois)
    parts, n_trades = [], 0
    for f in files:
        df = pd.read_parquet(f)
        df = df[df["iv"].notna() & (df["index_price"] > 0)]
        df["day"] = df["ts"].dt.floor("D")
        n_trades += len(df)
        parts.append(df.groupby("day").apply(day_features))
    feats = pd.concat(parts).sort_index()
    feats = feats[~feats.index.duplicated(keep="last")].reset_index()
    # deltas causaux (jour vs jour-1) — le signal est dans la VARIATION du positionnement
    for c in ["atm_iv_traded", "skew_25ish", "pc_volume_ratio", "top_strike_share"]:
        feats[f"d_{c}"] = feats[c].diff()
    atomic_write_parquet(feats, OUT / f"{args.currency}_daily.parquet")
    print(f"{len(feats)} jours ({feats['day'].min().date()} → {feats['day'].max().date()}) "
          f"depuis {n_trades:,} trades → "
          f"{(OUT / (args.currency + '_daily.parquet')).relative_to(ROOT)}")
    print(feats.tail(5)[["day", "atm_iv_traded", "skew_25ish", "pc_volume_ratio",
                         "top_strike_share", "n_trades"]].to_string(index=False))


if __name__ == "__main__":
    main()
