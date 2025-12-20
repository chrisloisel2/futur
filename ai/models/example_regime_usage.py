"""
EXAMPLE: REGIME-AWARE MODEL - COMPLETE USAGE

Demonstrates:
1. Data preparation with regime labels
2. Model creation and training
3. Evaluation and interpretation
4. Inference on new data

This is a self-contained example with synthetic data.
For production, replace with your S3 data loading.
"""

import numpy as np
import tensorflow as tf
from typing import Tuple

from regime_aware_model import (
    RegimeConfig,
    RegimeAwareMarketModel,
    RegimeAwareTrainer,
    compute_regime_labels,
    compute_regime_statistics,
    evaluate_regime_expert_performance,
)


# ============================================================================
# STEP 1: GENERATE SYNTHETIC DATA WITH REGIME PATTERNS
# ============================================================================
def generate_synthetic_market_data(
    n_samples: int = 50000,
    n_features: int = 44,
    lookback: int = 256,
    horizon: int = 12,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list]:
    """
    Generate synthetic financial data with embedded regime patterns.

    Returns:
        X_windows: [N, lookback, n_features]
        y_regime: [N]
        y_ret: [N, horizon]
        y_rv: [N]
        feature_keys: list of feature names
    """
    print("\n" + "=" * 80)
    print("STEP 1: GENERATING SYNTHETIC MARKET DATA")
    print("=" * 80)

    # Full sequence (longer than windows needed)
    T = n_samples + lookback + horizon
    X_full = np.zeros((T, n_features), dtype=np.float32)

    # Feature keys (matching expected format)
    feature_keys = [
        "log_ret",
        "rv_ann_60",
        "rsi_14",
        "dist_ema_20",
        "dist_ema_50",
    ] + [f"feat_{i}" for i in range(n_features - 5)]

    print(f"\nGenerating {T} timesteps with {n_features} features...")

    # Generate base features
    for i in range(n_features):
        X_full[:, i] = np.random.randn(T) * 0.1

    # Inject regime-specific patterns
    regime_length = T // 5

    # REGIME 0: TREND (strong upward slope)
    start = regime_length * 0
    end = regime_length * 1
    X_full[start:end, 0] = np.abs(np.random.randn(end - start)) * 0.02  # positive returns
    X_full[start:end, 3] = np.linspace(0, 0.5, end - start)  # dist_ema_20 increasing
    X_full[start:end, 2] = 60 + np.random.randn(end - start) * 5  # RSI neutral-high

    # REGIME 1: MEAN_REVERT (RSI extremes)
    start = regime_length * 1
    end = regime_length * 2
    # Alternate between overbought and oversold
    for t in range(start, end):
        if (t - start) % 100 < 50:
            X_full[t, 2] = 80 + np.random.randn() * 5  # RSI overbought
            X_full[t, 0] = -np.abs(np.random.randn()) * 0.01  # negative returns
        else:
            X_full[t, 2] = 20 + np.random.randn() * 5  # RSI oversold
            X_full[t, 0] = np.abs(np.random.randn()) * 0.01  # positive returns

    # REGIME 2: HIGH_VOL (high volatility)
    start = regime_length * 2
    end = regime_length * 3
    X_full[start:end, 0] = np.random.randn(end - start) * 0.05  # large returns
    X_full[start:end, 1] = np.abs(np.random.randn(end - start)) * 0.8  # high RV

    # REGIME 3: LOW_VOL (low volatility)
    start = regime_length * 3
    end = regime_length * 4
    X_full[start:end, 0] = np.random.randn(end - start) * 0.002  # tiny returns
    X_full[start:end, 1] = np.abs(np.random.randn(end - start)) * 0.01  # low RV

    # REGIME 4: RANGE (flat, no trend)
    start = regime_length * 4
    end = T
    X_full[start:end, 0] = np.random.randn(end - start) * 0.01
    X_full[start:end, 3] = np.random.randn(end - start) * 0.05  # small dist_ema
    X_full[start:end, 4] = np.random.randn(end - start) * 0.05

    # Create windows
    print(f"Creating windows (lookback={lookback}, horizon={horizon})...")

    n_windows = n_samples
    X_windows = np.zeros((n_windows, lookback, n_features), dtype=np.float32)
    y_ret = np.zeros((n_windows, horizon), dtype=np.float32)
    y_rv = np.zeros(n_windows, dtype=np.float32)

    for i in range(n_windows):
        # Input window
        X_windows[i] = X_full[i : i + lookback]

        # Future returns and volatility
        future_ret = X_full[i + lookback : i + lookback + horizon, 0]  # log_ret
        future_rv = X_full[i + lookback : i + lookback + horizon, 1]  # rv

        y_ret[i] = future_ret
        y_rv[i] = np.sqrt(np.mean(future_rv**2))  # RMS volatility

    # Compute regime labels
    print("Computing regime labels...")
    y_regime_full = compute_regime_labels(X_full, feature_keys, lookback=lookback)

    # Align with windows (label at end of window)
    y_regime = np.zeros(n_windows, dtype=np.int32)
    for i in range(n_windows):
        y_regime[i] = y_regime_full[i + lookback - 1]

    # Statistics
    stats = compute_regime_statistics(y_regime)
    print("\nRegime distribution:")
    for k, v in stats.items():
        print(f"  {k}: {v:.2f}")

    print(f"\n✓ Generated {n_windows} samples")
    print(f"  X_windows: {X_windows.shape}")
    print(f"  y_regime: {y_regime.shape}")
    print(f"  y_ret: {y_ret.shape}")
    print(f"  y_rv: {y_rv.shape}")

    return X_windows, y_regime, y_ret, y_rv, feature_keys


