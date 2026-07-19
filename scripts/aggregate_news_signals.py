#!/usr/bin/env python3
"""
scripts/aggregate_news_signals.py
─────────────────────────────────────────────────────────────────────────────
Agrège l'event lake news en séries QUOTIDIENNES par actif (causales par
construction : chaque item est horodaté à sa publication) :
  news_sent  : sentiment moyen pondéré du jour (articles citant l'actif)
  news_vol   : nombre d'articles/jour citant l'actif (attention)
Sortie : data/news_backfill/news_daily_{sent,vol}.parquet (pivot date×symbole)
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.news_collector.collector import load_news_lake

OUT = ROOT / "data" / "news_backfill"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_news_lake()
    if df.empty:
        print("event lake vide — lancer run_news_collector.py d'abord")
        return
    df = df[df["symbols"].astype(str).str.len() > 0].copy()
    df["symbols"] = df["symbols"].str.split(",")
    ex = df.explode("symbols")
    ex["day"] = ex["ts"].dt.floor("D")
    sent = ex.groupby(["day", "symbols"])["sentiment"].mean().unstack()
    vol = ex.groupby(["day", "symbols"]).size().unstack()
    sent.to_parquet(OUT / "news_daily_sent.parquet")
    vol.to_parquet(OUT / "news_daily_vol.parquet")
    print(f"news agrégé : {len(df)} items, {ex['symbols'].nunique()} actifs, "
          f"{df['ts'].min().date()} → {df['ts'].max().date()}")
    top = ex["symbols"].value_counts().head(6).to_dict()
    print(f"top couverture : {top}")


if __name__ == "__main__":
    main()
