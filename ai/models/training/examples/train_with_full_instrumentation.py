"""
EXAMPLE: Training with Full ML Instrumentation
================================================

This script demonstrates proper usage of the ML instrumentation framework.

CRITICAL: This is the MINIMUM acceptable standard for ML training.
"""

from __future__ import annotations
import os
import sys
import time
import argparse
from pathlib import Path

import numpy as np
import tensorflow as tf

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from training.common.instrumented_trainer import (
    InstrumentedTrainer,
    InstrumentedTrainingConfig
)


# ============================================================================
# DUMMY MODEL (replace with your actual model)
# ============================================================================

def create_dummy_model(input_dim: int) -> tf.keras.Model:
    """Create a simple binary classifier for demonstration."""
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(64, activation='relu', input_shape=(input_dim,)),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(32, activation='relu'),
        tf.keras.layers.Dropout(0.1),
        tf.keras.layers.Dense(1, activation='sigmoid')
    ])
    return model


# ============================================================================
# DUMMY DATA GENERATOR (replace with real data loading)
# ============================================================================

def generate_dummy_data(n_samples: int, n_features: int, seed: int = 42):
    """
    Generate synthetic data for demonstration.

    In production: Load from S3/Parquet with proper temporal splits.
    """
    np.random.seed(seed)

    # Features
    X = np.random.randn(n_samples, n_features).astype(np.float32)

    # Target: binary classification based on feature sum
    signal_strength = X.sum(axis=1)
    y = (signal_strength > 0).astype(np.float32)

    # Future returns (for paper trading)
    # CRITICAL: These must be truly "future" relative to X
    returns = signal_strength * 0.01 + np.random.randn(n_samples) * 0.005
    returns = returns.astype(np.float32)

    return X, y, returns


def split_data_temporal(X, y, returns, train_frac=0.6, val_frac=0.2):
    """
    Temporal split (CRITICAL: no shuffle).

    Train: [0, 60%]
    Val:   [60%, 80%]
    Test:  [80%, 100%]
    """
    n = len(X)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))

    train_indices = np.arange(0, train_end)
    val_indices = np.arange(train_end, val_end)
    test_indices = np.arange(val_end, n)

    return {
        "train": (X[:train_end], y[:train_end], returns[:train_end], train_indices),
        "val": (X[train_end:val_end], y[train_end:val_end], returns[train_end:val_end], val_indices),
        "test": (X[val_end:], y[val_end:], returns[val_end:], test_indices)
    }


# ============================================================================
# MAIN TRAINING SCRIPT
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch_size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--n_samples", type=int, default=10000)
    parser.add_argument("--n_features", type=int, default=16)
    parser.add_argument("--artifact_dir", default="artifacts/training_runs")
    args = parser.parse_args()

    print("=" * 80)
    print("FULLY INSTRUMENTED TRAINING EXAMPLE")
    print("=" * 80)

    # ========================================================================
    # 1. GENERATE DATA
    # ========================================================================
    print("\n[1/6] Generating data...")

    X, y, returns = generate_dummy_data(args.n_samples, args.n_features)
    data_splits = split_data_temporal(X, y, returns)

    X_train, y_train, returns_train, train_indices = data_splits["train"]
    X_val, y_val, returns_val, val_indices = data_splits["val"]
    X_test, y_test, returns_test, test_indices = data_splits["test"]

    print(f"   Train: {len(X_train):,} samples")
    print(f"   Val:   {len(X_val):,} samples")
    print(f"   Test:  {len(X_test):,} samples")

    # ========================================================================
    # 2. CREATE MODEL
    # ========================================================================
    print("\n[2/6] Creating model...")

    model = create_dummy_model(input_dim=args.n_features)
    optimizer = tf.keras.optimizers.Adam(learning_rate=args.lr)

    print(f"   Trainable params: {model.count_params():,}")

    # ========================================================================
    # 3. INITIALIZE INSTRUMENTATION
    # ========================================================================
    print("\n[3/6] Initializing instrumentation...")

    run_id = time.strftime("%Y%m%d-%H%M%S")

    config = InstrumentedTrainingConfig(
        run_id=run_id,
        model_name="DummyClassifier",
        artifact_dir=args.artifact_dir,
        # Acceptance criteria
        min_sharpe=0.5,
        max_drawdown=-0.25,
        warmup_epochs=5,
        min_roi=-0.15,
        # Trading costs
        fee_rate=0.001,  # 10 bps
        spread_bps=5.0,  # 5 bps
        latency_bars=1,
        # Misc
        check_data_leakage=True
    )

    trainer = InstrumentedTrainer(config, model, optimizer)

    # Log startup info
    feature_names = [f"feature_{i}" for i in range(args.n_features)]

    trainer.log_startup_info(
        training_config={
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.lr,
            "optimizer": "Adam",
            "architecture": "MLP",
        },
        dataset_info={
            "total_samples": args.n_samples,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "test_samples": len(X_test),
            "n_features": args.n_features,
            "feature_names": feature_names,
            "period": "synthetic",
            "horizon": "1-step",
        }
    )

    # ========================================================================
    # 4. DATA QUALITY VALIDATION
    # ========================================================================
    print("\n[4/6] Validating data quality...")

    leakage_report = trainer.check_data_quality(
        X_train=X_train,
        X_val=X_val,
        train_indices=train_indices,
        val_indices=val_indices,
        feature_names=feature_names
    )

    if not leakage_report.is_safe():
        print("❌ DATA QUALITY FAILED")
        sys.exit(1)

    print("   ✅ Data validation passed")

    # ========================================================================
    # 5. TRAINING LOOP
    # ========================================================================
    print(f"\n[5/6] Training for {args.epochs} epochs...\n")

    # Create TF dataset
    train_dataset = tf.data.Dataset.from_tensor_slices((X_train, y_train))
    train_dataset = train_dataset.shuffle(buffer_size=1024, seed=42).batch(args.batch_size)

    for epoch in range(args.epochs):
        # === TRAINING ===
        train_losses = []
        grad_norms = []

        for batch_X, batch_y in train_dataset:
            loss, grad_norm = trainer.train_step(
                batch_X=batch_X,
                batch_y=batch_y,
                loss_fn=tf.keras.losses.binary_crossentropy
            )
            train_losses.append(loss)
            grad_norms.append(grad_norm)

        avg_train_loss = np.mean(train_losses)
        avg_grad_norm = np.mean(grad_norms)

        # === VALIDATION & MONITORING ===
        should_stop, reason = trainer.validate_and_monitor(
            epoch=epoch,
            val_data=(X_val, y_val, returns_val),
            test_data=(X_test, y_test, returns_test),
            train_loss=avg_train_loss,
            grad_norm=avg_grad_norm,
            get_predictions=lambda X: model.predict(X, verbose=0).squeeze()
        )

        if should_stop:
            print(f"\n🛑 EARLY STOP TRIGGERED")
            print(f"   Reason: {reason}")
            print(f"   Epoch: {epoch}")
            break

    # ========================================================================
    # 6. FINALIZATION
    # ========================================================================
    print(f"\n[6/6] Finalizing...")

    trainer.finalize()

    print("\n" + "=" * 80)
    print("TRAINING COMPLETE")
    print("=" * 80)
    print(f"\n📊 Artifacts saved to: {trainer.artifact_manager.base_dir}")
    print(f"\nNext steps:")
    print(f"  1. Review report.md")
    print(f"  2. Check visualizations/dashboard.png")
    print(f"  3. Inspect logs.jsonl for detailed history")
    print(f"  4. Validate production acceptance criteria")
    print()


if __name__ == "__main__":
    main()
