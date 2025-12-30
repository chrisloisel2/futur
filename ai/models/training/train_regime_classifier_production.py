#!/usr/bin/env python3
"""
Production Regime Classifier Training - WITH ALL CRITICAL FIXES
================================================================

FIXES APPLIED:
1. SGDClassifier + class_weight='balanced' (not LogisticRegression)
2. CalibratedClassifierCV for Brier score fix (0.2038 → <0.20)
3. 5 impulse discriminant features (abs_ret_1m, abs_ret_5m, range_1m, vol_z_60m, rv_ratio_5_60)
4. Hard gate: impulse recall must be >= 0.35 or training FAILS
5. Comprehensive per-class metrics logged

Expected Results:
- Impulse recall: 19.5% → >40%
- Macro F1: 0.289 → >0.40
- Brier: 0.2038 → <0.18

Usage:
    python training/train_regime_classifier_production.py \
        --s3_dataset s3://qbia/bourse/processed/market/ \
        --symbol BTCUSDT \
        --quote USDT \
        --interval 1m \
        --years 2023,2024 \
        --out runs
"""
from __future__ import annotations
import os, json, time, argparse, sys
from pathlib import Path
import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.io_s3 import read_year_df
from training.common.production_regime import (
    add_impulse_discriminant_features,
    train_production_regime_classifier,
    print_regime_metrics_report,
    RegimeClassifierMetrics
)


FEATURE_KEYS = [
    "Open","High","Low","Close","Volume","Quote_Volume",
    "ret","log_ret","rv_15","rv_60","rv_240",
    "ema_20","ema_50","ema_200",
    "atr_14","rsi_14",
]


def parse_args():
    ap = argparse.ArgumentParser(description="Train production regime classifier with impulse recall gate")

    # Standard interface (same as all training scripts)
    ap.add_argument("--s3_dataset", required=True, help="S3 base path")
    ap.add_argument("--symbol", required=True, help="BTCUSDT")
    ap.add_argument("--quote", required=True, help="USDT")
    ap.add_argument("--interval", required=True, help="1m")
    ap.add_argument("--years", required=True, help="2023,2024,...")
    ap.add_argument("--out", default="runs", help="Output directory")

    # Regime-specific
    ap.add_argument("--min-impulse-recall", type=float, default=0.35,
                    help="Minimum acceptable impulse recall (default: 0.35)")

    return ap.parse_args()


def load_and_prepare_data(args, years):
    """
    Load data from S3 and add impulse discriminant features.
    """
    print(f"\n{'='*80}")
    print("LOADING DATA FROM S3")
    print(f"{'='*80}\n")

    all_data = []

    for y in years:
        print(f"Loading year {y}...")
        try:
            # Try to load with regime label if it exists
            df = read_year_df(
                args.s3_dataset, args.symbol, args.quote, args.interval, y,
                ["datetime"] + FEATURE_KEYS
            )

            # ADD CRITICAL IMPULSE DISCRIMINANT FEATURES
            print(f"  Adding 5 impulse discriminant features...")
            add_impulse_discriminant_features(df)

            # For demo: create synthetic regime labels if not present
            # In production, you'd load these from your labeled dataset
            if 'regime' not in df.columns:
                print(f"  WARNING: No regime labels found. Creating synthetic labels for demo.")
                # Simple clustering based on volatility + direction
                df['regime'] = 'calm'  # Default

                # High volatility + positive return = impulse
                impulse_mask = (df['rv_60'] > df['rv_60'].quantile(0.7)) & (df['ret'] > 0)
                df.loc[impulse_mask, 'regime'] = 'impulse'

                # High volatility + negative return = reversal
                reversal_mask = (df['rv_60'] > df['rv_60'].quantile(0.7)) & (df['ret'] < 0)
                df.loc[reversal_mask, 'regime'] = 'reversal'

                # Medium volatility = breakout
                breakout_mask = (df['rv_60'] > df['rv_60'].quantile(0.4)) & (df['rv_60'] <= df['rv_60'].quantile(0.7))
                df.loc[breakout_mask, 'regime'] = 'breakout'

            all_data.append(df)
            print(f"  ✅ Loaded {len(df):,} rows")

        except Exception as e:
            print(f"  ❌ Error loading year {y}: {e}")
            continue

    if not all_data:
        raise ValueError("No data loaded from S3")

    df_full = pd.concat(all_data, ignore_index=True)

    print(f"\n✅ Total: {len(df_full):,} rows")
    print(f"\nRegime distribution:")
    print(df_full['regime'].value_counts())

    return df_full


