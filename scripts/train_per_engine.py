#!/usr/bin/env python3
"""
scripts/train_per_engine.py
─────────────────────────────────────────────────────────────────────────────
Entraîne les modèles par moteur avec walk-forward strict + ModelSelector.

CHANGEMENTS v2 :
  - LightGBM GBDT (plus DART) avec early stopping fonctionnel
  - ModelSelector : compare Logistic vs LightGBM sur val, choisit le meilleur
  - Event metrics : PR-AUC, precision@k, expectancy@k pour labels rares
  - Meilleur modèle par fold sauvegardé + reporté

Split walk-forward (expanding window) :
    2022 : test fold 1  (train=2021, val=Q4-2021)
    2023 : test fold 2  (train=2021-2022, val=Q4-2022)
    2024 : test fold 3  (train=2021-2023, val=Q4-2023)
    2025 : test fold 4  (train=2021-2024, val=Q4-2024)

Usage :
    python3 scripts/train_per_engine.py --engine btc_eth_trend --target trend_cont_24h
    python3 scripts/train_per_engine.py --engine trm_event --target event_cont_4h
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.dataset_builder import EngineDatasetBuilder
from src.institutional.models.model_selector import ModelSelector
from src.institutional.evaluation.event_metrics import compute_event_evaluation
from src.institutional.experiments.experiment_logger import ExperimentLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


FOLDS = [
    {"train_start": "2021-01-01", "train_end": "2021-09-30",
     "val_start":   "2021-10-01", "val_end":   "2021-12-31",
     "test_start":  "2022-01-01", "test_end":  "2022-12-31"},

    {"train_start": "2021-01-01", "train_end": "2022-09-30",
     "val_start":   "2022-10-01", "val_end":   "2022-12-31",
     "test_start":  "2023-01-01", "test_end":  "2023-12-31"},

    {"train_start": "2021-01-01", "train_end": "2023-09-30",
     "val_start":   "2023-10-01", "val_end":   "2023-12-31",
     "test_start":  "2024-01-01", "test_end":  "2024-12-31"},

    {"train_start": "2021-01-01", "train_end": "2024-09-30",
     "val_start":   "2024-10-01", "val_end":   "2024-12-31",
     "test_start":  "2025-01-01", "test_end":  "2025-12-31"},
]

EXCLUDE_COLS  = {"asset", "engine", "vol_annual"}
LABEL_PREFIXES = ("trend_cont_", "event_cont_", "carry_net_", "vol_h_", "threshold_", "fwd_ret_")


def get_feature_cols(df: pd.DataFrame, target: str) -> list:
    label_cols = {c for c in df.columns if any(c.startswith(p) for p in LABEL_PREFIXES)}
    return [
        c for c in df.columns
        if c not in EXCLUDE_COLS
        and c not in label_cols
        and c != target
    ]


def _prepare_xy(
    df: pd.DataFrame,
    target: str,
    start: str,
    end: str,
    fwd_ret_col: str = None,
) -> tuple:
    """Prépare X, y (et optionnellement fwd_ret) pour une période."""
    subset = df.loc[start:end].copy().dropna(subset=[target])
    feat_cols = get_feature_cols(subset, target)

    leaked = [c for c in feat_cols if any(c.startswith(p) for p in LABEL_PREFIXES)]
    if leaked:
        raise ValueError(f"DATA LEAKAGE: {leaked}")

    X = subset[feat_cols].fillna(0)
    y = subset[target].astype(int)

    fwd_ret = None
    if fwd_ret_col and fwd_ret_col in subset.columns:
        fwd_ret = subset[fwd_ret_col].reindex(y.index)

    return X, y, fwd_ret


def _is_rare_label(y: pd.Series, threshold: float = 0.10) -> bool:
    """True si la classe positive représente moins de `threshold` des données."""
    counts = y.value_counts(normalize=True)
    return float(counts.get(1, 0)) < threshold


def run_walk_forward(
    df:           pd.DataFrame,
    target:       str,
    asset:        str,
    engine_name:  str,
    n_estimators: int = 500,
    save_dir:     Path = None,
) -> list:
    """Exécute le walk-forward avec ModelSelector et event metrics."""
    from sklearn.metrics import roc_auc_score

    # Inférer colonne fwd_ret correspondante
    fwd_ret_col = None
    if target.startswith("trend_cont_"):
        h = target.split("_")[-1]
        fwd_ret_col = f"fwd_ret_{h}"
    elif target.startswith("event_cont_"):
        h = target.split("_")[-1]
        fwd_ret_col = f"fwd_ret_{h}"

    fold_results = []

    for fold in FOLDS:
        test_year = fold["test_start"][:4]
        logger.info(f"\n  Fold {test_year}: "
                    f"train={fold['train_start']}:{fold['train_end']}  "
                    f"val={fold['val_start']}:{fold['val_end']}  "
                    f"test={fold['test_start']}:{fold['test_end']}")

        X_tr, y_tr, _     = _prepare_xy(df, target, fold["train_start"], fold["train_end"], fwd_ret_col)
        X_va, y_va, _     = _prepare_xy(df, target, fold["val_start"],   fold["val_end"],   fwd_ret_col)
        X_te, y_te, fwd_te = _prepare_xy(df, target, fold["test_start"],  fold["test_end"],  fwd_ret_col)

        if len(X_tr) < 200 or len(X_te) < 50:
            logger.warning(f"  Fold {test_year}: données insuffisantes — skip")
            continue

        logger.info(f"    n_train={len(X_tr):,}  n_val={len(X_va):,}  n_test={len(X_te):,}")
        logger.info(f"    y_train : {dict(y_tr.value_counts().sort_index())}")

        # ── Choisir la métrique de sélection selon le type de label ──────────
        rare = _is_rare_label(y_tr)
        primary_metric = "pr_auc_up" if rare else "auc_ovr"
        logger.info(f"    Rare label: {rare} → sélection par {primary_metric}")

        # ── ModelSelector : compare Logistic vs LightGBM ─────────────────────
        selector = ModelSelector(
            primary_metric=primary_metric,
            asset=asset,
            target=target,
            n_estimators=n_estimators,
        )
        sel_result = selector.select(X_tr, y_tr, X_va, y_va, fold_id=test_year)
        best_model = sel_result.selected_model

        # ── Évaluation test ───────────────────────────────────────────────────
        proba_te = best_model.predict_proba(X_te)
        classes  = best_model._classes if hasattr(best_model, "_classes") and best_model._classes is not None else np.unique(y_te)

        try:
            test_auc = float(roc_auc_score(y_te, proba_te, multi_class="ovr", labels=classes))
        except Exception:
            test_auc = 0.5

        # ── Event metrics (tous les cas) ─────────────────────────────────────
        event_eval = compute_event_evaluation(
            y_true=y_te,
            proba=proba_te,
            classes=classes,
            asset=asset,
            fold_id=test_year,
            fwd_ret=fwd_te,
            cost_bps=10.0,
        )
        event_eval.print()

        # ── Critère de pass ───────────────────────────────────────────────────
        if rare:
            # Pour labels rares : critère sur PR-AUC et precision@5%
            pass_fold = (
                event_eval.pr_auc_up > event_eval.prevalence_up * 1.5  # 50% au-dessus du hasard
                and event_eval.precision_at_5pct > event_eval.prevalence_up * 2.0  # 2× le hasard
            )
            test_score = event_eval.pr_auc_up
        else:
            pass_fold = test_auc >= 0.57
            test_score = test_auc

        logger.info(
            f"    Selected: {sel_result.selected_model_name}  "
            f"val={sel_result.winner_val_score:.4f}  "
            f"test={test_score:.4f}  "
            f"{'✓' if pass_fold else '✗'}"
        )

        # ── Sauvegarder le meilleur modèle ────────────────────────────────────
        if save_dir:
            fold_dir = save_dir / test_year
            fold_dir.mkdir(parents=True, exist_ok=True)
            best_model.save(fold_dir / f"model_{test_year}.pkl")

        fold_results.append({
            "fold":           test_year,
            "n_train":        len(X_tr),
            "n_val":          len(X_va),
            "n_test":         len(X_te),
            "selected_model": sel_result.selected_model_name,
            "val_score":      round(sel_result.winner_val_score, 4),
            "test_auc":       round(test_auc, 4),
            "test_pr_auc_up": round(event_eval.pr_auc_up, 4),
            "prec_at_5pct":   round(event_eval.precision_at_5pct, 4),
            "prevalence_up":  round(event_eval.prevalence_up, 4),
            "pass":           pass_fold,
            "rare_label":     rare,
            "primary_metric": primary_metric,
        })

    return fold_results


def print_summary(fold_results: list, asset: str, target: str) -> None:
    if not fold_results:
        print(f"  Aucun résultat pour {asset}/{target}")
        return

    rare = fold_results[0].get("rare_label", False)
    print(f"\n{'═'*80}")
    print(f"WALK-FORWARD — {asset} / {target}")
    print(f"{'─'*80}")

    if rare:
        print(f"{'Fold':6s} {'Train':>7s} {'Selected':>16s} {'Val':>7s} {'TestAUC':>9s} {'PR-AUC UP':>10s} {'Prec@5%':>8s} {'Pass':>5s}")
    else:
        print(f"{'Fold':6s} {'Train':>7s} {'Selected':>16s} {'Val':>7s} {'TestAUC':>9s} {'Pass':>5s}")
    print(f"{'─'*80}")

    n_pass = 0
    for r in fold_results:
        p = "✓" if r["pass"] else "✗"
        if rare:
            prev = r["prevalence_up"]
            print(f"  {r['fold']}  {r['n_train']:>7,}  {r['selected_model']:>16s}  "
                  f"{r['val_score']:>7.4f}  {r['test_auc']:>9.4f}  "
                  f"{r['test_pr_auc_up']:>10.4f}  {r['prec_at_5pct']:>8.4f}  {p}")
        else:
            print(f"  {r['fold']}  {r['n_train']:>7,}  {r['selected_model']:>16s}  "
                  f"{r['val_score']:>7.4f}  {r['test_auc']:>9.4f}  {p}")
        n_pass += r["pass"]

    print(f"{'─'*80}")

    test_aucs = [r["test_auc"] for r in fold_results]
    if rare:
        pr_aucs = [r["test_pr_auc_up"] for r in fold_results]
        print(f"  AUC test médiane   : {np.median(test_aucs):.4f}")
        print(f"  PR-AUC UP médiane  : {np.median(pr_aucs):.4f}")
        print(f"  Prévalence UP      : {np.mean([r['prevalence_up'] for r in fold_results]):.1%}")
        print(f"  Lift PR-AUC médian : {np.median(pr_aucs) / np.mean([r['prevalence_up'] for r in fold_results]):.2f}×")
    else:
        print(f"  AUC test médiane   : {np.median(test_aucs):.4f}")

    print(f"  Pass rate          : {n_pass}/{len(fold_results)}")

    verdict = "PAPER" if n_pass >= 3 else "INCUBATE" if n_pass >= 2 else "REJECT"
    print(f"  Verdict            : {verdict}")
    print(f"{'═'*80}\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--engine",       default="btc_eth_trend")
    p.add_argument("--target",       default="trend_cont_24h")
    p.add_argument("--assets",       default=None)
    p.add_argument("--start",        default="2021-01-01")
    p.add_argument("--end",          default="2025-12-31")
    p.add_argument("--n-estimators", type=int, default=500)
    p.add_argument("--skip-build",   action="store_true")
    args = p.parse_args()

    from src.institutional.data.dataset_builder import (
        btc_eth_trend_config, trm_event_config, carry_config,
    )
    CONFIG_MAP = {
        "btc_eth_trend": btc_eth_trend_config,
        "trm_event":     trm_event_config,
        "carry":         carry_config,
    }

    if args.engine not in CONFIG_MAP:
        logger.error(f"Moteur inconnu: {args.engine!r}. Options: {list(CONFIG_MAP)}")
        sys.exit(1)

    config = CONFIG_MAP[args.engine](start=args.start, end=args.end)
    assets = (
        [a.strip().upper() for a in args.assets.split(",")]
        if args.assets else list(config.assets)
    )
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    logger.info(f"Moteur  : {args.engine.upper()}")
    logger.info(f"Target  : {args.target}")
    logger.info(f"Assets  : {assets}")
    logger.info(f"Période : {args.start} → {args.end}")

    builder = EngineDatasetBuilder()
    exp_log = ExperimentLogger()

    for asset in assets:
        logger.info(f"\n{'━'*55}")
        logger.info(f"Asset : {asset}")

        # Charger le dataset
        if args.skip_build:
            df = builder.load(config.engine_name, asset, args.start, args.end)
        else:
            from src.institutional.data.dataset_builder import EngineDatasetConfig
            sc = EngineDatasetConfig(
                engine_name=config.engine_name, assets=[asset],
                start=config.start, end=config.end,
                feature_families=config.feature_families,
                label_family=config.label_family,
                label_horizons_h=config.label_horizons_h,
                label_k=config.label_k, label_cost_bps=config.label_cost_bps,
                include_funding=config.include_funding, include_oi=config.include_oi,
            )
            ds = builder.build(sc, validate_quality=False)
            if asset not in ds:
                logger.error(f"  {asset}: dataset non construit")
                continue
            df = ds[asset]

        if args.target not in df.columns:
            avail = [c for c in df.columns if any(c.startswith(p) for p in LABEL_PREFIXES)]
            logger.error(f"  Target {args.target!r} absent. Labels: {avail}")
            continue

        save_dir = Path(f"artifacts/institutional/backtests/{args.engine}/{asset}/v1.0")

        run_id = exp_log.start(
            engine_name=config.engine_name,
            signal_name=f"{args.engine}_{args.target}_{asset}",
            assets=(asset,),
            model_type="ModelSelector_WalkForward",
            train_period={"start": args.start, "end": args.end},
            notes=f"GBDT + ModelSelector + event_metrics",
        )

        fold_results = run_walk_forward(
            df, args.target, asset,
            engine_name=config.engine_name,
            n_estimators=args.n_estimators,
            save_dir=save_dir,
        )

        print_summary(fold_results, asset, args.target)

        n_pass  = sum(r["pass"] for r in fold_results)
        verdict = "PAPER" if n_pass >= 3 else "INCUBATE" if n_pass >= 2 else "REJECT"

        exp_log.finish(
            run_id=run_id,
            metrics={
                "test_auc_median": float(np.median([r["test_auc"] for r in fold_results])) if fold_results else 0.0,
                "n_pass": n_pass,
            },
            robustness_tests={"walk_forward_folds": fold_results},
            decision=verdict,
        )

    logger.info("Training terminé.")


if __name__ == "__main__":
    main()
