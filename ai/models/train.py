"""
UNIFIED TRAINING PIPELINE
Single entry point for all training/evaluation.

Usage:
    export S3_BUCKET="your-bucket"
    export S3_PREFIX="btc/1m/"
    python train.py
"""

from __future__ import annotations

import os
import sys
import json
import pickle
import time
from typing import Dict, Tuple

import numpy as np
import tensorflow as tf

from config import CONFIG, REGIME_NAMES
from regime_model import (
    compute_regime_labels,
    compute_regime_statistics,
    RegimeAwareModel,
    Trainer,
    Evaluator,
)

# Import from model.py (existing infrastructure)
from model import (
    FEATURE_KEYS,
    RunningRobustScaler,
    iter_s3_jsonl,
    build_numpy_from_stream,
    make_windows,
    set_seed,
)


# =========================
# UTILITIES
# =========================
def align_regime_labels_with_windows(
    y_regime_full: np.ndarray,
    lookback: int,
    n_windows: int,
    stride: int = 1,
) -> np.ndarray:
    """Align regime labels with windowed data"""
    y_regime_windows = np.zeros(n_windows, dtype=np.int32)

    for i in range(n_windows):
        end_idx = i * stride + lookback - 1
        if end_idx < len(y_regime_full):
            y_regime_windows[i] = y_regime_full[end_idx]
        else:
            y_regime_windows[i] = y_regime_full[-1]

    return y_regime_windows


def save_results(results: Dict, scaler, output_dir: str):
    """Save all outputs"""
    os.makedirs(output_dir, exist_ok=True)

    # Results JSON
    with open(os.path.join(output_dir, "results.json"), "w") as f:
        json.dump(convert_to_serializable(results), f, indent=2)

    # Scaler
    if CONFIG.save_scaler:
        with open(os.path.join(output_dir, "scaler.pkl"), "wb") as f:
            pickle.dump(scaler, f)

    print(f"\n✅ Results saved to {output_dir}/")


def convert_to_serializable(obj):
    """Convert numpy types to Python native for JSON"""
    if isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.int32, np.int64, np.integer)):
        return int(obj)
    elif isinstance(obj, (np.float32, np.float64, np.floating)):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    else:
        return obj


