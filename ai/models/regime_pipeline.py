"""
REGIME-AWARE MODEL - FULL TRAINING PIPELINE

Integration with existing model.py infrastructure:
- Reuses scaler, windowing, S3 loader
- Adds regime label computation
- Two-phase training support
- Comprehensive evaluation

Usage:
    export S3_BUCKET="your-bucket"
    export S3_PREFIX="btc/1m/"
    python regime_pipeline.py
"""

from __future__ import annotations

import os
import sys
import json
import time
from typing import Dict, Tuple, Optional

import numpy as np
import tensorflow as tf

# Import regime-aware components
from regime_aware_model import (
    RegimeConfig,
    RegimeAwareMarketModel,
    RegimeAwareTrainer,
    compute_regime_labels,
    compute_regime_statistics,
    evaluate_regime_expert_performance,
)

# Import infrastructure from model.py
from model import (
    FEATURE_KEYS,
    TARGET_RET_KEY,
    TARGET_RV_KEY,
    RunningRobustScaler,
    iter_s3_jsonl,
    build_numpy_from_stream,
    make_windows,
    set_seed,
)


# =========================
# REGIME LABEL ALIGNMENT
# =========================
def align_regime_labels_with_windows(
    y_regime_full: np.ndarray,
    lookback: int,
    n_windows: int,
    stride: int = 1,
) -> np.ndarray:
    """
    Align regime labels computed on full sequence with windowed data.

    Logic:
        Window i uses timesteps [i*stride : i*stride + lookback]
        The regime label for window i is the label at timestep (i*stride + lookback - 1)
        (i.e., the regime at the END of the input window)

    Args:
        y_regime_full: [T] - regime labels for full sequence
        lookback: window size
        n_windows: number of windows created by make_windows
        stride: stride used in make_windows

    Returns:
        y_regime_windows: [n_windows] - regime labels aligned with windows
    """
    y_regime_windows = np.zeros(n_windows, dtype=np.int32)

    for i in range(n_windows):
        # Window i ends at timestep (i*stride + lookback - 1)
        end_idx = i * stride + lookback - 1
        if end_idx < len(y_regime_full):
            y_regime_windows[i] = y_regime_full[end_idx]
        else:
            # Fallback (shouldn't happen if make_windows is correct)
            y_regime_windows[i] = y_regime_full[-1]

    return y_regime_windows


