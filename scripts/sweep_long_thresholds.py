#!/usr/bin/env python3
"""
scripts/sweep_long_thresholds.py — SWEEP DE SEUILS LONG-ONLY
============================================================

Teste toutes les combinaisons de seuils LONG et identifie
si une combinaison remplit les critères de déploiement.

Règle : ne pas abaisser les seuils pour maquiller les résultats.
Règle : n_trades < 50 → never deployable, quel que soit le PF.

Usage :
  python scripts/sweep_long_thresholds.py
  python scripts/sweep_long_thresholds.py --since 2021-01-01
  python scripts/sweep_long_thresholds.py --top-n 30
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from itertools import product
from pathlib import Path

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


def detect_uncertainty_thresholds(df: pd.DataFrame) -> list:
    """Calcule les quantiles de rv_24 pour calibrer le seuil d'incertitude."""
    if "rv_24" not in df.columns:
        return [0.20, 0.25, 0.30, 0.35, 0.40]
    rv = df["rv_24"].dropna()
    # width_approx = rv * 6 (mapping dans generate_signals)
    widths = (rv * 6.0).clip(0, 1)
    return [
        round(float(np.quantile(widths, q)), 3)
        for q in [0.50, 0.60, 0.70, 0.80, 0.90]
    ]


def run_sweep(
    df: pd.DataFrame,
    models,
    since: str = "2024-01-01",
    top_n: int = 20,
) -> tuple:
    df_test = df[df["datetime"] >= pd.Timestamp(since, tz="UTC")].copy()
    print(f"  Test set : {len(df_test):,} barres | {df_test['datetime'].min().date()} → {df_test['datetime'].max().date()}")

    unc_thresholds = detect_uncertainty_thresholds(df_test)
    print(f"  Incertitude width thresholds (quantiles rv_24×6): {unc_thresholds}")

    grid = {
        "filter_threshold":   [0.45, 0.48, 0.51, 0.54, 0.57],
        "dir_threshold":      [0.52, 0.55, 0.58, 0.61, 0.64],
        "uncertainty_width":  unc_thresholds,
    }

    total = (
        len(grid["filter_threshold"])
        * len(grid["dir_threshold"])
        * len(grid["uncertainty_width"])
    )
    print(f"  Combinaisons à tester : {total}")

    results = []
    params = BacktestParams()

    for idx, (ft, dt, uw) in enumerate(
        product(grid["filter_threshold"], grid["dir_threshold"], grid["uncertainty_width"])
    ):
        if (idx + 1) % 25 == 0:
            print(f"  [{idx+1}/{total}] ft={ft} dt={dt} uw={uw}")

        df_sig = generate_signals(
            df_test, models,
            filter_threshold=ft,
            edge_threshold=dt,
            uncertainty_width_threshold=uw,
        )
        m = run_backtest_core(df_sig, params)

        row = {
            "filter_threshold":      ft,
            "dir_threshold":         dt,
            "uncertainty_width_thr": uw,
            "n_trades":              m.get("n_trades", 0),
            "profit_factor":         m.get("profit_factor", 0),
            "expectancy":            m.get("expectancy", 0),
            "max_drawdown_pct":      m.get("max_drawdown_pct", 0),
            "sharpe":                m.get("sharpe", 0),
            "sortino":               m.get("sortino", 0),
            "calmar":                m.get("calmar", 0),
            "win_rate":              m.get("win_rate", 0),
            "total_return_pct":      m.get("total_return_pct", 0),
            "exposure_pct":          m.get("exposure_pct", 0),
            "turnover":              m.get("turnover", 0),
            "deployable":            m.get("deployable", False),
            "status":                m.get("status", "unknown"),
            "rejection_reason":      m.get("reason", ""),
            "yearly_profit_factor":  json.dumps(m.get("yearly_profit_factor", {})),
        }
        results.append(row)

    df_results = pd.DataFrame(results)

    # Trier par priorité : expectancy > 0, PF >= 1.2, n_trades >= 50, DD <= 12
    df_results["score"] = (
        (df_results["expectancy"] > 0).astype(int) * 1000
        + (df_results["profit_factor"] >= 1.20).astype(int) * 100
        + (df_results["n_trades"] >= MIN_LONG_TRADES_FOR_DEPLOY).astype(int) * 10
        + (df_results["max_drawdown_pct"].abs() <= 12.0).astype(int) * 5
        - df_results["max_drawdown_pct"].abs()
        + df_results["expectancy"].clip(-1, 5)
    )
    df_results = df_results.sort_values("score", ascending=False).reset_index(drop=True)

    # Sauvegarde CSV complet
    df_results.drop(columns=["score"]).to_csv(REPORT_DIR / "threshold_sweep.csv", index=False)

    # Top N en JSON
    top = df_results.head(top_n).drop(columns=["score"]).to_dict(orient="records")
    (REPORT_DIR / "threshold_sweep_top20.json").write_text(json.dumps(top, indent=2))

    return df_results, top


