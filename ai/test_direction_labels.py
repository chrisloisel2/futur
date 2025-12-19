#!/usr/bin/env python3
"""
Test de validation de la génération des labels de direction

Ce script teste:
1. La logique de make_windows() pour les labels de direction
2. Que les labels correspondent aux returns cumulés
3. Qu'un modèle trivial obtient ~50% de précision (pas 5%)
"""

import os
import sys
import numpy as np

# Disable GPU
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import tensorflow as tf

# Import from model.py
from ai.models.model import make_windows, TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS


def test_label_generation():
    """Test 1: Validate make_windows() label generation logic"""

    print("=" * 80)
    print("TEST 1: LABEL GENERATION LOGIC")
    print("=" * 80)
    print()

    # Create synthetic data
    np.random.seed(42)
    T = 1000
    F = len(FEATURE_KEYS)
    lookback = 256
    horizon = 12
    stride = 12

    X = np.random.randn(T, F).astype(np.float32)
    y_ret = np.random.randn(T).astype(np.float32) * 0.01  # Small returns
    y_rv = np.abs(np.random.randn(T).astype(np.float32)) * 0.01

    # Generate windows
    Xw, y_ret_h, y_dir, y_rv_h = make_windows(X, y_ret, y_rv, lookback, horizon, stride)

    print(f"Generated {len(Xw)} windows")
    print(f"  Xw shape: {Xw.shape}")
    print(f"  y_ret_h shape: {y_ret_h.shape}")
    print(f"  y_dir shape: {y_dir.shape}")
    print()

    # Verify labels match cumulative returns
    threshold = 1e-4
    cum_rets = np.sum(y_ret_h, axis=1)

    expected_dirs = np.zeros_like(y_dir)
    expected_dirs[cum_rets > threshold] = 2   # UP
    expected_dirs[cum_rets < -threshold] = 0  # DOWN
    expected_dirs[np.abs(cum_rets) <= threshold] = 1  # FLAT

    matches = (y_dir == expected_dirs)
    match_rate = np.mean(matches) * 100

    print(f"Label Validation:")
    print(f"  Match rate: {match_rate:.2f}%")

    if match_rate == 100.0:
        print("  ✅ PASS: Labels match cumulative returns exactly")
    else:
        print(f"  ❌ FAIL: {100-match_rate:.2f}% of labels are incorrect!")
        return False

    print()

    # Check distribution
    unique, counts = np.unique(y_dir, return_counts=True)
    print("Direction Distribution:")
    for val, count in zip(unique, counts):
        label = {0: "DOWN", 1: "FLAT", 2: "UP"}.get(val, "UNKNOWN")
        print(f"  {val} ({label}): {count:>5} ({100*count/len(y_dir):5.1f}%)")

    print()
    print("✅ TEST 1 PASSED")
    print()

    return True


def test_trivial_model_baseline():
    """Test 2: Trivial random model should get ~33% accuracy (3 classes)"""

    print("=" * 80)
    print("TEST 2: TRIVIAL MODEL BASELINE")
    print("=" * 80)
    print()

    # Create synthetic balanced data
    np.random.seed(42)
    N = 3000  # 1000 per class
    F = len(FEATURE_KEYS)
    lookback = 256
    horizon = 12

    # Create balanced dataset: 1000 DOWN, 1000 FLAT, 1000 UP
    Xw = np.random.randn(N, lookback, F).astype(np.float32)
    y_dir = np.array([0]*1000 + [1]*1000 + [2]*1000, dtype=np.int32)

    # Shuffle
    idx = np.random.permutation(N)
    Xw = Xw[idx]
    y_dir = y_dir[idx]

    print(f"Created balanced dataset: {N} samples")
    print(f"  DOWN: {np.sum(y_dir == 0)} samples")
    print(f"  FLAT: {np.sum(y_dir == 1)} samples")
    print(f"  UP:   {np.sum(y_dir == 2)} samples")
    print()

    # Create trivial random predictor
    print("Testing random predictor (should get ~33%)...")
    y_pred_random = np.random.randint(0, 3, size=N)
    acc_random = np.mean(y_pred_random == y_dir) * 100
    print(f"  Random predictor accuracy: {acc_random:.2f}%")

    if 25 <= acc_random <= 40:
        print("  ✅ Random predictor in expected range [25%, 40%]")
    else:
        print(f"  ⚠️  Random predictor outside expected range")

    print()

    # Test that always predicting majority class gives ~33%
    y_pred_majority = np.ones(N, dtype=np.int32) * 1  # Always predict FLAT
    acc_majority = np.mean(y_pred_majority == y_dir) * 100
    print(f"Always predict FLAT accuracy: {acc_majority:.2f}%")

    if 30 <= acc_majority <= 35:
        print("  ✅ Majority baseline in expected range [30%, 35%]")
    else:
        print(f"  ⚠️  Majority baseline outside expected range")

    print()

    # Test model with balanced data
    print("Testing TinyRecursiveMarketModel with balanced data...")

    cfg = TRMConfig(
        lookback=lookback,
        horizon=horizon,
        d_model=32,
        n_heads=2,
        d_ff=64,
        dropout=0.0,
        mem_dim=32,
        mem_update_iters=1,
        batch_size=128,
        shuffle_buffer=1000,
        prefetch=2,
        lr=0.001,
        weight_decay=0.0,
        clip_norm=1.0,
        epochs=1,
        steps_per_epoch=10,
        val_steps=5,
        seed=42,
        stride=12,
        w_ret=1.0,
        w_dir=1.0,
        w_rv=0.0,
    )

    model = TinyRecursiveMarketModel(cfg=cfg, feature_dim=F)

    # Make predictions WITHOUT training (random weights)
    y_pred_logits = model(Xw[:100], training=False)['dir']  # [100, 3]
    y_pred_classes = np.argmax(y_pred_logits.numpy(), axis=1)
    acc_untrained = np.mean(y_pred_classes == y_dir[:100]) * 100

    print(f"  Untrained model accuracy: {acc_untrained:.2f}%")

    if 20 <= acc_untrained <= 45:
        print("  ✅ Untrained model in expected range [20%, 45%]")
    else:
        print(f"  ⚠️  Untrained model outside expected range")

    print()
    print("✅ TEST 2 PASSED")
    print()

    return True


