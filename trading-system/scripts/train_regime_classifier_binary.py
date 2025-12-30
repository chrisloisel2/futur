"""
Training Script for BINARY Regime Classifier - PRODUCTION EXCELLENCE VERSION

This version implements:
1. ✅ Fixed Brier score calculation (binary, not multiclass)
2. ✅ Temporal split with embargo (no leakage)
3. ✅ Label builder with gray zone (calm/reversal/gray)
4. ✅ Threshold search to optimize recalls
5. ✅ 3 model variants comparison
6. ✅ Excellence artifact bundle
7. ✅ Sanity checks and reliability curves

Usage:
    python scripts/train_regime_classifier_binary.py \
        --start-date 2019-01-01 \
        --end-date 2023-12-31 \
        --symbol BTCUSDT \
        --output artifacts/models/regime/prod

Target Performance (BINARY):
    - Accuracy > 60%
    - Calm recall > 50%
    - Reversal recall > 50%
    - ECE < 0.10
    - Brier < 0.20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Add project paths
BASE_DIR = Path(__file__).resolve().parent.parent
PARENT_DIR = BASE_DIR.parent
for path in (
    BASE_DIR / "src",
    BASE_DIR / "ai/models/training/common",
    PARENT_DIR / "ai/models/training/common",
):
    if path.exists():
        sys.path.insert(0, str(path))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from sklearn.preprocessing import StandardScaler

# Import corrected modules
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    find_optimal_threshold,
    sanity_check_metrics,
    DEFAULT_CLASSES,
)
from production_gates import RegimeClassifierGates
from label_builder import build_binary_regime_labels, validate_label_quality, LabelConfig

logger = get_logger(__name__)


def temporal_split_with_embargo(
    df: pd.DataFrame,
    train_end_date: str = "2022-12-31",
    val_start_date: str = "2023-01-01",
    embargo_minutes: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split data temporally with embargo zone.

    Args:
        df: DataFrame with 'datetime' column
        train_end_date: Last date in training set
        val_start_date: First date in validation set
        embargo_minutes: Minutes to exclude around boundary

    Returns:
        (train_df, val_df)
    """
    # Convert to datetime if needed
    if 'datetime' not in df.columns:
        raise ValueError("DataFrame must have 'datetime' column")

    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df['datetime']):
        df['datetime'] = pd.to_datetime(df['datetime'])

    # Handle timezone: use UTC if df has timezone
    tz = df['datetime'].dt.tz if hasattr(df['datetime'].dt, 'tz') else None
    train_end = pd.to_datetime(train_end_date, utc=True) if tz else pd.to_datetime(train_end_date)
    val_start = pd.to_datetime(val_start_date, utc=True) if tz else pd.to_datetime(val_start_date)

    # Create embargo zone
    embargo_before = train_end - pd.Timedelta(minutes=embargo_minutes)
    embargo_after = val_start + pd.Timedelta(minutes=embargo_minutes)

    # Split
    train_mask = (df['datetime'] <= embargo_before)
    val_mask = (df['datetime'] >= embargo_after)

    train_df = df[train_mask].copy()
    val_df = df[val_mask].copy()

    n_embargo = len(df) - len(train_df) - len(val_df)

    logger.info({
        "msg": "Temporal split with embargo",
        "train_samples": len(train_df),
        "val_samples": len(val_df),
        "embargo_samples": n_embargo,
        "train_end": train_end.strftime("%Y-%m-%d"),
        "val_start": val_start.strftime("%Y-%m-%d"),
        "embargo_minutes": embargo_minutes,
    })

    return train_df, val_df


def extract_features_and_labels(
    df: pd.DataFrame,
    label_config: LabelConfig = None,
    fit_labels_on_train: bool = True,
) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """
    Extract features and build binary labels with gray zone.

    Args:
        df: Raw DataFrame
        label_config: Label configuration
        fit_labels_on_train: If True, fit label thresholds on first 80% only

    Returns:
        (features_df, labels, label_stats)
    """
    # Build labels with gray zone
    labels, label_stats = build_binary_regime_labels(
        df, config=label_config, fit_on_train_only=fit_labels_on_train
    )

    # Validate label quality
    is_valid, warning = validate_label_quality(label_stats, label_config)
    if not is_valid:
        logger.warning({"msg": "Label quality warning", "warning": warning})

    logger.info({
        "msg": "Label statistics",
        "n_calm": label_stats['n_calm'],
        "n_reversal": label_stats['n_reversal'],
        "n_gray": label_stats['n_gray'],
        "prop_calm": f"{label_stats['prop_calm']:.2%}",
        "prop_reversal": f"{label_stats['prop_reversal']:.2%}",
        "prop_gray": f"{label_stats['prop_gray']:.2%}",
    })

    # Filter out gray zone
    keep_mask = (labels != -1)
    df_filtered = df[keep_mask].copy()
    labels_filtered = labels[keep_mask]

    # Extract features
    exclude_cols = {
        'datetime', 'open_time', 'close_time', 'timestamp', 'event_time',
        'label_policy', 'label_tradeable', 'symbol',
        'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote',
    }

    feature_cols = [
        c for c in df_filtered.columns
        if c not in exclude_cols and pd.api.types.is_numeric_dtype(df_filtered[c])
    ]

    features_df = df_filtered[feature_cols].copy()

    # Clean NaNs
    mask_valid = ~(features_df.isna().any(axis=1) | pd.isna(labels_filtered))
    features_df = features_df[mask_valid]
    labels_filtered = labels_filtered[mask_valid]

    logger.info({
        "msg": "Features extracted",
        "feature_cols": len(feature_cols),
        "samples_after_cleaning": len(features_df),
        "label_distribution": {
            "calm": int(np.sum(labels_filtered == 0)),
            "reversal": int(np.sum(labels_filtered == 1)),
        },
    })

    return features_df, labels_filtered, label_stats


