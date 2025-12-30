"""
Training Script for BINARY Regime Classifier

⚠️  ARCHITECTURE CHANGE: Impulse moved to event detection
This script now supports BINARY regime classification (calm vs reversal)

Usage:
    python scripts/train_regime_classifier_binary.py \
        --start-date 2019-01-01 \
        --end-date 2023-12-31 \
        --symbol BTCUSDT \
        --output artifacts/models/regime/production_binary_v1.pkl

Target Performance (BINARY):
    - Accuracy > 60% (vs 46% with 3-class)
    - Calm recall > 50%
    - Reversal recall > 50%
    - ECE < 0.10
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

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "ai/models/training/common"))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns

# Import corrected modules
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    DEFAULT_CLASSES,
)
from production_gates import RegimeClassifierGates

logger = get_logger(__name__)


def load_training_data(
    symbol: str,
    start_date: str,
    end_date: str,
    binary: bool = True,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Load S3 data and extract features + labels.

    Args:
        binary: If True, use only calm/reversal (filter out impulse)

    Returns:
        features_df: DataFrame with features
        labels: numpy array with regime labels (0=calm, 1=reversal or impulse removed)
    """
    logger.info({
        "msg": "Loading training data from S3",
        "symbol": symbol,
        "start": start_date,
        "end": end_date,
        "binary": binary,
    })

    loader = S3MarketDataLoader()
    df = loader.load(symbol, start_date, end_date)
    df = normalize_columns(df)

    if df.empty:
        raise ValueError("No data loaded from S3")

    logger.info({
        "msg": "Data loaded",
        "rows": len(df),
        "start": df['datetime'].min() if 'datetime' in df.columns else None,
        "end": df['datetime'].max() if 'datetime' in df.columns else None,
    })

    # Extract labels
    if 'label_policy' not in df.columns:
        raise ValueError("label_policy column not found in S3 data")

    labels = df['label_policy'].copy()

    # Filter for binary if requested
    if binary:
        # Assume labels: 0=calm, 1=impulse, 2=reversal
        # Keep only calm (0) and reversal (2)
        mask = (labels == 0) | (labels == 2)
        df = df[mask]
        labels = labels[mask]

        # Remap: 0=calm stays 0, 2=reversal becomes 1
        labels = labels.replace(2, 1)

        logger.info({
            "msg": "Filtered for BINARY regimes",
            "remaining_rows": len(df),
            "classes": "calm (0), reversal (1)",
        })

    # Select feature columns
    exclude_cols = {
        'datetime', 'open_time', 'close_time', 'timestamp', 'event_time',
        'label_policy', 'label_tradeable', 'symbol',
        'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote',
    }

    feature_cols = [
        c for c in df.columns
        if c not in exclude_cols and pd.api.types.is_numeric_dtype(df[c])
    ]

    features_df = df[feature_cols].copy()

    logger.info({
        "msg": "Features extracted",
        "feature_cols": len(feature_cols),
    })

    # Clean data
    mask_valid = ~(features_df.isna().any(axis=1) | labels.isna())
    features_df = features_df[mask_valid]
    labels = labels[mask_valid].values  # Convert to numpy array

    logger.info({
        "msg": "Data cleaned",
        "rows_after_cleaning": len(features_df),
        "label_distribution": pd.Series(labels).value_counts().to_dict(),
    })

    return features_df, labels


def main():
    parser = argparse.ArgumentParser(description="Train BINARY Regime Classifier")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--output", type=str,
                        default="artifacts/models/regime/production_binary_v1.pkl")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--binary", action="store_true", default=True,
                        help="Use binary classification (calm vs reversal)")

    args = parser.parse_args()

    # Load data
    features_df, labels = load_training_data(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        binary=args.binary,
    )

    # Train/test split
    from sklearn.model_selection import train_test_split

    X_train, X_val, y_train, y_val = train_test_split(
        features_df.values,
        labels,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=labels,
    )

    logger.info({
        "msg": "Data split",
        "train_samples": len(X_train),
        "val_samples": len(X_val),
    })

    # Class names
    class_names = DEFAULT_CLASSES  # ['calm', 'reversal']

    # Train
    logger.info(f"Training BINARY regime classifier: {class_names}")

    clf = train_calibrated_regime_classifier(
        X_train, y_train,
        class_names=class_names,
    )

    # Evaluate
    metrics = evaluate_regime_classifier(
        clf, X_val, y_val,
        class_names=class_names,
    )

    # Print results
    print("\n" + "=" * 80)
    print("BINARY REGIME CLASSIFIER RESULTS")
    print("=" * 80)
    print(f"\nAccuracy: {metrics['accuracy']:.4f}")
    print(f"Macro F1: {metrics['macro_f1']:.4f}")
    print(f"ECE: {metrics['ece']:.4f}")
    print(f"Brier: {metrics.get('multiclass_brier', 0):.4f}")
    print(f"\nPer-class recall:")
    for cls, recall in metrics['recall_per_class'].items():
        print(f"  {cls:10s}: {recall:.4f}")
    print("\nConfusion Matrix:")
    print(np.array(metrics['confusion_matrix']))
    print("=" * 80)

    # Production gates
    gates = RegimeClassifierGates()
    passed, reason = gates.validate(metrics)

    print("\n" + "=" * 80)
    print("PRODUCTION GATES")
    print("=" * 80)

    if passed:
        print("✅ ALL GATES PASSED")
        output_path = Path(args.output)
    else:
        print(f"❌ GATES FAILED: {reason}")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_dir = Path(args.output).parent / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        output_path = failed_dir / f"failed_{run_id}.pkl"
        print(f"\n⚠️  Model NOT saved to production path")
        print(f"   Instead saved to: {output_path}")

    print("=" * 80 + "\n")

    # Save model
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import joblib
    joblib.dump(clf, output_path)

    # Save metrics
    metrics_path = output_path.parent / f"{output_path.stem}_metrics.json"
    with open(metrics_path, 'w') as f:
        metrics_json = {
            k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in metrics.items()
        }
        json.dump(metrics_json, f, indent=2)

    logger.info({
        "msg": "Training complete",
        "model_path": str(output_path),
        "metrics_path": str(metrics_path),
        "passed_gate": passed,
    })

    print(f"✅ Model saved to: {output_path}")
    print(f"✅ Metrics saved to: {metrics_path}")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