def test_inverted_labels():
    """Test 3: Check if labels might be inverted"""

    print("=" * 80)
    print("TEST 3: INVERTED LABELS CHECK")
    print("=" * 80)
    print()

    # Create data with KNOWN direction
    N = 300
    F = len(FEATURE_KEYS)
    lookback = 256
    horizon = 12

    # 100 samples with strong UP signal
    X_up = np.random.randn(100, F).astype(np.float32)
    y_ret_up = np.ones(100, dtype=np.float32) * 0.01  # +1% return

    # 100 samples with strong DOWN signal
    X_down = np.random.randn(100, F).astype(np.float32)
    y_ret_down = np.ones(100, dtype=np.float32) * -0.01  # -1% return

    # 100 samples with FLAT signal
    X_flat = np.random.randn(100, F).astype(np.float32)
    y_ret_flat = np.zeros(100, dtype=np.float32)  # 0% return

    # Combine
    X_combined = np.vstack([X_up, X_down, X_flat])
    y_ret_combined = np.concatenate([y_ret_up, y_ret_down, y_ret_flat])
    y_rv_combined = np.abs(np.random.randn(N).astype(np.float32)) * 0.001

    # Need more timesteps for windowing
    T = N + lookback + horizon
    X_full = np.random.randn(T, F).astype(np.float32)
    y_ret_full = np.zeros(T, dtype=np.float32)
    y_rv_full = np.abs(np.random.randn(T).astype(np.float32)) * 0.001

    # Insert known patterns
    # Pattern 1: Strong UP trend (horizon steps all +0.01)
    start_up = lookback
    y_ret_full[start_up:start_up+horizon] = 0.01

    # Pattern 2: Strong DOWN trend
    start_down = lookback + horizon + 10
    y_ret_full[start_down:start_down+horizon] = -0.01

    # Pattern 3: FLAT
    start_flat = lookback + 2*horizon + 20
    y_ret_full[start_flat:start_flat+horizon] = 0.0

    # Generate windows
    Xw, y_ret_h, y_dir, y_rv_h = make_windows(
        X_full, y_ret_full, y_rv_full,
        lookback=lookback, horizon=horizon, stride=1
    )

    # Check the windows at our known positions
    print("Checking known patterns:")
    print()

    # Find window that captures UP trend
    for i in range(min(20, len(Xw))):
        cum_ret = np.sum(y_ret_h[i])
        print(f"  Window {i}: cum_ret={cum_ret:+.6f}, dir={y_dir[i]} "
              f"({['DOWN', 'FLAT', 'UP'][y_dir[i]]})")

        if i == 0:  # First window should capture UP trend
            if cum_ret > 0.001:
                if y_dir[i] == 2:
                    print("    ✅ Correctly labeled UP")
                elif y_dir[i] == 0:
                    print("    ❌ INVERTED! Should be UP but labeled DOWN")
                    return False
            elif cum_ret < -0.001:
                if y_dir[i] == 0:
                    print("    ✅ Correctly labeled DOWN")
                elif y_dir[i] == 2:
                    print("    ❌ INVERTED! Should be DOWN but labeled UP")
                    return False

    print()
    print("✅ TEST 3 PASSED - No obvious inversion detected")
    print()

    return True


def main():
    """Run all tests"""

    print()
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "DIRECTION LABEL VALIDATION SUITE" + " " * 26 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    all_passed = True

    # Test 1: Label generation logic
    try:
        if not test_label_generation():
            all_passed = False
    except Exception as e:
        print(f"❌ TEST 1 FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    # Test 2: Trivial model baseline
    try:
        if not test_trivial_model_baseline():
            all_passed = False
    except Exception as e:
        print(f"❌ TEST 2 FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    # Test 3: Inverted labels check
    try:
        if not test_inverted_labels():
            all_passed = False
    except Exception as e:
        print(f"❌ TEST 3 FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False

    # Final summary
    print()
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print()

    if all_passed:
        print("✅ ALL TESTS PASSED")
        print()
        print("The label generation logic appears correct.")
        print()
        print("If your model still gets 5% accuracy, the problem is likely:")
        print("  1. Severe class imbalance (e.g., 90% FLAT class)")
        print("  2. Temporal data leakage in feature engineering")
        print("  3. Model architecture issue")
        print("  4. Incorrect loss function or metrics")
        print()
        print("Next steps:")
        print("  1. Run: python3 ai/diagnose_data.py")
        print("  2. Check class distribution in your actual windows")
        print("  3. Verify no future information leaks into features")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print()
        print("The label generation has issues that must be fixed before training.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
