#!/usr/bin/env python3
"""
scripts/institutional_walk_forward.py
─────────────────────────────────────────────────────────────────────────────
Walk-forward strict avec expanding window pour validation institutionnelle.

INTERDICTIONS :
  - threshold choisi sur test
  - feature sélectionnée sur test
  - scaler fit sur test
  - calibration sur test

Usage
-----
python3 scripts/institutional_walk_forward.py \
    --engine trend_following \
    --assets BTCUSDT,ETHUSDT \
    --config configs/institutional/walk_forward.yaml \
    --target tb_label \
    --version v1.0
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import yaml
import pandas as pd

from src.institutional.features.feature_store import FeatureStore
from src.institutional.labels.label_store import LabelStore
from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
from src.institutional.backtest.walk_forward import (
    WalkForwardConfig, run_walk_forward,
)
from src.institutional.experiments.experiment_logger import ExperimentLogger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--engine", default="trend_following")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT")
    p.add_argument("--config", default="configs/institutional/walk_forward.yaml")
    p.add_argument("--target", default="tb_label")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--folds", default="2022,2023,2024,2025",
                   help="Années à utiliser comme test folds")
    return p.parse_args()


def default_backtest_fn(model, X_test, y_test):
    """Backtest simplifié pour métriques walk-forward (sans simulation exécution)."""
    from sklearn.metrics import roc_auc_score, log_loss
    proba = model.predict_proba(X_test)
    classes = model._classes if hasattr(model, "_classes") and model._classes is not None else None

    metrics = {}
    try:
        metrics["auc_ovr"] = float(roc_auc_score(
            y_test, proba, multi_class="ovr",
            labels=classes,
        ))
    except Exception:
        pass
    try:
        metrics["logloss"] = float(log_loss(y_test, proba, labels=classes))
    except Exception:
        pass

    # Hit rate sur classe "1" (signal UP)
    if proba.ndim == 2:
        pred_class = model._label_encoder.inverse_transform(proba.argmax(axis=1))
    else:
        pred_class = (proba > 0.5).astype(int)

    mask_signal = pred_class == 1
    if mask_signal.sum() > 0:
        metrics["hit_rate_long"] = float((y_test[mask_signal] == 1).mean())
        metrics["n_signals"] = int(mask_signal.sum())

    return metrics


def main() -> None:
    args = parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",")]
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]
    folds = [y.strip() for y in args.folds.split(",")]

    # Charger la config
    wf_config_dict = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            wf_config_dict = yaml.safe_load(f).get("walk_forward", {})

    wf_config = WalkForwardConfig(
        train_start=wf_config_dict.get("train_start", "2021-01-01"),
        test_periods=folds,
        validation_months=wf_config_dict.get("validation_months", 3),
        embargo_bars=wf_config_dict.get("embargo_bars", 24 * 7),
        mode=wf_config_dict.get("mode", "expanding"),
    )

    logger.info(f"=== INSTITUTIONAL WALK-FORWARD ===")
    logger.info(f"Engine  : {args.engine}")
    logger.info(f"Assets  : {assets}")
    logger.info(f"Folds   : {folds}")
    logger.info(f"Mode    : {wf_config.mode}")

    fs = FeatureStore(version=args.version)
    ls = LabelStore(version=args.version)
    exp_logger = ExperimentLogger()

    for asset in assets:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"[{asset}] Walk-forward {args.engine}")

        try:
            features = fs.load(asset)
            labels = ls.load(asset)
        except FileNotFoundError as e:
            logger.error(f"  {e}")
            continue

        X_full, y_full = ls.prepare_dataset(features, labels, target_col=args.target)

        save_dir = (
            Path("artifacts/institutional/backtests")
            / args.engine / asset / args.version
        )
        save_dir.mkdir(parents=True, exist_ok=True)

        run_id = exp_logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name=f"{args.engine}_wf",
            assets=[asset],
            features_version=args.version,
            labels_version=args.version,
            model_type="LightGBM_WalkForward",
            walk_forward_config={
                "mode": wf_config.mode,
                "folds": folds,
                "embargo_bars": wf_config.embargo_bars,
            },
            cost_config={"cost_bps": 10.0},
        )

        report = run_walk_forward(
            features=X_full,
            labels=y_full.to_frame(args.target),
            target_col=args.target,
            model_factory=lambda: LightGBMClassifier(
                version=args.version,
                asset=asset,
                target=args.target,
            ),
            config=wf_config,
            backtest_fn=default_backtest_fn,
            save_dir=save_dir,
        )

        agg = report.aggregated_metrics
        pass_rate = agg.get("pass_rate", 0)
        decision = "PAPER" if pass_rate >= 0.75 else "INCUBATE" if pass_rate >= 0.50 else "REJECT"

        exp_logger.finish(
            run_id=run_id,
            metrics=agg,
            robustness_tests={"walk_forward": {"pass_rate": pass_rate}},
            decision=decision,
            artifact_paths={"wf_report": str(save_dir / "walk_forward_report.json")},
        )

        logger.info(f"\n[{asset}] Verdict: {decision} (pass_rate={pass_rate:.1%})")

    logger.info("\n=== WALK-FORWARD COMPLETE ===")


if __name__ == "__main__":
    main()
