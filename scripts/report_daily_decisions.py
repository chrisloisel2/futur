#!/usr/bin/env python3
"""
scripts/report_daily_decisions.py
─────────────────────────────────────────────────────────────────────────────
Rapport décisions : compte A/B/C, PnL shadow (B), near-miss PnL (C proches du
seuil), λ (trades/mois) par moteur et par régime. Répond à :
    le bot a-t-il évité du bruit ou raté de bons trades ?

Critère de succès Semaine 1 : 100% des non-trades expliqués, shadow mesuré.

Usage : python3 scripts/report_daily_decisions.py [--ledger PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.monitoring.decision_ledger import DecisionLedger


def _months(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    span = (df["timestamp"].max() - df["timestamp"].min())
    return max(span.days / 30.0, 1e-9)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=None)
    args = ap.parse_args()

    ledger = DecisionLedger(Path(args.ledger) if args.ledger else None)
    df = ledger.load()
    if df.empty:
        print("Ledger vide — lancer backfill_decision_ledger.py d'abord.")
        return

    print(f"\n{'='*72}\nDECISION LEDGER — {len(df):,} décisions  "
          f"({df['timestamp'].min().date()} → {df['timestamp'].max().date()})\n{'='*72}")

    # Global A/B/C
    s = ledger.summary()
    print(f"\nZones globales : A={s['n_A_trade']}  B(shadow)={s['n_B_shadow']}  C(reject)={s['n_C_reject']}")
    print(f"  shadow PnL moyen (B) : {s['shadow_pnl_mean']}")
    print(f"  near-miss count (C≈seuil) : {s['near_miss_count']}  | PnL moyen : {s['near_miss_pnl_mean']}")
    print(f"  A_TRADE PnL moyen : {s['a_trade_pnl_mean']}")
    print(f"  non-trades expliqués : {s['pct_explained']:.0%}")

    # Par moteur : λ (A_TRADE/mois), conversion, PnL
    print(f"\n{'Moteur':<20}{'λ A/mois':>10}{'A':>7}{'B':>7}{'C':>8}{'shadowPnL':>12}{'A_PnL':>10}")
    print("─" * 74)
    for eng, g in df.groupby("engine_id"):
        m = _months(g)
        a = g[g.decision_zone == "A_TRADE"]
        b = g[g.decision_zone == "B_SHADOW"]
        c = g[g.decision_zone == "C_REJECT"]
        lam = len(a) / m
        sh = b["realized_shadow_result"].dropna().mean()
        ap_ = a["realized_shadow_result"].dropna().mean()
        print(f"{eng:<20}{lam:>10.1f}{len(a):>7}{len(b):>7}{len(c):>8}"
              f"{(sh if sh==sh else 0):>12.4f}{(ap_ if ap_==ap_ else 0):>10.4f}")

    # Par régime (A_TRADE rate)
    print(f"\n{'Régime':<14}{'décisions':>10}{'A':>7}{'B':>7}{'C':>8}{'A-rate':>9}")
    print("─" * 55)
    for reg, g in df.groupby("regime"):
        a = (g.decision_zone == "A_TRADE").sum()
        b = (g.decision_zone == "B_SHADOW").sum()
        c = (g.decision_zone == "C_REJECT").sum()
        print(f"{str(reg):<14}{len(g):>10}{a:>7}{b:>7}{c:>8}{a/max(len(g),1):>9.1%}")
    print()


if __name__ == "__main__":
    main()