def train_and_compare_variants(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    class_names: list[str],
) -> dict:
    """
    Train and compare 3 model variants.

    Returns:
        Dict with variant results
    """
    variants = ["sgd_no_weight", "sgd_focal", "logreg"]
    results = {}

    print("\n" + "=" * 80)
    print("TRAINING 3 MODEL VARIANTS")
    print("=" * 80)

    for variant in variants:
        print(f"\n>>> Training variant: {variant}")

        # Train
        clf = train_calibrated_regime_classifier(
            X_train, y_train,
            class_names=class_names,
            variant=variant,
            calibration_method="isotonic",
        )

        # Find optimal threshold
        y_proba = clf.predict_proba(X_val)
        best_threshold, threshold_metrics = find_optimal_threshold(
            y_val, y_proba[:, 1],
            min_recall_per_class=0.50,
            metric="balanced_accuracy",
        )

        # Evaluate with optimal threshold
        metrics = evaluate_regime_classifier(
            clf, X_val, y_val,
            class_names=class_names,
            threshold=best_threshold,
        )

        # Sanity checks
        is_sane, warnings = sanity_check_metrics(metrics)

        results[variant] = {
            'model': clf,
            'threshold': best_threshold,
            'threshold_metrics': threshold_metrics,
            'metrics': metrics,
            'sanity_warnings': warnings,
            'is_sane': is_sane,
        }

        # Print summary
        print(f"  Threshold:          {best_threshold:.3f}")
        print(f"  Accuracy:           {metrics['accuracy']:.4f}")
        print(f"  Balanced Acc:       {metrics['balanced_accuracy']:.4f}")
        print(f"  Macro F1:           {metrics['macro_f1']:.4f}")
        print(f"  Brier:              {metrics['brier']:.4f}")
        print(f"  ECE:                {metrics['ece']:.4f}")
        print(f"  Calm recall:        {metrics['recall_per_class']['calm']:.4f}")
        print(f"  Reversal recall:    {metrics['recall_per_class']['reversal']:.4f}")
        print(f"  Reversal precision: {metrics['precision_per_class']['reversal']:.4f}")
        print(f"  PR-AUC reversal:    {metrics['pr_auc_reversal']:.4f}")
        print(f"  True rate:          {metrics['true_rate_reversal']:.4f}")
        print(f"  Pred rate:          {metrics['pred_rate_reversal']:.4f}")
        print(f"  Rate ratio:         {metrics['rate_ratio']:.4f}")

        if not is_sane:
            print(f"  ⚠️  Sanity warnings: {'; '.join(warnings)}")

    return results


def select_best_variant(results: dict, gates: RegimeClassifierGates) -> tuple[str, dict]:
    """
    Select best variant based on gates and metrics.

    Priority:
    1. Passes gates
    2. Highest balanced accuracy among passing variants
    3. If none pass, select best balanced accuracy

    Returns:
        (best_variant_name, best_result)
    """
    passing_variants = []
    failing_variants = []

    for variant, result in results.items():
        passed, reason = gates.validate(result['metrics'])
        if passed:
            passing_variants.append((variant, result))
        else:
            failing_variants.append((variant, result, reason))

    if passing_variants:
        # Select best among passing
        best_variant, best_result = max(
            passing_variants,
            key=lambda x: x[1]['metrics']['balanced_accuracy']
        )
        logger.info({
            "msg": "Best variant selected (PASSED GATES)",
            "variant": best_variant,
            "balanced_accuracy": best_result['metrics']['balanced_accuracy'],
        })
    else:
        # No variant passes, select best anyway
        best_variant, best_result = max(
            results.items(),
            key=lambda x: x[1]['metrics']['balanced_accuracy']
        )
        logger.warning({
            "msg": "No variant passes gates, selecting best anyway",
            "variant": best_variant,
            "balanced_accuracy": best_result['metrics']['balanced_accuracy'],
        })

    return best_variant, best_result