# ============================================================================
# STEP 2: TRAIN REGIME-AWARE MODEL
# ============================================================================
def train_model(
    X_train: np.ndarray,
    y_regime_train: np.ndarray,
    y_ret_train: np.ndarray,
    y_rv_train: np.ndarray,
    X_val: np.ndarray,
    y_regime_val: np.ndarray,
    y_ret_val: np.ndarray,
    y_rv_val: np.ndarray,
) -> RegimeAwareMarketModel:
    """Train the regime-aware model"""
    print("\n" + "=" * 80)
    print("STEP 2: TRAINING REGIME-AWARE MODEL")
    print("=" * 80)

    # Config
    cfg = RegimeConfig(
        lookback=X_train.shape[1],
        horizon=y_ret_train.shape[1],
        batch_size=128,
        regime_d_model=64,
        regime_n_layers=3,
        expert_d_model=64,
        expert_n_layers=2,
        gating_mode="soft",
        entropy_weight=0.01,
        epochs=10,  # Reduced for demo
        seed=42,
    )

    print(f"\nConfig:")
    print(f"  Lookback: {cfg.lookback}")
    print(f"  Horizon: {cfg.horizon}")
    print(f"  Gating: {cfg.gating_mode}")
    print(f"  Epochs: {cfg.epochs}")

    # Create model
    print("\nCreating model...")
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=X_train.shape[-1])

    # Build
    dummy = tf.zeros((1, cfg.lookback, X_train.shape[-1]), dtype=tf.float32)
    _ = model(dummy, training=False)

    print(f"  Total parameters: {model.count_params():,}")
    print(f"  Classifier params: {model.regime_classifier.count_params():,}")
    print(f"  Expert params (×{cfg.n_regimes}): {model.experts[0].count_params():,} each")

    # Create datasets
    print("\nCreating datasets...")
    ds_train = tf.data.Dataset.from_tensor_slices(
        (X_train, y_regime_train, y_ret_train, y_rv_train)
    )
    ds_train = ds_train.shuffle(10000).batch(cfg.batch_size).prefetch(2)

    ds_val = tf.data.Dataset.from_tensor_slices(
        (X_val, y_regime_val, y_ret_val, y_rv_val)
    )
    ds_val = ds_val.batch(cfg.batch_size).prefetch(2)

    # Create trainer
    trainer = RegimeAwareTrainer(model=model, cfg=cfg)

    # Training loop
    print("\nTraining...")
    best_val_loss = float("inf")

    for epoch in range(cfg.epochs):
        print(f"\nEpoch {epoch + 1}/{cfg.epochs}")

        # Train
        trainer.train_loss_tracker.reset_states()
        trainer.regime_acc_tracker.reset_states()

        for x_batch, regime_batch, ret_batch, rv_batch in ds_train:
            trainer.train_step(x_batch, regime_batch, ret_batch, rv_batch)

        train_loss = trainer.train_loss_tracker.result()
        regime_acc = trainer.regime_acc_tracker.result()

        # Validation
        trainer.val_loss_tracker.reset_states()

        for x_batch, regime_batch, ret_batch, rv_batch in ds_val:
            trainer.val_step(x_batch, regime_batch, ret_batch, rv_batch)

        val_loss = trainer.val_loss_tracker.result()

        print(
            f"  Train Loss: {train_loss:.4f} | "
            f"Regime Acc: {regime_acc:.2%} | "
            f"Val Loss: {val_loss:.4f}"
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            print("  ✓ Best val loss")

    print("\n✓ Training complete")
    return model


# ============================================================================
# STEP 3: EVALUATE MODEL
# ============================================================================
def evaluate_model(
    model: RegimeAwareMarketModel,
    X_test: np.ndarray,
    y_regime_test: np.ndarray,
    y_ret_test: np.ndarray,
    y_rv_test: np.ndarray,
):
    """Evaluate the trained model"""
    print("\n" + "=" * 80)
    print("STEP 3: EVALUATING MODEL")
    print("=" * 80)

    results = evaluate_regime_expert_performance(
        model=model,
        X=X_test,
        y_regime=y_regime_test,
        y_ret=y_ret_test,
        y_rv=y_rv_test,
    )

    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]

    print("\nPer-Regime Performance:")
    print("-" * 80)

    for regime_name in regime_names:
        if regime_name in results:
            metrics = results[regime_name]
            print(f"\n{regime_name}:")
            print(f"  Samples: {metrics['n_samples']}")
            print(f"  Return MAE: {metrics['ret_mae']:.4f}")
            print(f"  Volatility MAE: {metrics['rv_mae']:.4f}")
            print(f"  Directional Accuracy: {metrics['directional_acc']:.2%}")

            if metrics["beats_random"]:
                print("  ✓ Beats random (> 50%)")
            else:
                print("  ✗ Does not beat random")

    print(f"\n{'=' * 80}")
    print(f"Regime Classification Accuracy: {results['regime_classification_acc']:.2%}")
    print(f"{'=' * 80}")

    # Success criteria
    print("\nSuccess Criteria:")

    # 1. Regime stability
    stats = compute_regime_statistics(y_regime_test)
    switching_rate = stats["switches_per_1000"]
    print(f"\n1. Regime Stability: {switching_rate:.1f} transitions/1000 steps")
    if 50 < switching_rate < 200:
        print("   ✓ PASS")
    else:
        print("   ✗ FAIL (should be 50-200)")

    # 2. Expert specialization
    print("\n2. Expert Specialization:")
    all_beat_random = all(
        results[name]["beats_random"]
        for name in regime_names
        if name in results
    )
    if all_beat_random:
        print("   ✓ PASS (all experts > 50%)")
    else:
        print("   ✗ FAIL (some experts ≤ 50%)")

    # 3. Regime classification
    regime_acc = results["regime_classification_acc"]
    print(f"\n3. Regime Classification: {regime_acc:.2%}")
    if regime_acc > 0.60:
        print("   ✓ PASS (> 60%)")
    else:
        print("   ✗ FAIL (should be > 60%)")


