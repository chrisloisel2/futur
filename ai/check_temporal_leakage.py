#!/usr/bin/env python3
"""
Check for temporal data leakage in feature engineering

This script checks if features at time t contain information from t+1 or later,
which would make direction prediction trivial (and explain why the model learns wrong patterns)
"""

import numpy as np
import pandas as pd

from ai.s3_parquet_loader import S3ParquetLoader, compute_features, prepare_model_data
from ai.models.model import FEATURE_KEYS


def check_feature_temporal_alignment(df: pd.DataFrame, feature_name: str):
    """
    Check if a feature at time t depends on information from t+1

    Returns:
        is_leaking: bool - True if feature depends on future
        correlation: float - Correlation between feature[t] and return[t]
    """

    if feature_name not in df.columns:
        return None, None

    feature = df[feature_name].values
    future_return = df['log_ret'].shift(-1).values  # Return at t+1

    # Remove NaN
    mask = ~np.isnan(feature) & ~np.isnan(future_return)
    feature = feature[mask]
    future_return = future_return[mask]

    if len(feature) < 100:
        return None, None

    # Correlation between feature[t] and return[t+1]
    # If high, feature is leaking future information
    corr = np.corrcoef(feature, future_return)[0, 1]

    # Threshold: |corr| > 0.3 is suspicious
    is_leaking = abs(corr) > 0.3

    return is_leaking, corr


def analyze_temporal_leakage(bucket: str, base_prefix: str, year: int = 2024):
    """Analyze all features for temporal leakage"""

    print("=" * 80)
    print("TEMPORAL DATA LEAKAGE ANALYSIS")
    print("=" * 80)
    print()

    print(f"Loading year {year} from S3...")
    loader = S3ParquetLoader(bucket=bucket, base_prefix=base_prefix)

    year_data = loader.load_year(year, verbose=False)
    df = year_data.df

    print(f"  Loaded {len(df):,} rows")
    print()

    print("Computing features...")
    df = compute_features(df)
    print(f"  Features computed: {df.shape}")
    print()

    print("=" * 80)
    print("CHECKING EACH FEATURE FOR FUTURE LEAKAGE")
    print("=" * 80)
    print()

    leaking_features = []

    for feature in FEATURE_KEYS:
        is_leaking, corr = check_feature_temporal_alignment(df, feature)

        if is_leaking is None:
            status = "❓"
            msg = "Not found"
        elif is_leaking:
            status = "🔴"
            msg = f"LEAKING! corr={corr:+.3f}"
            leaking_features.append((feature, corr))
        elif abs(corr) > 0.1:
            status = "⚠️ "
            msg = f"Suspicious: corr={corr:+.3f}"
        else:
            status = "✅"
            msg = f"OK: corr={corr:+.3f}"

        print(f"  {status} {feature:20s} - {msg}")

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()

    if len(leaking_features) > 0:
        print(f"🔴 CRITICAL: {len(leaking_features)} features are leaking future information!")
        print()
        print("Leaking features:")
        for feat, corr in sorted(leaking_features, key=lambda x: abs(x[1]), reverse=True):
            print(f"  - {feat:20s}: correlation = {corr:+.3f}")
        print()
        print("EXPLANATION:")
        print("  These features at time t contain information from t+1 or later.")
        print("  This makes the model learn INVERTED patterns:")
        print("    - Feature[t] → predicts direction[t+1]")
        print("    - But label[t] = direction based on returns[t+1...t+12]")
        print("    - Model learns opposite pattern!")
        print()
        print("SOLUTION:")
        print("  Fix compute_features() to ensure all features only use data up to time t")
        print()
    else:
        print("✅ No obvious temporal leakage detected")
        print()
        print("   The features appear to be properly aligned.")
        print("   The 5% accuracy must be caused by something else:")
        print("     1. Model architecture collapse")
        print("     2. Loss function issue")
        print("     3. Optimizer/learning rate problem")
        print()

    # Additional check: log_ret itself
    print("=" * 80)
    print("SANITY CHECK: log_ret ALIGNMENT")
    print("=" * 80)
    print()

    # Check if log_ret at time t corresponds to return FROM t TO t+1
    # or FROM t-1 TO t

    close_t = df['close'].values
    close_t_minus_1 = df['close'].shift(1).values

    # Calculate what log_ret SHOULD be if it's return FROM t-1 TO t
    expected_log_ret_backward = np.log(close_t / close_t_minus_1)

    # Get actual log_ret
    actual_log_ret = df['log_ret'].values

    # Compare
    mask = ~np.isnan(expected_log_ret_backward) & ~np.isnan(actual_log_ret)
    corr_backward = np.corrcoef(
        expected_log_ret_backward[mask],
        actual_log_ret[mask]
    )[0, 1]

    print(f"Correlation between log_ret[t] and log(close[t]/close[t-1]): {corr_backward:+.6f}")

    if corr_backward > 0.999:
        print("  ✅ log_ret[t] = return FROM t-1 TO t (CORRECT)")
        print()
        print("  This means:")
        print("    - Feature X[t] uses data up to and including t")
        print("    - Target y_ret[t] = return from t-1 to t")
        print("    - Prediction task: X[t:t+lookback] → y_ret[t+lookback:t+lookback+horizon]")
        print("    - This is FUTURE prediction (CORRECT)")
        print()
    else:
        # Check forward (t to t+1)
        close_t_plus_1 = df['close'].shift(-1).values
        expected_log_ret_forward = np.log(close_t_plus_1 / close_t)

        mask_fwd = ~np.isnan(expected_log_ret_forward) & ~np.isnan(actual_log_ret)
        corr_forward = np.corrcoef(
            expected_log_ret_forward[mask_fwd],
            actual_log_ret[mask_fwd]
        )[0, 1]

        print(f"Correlation between log_ret[t] and log(close[t+1]/close[t]): {corr_forward:+.6f}")

        if corr_forward > 0.999:
            print("  🔴 CRITICAL: log_ret[t] = return FROM t TO t+1 (FUTURE LEAKAGE!)")
            print()
            print("  This means log_ret contains FUTURE information!")
            print("  The model can trivially predict direction by looking at log_ret in features.")
            print()
            print("  FIX: Make sure log_ret[t] = log(close[t] / close[t-1])")
            print()
        else:
            print("  ❓ Unclear temporal alignment")
            print()


if __name__ == "__main__":
    bucket = "qbia"
    base_prefix = "bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT"

    analyze_temporal_leakage(bucket, base_prefix, year=2024)
