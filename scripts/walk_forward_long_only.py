#!/usr/bin/env python3
"""
scripts/walk_forward_long_only.py — WALK-FORWARD LONG-ONLY
=========================================================

Validation robuste de la stratégie LONG sur plusieurs folds temporels.
Utilise les modèles entraînés (pas de re-training) — out-of-sample pur.

Découpage :
  Fold 1 : Test 2020
  Fold 2 : Test 2021
  Fold 3 : Test 2022
  Fold 4 : Test 2023
  Fold 5 : Test 2024
  Fold 6 : Test 2025
  Fold 7 : Test 2026 (partiel)

Critères globaux :
  - total_trades >= 50
  - majorité des folds PF >= 1.20
  - majorité des folds expectancy > 0
  - aucun fold catastrophique (DD > 12% ou PF < 0.80)

Usage :
  python scripts/walk_forward_long_only.py
  python scripts/walk_forward_long_only.py --ft 0.51 --dt 0.58
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import List, Dict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validation_engine import (
    load_alpha_data, load_models, generate_signals,
    run_backtest_core, BacktestParams,
)
from config.strategy_flags import MIN_LONG_TRADES_FOR_DEPLOY

REPORT_DIR = ROOT / "reports" / "long_only_validation"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

CATASTROPHIC_DD  = 12.0
CATASTROPHIC_PF  = 0.80
DEPLOY_PF        = 1.20


def run_walk_forward(
    df: pd.DataFrame,
    models,
    filter_threshold: float = 0.51,
    edge_threshold:   float = 0.58,
    unc_threshold:    float = 0.30,
    params: BacktestParams = None,
) -> Dict:
    if params is None:
        params = BacktestParams()

    # Déterminer les années disponibles
    years = sorted(df["datetime"].dt.year.unique())
    first_year = years[0]
    last_year  = years[-1]
    print(f"  Données disponibles : {first_year} → {last_year} ({len(years)} ans)")

    # Construire les folds : chaque année complète est un fold de test
    test_years = [y for y in years if y >= 2020]
    if not test_years:
        test_years = years[-min(4, len(years)):]

    folds_raw = []
    for ty in test_years:
        fold = {
            "test_start": f"{ty}-01-01",
            "test_end":   f"{ty}-12-31",
        }
        folds_raw.append(fold)

    fold_results: List[Dict] = []

    for fold in folds_raw:
        ts = fold["test_start"]
        te = fold["test_end"]

        df_fold = df[
            (df["datetime"] >= pd.Timestamp(ts, tz="UTC")) &
            (df["datetime"] <= pd.Timestamp(te, tz="UTC") + pd.Timedelta(hours=23))
        ].copy()

        if len(df_fold) < 100:
            print(f"  [{ts[:4]}] Insuffisant ({len(df_fold)} barres) — skip")
            continue

        # Génère les signaux (modèles fixes, pas de re-train)
        df_sig = generate_signals(
            df_fold, models,
            filter_threshold=filter_threshold,
            edge_threshold=edge_threshold,
            uncertainty_width_threshold=unc_threshold,
        )
        m = run_backtest_core(df_sig, params)

        n  = m.get("n_trades", 0)
        pf = m.get("profit_factor", 0.0)
        ex = m.get("expectancy", 0.0)
        dd = abs(m.get("max_drawdown_pct", 0.0))
        tr = m.get("total_return_pct", 0.0)
        exp_pct = m.get("exposure_pct", 0.0)
        turnover = m.get("turnover", 0.0)

        # B&H pour ce fold
        close_col = "close" if "close" in df_fold.columns else "Close"
        prices = df_fold[close_col].dropna()
        bh = (float(prices.iloc[-1]) - float(prices.iloc[0])) / float(prices.iloc[0]) * 100 if len(prices) > 1 else 0.0

        catastrophic = (dd > CATASTROPHIC_DD) or (pf < CATASTROPHIC_PF and n >= 5) or (ex < -50)
        deployable_fold = (
            n >= 5  # au moins 5 trades / an est raisonnable
            and pf >= DEPLOY_PF
            and ex > 0
            and not catastrophic
        )

        fold_result = {
            "test_start":          ts[:4],
            "test_end":            te[:4],
            "year":                ts[:4],
            "n_bars":              len(df_fold),
            "n_trades":            n,
            "profit_factor":       round(pf, 3),
            "expectancy":          round(ex, 4),
            "max_drawdown_pct":    round(-dd, 2),
            "win_rate":            round(m.get("win_rate", 0), 4),
            "total_return_pct":    round(tr, 2),
            "exposure_pct":        round(exp_pct, 2),
            "turnover":            round(turnover, 6),
            "benchmark_bh_return": round(bh, 2),
            "sharpe":              round(m.get("sharpe", 0), 3),
            "sortino":             round(m.get("sortino", 0), 3),
            "deployable_fold":     deployable_fold,
            "catastrophic":        catastrophic,
            "reason":              m.get("reason", ""),
        }
        fold_results.append(fold_result)

        flag = "✓" if deployable_fold else ("💀" if catastrophic else "✗")
        print(f"  [{ts[:4]}] {flag}  n={n:>3}  PF={pf:.3f}  E={ex:+.4f}  DD={dd:.1f}%  B&H={bh:+.1f}%")

    if not fold_results:
        return {"folds": [], "walk_forward_pass": False, "reason": "no_folds"}

    # Synthèse globale
    total_trades    = sum(f["n_trades"] for f in fold_results)
    n_folds         = len(fold_results)
    n_deploy        = sum(f["deployable_fold"] for f in fold_results)
    n_catastrophic  = sum(f["catastrophic"] for f in fold_results)
    pfs             = [f["profit_factor"] for f in fold_results if f["n_trades"] >= 3]
    exps            = [f["expectancy"]    for f in fold_results if f["n_trades"] >= 3]
    majority_pf_ok  = sum(p >= DEPLOY_PF for p in pfs) > len(pfs) / 2 if pfs else False
    majority_exp_ok = sum(e > 0 for e in exps) > len(exps) / 2 if exps else False

    wf_pass = (
        total_trades >= MIN_LONG_TRADES_FOR_DEPLOY
        and majority_pf_ok
        and majority_exp_ok
        and n_catastrophic == 0
    )

    reasons = []
    if total_trades < MIN_LONG_TRADES_FOR_DEPLOY:
        reasons.append(f"total_trades={total_trades} < {MIN_LONG_TRADES_FOR_DEPLOY}")
    if not majority_pf_ok:
        bad_pf = [f"{f['year']}: PF={f['profit_factor']:.2f}" for f in fold_results if f['profit_factor'] < DEPLOY_PF and f['n_trades'] >= 3]
        reasons.append(f"majority_pf_failed: {bad_pf}")
    if not majority_exp_ok:
        bad_ex = [f"{f['year']}: E={f['expectancy']:.4f}" for f in fold_results if f['expectancy'] <= 0 and f['n_trades'] >= 3]
        reasons.append(f"majority_exp_failed: {bad_ex}")
    if n_catastrophic > 0:
        cat = [f["year"] for f in fold_results if f["catastrophic"]]
        reasons.append(f"catastrophic_folds={cat}")

    return {
        "folds":            fold_results,
        "total_trades":     total_trades,
        "n_folds":          n_folds,
        "n_deployable_folds": n_deploy,
        "n_catastrophic_folds": n_catastrophic,
        "majority_pf_ok":  majority_pf_ok,
        "majority_exp_ok": majority_exp_ok,
        "walk_forward_pass": wf_pass,
        "reason":          "; ".join(reasons) if reasons else "all criteria met",
        "params": {
            "filter_threshold": filter_threshold,
            "edge_threshold":   edge_threshold,
            "unc_threshold":    unc_threshold,
        },
    }


def print_wf_summary(result: Dict) -> None:
    sep = "─" * 70
    print(f"\n{sep}")
    print("WALK-FORWARD LONG-ONLY — RÉSULTATS")
    print(sep)
    print(f"Walk-forward pass : {'✓ OUI' if result['walk_forward_pass'] else '✗ NON'}")
    if result.get("reason"):
        print(f"Raison : {result['reason']}")
    print(f"Total trades : {result.get('total_trades', 0)}")
    print(f"Folds analysés : {result.get('n_folds', 0)}")
    print(f"Folds déployables : {result.get('n_deployable_folds', 0)}")
    print(f"Folds catastrophiques : {result.get('n_catastrophic_folds', 0)}")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward LONG-only")
    parser.add_argument("--ft",  default=0.51, type=float, help="Filter threshold")
    parser.add_argument("--dt",  default=0.58, type=float, help="Direction threshold")
    parser.add_argument("--uw",  default=0.30, type=float, help="Uncertainty width threshold")
    args = parser.parse_args()

    print("Chargement données et modèles…")
    df     = load_alpha_data()
    models = load_models()

    print(f"\nWalk-forward avec ft={args.ft}, dt={args.dt}, uw={args.uw}")
    result = run_walk_forward(df, models, args.ft, args.dt, args.uw)

    print_wf_summary(result)

    # Sauvegarde
    out_json = result.copy()
    out_json_path = REPORT_DIR / "walk_forward_results.json"
    out_json_path.write_text(json.dumps(out_json, indent=2))

    if result.get("folds"):
        df_folds = pd.DataFrame(result["folds"])
        df_folds.to_csv(REPORT_DIR / "walk_forward_results.csv", index=False)

    print(f"\nRésultats sauvegardés dans {REPORT_DIR}/")


if __name__ == "__main__":
    main()