# ============================================================================
# STEP 4: INFERENCE EXAMPLE
# ============================================================================
def inference_example(
    model: RegimeAwareMarketModel,
    X_sample: np.ndarray,
):
    """Demonstrate inference on a single sample"""
    print("\n" + "=" * 80)
    print("STEP 4: INFERENCE EXAMPLE")
    print("=" * 80)

    # Ensure batch dimension
    if len(X_sample.shape) == 2:
        X_sample = tf.expand_dims(X_sample, axis=0)  # [1, L, F]

    print(f"\nInput shape: {X_sample.shape}")

    # Inference
    outputs = model(X_sample, training=False, return_regime_probs=True)

    ret_pred = outputs["ret"].numpy()[0]  # [horizon]
    rv_pred = outputs["rv"].numpy()[0]  # scalar
    regime_probs = outputs["regime_probs"].numpy()[0]  # [5]

    # Interpret
    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]
    current_regime = regime_names[np.argmax(regime_probs)]
    confidence = regime_probs.max()

    print("\nPredictions:")
    print(f"  Current Regime: {current_regime} (confidence: {confidence:.2%})")
    print(f"  Cumulative Return (12 steps): {ret_pred.sum():.4f}")
    print(f"  Predicted Volatility: {rv_pred:.4f}")

    print("\nRegime Probabilities:")
    for name, prob in zip(regime_names, regime_probs):
        bar = "█" * int(prob * 50)
        print(f"  {name:15s} {prob:.2%} {bar}")

    print("\nReturn Forecast (per step):")
    for i, r in enumerate(ret_pred):
        print(f"  Step {i+1:2d}: {r:+.4f}")

    # Trading signal example
    print("\nExample Trading Logic:")
    if regime_probs[2] > 0.7:  # HIGH_VOL
        print("  → REDUCE POSITION SIZE (high volatility detected)")
    elif regime_probs[0] > 0.6 and ret_pred.sum() > 0.01:  # TREND UP
        print("  → LONG SIGNAL (uptrend detected with positive forecast)")
    elif regime_probs[1] > 0.6:  # MEAN_REVERT
        if ret_pred.sum() < -0.01:
            print("  → SHORT SIGNAL (mean reversion + negative forecast)")
        else:
            print("  → WAIT (mean revert regime, unclear direction)")
    else:
        print("  → NEUTRAL (no clear signal)")


