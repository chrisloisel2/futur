#!/usr/bin/env python3
"""
Script de diagnostic pour analyser les labels de direction
"""

import os
import numpy as np
from collections import Counter

def analyze_direction_labels(windows_dir: str):
    """Analyze direction label distribution and validity"""

    print("=" * 80)
    print("DIRECTION LABELS DIAGNOSTIC")
    print("=" * 80)
    print()

    # Find all NPZ files
    npz_files = [f for f in os.listdir(windows_dir) if f.endswith('.npz')]

    if not npz_files:
        print(f"❌ No NPZ files found in {windows_dir}")
        return

    print(f"Found {len(npz_files)} NPZ files")
    print()

    all_dirs = []
    all_cum_rets = []

    for npz_file in sorted(npz_files):
        path = os.path.join(windows_dir, npz_file)
        data = np.load(path)

        y_dir = data['y_dir']  # [N]
        y_ret = data['y_ret']  # [N, horizon]

        # Calculate cumulative returns for each sample
        cum_rets = np.sum(y_ret, axis=1)  # [N]

        all_dirs.extend(y_dir.tolist())
        all_cum_rets.extend(cum_rets.tolist())

        print(f"  {npz_file}:")
        print(f"    Samples: {len(y_dir)}")
        print(f"    Direction distribution: {Counter(y_dir)}")
        print()

    # Overall statistics
    all_dirs = np.array(all_dirs)
    all_cum_rets = np.array(all_cum_rets)

    print("=" * 80)
    print("OVERALL STATISTICS")
    print("=" * 80)
    print()

    print(f"Total samples: {len(all_dirs)}")
    print()

    # Direction distribution
    dir_counts = Counter(all_dirs)
    print("Direction Distribution:")
    print(f"  0 (DOWN):  {dir_counts[0]:>8} ({100*dir_counts[0]/len(all_dirs):6.2f}%)")
    print(f"  1 (FLAT):  {dir_counts[1]:>8} ({100*dir_counts[1]/len(all_dirs):6.2f}%)")
    print(f"  2 (UP):    {dir_counts[2]:>8} ({100*dir_counts[2]/len(all_dirs):6.2f}%)")
    print()

    # Check if labels match cumulative returns
    threshold = 1e-4

    # Recalculate what labels SHOULD be
    expected_dirs = np.zeros_like(all_dirs)
    expected_dirs[all_cum_rets > threshold] = 2   # UP
    expected_dirs[all_cum_rets < -threshold] = 0  # DOWN
    expected_dirs[np.abs(all_cum_rets) <= threshold] = 1  # FLAT

    # Compare
    matches = (all_dirs == expected_dirs)
    match_rate = np.mean(matches) * 100

    print(f"Label Validation:")
    print(f"  Match rate: {match_rate:.2f}%")
    print()

    if match_rate < 99.0:
        print("⚠️  WARNING: Labels don't match cumulative returns!")
        print()

        # Show examples of mismatches
        mismatches = np.where(~matches)[0][:10]
        print("First 10 mismatches:")
        for i in mismatches:
            print(f"  Sample {i}: cum_ret={all_cum_rets[i]:+.6f}, "
                  f"actual_dir={all_dirs[i]}, expected_dir={expected_dirs[i]}")
        print()
    else:
        print("✓ Labels match cumulative returns")
        print()

    # Check cumulative return statistics
    print("Cumulative Return Statistics:")
    print(f"  Mean:   {np.mean(all_cum_rets):+.6f}")
    print(f"  Median: {np.median(all_cum_rets):+.6f}")
    print(f"  Std:    {np.std(all_cum_rets):.6f}")
    print(f"  Min:    {np.min(all_cum_rets):+.6f}")
    print(f"  Max:    {np.max(all_cum_rets):+.6f}")
    print()

    # Check for extreme class imbalance
    max_class_pct = max(dir_counts.values()) / len(all_dirs) * 100

    if max_class_pct > 80:
        print(f"⚠️  WARNING: Severe class imbalance! Dominant class: {max_class_pct:.1f}%")
        print("   This makes the classification task very difficult.")
        print()

    # Analyze threshold sensitivity
    print("Threshold Sensitivity Analysis:")
    for thresh in [1e-5, 1e-4, 1e-3, 1e-2]:
        test_dirs = np.zeros_like(all_dirs)
        test_dirs[all_cum_rets > thresh] = 2
        test_dirs[all_cum_rets < -thresh] = 0
        test_dirs[np.abs(all_cum_rets) <= thresh] = 1

        counts = Counter(test_dirs)
        flat_pct = counts[1] / len(test_dirs) * 100
        print(f"  threshold={thresh:.0e}: FLAT={flat_pct:5.1f}%, "
              f"DOWN={counts[0]/len(test_dirs)*100:5.1f}%, "
              f"UP={counts[2]/len(test_dirs)*100:5.1f}%")
    print()

    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    if max_class_pct > 80:
        print("🔴 CRITICAL: The direction task is unbalanced.")
        print("   Consider:")
        print("   1. Increase threshold to reduce FLAT class")
        print("   2. Use class weights in the loss function")
        print("   3. Disable direction task entirely (w_dir=0.0)")
    elif match_rate < 99.0:
        print("🔴 CRITICAL: Labels are CORRUPTED!")
        print("   The direction labels don't match the cumulative returns.")
        print("   You must regenerate the windows.")
    else:
        print("✓ Labels appear valid, but check the model's interpretation.")
        print()
        print("  If training gives ~5% accuracy, possible causes:")
        print("  1. Model outputs class probabilities in wrong order")
        print("  2. Loss function uses wrong label format")
        print("  3. Temporal leakage in feature engineering")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        windows_dir = sys.argv[1]
    else:
        windows_dir = "training_output/windows_train"

    if not os.path.exists(windows_dir):
        print(f"Error: Directory not found: {windows_dir}")
        sys.exit(1)

    analyze_direction_labels(windows_dir)
