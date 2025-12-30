"""
Training Script for Regime Classifier

Trains a multinomial logistic regression regime classifier on historical market data.
Uses 'label_policy' from S3 processed data as ground truth labels.

Usage:
    python scripts/train_regime_classifier.py \
        --start-date 2019-01-01 \
        --end-date 2023-12-31 \
        --symbol BTCUSDT \
        --output artifacts/models/regime/production_v1.pkl

Target Performance:
    - Accuracy > 60% (6 classes)
    - Entropy < 1.5 (confident predictions)
    - Calibration: Brier score < 0.20
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import train_test_split

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from pipeline.models.regime.classifier import RegimeClassifierModel

logger = get_logger(__name__)


def load_training_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Load S3 data and extract features + labels.

    Returns:
        features_df: DataFrame with features (numeric columns)
        labels: Series with regime labels (label_policy)
    """
    logger.info({
        "msg": "Loading training data from S3",
        "symbol": symbol,
        "start": start_date,
        "end": end_date,
    })

    loader = S3MarketDataLoader()
    df = loader.load(symbol, start_date, end_date)
    df = normalize_columns(df)

    if df.empty:
        raise ValueError("No data loaded from S3")

    logger.info({
        "msg": "Data loaded",
        "rows": len(df),
        "columns": len(df.columns),
        "start": df['datetime'].min() if 'datetime' in df.columns else None,
        "end": df['datetime'].max() if 'datetime' in df.columns else None,
    })

    # Extract labels
    if 'label_policy' not in df.columns:
        raise ValueError("label_policy column not found in S3 data")

    labels = df['label_policy'].copy()

    # Select feature columns (exclude labels, timestamps, metadata)
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
        "sample_features": feature_cols[:10],
    })

    # Clean data: drop NaN rows
    mask_valid = ~(features_df.isna().any(axis=1) | labels.isna())
    features_df = features_df[mask_valid]
    labels = labels[mask_valid]

    logger.info({
        "msg": "Data cleaned",
        "rows_after_cleaning": len(features_df),
        "label_distribution": labels.value_counts().to_dict(),
    })

    return features_df, labels


def train_regime_classifier(
    features_df: pd.DataFrame,
    labels: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[RegimeClassifierModel, dict]:
    """
    Train regime classifier with train/test split.

    Returns:
        model: Trained RegimeClassifierModel
        metrics: Dict with performance metrics
    """
    # Get unique classes (convert to strings for sklearn compatibility)
    unique_labels = sorted(labels.unique().tolist())

    # Map integer labels to string names if they are integers
    if all(isinstance(label, (int, np.integer)) for label in unique_labels):
        # Default regime names
        regime_names = {
            0: "calm",
            1: "impulse",
            2: "reversal",
            3: "breakout",
            4: "squeeze",
            5: "chop",
        }
        classes = [regime_names.get(label, f"regime_{label}") for label in unique_labels]
        # Convert labels to strings
        labels = labels.map(lambda x: regime_names.get(x, f"regime_{x}"))
    else:
        classes = unique_labels

    logger.info({
        "msg": "Training regime classifier",
        "classes": classes,
        "n_classes": len(classes),
        "n_samples": len(features_df),
    })

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        features_df,
        labels,
        test_size=test_size,
        random_state=random_state,
        stratify=labels,
    )

    logger.info({
        "msg": "Data split",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "train_label_dist": y_train.value_counts().to_dict(),
    })

    # Initialize model
    model = RegimeClassifierModel(classes=classes)

    # Train
    logger.info("Starting training...")
    model.fit(X_train, y_train)

    # Evaluate on test set
    logger.info("Evaluating on test set...")
    predictions_df = model.predict(X_test)

    # Get predicted class (argmax)
    predicted_probs = predictions_df[classes].values
    predicted_classes = [classes[i] for i in predicted_probs.argmax(axis=1)]

    # Metrics
    accuracy = accuracy_score(y_test, predicted_classes)

    # Brier score (average over all classes)
    y_test_onehot = pd.get_dummies(y_test)[classes].values
    brier_scores = []
    for i, cls in enumerate(classes):
        if y_test_onehot[:, i].sum() > 0:  # Skip classes with no samples
            bs = brier_score_loss(y_test_onehot[:, i], predicted_probs[:, i])
            brier_scores.append(bs)

    avg_brier = np.mean(brier_scores) if brier_scores else np.nan

    # Entropy
    avg_entropy = predictions_df['entropy'].mean()

    # Confusion matrix
    cm = confusion_matrix(y_test, predicted_classes, labels=classes)

    # Classification report
    report = classification_report(y_test, predicted_classes, target_names=classes, output_dict=True)

    metrics = {
        "accuracy": accuracy,
        "avg_brier_score": avg_brier,
        "avg_entropy": avg_entropy,
        "confusion_matrix": cm.tolist(),
        "classification_report": report,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "classes": classes,
    }

    logger.info({
        "msg": "Training complete",
        "accuracy": f"{accuracy:.4f}",
        "avg_brier_score": f"{avg_brier:.4f}",
        "avg_entropy": f"{avg_entropy:.4f}",
    })

    # Print detailed report
    print("\n" + "=" * 80)
    print("REGIME CLASSIFIER TRAINING RESULTS")
    print("=" * 80)
    print(f"\nAccuracy: {accuracy:.4f}")
    print(f"Avg Brier Score: {avg_brier:.4f}")
    print(f"Avg Entropy: {avg_entropy:.4f}")
    print(f"\nTrain samples: {len(X_train):,}")
    print(f"Test samples: {len(X_test):,}")
    print(f"\nClasses: {classes}")
    print("\nClassification Report:")
    print(classification_report(y_test, predicted_classes, target_names=classes))
    print("\nConfusion Matrix:")
    print(cm)
    print("=" * 80)

    # Check if meets targets (adjusted for number of classes)
    n_classes = len(classes)
    random_baseline = 1.0 / n_classes
    target_accuracy = random_baseline + 0.15  # 15% above random

    meets_accuracy = accuracy > target_accuracy
    meets_entropy = avg_entropy < 1.5
    meets_brier = avg_brier < 0.20

    print("\n" + "=" * 80)
    print("TARGET PERFORMANCE CHECK")
    print("=" * 80)
    print(f"Random baseline: {random_baseline:.1%} ({n_classes} classes)")
    print(f"Accuracy > {target_accuracy:.1%}: {'✅' if meets_accuracy else '❌'} ({accuracy:.1%})")
    print(f"Entropy < 1.5: {'✅' if meets_entropy else '❌'} ({avg_entropy:.2f})")
    print(f"Brier < 0.20: {'✅' if meets_brier else '❌'} ({avg_brier:.4f})")

    if meets_accuracy and meets_entropy and meets_brier:
        print("\n🎉 ALL TARGETS MET - Model ready for production!")
    else:
        print("\n⚠️ Some targets not met - Consider:")
        if not meets_accuracy:
            print("  - Feature engineering (add more technical indicators)")
            print("  - Increase training data (more years)")
            print("  - Try different model (RandomForest, XGBoost)")
        if not meets_entropy:
            print("  - Model is not confident - may need better features")
        if not meets_brier:
            print("  - Calibration issue - consider CalibratedClassifierCV")

    print("=" * 80 + "\n")

    return model, metrics


