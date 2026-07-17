#!/usr/bin/env python3
"""
scripts/test_hl_premium_leadlag_scan.py
─────────────────────────────────────────────────────────────────────────────
Scan lead-lag GROSSIER (1h, gratuit) : le premium Hyperliquid (mark vs oracle,
= pression d'ordre-flow locale HL) précède-t-il les retours Binance ?

Ce N'EST PAS le moteur CEX-DEX final (qui exige du tick fin, requester-pays) —
c'est le test de viabilité gratuit : s'il n'y a AUCUNE trace à 1h sur 2.5 ans,
la fragmentation grossière n'existe pas et seul le fin resterait à explorer.

Causal : premium settle à t (calculé sur l'heure précédente) → retour Binance
close(t)→close(t+h). IC Spearman + décile extrême, cross-corrélation ±24h.

    .venv/bin/python scripts/test_hl_premium_leadlag_scan.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HL = ROOT / "data" / "derivatives_backfill" / "hyperliquid" / "funding"
ENRICHED = ROOT / "data" / "enriched"
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
FWD_H = [1, 4, 12, 24]


def main() -> None:
    print(f"{'coin':<5}{'fwd_h':>6}{'IC':>8}{'p':>10}{'top-bot 10% (bps)':>20}{'n':>8}")
    print("─" * 60)
    for coin, sym in PAIRS.items():
        hl = pd.read_parquet(HL / f"{coin}.parquet")
        # les settles HL portent des ms (…:00.151) → floor à l'heure pour l'alignement
        hl["timestamp"] = hl["timestamp"].dt.floor("H")
        hl = hl.drop_duplicates("timestamp").set_index("timestamp").sort_index()
        px = pd.read_parquet(ENRICHED / f"{sym}_1h_enriched.parquet",
                             columns=["datetime", "close"])
        px = px.set_index(pd.DatetimeIndex(px["datetime"]))["close"].sort_index()
        # z-score causal du premium (fenêtre 7 j, pas de centre)
        z = ((hl["premium"] - hl["premium"].rolling(168, min_periods=48).mean())
             / hl["premium"].rolling(168, min_periods=48).std())
        df = pd.DataFrame({"z": z}).join(px.rename("close"), how="inner").dropna()
        for h in FWD_H:
            fwd = df["close"].shift(-h) / df["close"] - 1
            m = df["z"].notna() & fwd.notna()
            ic, p = spearmanr(df.loc[m, "z"], fwd[m])
            hi, lo = df["z"].quantile(0.9), df["z"].quantile(0.1)
            spread = (fwd[m & (df["z"] >= hi)].mean() - fwd[m & (df["z"] <= lo)].mean()) * 1e4
            print(f"{coin:<5}{h:>6}{ic:>8.3f}{p:>10.1e}{spread:>20.1f}{int(m.sum()):>8}")
        # cross-corrélation : premium(t) vs retour 1h à t+lag — lead si asymétrie droite
        r1 = (px.shift(-1) / px - 1).reindex(df.index)
        xc = {lag: df["z"].corr(r1.shift(-lag)) for lag in range(-24, 25)}
        best = max(xc, key=lambda k: abs(xc[k]) if np.isfinite(xc[k]) else 0)
        lead = np.nanmean([abs(xc[l]) for l in range(1, 25)])
        lag_ = np.nanmean([abs(xc[l]) for l in range(-24, 0)])
        print(f"{coin:<5} xcorr |lead(t+1..24)|moy={lead:.4f} vs |lag(t-24..-1)|moy={lag_:.4f} "
              f"(pic lag={best:+d}, r={xc[best]:+.4f})")
    print("\nLecture : IC>0 significatif + asymétrie lead>lag ⇒ trace de discovery HL→Binance.")
    print("Coûts non déduits (scan) ; toute exploitation exigerait coûts ×2 + latence.")


if __name__ == "__main__":
    main()
