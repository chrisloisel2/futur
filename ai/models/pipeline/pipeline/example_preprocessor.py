"""Example usage of advanced preprocessor."""
from datetime import datetime, timedelta

import pandas as pd

from cache import RedisCache
from data_sources import CcxtDataSource, ohlcv_to_df
from features import build_feature_set
from logging_config import setup_logging
from preprocessor import AdvancedPreprocessor


def main():
    """Run complete example with preprocessing."""
    # Setup
    setup_logging(level="INFO")
    print("=" * 80)
    print("ADVANCED PREPROCESSING EXAMPLE")
    print("=" * 80)

    # 1. Fetch data
    print("\n[1/4] Fetching OHLCV data...")
    cache = RedisCache()
    source = CcxtDataSource(cache=cache)

    symbol = "BTC/USDT"
    timeframe = "1h"
    end = datetime.now()
    start = end - timedelta(days=60)  # 60 days of hourly data

    try:
        ohlcv = source.fetch_historical_range(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        df = ohlcv_to_df(ohlcv)
        print(f"✓ Fetched {len(df)} candles")
    except Exception as e:
        print(f"✗ Error: {e}")
        print("Using synthetic data instead...")

        # Generate synthetic data for demo
        import numpy as np
        n = 1440  # 60 days * 24 hours
        dates = pd.date_range(start, periods=n, freq="1H")

        close = 30000 + np.cumsum(np.random.randn(n) * 100)
        high = close + np.abs(np.random.randn(n) * 50)
        low = close - np.abs(np.random.randn(n) * 50)
        open_ = close + np.random.randn(n) * 30
        volume = np.abs(np.random.randn(n) * 1000 + 5000)

        df = pd.DataFrame({
            "timestamp": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    # 2. Build features
    print(f"\n[2/4] Building features...")
    features_df = build_feature_set(
        df,
        drop_na=True,
        windows={
            "sma": [10, 20, 50],
            "ema": [12, 26],
            "rsi": [14, 21],
            "volatility": [14, 30],
            "returns": [1, 4, 12],
        }
    )

    print(f"✓ Created {len(features_df.columns)} features from {len(features_df)} rows")

    # 3. Create target variable (next period return)
    print(f"\n[3/4] Creating target variable...")

    # Reset index to get timestamp as column
    features_df = features_df.reset_index()

    # Calculate forward return (1-period ahead)
    if "close" in df.columns:
        # Merge with original close prices
        df_with_close = df[["timestamp", "close"]].copy()
        features_df = features_df.merge(df_with_close, on="timestamp", how="left")

        # Forward return
        features_df["target"] = features_df["close"].pct_change().shift(-1)

        # Drop close (already have close features)
        features_df = features_df.drop(columns=["close"])
    else:
        # Use ret_1 as proxy if close not available
        features_df["target"] = features_df["ret_1"].shift(-1)

    # Drop rows with no target
    features_df = features_df.dropna(subset=["target"])

    # Set timestamp as index
    features_df = features_df.set_index("timestamp")

    print(f"✓ Target variable created: {features_df['target'].describe()}")

    # 4. Advanced preprocessing
    print(f"\n[4/4] Running advanced preprocessing pipeline...")
    print("-" * 80)

    preprocessor = AdvancedPreprocessor(
        target_col="target",
        frac_diff_d=0.5,              # Fractional differentiation
        rolling_window=30,             # 30-period rolling normalization
        mi_threshold=0.01,             # Mutual information threshold
        use_boruta=False,              # Set to True if BorutaPy installed
        interpolation_method="time",   # Temporal interpolation
        test_stationarity=True         # Test with ADF
    )

    try:
        df_processed = preprocessor.fit_transform(features_df)

        print("\n" + "=" * 80)
        print("PREPROCESSING RESULTS")
        print("=" * 80)

        print(f"\nOriginal shape: {features_df.shape}")
        print(f"Processed shape: {df_processed.shape}")

        print(f"\nSelected features ({len(preprocessor.selected_features_)}):")
        for i, feat in enumerate(preprocessor.selected_features_[:10], 1):
            mi_score = preprocessor.feature_selector.mi_scores_[feat]
            print(f"  {i}. {feat:30s} (MI: {mi_score:.4f})")

        if len(preprocessor.selected_features_) > 10:
            print(f"  ... and {len(preprocessor.selected_features_) - 10} more")

        print(f"\nStationarity test results:")
        stationary_count = sum(
            r.get("is_stationary", False)
            for r in preprocessor.stationarity_results_.values()
        )
        print(f"  Stationary features: {stationary_count}/{len(preprocessor.stationarity_results_)}")

        # Show some examples
        for col, result in list(preprocessor.stationarity_results_.items())[:5]:
            status = "✓" if result.get("is_stationary") else "✗"
            p_val = result.get("p_value", 1.0)
            print(f"  {status} {col:30s} (p-value: {p_val:.4f})")

        # 5. Purged walk-forward cross-validation
        print(f"\n" + "=" * 80)
        print("PURGED WALK-FORWARD CROSS-VALIDATION")
        print("=" * 80)

        cv = preprocessor.get_cv_splits(
            df_processed,
            n_splits=5,
            test_size=100,
            purge_gap=10
        )

        print(f"\nCV Configuration:")
        print(f"  Splits: {cv.n_splits}")
        print(f"  Test size: {cv.test_size}")
        print(f"  Purge gap: {cv.purge_gap}")
        print(f"  Embargo gap: {cv.embargo_gap}")

        print(f"\nSplit details:")
        for i, (train_idx, test_idx) in enumerate(cv.split(df_processed), 1):
            print(f"  Split {i}: Train={len(train_idx):4d}, Test={len(test_idx):3d}")

        # 6. Sample of processed data
        print(f"\n" + "=" * 80)
        print("SAMPLE OF PROCESSED DATA")
        print("=" * 80)
        print(df_processed.head(10))

        print(f"\n" + "=" * 80)
        print("✓ PREPROCESSING COMPLETE")
        print("=" * 80)

        # Summary statistics
        print(f"\nData ready for ML:")
        print(f"  • {len(df_processed)} samples")
        print(f"  • {len(df_processed.columns) - 1} features (+ target)")
        print(f"  • {stationary_count} stationary features")
        print(f"  • Ready for walk-forward validation")

        return df_processed, preprocessor

    except Exception as e:
        print(f"\n✗ Preprocessing failed: {e}")
        import traceback
        traceback.print_exc()
        return None, None


if __name__ == "__main__":
    df_processed, preprocessor = main()

    if df_processed is not None:
        print("\nYou can now use this data for ML training:")
        print("  X = df_processed.drop(columns=['target'])")
        print("  y = df_processed['target']")
        print("\nWith purged walk-forward CV:")
        print("  cv = preprocessor.get_cv_splits(df_processed)")
        print("  for train_idx, test_idx in cv.split(df_processed):")
        print("      X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]")
        print("      X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]")
        print("      # Train and evaluate model...")