def main():
    parser = argparse.ArgumentParser(description="Train Regime Classifier")
    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading symbol",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="artifacts/models/regime/production_v1.pkl",
        help="Output path for trained model",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Test set proportion (default 0.2)",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )

    args = parser.parse_args()

    # Load data
    features_df, labels = load_training_data(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    # Train
    model, metrics = train_regime_classifier(
        features_df,
        labels,
        test_size=args.test_size,
        random_state=args.random_state,
    )

    # HARD GATE: Vérifier critères production avant sauvegarde
    accuracy = metrics["accuracy"]
    avg_brier_score = metrics["avg_brier_score"]
    report = metrics["classification_report"]

    # Impulse recall critique
    impulse_recall = report.get("impulse", {}).get("recall", 0.0)

    # Critères production
    passed = (
        accuracy >= 0.48
        and avg_brier_score <= 0.20
        and impulse_recall >= 0.35
    )

    # Déterminer chemin de sauvegarde
    if passed:
        output_path = Path(args.output)
        print("\n✅ PRODUCTION GATE PASSED - Saving to production path")
    else:
        # Sauver dans failed/ avec timestamp
        from datetime import datetime
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_dir = Path(args.output).parent / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        output_path = failed_dir / f"failed_{run_id}.pkl"

        print("\n❌ PRODUCTION GATE FAILED - Saving to failed/")
        print(f"   Accuracy: {accuracy:.4f} (need >= 0.48)")
        print(f"   Brier: {avg_brier_score:.4f} (need <= 0.20)")
        print(f"   Impulse recall: {impulse_recall:.4f} (need >= 0.35)")
        print(f"\n⚠️  Model NOT saved to production path: {args.output}")
        print(f"   Instead saved to: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info({
        "msg": "Saving model",
        "path": str(output_path),
        "passed_gate": passed,
    })

    model.save(str(output_path))

    # Save metrics
    metrics_path = output_path.parent / f"{output_path.stem}_metrics.json"
    import json
    with open(metrics_path, 'w') as f:
        # Convert numpy types to python types for JSON
        metrics_json = {
            k: v.tolist() if isinstance(v, np.ndarray) else v
            for k, v in metrics.items()
            if k != 'confusion_matrix'  # Already converted to list
        }
        metrics_json['confusion_matrix'] = metrics['confusion_matrix']
        json.dump(metrics_json, f, indent=2)

    logger.info({
        "msg": "Training complete",
        "model_path": str(output_path),
        "metrics_path": str(metrics_path),
    })

    print(f"\n✅ Model saved to: {output_path}")
    print(f"✅ Metrics saved to: {metrics_path}")


if __name__ == "__main__":
    main()
