#!/usr/bin/env python3
"""
Analyse la distribution des directions dans les données S3 réelles
pour identifier un éventuel déséquilibre de classes
"""

import sys
import numpy as np
from collections import Counter

from ai.s3_parquet_loader import S3ParquetLoader, compute_features, prepare_model_data
from ai.models.model import make_windows, FEATURE_KEYS


def analyze_real_data_direction_distribution(
    bucket: str,
    base_prefix: str,
    years: list,
    lookback: int = 256,
    horizon: int = 12,
    stride: int = 12,
    max_windows_per_year: int = 10000,
):
    """Analyze direction class distribution in real S3 data"""

    print("=" * 80)
    print("DIRECTION CLASS DISTRIBUTION ANALYSIS - REAL S3 DATA")
    print("=" * 80)
    print()

    loader = S3ParquetLoader(bucket=bucket, base_prefix=base_prefix)

    all_dirs = []
    all_cum_rets = []

    for year in years:
        print(f"Loading year {year}...")

        try:
            year_data = loader.load_year(year)
            df = year_data.df
            print(f"  Loaded {len(df):,} rows")

            # Compute features
            df = compute_features(df)

            # Prepare data
            X_full, y_ret_full, y_rv_full = prepare_model_data(df, FEATURE_KEYS)
            print(f"  Prepared: X={X_full.shape}, y_ret={y_ret_full.shape}")

            # Create windows (limited number for analysis)
            Xw, y_ret_h, y_dir, y_rv_h = make_windows(
                X_full, y_ret_full, y_rv_full,
                lookback=lookback,
                horizon=horizon,
                stride=stride
            )

            # Sample if too many
            if len(Xw) > max_windows_per_year:
                idx = np.random.choice(len(Xw), max_windows_per_year, replace=False)
                y_dir = y_dir[idx]
                y_ret_h = y_ret_h[idx]

            # Calculate cumulative returns
            cum_rets = np.sum(y_ret_h, axis=1)

            all_dirs.extend(y_dir.tolist())
            all_cum_rets.extend(cum_rets.tolist())

            # Show stats for this year
            counts = Counter(y_dir)
            print(f"  Windows: {len(y_dir):,}")
            print(f"    DOWN:  {counts[0]:>6} ({100*counts[0]/len(y_dir):5.1f}%)")
            print(f"    FLAT:  {counts[1]:>6} ({100*counts[1]/len(y_dir):5.1f}%)")
            print(f"    UP:    {counts[2]:>6} ({100*counts[2]/len(y_dir):5.1f}%)")
            print()

        except Exception as e:
            print(f"  ❌ Error loading year {year}: {e}")
            print()
            continue

    # Overall statistics
    all_dirs = np.array(all_dirs)
    all_cum_rets = np.array(all_cum_rets)

    print("=" * 80)
    print("OVERALL STATISTICS (All Years)")
    print("=" * 80)
    print()

    print(f"Total windows analyzed: {len(all_dirs):,}")
    print()

    # Direction distribution
    counts = Counter(all_dirs)
    print("Direction Distribution:")
    print(f"  0 (DOWN):  {counts[0]:>8,} ({100*counts[0]/len(all_dirs):6.2f}%)")
    print(f"  1 (FLAT):  {counts[1]:>8,} ({100*counts[1]/len(all_dirs):6.2f}%)")
    print(f"  2 (UP):    {counts[2]:>8,} ({100*counts[2]/len(all_dirs):6.2f}%)")
    print()

    # Check for severe imbalance
    max_class_pct = max(counts.values()) / len(all_dirs) * 100
    min_class_pct = min(counts.values()) / len(all_dirs) * 100

    print(f"Class Balance:")
    print(f"  Max class: {max_class_pct:6.2f}%")
    print(f"  Min class: {min_class_pct:6.2f}%")
    print(f"  Imbalance ratio: {max_class_pct/min_class_pct:.2f}:1")
    print()

    if max_class_pct > 80:
        print("🔴 CRITICAL: Severe class imbalance detected!")
        print(f"   The dominant class represents {max_class_pct:.1f}% of the data.")
        print()
        print("   This explains the 5% accuracy:")
        print("   - The model learns to always predict the FLAT class")
        print("   - It gets high accuracy on FLAT but fails on DOWN/UP")
        print("   - Overall accuracy collapses")
        print()
        print("   SOLUTIONS:")
        print("   1. Increase threshold (reduce FLAT class)")
        print("   2. Use class_weight in model.fit()")
        print("   3. Focal loss for hard samples")
        print("   4. Disable direction task (w_dir=0.0)")
        print()
    elif max_class_pct > 60:
        print("⚠️  WARNING: Moderate class imbalance")
        print(f"   Dominant class: {max_class_pct:.1f}%")
        print()
        print("   Recommendations:")
        print("   1. Use class_weight parameter")
        print("   2. Monitor per-class metrics (not just overall accuracy)")
        print()
    else:
        print("✅ Classes are reasonably balanced")
        print()

    # Cumulative return statistics
    print("Cumulative Return Statistics:")
    print(f"  Mean:   {np.mean(all_cum_rets):+.6f}")
    print(f"  Median: {np.median(all_cum_rets):+.6f}")
    print(f"  Std:    {np.std(all_cum_rets):.6f}")
    print(f"  Min:    {np.min(all_cum_rets):+.6f}")
    print(f"  Max:    {np.max(all_cum_rets):+.6f}")
    print()

    # Percentiles
    print("Cumulative Return Percentiles:")
    for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        val = np.percentile(all_cum_rets, pct)
        print(f"  {pct:>2}th: {val:+.6f}")
    print()

    # Threshold sensitivity
    print("=" * 80)
    print("THRESHOLD SENSITIVITY ANALYSIS")
    print("=" * 80)
    print()

    print("Testing different thresholds for classification:")
    print()

    for thresh in [1e-5, 5e-5, 1e-4, 5e-4, 1e-3, 5e-3, 1e-2]:
        test_dirs = np.zeros_like(all_dirs)
        test_dirs[all_cum_rets > thresh] = 2
        test_dirs[all_cum_rets < -thresh] = 0
        test_dirs[np.abs(all_cum_rets) <= thresh] = 1

        test_counts = Counter(test_dirs)
        down_pct = test_counts[0] / len(test_dirs) * 100
        flat_pct = test_counts[1] / len(test_dirs) * 100
        up_pct = test_counts[2] / len(test_dirs) * 100

        print(f"  threshold = {thresh:.0e}:  "
              f"DOWN={down_pct:5.1f}%  FLAT={flat_pct:5.1f}%  UP={up_pct:5.1f}%")

    print()

    # Recommendation
    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    if max_class_pct > 80:
        print("🔴 The direction task is UNBALANCED and will fail.")
        print()
        print("   Option 1: INCREASE THRESHOLD")
        print(f"     Current: 1e-4 → {counts[1]/len(all_dirs)*100:.1f}% FLAT")
        print("     Try: 1e-3 or 5e-4 to reduce FLAT class")
        print()
        print("   Option 2: USE CLASS WEIGHTS")
        print("     Add to model.compile():")
        down_weight = len(all_dirs) / (3 * counts[0])
        flat_weight = len(all_dirs) / (3 * counts[1])
        up_weight = len(all_dirs) / (3 * counts[2])
        print(f"     class_weight={{0: {down_weight:.2f}, 1: {flat_weight:.2f}, 2: {up_weight:.2f}}}")
        print()
        print("   Option 3: DISABLE DIRECTION")
        print("     Set w_dir=0.0 in config and focus on returns prediction")
        print()
    elif max_class_pct > 60:
        print("⚠️  Use class weights to handle imbalance:")
        down_weight = len(all_dirs) / (3 * counts[0])
        flat_weight = len(all_dirs) / (3 * counts[1])
        up_weight = len(all_dirs) / (3 * counts[2])
        print(f"   class_weight={{0: {down_weight:.2f}, 1: {flat_weight:.2f}, 2: {up_weight:.2f}}}")
        print()
    else:
        print("✅ Class distribution is OK.")
        print()
        print("   If you still get 5% accuracy, check:")
        print("   1. Temporal leakage in features (future info in inputs)")
        print("   2. Model architecture (too small, collapse)")
        print("   3. Loss function (check SparseCategoricalCrossentropy)")
        print()


if __name__ == "__main__":
    # Configuration
    bucket = "qbia"
    base_prefix = "bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT"

    # Test on 2-3 recent years
    years = [2022, 2023, 2024]

    print()
    print(f"Bucket: {bucket}")
    print(f"Prefix: {base_prefix}")
    print(f"Years: {years}")
    print()

    analyze_real_data_direction_distribution(
        bucket=bucket,
        base_prefix=base_prefix,
        years=years,
        lookback=256,
        horizon=12,
        stride=12,
        max_windows_per_year=10000  # Limit for speed
    )
