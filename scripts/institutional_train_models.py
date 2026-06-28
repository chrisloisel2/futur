#!/usr/bin/env python3
"""
scripts/institutional_train_models.py
─────────────────────────────────────────────────────────────────────────────
Entraîne les modèles institutionnels sur un fold donné.

Ordre obligatoire :
  1. Charger features (depuis feature store)
  2. Charger labels (depuis label store)
  3. Split temporel (train/val) — jamais shuffle
  4. Scaler fit UNIQUEMENT sur train
  5. Entraîner baselines (Ridge, Logistic)
  6. Entraîner LightGBM avec early stopping sur val
  7. Comparer vs baselines — rejeter si pas de gain
  8. Sauvegarder le modèle + model card

Usage
-----
python3 scripts/institutional_train_models.py \
    --engine trend_following \
    --assets BTCUSDT,ETHUSDT \
    --train-start 2021-01-01 \
    --train-end 2024-12-31 \
    --val-months 3 \
    --target tb_label \
    --version v1.0
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.features.feature_store import FeatureStore
from src.institutional.labels.label_store import LabelStore
from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
from src.institutional.models.linear.ridge import LogisticBaselineClassifier
from src.institutional.experiments.experiment_logger import ExperimentLogger

import pandas as pd
import numpy as np
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--engine", default="trend_following")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT")
    p.add_argument("--train-start", default="2021-01-01")
    p.add_argument("--train-end", default="2024-12-31")
    p.add_argument("--val-months", type=int, default=3)
    p.add_argument("--target", default="tb_label",
                   help="Colonne cible dans le label store")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--n-estimators", type=int, default=1000)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",")]
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    logger.info(f"=== INSTITUTIONAL TRAIN MODELS ===")
    logger.info(f"Engine  : {args.engine}")
    logger.info(f"Assets  : {assets}")
    logger.info(f"Target  : {args.target}")
    logger.info(f"Period  : {args.train_start} → {args.train_end}")

    fs = FeatureStore(version=args.version)
    ls = LabelStore(version=args.version)
    exp_logger = ExperimentLogger()

    for asset in assets:
        logger.info(f"\n{'=' * 50}")
        logger.info(f"[{asset}] Training {args.engine}")

        # Charger features et labels
        try:
            features = fs.load(asset)
            labels = ls.load(asset)
        except FileNotFoundError as e:
            logger.error(f"  {e} — run build_features/labels d'abord")
            continue

        # Préparer dataset
        X, y = ls.prepare_dataset(features, labels, target_col=args.target)
        logger.info(f"  Dataset: {len(X)} lignes × {len(X.columns)} features")
        logger.info(f"  Classes: {y.value_counts().to_dict()}")

        # Split temporel train/val
        val_cutoff = pd.Timestamp(args.train_end) - pd.DateOffset(months=args.val_months)
        train_end = pd.Timestamp(args.train_end)

        X = X.loc[args.train_start:args.train_end]
        y = y.loc[args.train_start:args.train_end]

        X_train = X[:val_cutoff]
        y_train = y[:val_cutoff]
        X_val = X[val_cutoff:]
        y_val = y[val_cutoff:]

        logger.info(f"  Train: {len(X_train)} | Val: {len(X_val)}")

        if len(X_train) < 500:
            logger.warning(f"  Train trop petit ({len(X_train)} < 500) — skip")
            continue

        # Start experiment
        run_id = exp_logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name=f"{args.engine}_{asset}",
            assets=[asset],
            features_version=args.version,
            labels_version=args.version,
            model_type="LightGBM+Logistic",
            train_period={"start": args.train_start, "end": str(val_cutoff.date())},
            validation_period={"start": str(val_cutoff.date()), "end": args.train_end},
        )

        # 1. Baseline logistic
        logger.info(f"  Entraînement baseline Logistic...")
        baseline = LogisticBaselineClassifier(
            version=args.version, asset=asset, target=args.target
        )
        baseline.fit(X_train, y_train, X_val=X_val, y_val=y_val)
        baseline_auc = baseline.card.validation_metrics.get("auc_ovr", 0)
        logger.info(f"  Baseline AUC (val): {baseline_auc:.4f}")

        # 2. LightGBM
        logger.info(f"  Entraînement LightGBM...")
        lgbm = LightGBMClassifier(
            version=args.version,
            asset=asset,
            target=args.target,
            n_estimators=args.n_estimators,
        )
        lgbm.fit(X_train, y_train, X_val=X_val, y_val=y_val)
        lgbm_auc = lgbm.card.validation_metrics.get("auc_ovr", 0)
        logger.info(f"  LightGBM AUC (val): {lgbm_auc:.4f}")

        # 3. Comparaison baseline
        improvement = lgbm_auc - baseline_auc
        if improvement < -0.01:
            logger.warning(
                f"  LightGBM ne bat pas le baseline "
                f"({lgbm_auc:.4f} vs {baseline_auc:.4f}) — vérifier les features"
            )

        # 4. Sauvegarde
        save_dir = Path("artifacts/institutional/models") / args.engine / asset
        lgbm.save(save_dir / f"{args.version}_lgbm.pkl")
        baseline.save(save_dir / f"{args.version}_logistic.pkl")

        # 5. Top features
        imp = lgbm.feature_importance()
        top10 = sorted(imp.items(), key=lambda x: -x[1])[:10]
        logger.info(f"  Top 5 features: {top10[:5]}")

        # Finaliser l'expérience
        exp_logger.finish(
            run_id=run_id,
            metrics={
                "auc_ovr_val": lgbm_auc,
                "baseline_auc_val": baseline_auc,
                "improvement": improvement,
            },
            robustness_tests={},
            decision="INCUBATE" if lgbm_auc > 0.55 else "REJECT",
            artifact_paths={
                "lgbm_model": str(save_dir / f"{args.version}_lgbm.pkl"),
                "logistic_model": str(save_dir / f"{args.version}_logistic.pkl"),
            },
        )

    logger.info("\n=== TRAINING COMPLETE ===")


if __name__ == "__main__":
    main()