def print_sweep_report(df_results: pd.DataFrame, top_n: int = 10) -> None:
    total      = len(df_results)
    deployable = df_results["deployable"].sum()
    enough_tr  = (df_results["n_trades"] >= MIN_LONG_TRADES_FOR_DEPLOY).sum()
    pf_ok      = (df_results["profit_factor"] >= 1.20).sum()
    exp_pos    = (df_results["expectancy"] > 0).sum()

    sep = "─" * 70
    print(f"\n{sep}")
    print(f"SWEEP DE SEUILS LONG-ONLY — {total} combinaisons testées")
    print(sep)
    print(f"Déployables (toutes gates) : {deployable} / {total}")
    print(f"n_trades >= {MIN_LONG_TRADES_FOR_DEPLOY}          : {enough_tr} / {total}")
    print(f"Profit Factor >= 1.20     : {pf_ok} / {total}")
    print(f"Expectancy > 0            : {exp_pos} / {total}")
    print(sep)

    top = df_results.head(top_n)
    print(f"\nTOP {top_n} combinaisons (triées par score) :")
    print(f"{'FT':>5} {'DT':>5} {'UW':>5} {'Trades':>7} {'PF':>6} {'E/trade':>8} {'DD%':>7} {'Sharpe':>7} {'Deploy':>8}")
    print("─" * 70)
    for _, r in top.iterrows():
        dep = "✓ OUI" if r["deployable"] else "✗ NON"
        n_flag = f"⚠{r['n_trades']}" if r["n_trades"] < MIN_LONG_TRADES_FOR_DEPLOY else str(int(r["n_trades"]))
        print(
            f"{r['filter_threshold']:>5.2f} {r['dir_threshold']:>5.2f} {r['uncertainty_width_thr']:>5.3f}"
            f" {n_flag:>7} {r['profit_factor']:>6.3f} {r['expectancy']:>+8.4f}"
            f" {r['max_drawdown_pct']:>7.2f} {r['sharpe']:>7.3f} {dep:>8}"
        )

    # Verdict global
    print(sep)
    if deployable > 0:
        best = df_results[df_results["deployable"]].iloc[0]
        print(f"✓ VALIDATION PARTIELLE : {deployable} combinaison(s) remplissent les gates.")
        print(f"  Meilleure : ft={best['filter_threshold']} dt={best['dir_threshold']}"
              f" → n_trades={int(best['n_trades'])}, PF={best['profit_factor']:.3f}")
    elif enough_tr > 0:
        best = df_results[df_results["n_trades"] >= MIN_LONG_TRADES_FOR_DEPLOY].iloc[0]
        print(f"✗ INSUFFISANT : {enough_tr} combinaison(s) ont >= {MIN_LONG_TRADES_FOR_DEPLOY} trades")
        print(f"  mais aucune ne valide toutes les gates.")
        print(f"  Meilleure avec {int(best['n_trades'])} trades : PF={best['profit_factor']:.3f}")
    else:
        print(f"✗ REJETÉ : aucune combinaison n'atteint {MIN_LONG_TRADES_FOR_DEPLOY} trades.")
        print(f"  Maximum observé : {int(df_results['n_trades'].max())} trades.")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep de seuils LONG-only")
    parser.add_argument("--since",  default="2024-01-01", help="Date début test (ISO)")
    parser.add_argument("--top-n",  default=20, type=int,  help="Nombre de combinaisons top à sauvegarder")
    args = parser.parse_args()

    print("Chargement des données et des modèles…")
    df     = load_alpha_data()
    models = load_models()

    print(f"Sweep de {len(list(product([0]*5, [0]*5, [0]*5)))} combinaisons…")
    df_results, top = run_sweep(df, models, since=args.since, top_n=args.top_n)

    print_sweep_report(df_results, top_n=min(10, args.top_n))

    print(f"\nRésultats sauvegardés dans {REPORT_DIR}/")
    print(f"  threshold_sweep.csv           ({len(df_results)} lignes)")
    print(f"  threshold_sweep_top20.json    ({min(args.top_n, len(df_results))} lignes)")


if __name__ == "__main__":
    main()