# =========================
# TRAINING PIPELINE
# =========================
def train_regime_aware_model(
    Xw_train: np.ndarray,
    yret_train: np.ndarray,
    yrv_train: np.ndarray,
    X_train_full: np.ndarray,
    Xw_val: np.ndarray,
    yret_val: np.ndarray,
    yrv_val: np.ndarray,
    X_val_full: np.ndarray,
    cfg: RegimeConfig,
    out_dir: str = "regime_out",
    two_phase: bool = False,
) -> Tuple[RegimeAwareMarketModel, Dict]:
    """
    Full training pipeline for regime-aware model.

    Args:
        Xw_train: [N_train, lookback, F] - training windows
        yret_train: [N_train, horizon] - training return targets
        yrv_train: [N_train] - training volatility targets
        X_train_full: [T_train, F] - full training sequence (for regime computation)
        Xw_val: [N_val, lookback, F] - validation windows
        yret_val: [N_val, horizon] - validation return targets
        yrv_val: [N_val] - validation volatility targets
        X_val_full: [T_val, F] - full validation sequence
        cfg: RegimeConfig
        out_dir: output directory
        two_phase: if True, use two-phase training

    Returns:
        model: trained RegimeAwareMarketModel
        results: evaluation results dict
    """
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 80)
    print("REGIME-AWARE MODEL TRAINING PIPELINE")
    print("=" * 80)

    # 1) Compute regime labels
    print("\n[1/6] Computing regime labels...")
    start = time.time()

    y_regime_train_full = compute_regime_labels(
        X_train_full, FEATURE_KEYS, lookback=cfg.lookback
    )
    y_regime_val_full = compute_regime_labels(
        X_val_full, FEATURE_KEYS, lookback=cfg.lookback
    )

    # Align with windows
    y_regime_train = align_regime_labels_with_windows(
        y_regime_train_full, cfg.lookback, len(Xw_train), stride=1
    )
    y_regime_val = align_regime_labels_with_windows(
        y_regime_val_full, cfg.lookback, len(Xw_val), stride=1
    )

    print(f"  Done in {time.time() - start:.1f}s")
    print(f"  Train regime shape: {y_regime_train.shape}")
    print(f"  Val regime shape: {y_regime_val.shape}")

    # 2) Regime statistics
    print("\n[2/6] Regime statistics...")
    print("\nTrain:")
    stats_train = compute_regime_statistics(y_regime_train)
    for k, v in sorted(stats_train.items()):
        print(f"  {k}: {v:.2f}")

    print("\nValidation:")
    stats_val = compute_regime_statistics(y_regime_val)
    for k, v in sorted(stats_val.items()):
        print(f"  {k}: {v:.2f}")

    # Save stats
    with open(os.path.join(out_dir, "regime_statistics.json"), "w") as f:
        json.dump({"train": stats_train, "val": stats_val}, f, indent=2)

    # 3) Create model
    print("\n[3/6] Creating model...")
    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=Xw_train.shape[-1])

    # Build
    dummy = tf.zeros((1, cfg.lookback, Xw_train.shape[-1]), dtype=tf.float32)
    _ = model(dummy, training=False)

    print(f"  Total parameters: {model.count_params():,}")
    print(f"  Regime classifier params: {model.regime_classifier.count_params():,}")
    print(f"  Expert params (each): {model.experts[0].count_params():,}")
    print(f"  Gating mode: {cfg.gating_mode}")

    # 4) Create datasets
    print("\n[4/6] Creating datasets...")
    ds_train = tf.data.Dataset.from_tensor_slices((
        Xw_train, y_regime_train, yret_train, yrv_train
    ))
    ds_train = ds_train.shuffle(50000).batch(cfg.batch_size).prefetch(2)

    ds_val = tf.data.Dataset.from_tensor_slices((
        Xw_val, y_regime_val, yret_val, yrv_val
    ))
    ds_val = ds_val.batch(cfg.batch_size).prefetch(2)

    # 5) Training
    print("\n[5/6] Training...")

    if two_phase:
        print("\n>>> PHASE 1: Pre-train regime classifier <<<")
        # Freeze experts
        for expert in model.experts:
            expert.trainable = False

        trainer_phase1 = RegimeAwareTrainer(model=model, cfg=cfg)
        _train_loop(
            trainer_phase1,
            ds_train,
            ds_val,
            epochs=cfg.pretrain_regime_epochs,
            out_dir=out_dir,
            phase_name="phase1",
        )

        # Unfreeze
        for expert in model.experts:
            expert.trainable = True

        print("\n>>> PHASE 2: Joint training <<<")
        trainer_phase2 = RegimeAwareTrainer(model=model, cfg=cfg)
        _train_loop(
            trainer_phase2,
            ds_train,
            ds_val,
            epochs=cfg.epochs - cfg.pretrain_regime_epochs,
            out_dir=out_dir,
            phase_name="phase2",
        )

    else:
        # Single-phase joint training
        trainer = RegimeAwareTrainer(model=model, cfg=cfg)
        _train_loop(
            trainer,
            ds_train,
            ds_val,
            epochs=cfg.epochs,
            out_dir=out_dir,
            phase_name="joint",
        )

    # Load best weights
    best_weights_path = os.path.join(out_dir, "best_weights.h5")
    if os.path.exists(best_weights_path):
        model.load_weights(best_weights_path)
        print(f"\nLoaded best weights from {best_weights_path}")

    # 6) Evaluation
    print("\n[6/6] Evaluating...")
    results = evaluate_regime_expert_performance(
        model=model,
        X=Xw_val,
        y_regime=y_regime_val,
        y_ret=yret_val,
        y_rv=yrv_val,
    )

    print("\n" + "=" * 80)
    print("EVALUATION RESULTS")
    print("=" * 80)

    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]

    for regime_name in regime_names:
        if regime_name in results:
            metrics = results[regime_name]
            print(f"\n{regime_name}:")
            for k, v in metrics.items():
                if isinstance(v, float):
                    print(f"  {k}: {v:.4f}")
                elif isinstance(v, bool):
                    symbol = "✓" if v else "✗"
                    print(f"  {k}: {symbol}")
                else:
                    print(f"  {k}: {v}")

    if "regime_classification_acc" in results:
        print(f"\n{'=' * 80}")
        print(f"Regime Classification Accuracy: {results['regime_classification_acc']:.2%}")
        print(f"{'=' * 80}")

    # Save results
    with open(os.path.join(out_dir, "evaluation_results.json"), "w") as f:
        # Convert bool to str for JSON
        results_serializable = {}
        for k, v in results.items():
            if isinstance(v, dict):
                results_serializable[k] = {
                    kk: (str(vv) if isinstance(vv, bool) else vv)
                    for kk, vv in v.items()
                }
            else:
                results_serializable[k] = v
        json.dump(results_serializable, f, indent=2)

    # Save model
    model.save(os.path.join(out_dir, "final_model.keras"))
    print(f"\nModel saved to {out_dir}/final_model.keras")

    return model, results


