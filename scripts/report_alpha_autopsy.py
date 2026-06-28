#!/usr/bin/env python3
"""
scripts/report_alpha_autopsy.py
─────────────────────────────────────────────────────────────────────────────
Alpha Autopsy (Phase 17) — explique POURQUOI le portefeuille perd.

Croise la matrice d'ablation (contribution de chaque brique) et le Decision
Ledger (qualité du signal par moteur/actif/régime/mois). Produit un rapport
markdown avec verdict garder / tuer / reconstruire par moteur.

Question unique : le portefeuille perd-il faute d'alpha, ou parce qu'il
exécute mal l'alpha ?

Usage :
    python3 scripts/report_alpha_autopsy.py --ablation reports/ablation_2026_oos.json \
        --start 2026-01-01 --end 2026-06-20 --out reports/alpha_autopsy_2026_oos.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.monitoring.decision_ledger import DecisionLedger
from src.institutional.evaluation.live_validation import profit_factor


def _pf(x: pd.Series) -> float:
    return profit_factor(x.dropna().to_numpy())


def _engine_verdict(pf: float, n: int) -> str:
    if n < 20:
        return "INSUFFICIENT_SAMPLE → SHADOW"
    if pf >= 1.25:
        return "KEEP / STUDY"
    if pf >= 1.05:
        return "MARGINAL → SHADOW"
    return "KILL or REBUILD"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", default="reports/ablation_2026_oos.json")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--out", default="reports/alpha_autopsy_2026_oos.md")
    args = ap.parse_args()

    abl = json.loads(Path(args.ablation).read_text())["runs"] if Path(args.ablation).exists() else {}
    df = DecisionLedger().load()
    if not df.empty:
        df = df[(df["timestamp"] >= pd.Timestamp(args.start, tz="UTC")) &
                (df["timestamp"] <= pd.Timestamp(args.end, tz="UTC"))]
    a = df[df["decision_zone"] == "A_TRADE"].copy()

    L = []
    L.append(f"# Alpha Autopsy — OOS {args.start} → {args.end}\n")
    L.append("Question : le portefeuille perd-il **faute d'alpha** ou par **mauvaise exécution** ?\n")

    # ── 1. décomposition par brique (ablation) ────────────────────────────────
    L.append("## 1. Contribution marginale de chaque brique (ablation)\n")
    if abl:
        def roi(k): return abl.get(k, {}).get("roi", float("nan"))
        rows = [
            ("alpha brut (G_all_raw)", roi("G_all_raw")),
            ("+ allocator (H−G)", roi("H_all_alloc") - roi("G_all_raw")),
            ("+ exit (I−H)", roi("I_all_alloc_exit") - roi("H_all_alloc")),
            ("+ governor (J−I)", roi("J_all_full") - roi("I_all_alloc_exit")),
            ("= full-stack (J)", roi("J_all_full")),
        ]
        L.append("| Brique | Δ ROI |")
        L.append("|---|---:|")
        for name, v in rows:
            L.append(f"| {name} | {v*100:+.1f}% |")
        L.append("")
        L.append("| Run | ROI | PF | trades | t/mois | maxDD | gate |")
        L.append("|---|---:|---:|---:|---:|---:|---|")
        for k, r in abl.items():
            L.append(f"| {k} | {r['roi']*100:+.1f}% | {r['pf']:.2f} | {r['n_trades']} | "
                     f"{r['trades_month']:.1f} | {r['max_dd']*100:.1f}% | {r['gate']} |")
        L.append("")

    # ── 2. qualité du signal par moteur (ledger A_TRADE) ──────────────────────
    L.append("## 2. Qualité du signal par moteur (A_TRADE, realized_shadow_result)\n")
    L.append("| Moteur | n A | PF | PnL moy | PnL méd | WR | verdict |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    keep, kill = [], []
    for eng, g in a.groupby("engine_id"):
        r = g["realized_shadow_result"].dropna()
        if r.empty:
            continue
        pf = _pf(r)
        wr = float((r > 0).mean())
        verdict = _engine_verdict(pf, len(r))
        (keep if pf >= 1.25 and len(r) >= 20 else kill).append(eng)
        L.append(f"| {eng} | {len(r)} | {pf:.2f} | {r.mean()*100:+.2f}% | "
                 f"{r.median()*100:+.2f}% | {wr:.0%} | {verdict} |")
    L.append("")

    # ── 3. PnL par actif ──────────────────────────────────────────────────────
    L.append("## 3. PnL A_TRADE par actif\n")
    L.append("| Actif | n | PF | PnL moy |")
    L.append("|---|---:|---:|---:|")
    for asset, g in a.groupby("asset"):
        r = g["realized_shadow_result"].dropna()
        if len(r):
            L.append(f"| {asset} | {len(r)} | {_pf(r):.2f} | {r.mean()*100:+.2f}% |")
    L.append("")

    # ── 4. PnL par régime ─────────────────────────────────────────────────────
    L.append("## 4. PnL A_TRADE par régime\n")
    L.append("| Régime | n | PF | PnL moy |")
    L.append("|---|---:|---:|---:|")
    for reg, g in a.groupby("regime"):
        r = g["realized_shadow_result"].dropna()
        if len(r):
            L.append(f"| {reg} | {len(r)} | {_pf(r):.2f} | {r.mean()*100:+.2f}% |")
    L.append("")

    # ── 5. PnL par mois ───────────────────────────────────────────────────────
    L.append("## 5. PnL A_TRADE par mois (tous moteurs)\n")
    L.append("| Mois | n | PF | PnL moy |")
    L.append("|---|---:|---:|---:|")
    a["_month"] = a["timestamp"].dt.strftime("%Y-%m")
    for mois, g in a.groupby("_month"):
        r = g["realized_shadow_result"].dropna()
        if len(r):
            L.append(f"| {mois} | {len(r)} | {_pf(r):.2f} | {r.mean()*100:+.2f}% |")
    L.append("")

    # ── 6. diagnostic ─────────────────────────────────────────────────────────
    L.append("## 6. Diagnostic\n")
    if abl:
        g_roi = abl.get("G_all_raw", {}).get("roi", 0)
        diag = ("**FAUTE D'ALPHA** : l'alpha brut (sans exécution) est déjà négatif → "
                "le problème est l'inventaire de signaux, pas seulement l'exécution."
                if g_roi < 0 else
                "Alpha brut positif → la perte vient de l'exécution (coûts/exits/contraintes).")
        L.append(diag + "\n")
    L.append(f"- Moteurs à garder/étudier : {keep or '—'}")
    L.append(f"- Moteurs à tuer/reconstruire : {kill or '—'}")
    L.append("- ⚠ AVAX/BNB/DOT/LINK enriched CORROMPUS → conclusions cross-sectional/multi-actifs invalides tant que non réparés.")
    L.append("- ⚠ Exit engine : audit requis (ablation I−H négatif = churn destructeur).")
    L.append("")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(L))
    print("\n".join(L))
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
