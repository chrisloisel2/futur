#!/usr/bin/env python3
"""
scripts/report_cross_exchange_funding_edge.py
─────────────────────────────────────────────────────────────────────────────
Teste l'edge funding Binance×Bybit (~3.6 ans) sur 4 hypothèses, dans l'ordre de
priorité : Carry Gate V2 > Risk-off Gate > Directionnel > Crowding.

    python3 scripts/report_cross_exchange_funding_edge.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.data.derivatives.features.cross_exchange_features import build_universe

OUT = ROOT / "reports" / "BINANCE_BYBIT_FUNDING_EDGE_REPORT.md"


def _pf(x):
    x = pd.Series(x).dropna()
    pos = x[x > 0].sum(); neg = -x[x < 0].sum()
    return float(pos / neg) if neg > 1e-12 else float("inf")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    args = ap.parse_args()
    syms = [s.strip() for s in args.symbols.split(",")]
    df = build_universe(syms)
    if df.empty:
        print("Aucun panel — backfill Bybit manquant ?"); return
    span_min, span_max = df.index.min(), df.index.max()
    df = df.reset_index(drop=True)   # index entier unique (4 actifs partagent les ts 8h)

    L = ["# Binance×Bybit Funding Edge Report\n",
         f"- symbols: {syms}",
         f"- overlap: {span_min.date()} → {span_max.date()}  ({len(df):,} obs 8h, {df['symbol'].nunique()} actifs)",
         f"- funding_spread (bybit−binance) médian: {df['funding_spread'].median()*1e4:.2f} bps  "
         f"p99 abs: {df['abs_funding_spread'].quantile(0.99)*1e4:.2f} bps\n"]

    # ── H3 CARRY QUALITY (priorité 1) ──
    L.append("## H3 — Carry Gate V2 (priorité)\n")
    d = df.dropna(subset=["future_net_carry_24h", "future_flip_24h"]).copy()
    base_carry = d["future_net_carry_24h"]
    gate = (d["funding_positive_both"] == 1) & (d["abs_spread_pct"] < 0.90)  # consensus + faible dispersion
    g_carry = d.loc[gate, "future_net_carry_24h"]
    L.append(f"| set | n | net_carry_24h moyen | flip_rate_24h |")
    L.append(f"|---|---:|---:|---:|")
    L.append(f"| tous | {len(d)} | {base_carry.mean()*1e4:.2f} bps | {d['future_flip_24h'].mean():.1%} |")
    L.append(f"| **gated (pos_both & disp<90pct)** | {int(gate.sum())} | **{g_carry.mean()*1e4:.2f} bps** | "
             f"{d.loc[gate,'future_flip_24h'].mean():.1%} |")
    carry_better = g_carry.mean() > base_carry.mean() and d.loc[gate, "future_flip_24h"].mean() <= d["future_flip_24h"].mean()
    L.append(f"\n→ Carry gate {'AMÉLIORE' if carry_better else 'N AMÉLIORE PAS'} le net carry / flips. "
             f"{'**CARRY_GATE_V2 = VALIDATED**' if carry_better else 'non validé'}\n")

    # ── H2 RISK-OFF GATE (priorité 2) ──
    L.append("## H2 — Risk-off Gate (dispersion → drawdown futur)\n")
    d = df.dropna(subset=["future_max_drawdown_24h"]).copy()
    hi = d["abs_spread_pct"] >= 0.95
    L.append(f"| set | n | future_maxDD_24h moyen | future_ret_24h moyen |")
    L.append(f"|---|---:|---:|---:|")
    L.append(f"| tous | {len(d)} | {d['future_max_drawdown_24h'].mean()*100:.2f}% | {d['forward_return_24h'].mean()*100:+.2f}% |")
    L.append(f"| **top5% abs_spread** | {int(hi.sum())} | **{d.loc[hi,'future_max_drawdown_24h'].mean()*100:.2f}%** | "
             f"{d.loc[hi,'forward_return_24h'].mean()*100:+.2f}% |")
    risk_ok = d.loc[hi, "future_max_drawdown_24h"].mean() < d["future_max_drawdown_24h"].mean()
    L.append(f"\n→ Dispersion élevée {'PRÉCÈDE des DD plus profonds' if risk_ok else 'ne prédit pas de DD plus profond'}. "
             f"{'**CROSS_EXCHANGE_STRESS_GATE = VALIDATED**' if risk_ok else 'non validé'}\n")

    # ── H1 DIRECTIONNEL (priorité 3) ──
    L.append("## H1 — Directionnel (spread_zscore → forward_return_24h)\n")
    d = df.dropna(subset=["funding_spread_zscore_90d", "forward_return_24h"]).copy()
    try:
        d["decile"] = pd.qcut(d["funding_spread_zscore_90d"], 5,
                              labels=["D1(bas)", "D2", "D3", "D4", "D5(haut)"], duplicates="drop")
        g = d.groupby("decile")["forward_return_24h"].mean()
        L.append("| decile spread_z | fwd_ret_24h moyen |")
        L.append("|---|---:|")
        for k, v in g.items():
            L.append(f"| {k} | {v*100:+.3f}% |")
        mono = (g.is_monotonic_increasing or g.is_monotonic_decreasing)
        L.append(f"\n→ monotonicité par decile : {mono}. "
                 f"{'edge directionnel possible' if mono else '**pas d edge directionnel brut**'}\n")
    except Exception as e:
        L.append(f"(decile échec: {e})\n")

    # ── robustesse par année (carry gate) ──
    L.append("## Robustesse par année (net carry gated − base)\n| année | Δ net_carry_24h (bps) |\n|---|---:|")
    d = df.dropna(subset=["future_net_carry_24h"]).copy()
    for y, gy in d.groupby("year"):
        gmask = (gy["funding_positive_both"] == 1) & (gy["abs_spread_pct"] < 0.90)
        gm = gy.loc[gmask, "future_net_carry_24h"].mean()
        bm = gy["future_net_carry_24h"].mean()
        if pd.notna(gm):
            L.append(f"| {y} | {(gm-bm)*1e4:+.2f} |")

    L.append("\n## Décision (priorité carry > risk > directionnel)")
    L.append(f"- CARRY_GATE_V2 : {'VALIDATED' if carry_better else 'non'}")
    L.append(f"- CROSS_EXCHANGE_STRESS_GATE : {'VALIDATED' if risk_ok else 'non'}")
    L.append("- Directionnel : voir monotonicité ci-dessus (attendu : faible)")

    OUT.write_text("\n".join(L))
    print("\n".join(L))
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