def _train_loop(
    trainer: RegimeAwareTrainer,
    ds_train: tf.data.Dataset,
    ds_val: tf.data.Dataset,
    epochs: int,
    out_dir: str,
    phase_name: str,
):
    """
    Training loop with early stopping.

    Args:
        trainer: RegimeAwareTrainer instance
        ds_train: training dataset
        ds_val: validation dataset
        epochs: number of epochs
        out_dir: output directory
        phase_name: "phase1", "phase2", or "joint"
    """
    best_val_loss = float("inf")
    patience_counter = 0
    max_patience = 5

    for epoch in range(epochs):
        print(f"\nEpoch {epoch + 1}/{epochs}")

        # Train
        trainer.train_loss_tracker.reset_states()
        trainer.regime_acc_tracker.reset_states()

        for step, (x_batch, regime_batch, ret_batch, rv_batch) in enumerate(ds_train):
            trainer.train_step(x_batch, regime_batch, ret_batch, rv_batch)

            # Print progress every 100 steps
            if (step + 1) % 100 == 0:
                train_loss = trainer.train_loss_tracker.result()
                regime_acc = trainer.regime_acc_tracker.result()
                print(
                    f"  Step {step + 1}: "
                    f"Loss={train_loss:.4f}, "
                    f"RegimeAcc={regime_acc:.2%}",
                    end="\r"
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
            trainer.model.save_weights(os.path.join(out_dir, "best_weights.h5"))
            print(f"  ✓ Best val loss: {best_val_loss:.4f}")
        else:
            patience_counter += 1
            print(f"  Patience: {patience_counter}/{max_patience}")
            if patience_counter >= max_patience:
                print("  Early stopping triggered")
                break


# =========================
# MAIN
# =========================
def main():
    """
    Full pipeline matching model.py structure.

    Steps:
    1. Load data from S3
    2. Fit scaler
    3. Transform + windowing
    4. Temporal split
    5. Train regime-aware model
    6. Evaluate
    """
    # Config
    cfg = RegimeConfig(
        lookback=256,
        horizon=12,
        batch_size=256,
        regime_backbone="cnn",
        regime_d_model=64,
        regime_n_layers=3,
        expert_type="tcn",
        expert_d_model=64,
        expert_n_layers=2,
        gating_mode="soft",
        entropy_weight=0.01,
        epochs=20,
        pretrain_regime_epochs=5,
        w_regime=0.3,
        w_ret=1.0,
        w_rv=0.4,
        seed=1337,
    )

    set_seed(cfg.seed)

    # S3 params
    bucket = os.environ.get("S3_BUCKET", "").strip()
    prefix = os.environ.get("S3_PREFIX", "").strip()
    aws_profile = os.environ.get("AWS_PROFILE", "").strip() or None
    region = os.environ.get("AWS_REGION", "").strip() or None

    if not bucket or not prefix:
        print("ERROR: Set S3_BUCKET and S3_PREFIX environment variables")
        print("Example:")
        print("  export S3_BUCKET=my-bucket")
        print("  export S3_PREFIX=btc/1m/")
        sys.exit(1)

    print("=" * 80)
    print("DATA LOADING")
    print("=" * 80)

    # 1) Fit scaler
    print("\n[1/4] Fitting scaler...")
    scaler = RunningRobustScaler(
        feature_dim=len(FEATURE_KEYS), reservoir_size=200_000, seed=cfg.seed
    )
    stream1 = iter_s3_jsonl(
        bucket=bucket, prefix=prefix, region=region, aws_profile=aws_profile
    )
    X_all, y_ret, y_rv = build_numpy_from_stream(
        stream1, scaler=scaler, limit_rows=None
    )
    scaler.finalize()

    print(f"  Loaded {X_all.shape[0]} timesteps")
    print(f"  Feature dim: {X_all.shape[1]}")

    # 2) Transform
    print("\n[2/4] Transforming...")
    X_all = scaler.transform(X_all)

    # 3) Windowing
    print("\n[3/4] Creating windows...")
    Xw, yret_h, ydir, yrv_h = make_windows(
        X_all,
        y_ret,
        y_rv,
        lookback=cfg.lookback,
        horizon=cfg.horizon,
        stride=1,
    )

    print(f"  Windows shape: {Xw.shape}")
    print(f"  Returns shape: {yret_h.shape}")
    print(f"  Volatility shape: {yrv_h.shape}")

    # 4) Temporal split
    print("\n[4/4] Temporal split (90/10)...")
    n = Xw.shape[0]
    split = int(n * 0.9)

    Xw_train, Xw_val = Xw[:split], Xw[split:]
    yret_train, yret_val = yret_h[:split], yret_h[split:]
    yrv_train, yrv_val = yrv_h[:split], yrv_h[split:]

    # For regime computation, keep full sequences
    # Split X_all at corresponding temporal point
    # Window i uses X_all[i : i+lookback], so split_full = split + lookback
    split_full = split + cfg.lookback
    if split_full > len(X_all):
        split_full = len(X_all)

    X_train_full = X_all[:split_full]
    X_val_full = X_all[split_full - cfg.lookback :]  # Overlap by lookback for first window

    print(f"  Train windows: {Xw_train.shape[0]}")
    print(f"  Val windows: {Xw_val.shape[0]}")
    print(f"  Train full seq: {X_train_full.shape[0]}")
    print(f"  Val full seq: {X_val_full.shape[0]}")

    # 5) Train
    two_phase = True  # Set to False for single-phase joint training

    model, results = train_regime_aware_model(
        Xw_train,
        yret_train,
        yrv_train,
        X_train_full,
        Xw_val,
        yret_val,
        yrv_val,
        X_val_full,
        cfg=cfg,
        out_dir="regime_out",
        two_phase=two_phase,
    )

    # 6) Success criteria check
    print("\n" + "=" * 80)
    print("SUCCESS CRITERIA VALIDATION")
    print("=" * 80)

    # Criterion 1: Regime stability
    stats_val = compute_regime_statistics(
        align_regime_labels_with_windows(
            compute_regime_labels(X_val_full, FEATURE_KEYS, cfg.lookback),
            cfg.lookback,
            len(Xw_val),
            stride=1,
        )
    )
    switching_rate = stats_val["switches_per_1000"]
    print(f"\n1. Regime Stability:")
    print(f"   Switching rate: {switching_rate:.1f} transitions/1000 steps")
    if 50 < switching_rate < 200:
        print("   ✓ PASS (50-200 range)")
    else:
        print("   ✗ FAIL (should be 50-200)")

    # Criterion 2: Expert specialization
    print(f"\n2. Expert Specialization:")
    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]
    all_beat_random = True
    for regime_name in regime_names:
        if regime_name in results:
            dir_acc = results[regime_name].get("directional_acc", 0.0)
            beats_random = dir_acc > 0.50
            symbol = "✓" if beats_random else "✗"
            print(f"   {regime_name}: {dir_acc:.2%} {symbol}")
            if not beats_random:
                all_beat_random = False

    if all_beat_random:
        print("   ✓ PASS (all experts > 50%)")
    else:
        print("   ✗ FAIL (some experts ≤ 50%)")

    # Criterion 3: Regime classification
    regime_acc = results.get("regime_classification_acc", 0.0)
    print(f"\n3. Regime Classification:")
    print(f"   Accuracy: {regime_acc:.2%}")
    if regime_acc > 0.60:
        print("   ✓ PASS (> 60%)")
    else:
        print("   ✗ FAIL (should be > 60%)")

    print("\n" + "=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    print(f"\nOutputs saved to: regime_out/")
    print(f"  - final_model.keras")
    print(f"  - best_weights.h5")
    print(f"  - evaluation_results.json")
    print(f"  - regime_statistics.json")


if __name__ == "__main__":
    main()
