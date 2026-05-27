#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_hedge_fund.py
===================
Entraîne train_pipeline.py sur chaque actif du bundle hedge_fund,
puis génère un rapport de comparaison ultra-profond vs les runs existants.

Usage:
    cd /home/qbee/futur
    python scripts/train_hedge_fund.py
    python scripts/train_hedge_fund.py --symbols BTCUSDT ETHUSDT --skip-prep
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

FUTUR     = Path(__file__).resolve().parents[1]
DATA_HF   = FUTUR / "data_hedge_fund"
RUNS_HF   = FUTUR / "runs" / "hedge_fund"
RUNS_OLD  = FUTUR / "runs" / "pipeline"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "AVAXUSDT", "XRPUSDT", "LINKUSDT",
    "DOGEUSDT",
]


# ─────────────────────────────────────────────────────────────────────────────
# Recherche du meilleur run existant par symbole
# ─────────────────────────────────────────────────────────────────────────────

def find_best_existing_run(symbol: str) -> Optional[Dict]:
    """Retourne le pipeline_summary.json du run le plus récent pour ce symbole."""
    sym_lower = symbol.lower()
    candidates = []
    for d in RUNS_OLD.iterdir():
        if not d.is_dir():
            continue
        n = d.name.lower()
        if sym_lower in n or sym_lower.rstrip("t") in n:
            s = d / "pipeline_summary.json"
            if s.exists():
                candidates.append((d.name, s))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    with open(candidates[0][1]) as f:
        data = json.load(f)
    data["_run_id"] = candidates[0][0]
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Entraînement
# ─────────────────────────────────────────────────────────────────────────────