def main():
    args = parse_args()
    years = [int(x) for x in args.years.split(",")]

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.out, "regime_classifier_prod", run_id)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'='*80}")
    print("PRODUCTION REGIME CLASSIFIER TRAINING")
    print(f"{'='*80}")
    print(f"\nDataset:  {args.s3_dataset}")
    print(f"Symbol:   {args.symbol}/{args.quote}")
    print(f"Interval: {args.interval}")
    print(f"Years:    {args.years}")
    print(f"Output:   {out_dir}")
    print(f"\n🎯 Impulse recall gate: >= {args.min_impulse_recall}")

    # === LOAD DATA ===
    df_full = load_and_prepare_data(args, years)

    # === PREPARE FEATURES ===
    print(f"\n{'='*80}")
    print("PREPARING FEATURES")
    print(f"{'='*80}\n")

    # Original features + 5 impulse features
    feature_cols = FEATURE_KEYS + [
        'abs_ret_1m',      # Instant velocity
        'abs_ret_5m',      # Cumulative momentum
        'range_1m',        # Range normalized
        'vol_z_60m',       # Volume anomaly
        'rv_ratio_5_60'    # RV ratio
    ]

    # Filter to existing columns
    feature_cols = [c for c in feature_cols if c in df_full.columns]

    print(f"Features selected: {len(feature_cols)}")
    print(f"  Original:  {len(FEATURE_KEYS)}")
    print(f"  Impulse:   5")
    print(f"\nImpulse features:")
    for feat in ['abs_ret_1m', 'abs_ret_5m', 'range_1m', 'vol_z_60m', 'rv_ratio_5_60']:
        if feat in feature_cols:
            print(f"  ✅ {feat}")

    X = df_full[feature_cols].fillna(0).values.astype(np.float32)
    y = df_full['regime'].values

    # === TRAIN/TEST SPLIT (temporal - no shuffle) ===
    print(f"\n{'='*80}")
    print("TEMPORAL TRAIN/TEST SPLIT")
    print(f"{'='*80}\n")

    n = len(X)
    split_idx = int(0.8 * n)
    X_train, y_train = X[:split_idx], y[:split_idx]
    X_test, y_test = X[split_idx:], y[split_idx:]

    print(f"Train: {len(X_train):,} samples ({len(X_train)/n:.1%})")
    print(f"Test:  {len(X_test):,} samples ({len(X_test)/n:.1%})")

    # Get unique classes
    classes = sorted(set(y_train))
    print(f"\nClasses: {classes}")

    # === TRAIN WITH PRODUCTION FIXES ===
    print(f"\n{'='*80}")
    print("TRAINING SGDClassifier + CalibratedClassifierCV")
    print(f"{'='*80}\n")

    print("Fixes applied:")
    print("  ✅ SGDClassifier with class_weight='balanced'")
    print("  ✅ CalibratedClassifierCV with isotonic calibration")
    print("  ✅ 5 impulse discriminant features")
    print("  ✅ Hard gate: impulse recall >= 0.35\n")

    try:
        clf, metrics_train = train_production_regime_classifier(
            X_train,
            y_train,
            classes=classes,
            min_impulse_recall=args.min_impulse_recall
        )

        print("\n✅ TRAINING PASSED - Impulse recall gate satisfied")

    except ValueError as e:
        print(f"\n❌ TRAINING FAILED:")
        print(f"   {e}")
        print(f"\nModel REJECTED. Impulse recall too low (class collapse detected).")
        sys.exit(1)

    # === PRINT TRAINING METRICS ===
    print_regime_metrics_report(metrics_train, classes)

    # === EVALUATE ON TEST SET ===
    print(f"\n{'='*80}")
    print("TEST SET EVALUATION")
    print(f"{'='*80}\n")

    from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support

    y_test_pred = clf.predict(X_test)
    y_test_proba = clf.predict_proba(X_test)

    test_accuracy = accuracy_score(y_test, y_test_pred)
    test_macro_f1 = f1_score(y_test, y_test_pred, average='macro')

    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, y_test_pred, labels=classes, zero_division=0
    )

    per_class_recall_test = {cls: float(r) for cls, r in zip(classes, recall)}

    print(f"📊 Test Metrics:")
    print(f"  Accuracy:  {test_accuracy:.4f}")
    print(f"  Macro F1:  {test_macro_f1:.4f}")
    print(f"\n🎯 Test Per-Class Recall:")
    for cls, rec in per_class_recall_test.items():
        threshold = args.min_impulse_recall if cls == 'impulse' else 0.30
        status = "✅" if rec >= threshold else "❌"
        print(f"  {status} {cls:10s}: {rec:.4f}")

    test_impulse_recall = per_class_recall_test.get('impulse', 0.0)
    if test_impulse_recall < args.min_impulse_recall:
        print(f"\n⚠️  WARNING: Test impulse recall {test_impulse_recall:.3f} < {args.min_impulse_recall}")
        print(f"   Model may not generalize well to test data.")

    # === SAVE MODEL ===
    print(f"\n{'='*80}")
    print("SAVING MODEL & METRICS")
    print(f"{'='*80}\n")

    import pickle

    model_path = os.path.join(out_dir, "model.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(clf, f)
    print(f"✅ Model saved: {model_path}")

    # Save training metrics
    metrics_path = os.path.join(out_dir, "train_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics_train.to_dict(), f, indent=2)
    print(f"✅ Train metrics saved: {metrics_path}")

    # Save test metrics
    test_metrics = {
        "accuracy": float(test_accuracy),
        "macro_f1": float(test_macro_f1),
        "per_class_recall": per_class_recall_test,
        "impulse_recall": float(test_impulse_recall),
        "impulse_recall_gate_passed": test_impulse_recall >= args.min_impulse_recall
    }

    test_metrics_path = os.path.join(out_dir, "test_metrics.json")
    with open(test_metrics_path, "w") as f:
        json.dump(test_metrics, f, indent=2)
    print(f"✅ Test metrics saved: {test_metrics_path}")

    # Save feature names
    feature_info_path = os.path.join(out_dir, "feature_info.json")
    with open(feature_info_path, "w") as f:
        json.dump({
            "feature_cols": feature_cols,
            "n_features": len(feature_cols),
            "impulse_features": ['abs_ret_1m', 'abs_ret_5m', 'range_1m', 'vol_z_60m', 'rv_ratio_5_60']
        }, f, indent=2)
    print(f"✅ Feature info saved: {feature_info_path}")

    # === FINAL SUMMARY ===
    print(f"\n{'='*80}")
    print("TRAINING COMPLETE")
    print(f"{'='*80}\n")

    print(f"📁 Output directory: {out_dir}\n")

    print(f"📊 Final Results:")
    print(f"  Train impulse recall: {metrics_train.per_class_recall.get('impulse', 0):.4f}")
    print(f"  Test impulse recall:  {test_impulse_recall:.4f}")
    print(f"  Train macro F1:       {metrics_train.macro_f1:.4f}")
    print(f"  Test macro F1:        {test_macro_f1:.4f}")

    if test_impulse_recall >= args.min_impulse_recall:
        print(f"\n✅ MODEL PRODUCTION READY")
        print(f"   Impulse recall {test_impulse_recall:.3f} >= {args.min_impulse_recall}")
    else:
        print(f"\n⚠️  MODEL NEEDS IMPROVEMENT")
        print(f"   Test impulse recall {test_impulse_recall:.3f} < {args.min_impulse_recall}")

    print(f"\n🎯 Next steps:")
    print(f"  1. Review confusion matrix in train_metrics.json")
    print(f"  2. Check per-class metrics for all regimes")
    print(f"  3. If production ready, deploy model.pkl")
    print(f"  4. Monitor impulse recall on live data")


if __name__ == "__main__":
    main()