# =========================
# MAIN TRAINING PIPELINE
# =========================
def train_pipeline():
    """
    Main training pipeline.

    Steps:
    1. Load data from S3
    2. Fit scaler
    3. Transform + windowing
    4. Compute regime labels
    5. Split train/val
    6. Train model
    7. Evaluate
    8. Save results
    """
    print("\n" + "=" * 80)
    print("REGIME-AWARE MODEL - UNIFIED TRAINING PIPELINE")
    print("=" * 80)

    set_seed(CONFIG.seed)

    # TensorFlow setup
    if CONFIG.mixed_precision:
        try:
            from tensorflow.keras import mixed_precision
            mixed_precision.set_global_policy("mixed_float16")
            print("✓ Mixed precision enabled")
        except Exception:
            pass

    if CONFIG.xla:
        try:
            tf.config.optimizer.set_jit(True)
            print("✓ XLA enabled")
        except Exception:
            pass

    # Validate S3 config
    if not CONFIG.s3_bucket or not CONFIG.s3_prefix:
        print("\n❌ ERROR: S3_BUCKET and S3_PREFIX must be set")
        print("Example:")
        print("  export S3_BUCKET=my-bucket")
        print("  export S3_PREFIX=btc/1m/")
        sys.exit(1)

    print(f"\nConfiguration:")
    print(f"  S3: s3://{CONFIG.s3_bucket}/{CONFIG.s3_prefix}")
    print(f"  Lookback: {CONFIG.lookback}")
    print(f"  Horizon: {CONFIG.horizon}")
    print(f"  Epochs: {CONFIG.epochs}")
    print(f"  Batch size: {CONFIG.batch_size}")
    print(f"  Gating: {CONFIG.gating_mode}")

    # ===== STEP 1: LOAD DATA =====
    print("\n" + "=" * 80)
    print("STEP 1: LOADING DATA FROM S3")
    print("=" * 80)

    start_time = time.time()

    scaler = RunningRobustScaler(
        feature_dim=len(CONFIG.feature_keys),
        reservoir_size=200_000,
        seed=CONFIG.seed,
    )

    stream = iter_s3_jsonl(
        bucket=CONFIG.s3_bucket,
        prefix=CONFIG.s3_prefix,
        region=CONFIG.aws_region,
        aws_profile=CONFIG.aws_profile,
    )

    X_all, y_ret, y_rv = build_numpy_from_stream(stream, scaler=scaler, limit_rows=None)
    scaler.finalize()

    print(f"✓ Loaded {X_all.shape[0]:,} timesteps in {time.time() - start_time:.1f}s")
    print(f"  Features: {X_all.shape[1]}")

    # ===== STEP 2: TRANSFORM =====
    print("\n" + "=" * 80)
    print("STEP 2: TRANSFORMING DATA")
    print("=" * 80)

    X_all = scaler.transform(X_all)
    print("✓ Data normalized (RobustScaler)")

    # ===== STEP 3: WINDOWING =====
    print("\n" + "=" * 80)
    print("STEP 3: CREATING WINDOWS")
    print("=" * 80)

    Xw, yret_h, ydir, yrv_h = make_windows(
        X_all,
        y_ret,
        y_rv,
        lookback=CONFIG.lookback,
        horizon=CONFIG.horizon,
        stride=CONFIG.stride,
    )

    print(f"✓ Created {Xw.shape[0]:,} windows")
    print(f"  Shape: {Xw.shape}")

    # ===== STEP 4: REGIME LABELS =====
    print("\n" + "=" * 80)
    print("STEP 4: COMPUTING REGIME LABELS")
    print("=" * 80)

    y_regime_full = compute_regime_labels(
        X_all,
        list(CONFIG.feature_keys),
        lookback=CONFIG.lookback,
    )

    y_regime = align_regime_labels_with_windows(
        y_regime_full,
        CONFIG.lookback,
        len(Xw),
        stride=CONFIG.stride,
    )

    stats = compute_regime_statistics(y_regime)
    print("\nRegime Distribution:")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v:.2f}")

    # ===== STEP 5: SPLIT =====
    print("\n" + "=" * 80)
    print("STEP 5: TEMPORAL SPLIT (90/10)")
    print("=" * 80)

    n = Xw.shape[0]
    split = int(n * 0.9)

    Xw_train, Xw_val = Xw[:split], Xw[split:]
    yret_train, yret_val = yret_h[:split], yret_h[split:]
    yrv_train, yrv_val = yrv_h[:split], yrv_h[split:]
    y_regime_train, y_regime_val = y_regime[:split], y_regime[split:]

    print(f"  Train: {len(Xw_train):,} samples")
    print(f"  Val: {len(Xw_val):,} samples")

    # ===== STEP 6: CREATE MODEL =====
    print("\n" + "=" * 80)
    print("STEP 6: CREATING MODEL")
    print("=" * 80)

    model = RegimeAwareModel(feature_dim=Xw_train.shape[-1])

    # Build
    dummy = tf.zeros((1, CONFIG.lookback, Xw_train.shape[-1]), dtype=tf.float32)
    _ = model(dummy, training=False)

    print(f"  Total parameters: {model.count_params():,}")
    print(f"  Classifier params: {model.regime_classifier.count_params():,}")
    print(f"  Expert params (×{CONFIG.n_regimes}): {model.experts[0].count_params():,} each")

    # ===== STEP 7: TRAIN =====
    print("\n" + "=" * 80)
    print("STEP 7: TRAINING")
    print("=" * 80)

    trainer = Trainer(model)

    # Datasets
    ds_train = tf.data.Dataset.from_tensor_slices(
        (Xw_train, y_regime_train, yret_train, yrv_train)
    )
    ds_train = ds_train.shuffle(CONFIG.shuffle_buffer).batch(CONFIG.batch_size).prefetch(CONFIG.prefetch)

    ds_val = tf.data.Dataset.from_tensor_slices(
        (Xw_val, y_regime_val, yret_val, yrv_val)
    )
    ds_val = ds_val.batch(CONFIG.batch_size).prefetch(CONFIG.prefetch)

    # Training loop
    best_val_loss = float("inf")
    patience_counter = 0
    max_patience = 5

    for epoch in range(CONFIG.epochs):
        print(f"\nEpoch {epoch + 1}/{CONFIG.epochs}")

        # Train
        trainer.train_loss_tracker.reset_states()
        trainer.regime_acc_tracker.reset_states()

        for step, (x_batch, regime_batch, ret_batch, rv_batch) in enumerate(ds_train):
            trainer.train_step(x_batch, regime_batch, ret_batch, rv_batch)

            if (step + 1) % 100 == 0:
                train_loss = trainer.train_loss_tracker.result()
                regime_acc = trainer.regime_acc_tracker.result()
                print(
                    f"  Step {step + 1}: Loss={train_loss:.4f}, RegimeAcc={regime_acc:.2%}",
                    end="\r",
                )

        train_loss = trainer.train_loss_tracker.result()
        regime_acc = trainer.regime_acc_tracker.result()

        # Validation
        trainer.val_loss_tracker.reset_states()

        for x_batch, regime_batch, ret_batch, rv_batch in ds_val:
            trainer.val_step(x_batch, regime_batch, ret_batch, rv_batch)

        val_loss = trainer.val_loss_tracker.result()

        print(
            f"\n  Train Loss: {train_loss:.4f} | Regime Acc: {regime_acc:.2%} | "
            f"Val Loss: {val_loss:.4f}"
        )

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            model.save_weights(os.path.join(CONFIG.output_dir, "best_weights.h5"))
            print(f"  ✓ Best val loss: {best_val_loss:.4f}")
        else:
            patience_counter += 1
            print(f"  Patience: {patience_counter}/{max_patience}")
            if patience_counter >= max_patience:
                print("  Early stopping triggered")
                break

    # Load best
    model.load_weights(os.path.join(CONFIG.output_dir, "best_weights.h5"))

    # ===== STEP 8: EVALUATE =====
    print("\n" + "=" * 80)
    print("STEP 8: EVALUATION")
    print("=" * 80)

    evaluator = Evaluator(scaler=scaler, feature_keys=list(CONFIG.feature_keys))

    results = evaluator.evaluate(
        model=model,
        X=Xw_val,
        y_regime=y_regime_val,
        y_ret=yret_val,
        y_rv=yrv_val,
        y_ret_train=yret_train,
    )

    # Print results
    print("\nOverall Performance:")
    print(f"  MAE (mean): {results['overall']['mae_mean']:.4f}%")
    print(f"  Correlation (mean): {results['overall']['correlation_mean']:.3f}")
    print(f"  Direction Acc: {results['overall']['direction']['accuracy']:.2%}")
    print(f"    p-value: {results['overall']['direction']['p_value']:.4f}")

    if results['overall']['direction']['significant']:
        print("    ✓ Statistically significant")
    else:
        print("    ✗ Not significant")

    if "baselines" in results["overall"]:
        print(f"\nBaselines:")
        print(f"  Persistence MAE: {results['overall']['baselines']['mae_persistence']:.4f}%")
        print(f"  Mean MAE: {results['overall']['baselines']['mae_mean']:.4f}%")

    print("\nPer-Regime Performance:")
    for regime_name, metrics in results["per_regime"].items():
        print(f"\n  {regime_name}:")
        print(f"    Samples: {metrics['n_samples']}")
        print(f"    MAE: {metrics['mae']:.4f}%")
        print(f"    Direction Acc: {metrics['directional_acc']:.2%}")

    print(f"\nRegime Classification Accuracy: {results['regime_classification_acc']:.2%}")

    # ===== STEP 9: SAVE =====
    print("\n" + "=" * 80)
    print("STEP 9: SAVING RESULTS")
    print("=" * 80)

    save_results(results, scaler, CONFIG.output_dir)

    # Save model
    model.save(os.path.join(CONFIG.output_dir, "model.keras"))
    print(f"✓ Model saved to {CONFIG.output_dir}/model.keras")

    print("\n" + "=" * 80)
    print("TRAINING COMPLETE")
    print("=" * 80)
    print(f"\nOutputs:")
    print(f"  {CONFIG.output_dir}/model.keras")
    print(f"  {CONFIG.output_dir}/best_weights.h5")
    print(f"  {CONFIG.output_dir}/results.json")
    print(f"  {CONFIG.output_dir}/scaler.pkl")


if __name__ == "__main__":
    train_pipeline()
