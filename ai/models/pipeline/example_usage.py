"""Example usage of the improved pipeline."""
from datetime import datetime, timedelta

from cache import RedisCache
from config_loader import get_config
from data_quality import DataQualityValidator
from data_sources import CcxtDataSource, GlassnodeClient, merge_onchain_asof, ohlcv_to_df
from features import build_feature_set
from logging_config import MetricsLogger, get_metrics, setup_logging
from memory_optimizer import downsample_old_data, optimize_dtypes
from normalization import AdaptiveNormalizer


def main():
    """Run complete pipeline example."""
    # 1. Setup logging and config
    setup_logging(level="INFO", log_format="text", log_file="pipeline.log")
    logger = MetricsLogger(__name__)

    try:
        config = get_config()
    except FileNotFoundError:
        print("Config file not found. Using defaults.")
        config = None

    print("=" * 60)
    print("CRYPTO DATA PIPELINE - PRODUCTION READY")
    print("=" * 60)

    # 2. Initialize components
    print("\n[1/8] Initializing components...")
    cache = RedisCache(timeout=2.0)
    source = CcxtDataSource(
        cache=cache,
        circuit_breaker_threshold=5,
        circuit_breaker_timeout=300,
    )

    glassnode = GlassnodeClient(cache=cache)

    # 3. Fetch OHLCV data
    print("\n[2/8] Fetching OHLCV data...")
    symbol = "BTC/USDT"
    timeframe = "1h"
    end = datetime.now()
    start = end - timedelta(days=7)

    with get_metrics().timer("fetch_ohlcv"):
        try:
            ohlcv = source.fetch_historical_range(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                use_cache=True,
            )
            df = ohlcv_to_df(ohlcv)
            print(f"✓ Fetched {len(df)} candles from {start.date()} to {end.date()}")
        except Exception as e:
            print(f"✗ Error fetching OHLCV: {e}")
            return

    # 4. Validate data quality
    print("\n[3/8] Validating data quality...")
    validator = DataQualityValidator(
        max_gap_multiplier=2.0,
        volatility_threshold=10.0,
        price_change_threshold=0.5,
    )

    report = validator.validate(df, timeframe=timeframe)

    if report.is_valid:
        print(f"✓ Data quality: VALID ({report.total_rows} rows)")
        if report.warnings:
            print(f"  Warnings: {len(report.warnings)}")
            for warning in report.warnings[:3]:
                print(f"    - {warning}")
    else:
        print(f"✗ Data quality: INVALID")
        for error in report.errors:
            print(f"    - {error}")
        return

    # 5. Fetch on-chain data (optional)
    print("\n[4/8] Fetching on-chain data...")
    try:
        onchain_data = glassnode.fetch_metric(
            "addresses/active_count",
            asset="BTC",
            params={
                "i": "24h",
                "s": int(start.timestamp()),
                "u": int(end.timestamp()),
            },
        )
        onchain_df = GlassnodeClient.to_df(onchain_data)

        # Merge with OHLCV
        df = merge_onchain_asof(df, onchain_df, tolerance="6h")
        print(f"✓ Merged {len(onchain_df)} on-chain data points")
    except Exception as e:
        print(f"⚠ Skipping on-chain data: {e}")
        onchain_df = None

    # 6. Build features
    print("\n[5/8] Building features...")
    with get_metrics().timer("feature_engineering"):
        features = build_feature_set(
            df,
            onchain_column="onchain_value" if onchain_df is not None else None,
            drop_na=True,
            windows={
                "sma": [10, 20, 50],
                "ema": [12, 26],
                "rsi": [14, 21],
                "volatility": [14, 30],
                "returns": [1, 4, 12],
            },
        )
        print(f"✓ Created {len(features.columns)} features from {len(features)} rows")

    # 7. Normalize features
    print("\n[6/8] Normalizing features...")
    normalizer = AdaptiveNormalizer(
        window=500,
        z_threshold=4.0,
        method="robust",
    )

    # Split train/test
    split_idx = int(len(features) * 0.8)
    train_features = features.iloc[:split_idx]
    test_features = features.iloc[split_idx:]

    # Fit on train, transform both
    normalizer.fit(train_features)
    train_normalized = normalizer.transform(train_features)
    test_normalized = normalizer.transform(test_features)

    print(f"✓ Normalized {len(train_normalized)} train + {len(test_normalized)} test rows")

    # Save normalizer state
    normalizer.save_state("normalizer_state.json")
    print("✓ Saved normalizer state to normalizer_state.json")

    # 8. Optimize memory
    print("\n[7/8] Optimizing memory usage...")
    train_optimized = optimize_dtypes(train_normalized, aggressive=True)
    test_optimized = optimize_dtypes(test_normalized, aggressive=True)

    # Optional: downsample old data if dataset is very large
    if len(df) > 5000:
        df_downsampled = downsample_old_data(
            df,
            recent_periods=1000,
            downsample_freq="4H",
        )
        print(f"✓ Downsampled old data: {len(df)} -> {len(df_downsampled)} rows")

    # 9. Display metrics
    print("\n[8/8] Pipeline metrics summary")
    print("=" * 60)
    metrics = get_metrics()
    summary = metrics.summary()

    print(f"API calls: {summary['api_calls']}")
    print(f"Cache hit rate: {summary['cache_hit_rate']:.1%}")
    print(f"Cache hits: {summary['cache_hits']}")
    print(f"Cache misses: {summary['cache_misses']}")
    print(f"Data rows processed: {summary['data_rows_processed']}")
    print(f"Errors: {summary['errors']}")

    if summary["avg_execution_times"]:
        print("\nAverage execution times:")
        for op, avg_time in summary["avg_execution_times"].items():
            if avg_time:
                print(f"  {op}: {avg_time:.2f}s")

    print("\n" + "=" * 60)
    print("✓ Pipeline completed successfully!")
    print("=" * 60)

    # Display sample of final data
    print("\nSample normalized features (first 5 rows):")
    print(train_optimized.head())

    print("\nFeature columns:")
    print(train_optimized.columns.tolist()[:10], "...")

    return train_optimized, test_optimized, normalizer


if __name__ == "__main__":
    train, test, normalizer = main()
