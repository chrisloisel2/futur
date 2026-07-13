#!/usr/bin/env python3
"""
scripts/backfill_fear_greed.py — Fear & Greed Index (alternative.me), TOUT
l'historique quotidien depuis 2018-02. API publique documentée, hashable.
Sortie : data/news_backfill/fear_greed.parquet
"""
from __future__ import annotations
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news_backfill"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://api.alternative.me/fng/?limit=0&format=json",
                                 headers={"User-Agent": "futur-research/1.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df["fear_greed"] = df["value"].astype(int)
    df = df[["date", "fear_greed", "value_classification"]].sort_values("date")
    df.to_parquet(OUT / "fear_greed.parquet", index=False)
    print(f"Fear & Greed : {len(df)} jours, {df.date.min().date()} → {df.date.max().date()}")
    print(f"→ {OUT / 'fear_greed.parquet'}")


if __name__ == "__main__":
    main()
