#!/usr/bin/env python3
"""
scripts/validate_cross_exchange_signals.py
─────────────────────────────────────────────────────────────────────────────
Teste les néo-signaux cross-exchange : couverture, distribution du spread, et
relation funding-divergence → rendement forward (mean-reversion / stress).

    python3 scripts/validate_cross_exchange_signals.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.derivatives.cross_exchange import funding_divergence_signal, EXCHANGES
from src.institutional.engines.legacy_bridge import load_enriched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    args = ap.parse_args()

    print(f"{'Asset':<10}{'overlap':>9}{'exch':>6}{'spread_med':>12}{'spread_p99':>12}")
    print("─" * 50)
    panels = {}
    for sym in [s.strip() for s in args.symbols.split(",")]:
        df = funding_divergence_signal(sym)
        if df.empty:
            print(f"{sym:<10}  pas d'overlap"); continue
        panels[sym] = df
        nex = len([c for c in EXCHANGES if c in df.columns])
        print(f"{sym:<10}{len(df):>9}{nex:>6}{df['spread'].median()*1e4:>11.2f}b"
              f"{df['spread'].quantile(0.99)*1e4:>11.2f}b")

    # relation : spread_zscore élevé → rendement forward 24h (mean-reversion ?)
    print("\n=== funding divergence → forward 24h return (BTC), par bucket spread_zscore ===")
    if "BTCUSDT" in panels:
        df = panels["BTCUSDT"].copy()
        enr = load_enriched("BTCUSDT", required_cols=["close"])
        px = enr.set_index("datetime")["close"].sort_index()
        idx = df.index
        p0 = px.reindex(idx, method="ffill").to_numpy()
        p24 = px.reindex(idx + pd.Timedelta(hours=24), method="ffill").to_numpy()
        df["fwd_ret_24h"] = p24 / p0 - 1.0
        df = df.dropna(subset=["spread_zscore", "fwd_ret_24h"])
        if len(df) > 30:
            df["bucket"] = pd.qcut(df["spread_zscore"], 4, labels=["low", "mid-lo", "mid-hi", "high"], duplicates="drop")
            g = df.groupby("bucket")["fwd_ret_24h"].agg(["count", "mean"])
            for b, r in g.iterrows():
                print(f"  spread_z {str(b):<8} n={int(r['count']):>4}  fwd24h_mean={r['mean']*100:+.3f}%")
        else:
            print(f"  overlap trop court ({len(df)} pts) pour le test directionnel")
    print("\nNote : edge cross-exchange = mesurable ; overlap limité par OKX (~3 mois gratuits).")


if __name__ == "__main__":
    main()