# ============================================================================
# MAIN
# ============================================================================
def main():
    """Run complete example"""
    print("\n" + "=" * 80)
    print("REGIME-AWARE MODEL - COMPLETE EXAMPLE")
    print("=" * 80)

    # Set random seed
    np.random.seed(42)
    tf.random.set_seed(42)

    # Step 1: Generate data
    X_all, y_regime_all, y_ret_all, y_rv_all, feature_keys = generate_synthetic_market_data(
        n_samples=20000,
        n_features=44,
        lookback=256,
        horizon=12,
    )

    # Split train/val/test (70/15/15)
    n = len(X_all)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    X_train = X_all[:train_end]
    y_regime_train = y_regime_all[:train_end]
    y_ret_train = y_ret_all[:train_end]
    y_rv_train = y_rv_all[:train_end]

    X_val = X_all[train_end:val_end]
    y_regime_val = y_regime_all[train_end:val_end]
    y_ret_val = y_ret_all[train_end:val_end]
    y_rv_val = y_rv_all[train_end:val_end]

    X_test = X_all[val_end:]
    y_regime_test = y_regime_all[val_end:]
    y_ret_test = y_ret_all[val_end:]
    y_rv_test = y_rv_all[val_end:]

    print(f"\nData split:")
    print(f"  Train: {len(X_train)} samples")
    print(f"  Val: {len(X_val)} samples")
    print(f"  Test: {len(X_test)} samples")

    # Step 2: Train
    model = train_model(
        X_train,
        y_regime_train,
        y_ret_train,
        y_rv_train,
        X_val,
        y_regime_val,
        y_ret_val,
        y_rv_val,
    )

    # Step 3: Evaluate
    evaluate_model(model, X_test, y_regime_test, y_ret_test, y_rv_test)

    # Step 4: Inference
    sample_idx = 100
    inference_example(model, X_test[sample_idx])

    print("\n" + "=" * 80)
    print("EXAMPLE COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("  1. Replace synthetic data with real S3 data (regime_pipeline.py)")
    print("  2. Tune hyperparameters (RegimeConfig)")
    print("  3. Validate on out-of-sample test set (≥ 3 months)")
    print("  4. Backtest with transaction costs")
    print("  5. Deploy to production")


if __name__ == "__main__":
    import os

    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    main()