def train_symbol(symbol: str, parquet: Path) -> Optional[Dict]:
    """Lance train_pipeline.py et retourne le pipeline_summary.json."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"hf_{symbol.lower()}_{ts}"
    out_dir = str(RUNS_HF)

    cmd = [
        sys.executable,
        str(FUTUR / "train_pipeline.py"),
        "--data",         str(parquet),
        "--out",          out_dir,
        "--run-id",       run_id,
        "--mode",         "combined",
        "--auto-calibrate",
        "--skip-tcn",
        "--test-from",    "2024",
    ]

    print(f"\n  $ {' '.join(cmd[-10:])}")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(FUTUR), capture_output=False, text=True)
    elapsed = time.time() - t0

    if result.returncode != 0:
        print(f"  ✗ Entraînement échoué pour {symbol} ({elapsed:.0f}s)")
        return None

    summary_path = RUNS_HF / run_id / "pipeline_summary.json"
    if not summary_path.exists():
        print(f"  ✗ pipeline_summary.json introuvable : {summary_path}")
        return None

    with open(summary_path) as f:
        data = json.load(f)
    data["_run_id"] = run_id
    data["_elapsed"] = round(elapsed, 1)
    print(f"  ✓ Terminé en {elapsed:.0f}s → {run_id}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Helpers d'extraction
# ─────────────────────────────────────────────────────────────────────────────

def _v(d: dict, *keys, default="—"):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _bl(d: dict):
    return d.get("backtest_long") or {}


def _bs(d: dict):
    return d.get("backtest_short") or {}


def _bc(d: dict):
    return d.get("backtest_combined") or {}


def _fm(d: dict):
    return d.get("filter_metrics") or {}


def _em_long(d: dict):
    mets = d.get("edge_long_metrics") or []
    for m in mets:
        if m.get("model") == "HistGBT":
            return m
    return mets[0] if mets else {}


def _em_short(d: dict):
    mets = d.get("edge_short_metrics") or []
    for m in mets:
        if m.get("model") == "HistGBT":
            return m
    return mets[0] if mets else {}


def _delta(new_val, old_val, higher_is_better=True, fmt=".4f"):
    if new_val == "—" or old_val == "—":
        return ""
    try:
        diff = float(new_val) - float(old_val)
        sign = "↑" if (diff > 0) == higher_is_better else "↓"
        return f" ({sign}{abs(diff):{fmt}})"
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Rapport de comparaison
# ─────────────────────────────────────────────────────────────────────────────

def format_comparison(symbol: str, old: Optional[Dict], new: Optional[Dict]) -> str:
    lines = []
    W = 70
    lines.append("=" * W)
    lines.append(f"  {symbol}  —  COMPARAISON HEDGE FUND DATA vs ANCIENS RUNS")
    lines.append("=" * W)

    if old is None:
        lines.append("  [!] Aucun run existant trouvé pour ce symbole")
    else:
        lines.append(f"  Run ancien   : {old.get('_run_id', '?')}")
        lines.append(f"  Data ancienne: {old.get('data','?')}")

    if new is None:
        lines.append("  [!] Entraînement hedge_fund échoué ou non lancé")
        return "\n".join(lines)

    lines.append(f"  Run nouveau  : {new.get('_run_id','?')}  ({new.get('_elapsed','?')}s)")
    lines.append("")

    # ── LABELS ───────────────────────────────────────────────────────────────
    old_lb = old.get("label_stats", {}) if old else {}
    new_lb = new.get("label_stats", {}) or {}
    lines.append("── LABELS ─────────────────────────────────────────────────")
    for key, label in [
        ("n_total", "Barres totales"),
        ("n_long_net", "Long nets"),
        ("n_short_net", "Short nets"),
        ("frac_long", "Fraction long"),
        ("frac_short", "Fraction short"),
    ]:
        ov = _v(old_lb, key) if old else "—"
        nv = _v(new_lb, key)
        d  = _delta(nv, ov, fmt=".4f")
        lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")
    lines.append("")

    # ── FILTRE ───────────────────────────────────────────────────────────────
    old_f = _fm(old) if old else {}
    new_f = _fm(new)
    lines.append("── FILTRE (tradeable) ─────────────────────────────────────")
    for key, label, hib in [
        ("val_auc",   "AUC val",    True),
        ("val_acc",   "Acc val",    True),
        ("val_f1",    "F1 val",     True),
    ]:
        ov = _v(old_f, key) if old else "—"
        nv = _v(new_f, key)
        d  = _delta(nv, ov, higher_is_better=hib)
        lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")
    lines.append("")

    # ── MODÈLE LONG ──────────────────────────────────────────────────────────
    old_ml = _em_long(old) if old else {}
    new_ml = _em_long(new)
    lines.append("── EDGE MODEL LONG ────────────────────────────────────────")
    for key, label, hib in [
        ("auc",            "AUC",           True),
        ("acc",            "Accuracy",      True),
        ("macro_f1",       "Macro F1",      True),
        ("precision_long", "Précision",     True),
        ("recall_long",    "Rappel",        True),
    ]:
        ov = _v(old_ml, key) if old else "—"
        nv = _v(new_ml, key)
        d  = _delta(nv, ov, higher_is_better=hib)
        lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")
    lines.append("")

    # ── MODÈLE SHORT ─────────────────────────────────────────────────────────
    old_ms = _em_short(old) if old else {}
    new_ms = _em_short(new)
    short_ok_old = (old.get("short_enabled_for_inference", False)) if old else False
    short_ok_new = new.get("short_enabled_for_inference", False)
    lines.append("── EDGE MODEL SHORT ───────────────────────────────────────")
    lines.append(f"  Short déployé  ancien={str(short_ok_old):<12} nouveau={short_ok_new}")
    for key, label, hib in [
        ("auc",             "AUC",           True),
        ("acc",             "Accuracy",      True),
        ("precision_short", "Précision",     True),
        ("recall_short",    "Rappel",        True),
    ]:
        ov = _v(old_ms, key) if old else "—"
        nv = _v(new_ms, key)
        d  = _delta(nv, ov, higher_is_better=hib)
        lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")
    if not short_ok_new:
        reason = new.get("short_disabled_reason") or "N/A"
        lines.append(f"  Raison desactive : {str(reason)[:65]}")
    lines.append("")

    # ── BACKTEST LONG ────────────────────────────────────────────────────────
    old_bl = _bl(old) if old else {}
    new_bl = _bl(new)
    lines.append("── BACKTEST LONG (test ≥ 2024) ────────────────────────────")
    for key, label, hib in [
        ("n_trades",          "Nb trades",       True),
        ("profit_factor",     "Profit Factor",   True),
        ("sharpe_annualized", "Sharpe ann.",      True),
        ("win_rate",          "Win rate",         True),
        ("total_return_pct",  "Return %",         True),
        ("max_drawdown",      "Max drawdown",     False),
        ("avg_win",           "Avg win",          True),
        ("avg_loss",          "Avg loss (abs)",   True),
    ]:
        ov = _v(old_bl, key) if old else "—"
        nv = _v(new_bl, key)
        d  = _delta(nv, ov, higher_is_better=hib)
        lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")

    # Détail par année (nouveau)
    by_yr = new_bl.get("by_year", {})
    if by_yr:
        lines.append("  Détail par année (nouveau) :")
        for yr, stats in sorted(by_yr.items()):
            lines.append(f"    {yr}: trades={stats.get('trades','?')}  PF={stats.get('pf','?')}  WR={stats.get('win_rate','?')}  PnL={stats.get('pnl_sum','?')}")
    lines.append("")

    # ── BACKTEST SHORT ───────────────────────────────────────────────────────
    old_bs = _bs(old) if old else {}
    new_bs = _bs(new)
    if new_bs or old_bs:
        lines.append("── BACKTEST SHORT ─────────────────────────────────────────")
        for key, label, hib in [
            ("n_trades",          "Nb trades",       True),
            ("profit_factor",     "Profit Factor",   True),
            ("sharpe_annualized", "Sharpe ann.",      True),
            ("win_rate",          "Win rate",         True),
            ("total_return_pct",  "Return %",         True),
            ("max_drawdown",      "Max drawdown",     False),
        ]:
            ov = _v(old_bs, key) if old else "—"
            nv = _v(new_bs, key)
            d  = _delta(nv, ov, higher_is_better=hib)
            lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}{d}")
        lines.append("")

    # ── STABILITÉ SHORT ──────────────────────────────────────────────────────
    swf_old = (old.get("short_wf_robustness") or {}) if old else {}
    swf_new = new.get("short_wf_robustness") or {}
    if swf_old or swf_new:
        lines.append("── STABILITÉ SHORT (walk-forward) ─────────────────────────")
        for key, label in [("bad_years", "Années mauvaises"), ("deploy", "Déployable")]:
            ov = _v(swf_old, key) if old else "—"
            nv = _v(swf_new, key)
            lines.append(f"  {label:<22} ancien={ov!s:<12} nouveau={nv!s}")
        lines.append("")

    # ── VERDICT GLOBAL ───────────────────────────────────────────────────────
    lines.append("── VERDICT ────────────────────────────────────────────────")
    scores = []

    # Score filtre (AUC)
    old_auc = _v(_fm(old) if old else {}, "val_auc")
    new_auc = _v(_fm(new), "val_auc")
    if new_auc != "—" and old_auc != "—":
        auc_diff = float(new_auc) - float(old_auc)
        scores.append(("Filtre AUC", auc_diff, auc_diff > 0))

    # Score long AUC
    old_lauc = _v(_em_long(old) if old else {}, "auc")
    new_lauc = _v(_em_long(new), "auc")
    if new_lauc != "—" and old_lauc != "—":
        diff = float(new_lauc) - float(old_lauc)
        scores.append(("Long AUC", diff, diff > 0))

    # Score backtest long PF
    old_pf = _v(_bl(old) if old else {}, "profit_factor")
    new_pf = _v(_bl(new), "profit_factor")
    if new_pf != "—" and old_pf != "—" and str(old_pf) != "inf" and str(new_pf) != "inf":
        diff = float(new_pf) - float(old_pf)
        scores.append(("Long PF", diff, diff > 0))

    improvements = sum(1 for _, _, b in scores if b)
    regressions  = sum(1 for _, _, b in scores if not b)

    if scores:
        for name, diff, improved in scores:
            sign = "✓ AMÉLIORATION" if improved else "✗ RÉGRESSION"
            lines.append(f"  {sign:<18} {name}: {diff:+.4f}")
        lines.append("")
        verdict = "MEILLEUR" if improvements > regressions else (
                  "MOINS BON" if regressions > improvements else "ÉQUIVALENT")
        lines.append(f"  ► VERDICT GLOBAL : {verdict} que l'ancien run")
    else:
        lines.append("  Données insuffisantes pour verdict (0 trades ?)")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tableau récapitulatif
# ─────────────────────────────────────────────────────────────────────────────

def print_summary_table(results: list):
    print("\n" + "="*90)
    print("  TABLEAU RÉCAPITULATIF — HEDGE FUND DATA vs RUNS EXISTANTS")
    print("="*90)
    header = f"{'Symbole':<10} {'Long PF (anc)':<15} {'Long PF (new)':<15} {'Long Shr (anc)':<16} {'Long Shr (new)':<16} {'Short OK':<10}"
    print(header)
    print("-"*90)
    for sym, old, new in results:
        if new is None:
            print(f"  {sym:<10} (entraînement échoué)")
            continue
        old_pf  = _v(_bl(old) if old else {}, "profit_factor")
        new_pf  = _v(_bl(new), "profit_factor")
        old_shr = _v(_bl(old) if old else {}, "sharpe_annualized")
        new_shr = _v(_bl(new), "sharpe_annualized")
        short_ok = "OUI" if new.get("short_enabled_for_inference") else "non"
        print(f"  {sym:<10} {str(old_pf):<15} {str(new_pf):<15} {str(old_shr):<16} {str(new_shr):<16} {short_ok}")
    print("="*90)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols",   nargs="+", default=SYMBOLS)
    ap.add_argument("--skip-prep", action="store_true",
                    help="Ne pas relancer prepare_hedge_fund_data.py")
    ap.add_argument("--skip-train", action="store_true",
                    help="Comparaison uniquement (utilise les derniers runs HF)")
    args = ap.parse_args()

    RUNS_HF.mkdir(parents=True, exist_ok=True)

    # ── Préparation données ──────────────────────────────────────────────────
    if not args.skip_prep:
        print("\n" + "="*60)
        print("  ÉTAPE 1 : PRÉPARATION DES DONNÉES")
        print("="*60)
        prep_cmd = [
            sys.executable,
            str(FUTUR / "scripts" / "prepare_hedge_fund_data.py"),
            "--symbols", *args.symbols,
        ]
        r = subprocess.run(prep_cmd, cwd=str(FUTUR))
        if r.returncode != 0:
            print("Préparation échouée — abandon")
            sys.exit(1)

    # ── Entraînement ─────────────────────────────────────────────────────────
    results = []  # (sym, old_summary, new_summary)

    if not args.skip_train:
        print("\n" + "="*60)
        print("  ÉTAPE 2 : ENTRAÎNEMENT")
        print("="*60)

    for sym in args.symbols:
        old_run = find_best_existing_run(sym)
        parquet = DATA_HF / f"{sym}_1m_bundle.parquet"

        if args.skip_train:
            # Chercher le dernier run HF
            new_run = None
            for d in sorted(RUNS_HF.iterdir(), reverse=True):
                if sym.lower() in d.name.lower():
                    s = d / "pipeline_summary.json"
                    if s.exists():
                        with open(s) as f:
                            new_run = json.load(f)
                        new_run["_run_id"] = d.name
                        break
        else:
            if not parquet.exists():
                print(f"\n  {sym}: parquet introuvable ({parquet}), saut")
                results.append((sym, old_run, None))
                continue
            print(f"\n{'─'*60}")
            print(f"  Entraînement {sym}")
            print(f"{'─'*60}")
            new_run = train_symbol(sym, parquet)

        results.append((sym, old_run, new_run))

    # ── Rapport de comparaison ───────────────────────────────────────────────
    print("\n" + "="*60)
    print("  ÉTAPE 3 : RAPPORT DE COMPARAISON")
    print("="*60)

    report_lines = []
    for sym, old, new in results:
        block = format_comparison(sym, old, new)
        print("\n" + block)
        report_lines.append(block)

    print_summary_table(results)

    # Sauvegarde du rapport
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = FUTUR / "reports" / f"hedge_fund_comparison_{ts}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n\n".join(report_lines))
    print(f"\n  Rapport sauvegardé : {report_path}")


if __name__ == "__main__":
    main()