def save_excellence_bundle(
    output_dir: Path,
    model,
    threshold: float,
    metrics: dict,
    feature_cols: list,
    label_stats: dict,
    variant: str,
    passed_gates: bool,
):
    """
    Save production excellence bundle.

    Artifacts:
    - model.pkl
    - threshold.json
    - metrics.json
    - feature_list.json
    - data_contract.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save model
    import joblib
    model_path = output_dir / "model.pkl"
    joblib.dump(model, model_path)

    # Save threshold
    threshold_path = output_dir / "threshold.json"
    with open(threshold_path, 'w') as f:
        json.dump({
            'threshold': threshold,
            'calm_recall': metrics['recall_per_class']['calm'],
            'reversal_recall': metrics['recall_per_class']['reversal'],
            'balanced_accuracy': metrics['balanced_accuracy'],
        }, f, indent=2)

    # Save metrics
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, 'w') as f:
        metrics_json = {
            k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in metrics.items()
        }
        json.dump(metrics_json, f, indent=2)

    # Save feature list
    features_path = output_dir / "feature_list.json"
    with open(features_path, 'w') as f:
        json.dump({'features': feature_cols}, f, indent=2)

    # Save data contract
    contract_path = output_dir / "data_contract.json"
    with open(contract_path, 'w') as f:
        json.dump({
            'model_type': 'binary_regime_classifier',
            'version': '2.0',
            'variant': variant,
            'classes': ['calm', 'reversal'],
            'num_features': len(feature_cols),
            'training': {
                'train_period': '2019-01-01 to 2022-12-31',
                'val_period': '2023-01-01 to 2023-12-31',
                'embargo_minutes': 60,
                'temporal_split': True,
            },
            'label_config': {
                'horizon': label_stats.get('horizon', 60),
                'rv_threshold': label_stats.get('rv_threshold'),
                'dd_small_threshold': label_stats.get('dd_small_threshold'),
                'dd_big_threshold': label_stats.get('dd_big_threshold'),
                'gray_zone_proportion': label_stats.get('prop_gray'),
            },
            'calibration': 'isotonic',
            'scaler': 'StandardScaler',
            'passed_gates': passed_gates,
            'timestamp': datetime.now().isoformat(),
        }, f, indent=2)

    logger.info({
        "msg": "Excellence bundle saved",
        "output_dir": str(output_dir),
        "artifacts": [
            "model.pkl",
            "threshold.json",
            "metrics.json",
            "feature_list.json",
            "data_contract.json",
        ],
    })

    return {
        'model_path': str(model_path),
        'threshold_path': str(threshold_path),
        'metrics_path': str(metrics_path),
        'features_path': str(features_path),
        'contract_path': str(contract_path),
    }


def main():
    parser = argparse.ArgumentParser(description="Train BINARY Regime Classifier - Production Excellence")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--output", type=str, default="artifacts/models/regime/prod")
    parser.add_argument("--train-end", type=str, default="2022-12-31")
    parser.add_argument("--val-start", type=str, default="2023-01-01")
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--label-horizon", type=int, default=60)

    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("BINARY REGIME CLASSIFIER - PRODUCTION EXCELLENCE TRAINING")
    print("=" * 80)
    print(f"Symbol:       {args.symbol}")
    print(f"Data period:  {args.start_date} to {args.end_date}")
    print(f"Train period: {args.start_date} to {args.train_end}")
    print(f"Val period:   {args.val_start} to {args.end_date}")
    print(f"Embargo:      {args.embargo_minutes} minutes")
    print("=" * 80)

    # Load data from S3
    logger.info({"msg": "Loading data from S3"})
    loader = S3MarketDataLoader()
    df = loader.load(args.symbol, args.start_date, args.end_date)
    df = normalize_columns(df)

    if df.empty:
        raise ValueError("No data loaded from S3")

    logger.info({
        "msg": "Data loaded",
        "rows": len(df),
        "cols": len(df.columns),
    })

    # Temporal split with embargo
    train_df, val_df = temporal_split_with_embargo(
        df,
        train_end_date=args.train_end,
        val_start_date=args.val_start,
        embargo_minutes=args.embargo_minutes,
    )

    # Build labels and extract features
    label_config = LabelConfig(horizon=args.label_horizon)

    train_features, train_labels, train_label_stats = extract_features_and_labels(
        train_df, label_config, fit_labels_on_train=True
    )

    val_features, val_labels, val_label_stats = extract_features_and_labels(
        val_df, label_config, fit_labels_on_train=False
    )

    # Ensure same feature columns
    common_cols = list(set(train_features.columns) & set(val_features.columns))
    train_features = train_features[common_cols]
    val_features = val_features[common_cols]

    logger.info({
        "msg": "Feature alignment complete",
        "common_features": len(common_cols),
        "train_samples": len(train_features),
        "val_samples": len(val_features),
    })

    # Scale features (fit on train only!)
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_features.values)
    X_val = scaler.transform(val_features.values)
    y_train = train_labels
    y_val = val_labels

    class_names = DEFAULT_CLASSES

    # Train and compare variants
    results = train_and_compare_variants(
        X_train, y_train, X_val, y_val, class_names
    )

    # Print comparison table
    print("\n" + "=" * 120)
    print("VARIANT COMPARISON")
    print("=" * 120)
    print(f"{'Variant':<15} {'Acc':>8} {'BalAcc':>8} {'MacroF1':>8} {'Brier':>8} {'ECE':>8} {'CalmRec':>8} {'RevRec':>8} {'RevPrc':>8} {'PR-AUC':>8} {'RateR':>8}")
    print("-" * 120)
    for variant, result in results.items():
        m = result['metrics']
        print(
            f"{variant:<15} "
            f"{m['accuracy']:>8.4f} "
            f"{m['balanced_accuracy']:>8.4f} "
            f"{m['macro_f1']:>8.4f} "
            f"{m['brier']:>8.4f} "
            f"{m['ece']:>8.4f} "
            f"{m['recall_per_class']['calm']:>8.4f} "
            f"{m['recall_per_class']['reversal']:>8.4f} "
            f"{m['precision_per_class']['reversal']:>8.4f} "
            f"{m['pr_auc_reversal']:>8.4f} "
            f"{m['rate_ratio']:>8.2f}"
        )
    print("=" * 120)

    # Select best variant
    gates = RegimeClassifierGates()
    best_variant, best_result = select_best_variant(results, gates)

    # Validate with production gates
    final_metrics = best_result['metrics']
    passed, reason = gates.validate(final_metrics)

    # Print final results
    print("\n" + "=" * 80)
    print("FINAL RESULTS - BEST VARIANT: " + best_variant.upper())
    print("=" * 80)
    print(f"Threshold:          {best_result['threshold']:.4f}")
    print(f"Accuracy:           {final_metrics['accuracy']:.4f}")
    print(f"Balanced Acc:       {final_metrics['balanced_accuracy']:.4f}")
    print(f"Macro F1:           {final_metrics['macro_f1']:.4f}")
    print(f"Brier:              {final_metrics['brier']:.4f}")
    print(f"ECE:                {final_metrics['ece']:.4f}")
    print(f"\nPer-class metrics:")
    for cls in ['calm', 'reversal']:
        rec = final_metrics['recall_per_class'][cls]
        prec = final_metrics['precision_per_class'][cls]
        f1 = final_metrics['f1_per_class'][cls]
        print(f"  {cls:10s}: recall={rec:.4f}, precision={prec:.4f}, f1={f1:.4f}")
    print(f"\nExcellence metrics (reversal):")
    print(f"  PR-AUC:             {final_metrics['pr_auc_reversal']:.4f}")
    print(f"  True rate:          {final_metrics['true_rate_reversal']:.4f}")
    print(f"  Pred rate:          {final_metrics['pred_rate_reversal']:.4f}")
    print(f"  Rate ratio:         {final_metrics['rate_ratio']:.4f}")
    print("\nConfusion Matrix:")
    print(np.array(final_metrics['confusion_matrix']))
    print("=" * 80)

    # Production gates
    print("\n" + "=" * 80)
    print("PRODUCTION GATES")
    print("=" * 80)

    if passed:
        print("✅ ALL GATES PASSED")
        output_dir = Path(args.output)
    else:
        print(f"❌ GATES FAILED: {reason}")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_dir = Path(args.output).parent / "failed"
        output_dir = failed_dir / f"failed_{run_id}"
        print(f"\n⚠️  Model saved to failed directory")

    print("=" * 80 + "\n")

    # Save excellence bundle
    paths = save_excellence_bundle(
        output_dir,
        model=best_result['model'],
        threshold=best_result['threshold'],
        metrics=final_metrics,
        feature_cols=common_cols,
        label_stats=train_label_stats,
        variant=best_variant,
        passed_gates=passed,
    )

    print("\n" + "=" * 80)
    print("ARTIFACTS SAVED")
    print("=" * 80)
    for name, path in paths.items():
        print(f"  {name:20s}: {path}")
    print("=" * 80 + "\n")

    # Print sanity warnings if any
    if not best_result['is_sane']:
        print("⚠️  SANITY CHECK WARNINGS:")
        for warning in best_result['sanity_warnings']:
            print(f"  - {warning}")
        print()

    if not passed:
        sys.exit(1)

    print("✅ Training complete - model ready for production")


if __name__ == "__main__":
    main()
