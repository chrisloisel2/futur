"""Example usage of tabular model benchmark."""
import numpy as np
import pandas as pd
from sklearn.datasets import make_classification, make_regression

from models.tabular.benchmarks import TabularBenchmark


def example_regression():
    """Example: Regression on synthetic crypto-like data."""
    print("=" * 80)
    print("TABULAR REGRESSION BENCHMARK")
    print("=" * 80)

    # Generate synthetic regression data
    # Simulate crypto features: returns, volume, volatility, RSI, MACD, etc.
    np.random.seed(42)
    n_samples = 5000
    n_features = 20

    X, y = make_regression(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=15,
        n_redundant=3,
        noise=10.0,
        random_state=42,
    )

    print(f"\nDataset:")
    print(f"  Samples: {n_samples}")
    print(f"  Features: {n_features}")
    print(f"  Target range: [{y.min():.2f}, {y.max():.2f}]")

    # Initialize benchmark
    benchmark = TabularBenchmark(
        task_type="regression",
        random_state=42,
        device="cpu",
    )

    # Run benchmark
    results = benchmark.run_benchmark(
        X, y,
        models_to_run=["ft_transformer", "xgboost"],
        ft_transformer={
            "n_epochs": 50,
            "batch_size": 256,
            "lr": 1e-3,
            "early_stopping_patience": 10,
            "d_token": 32,
            "n_blocks": 3,
            "attention_n_heads": 4,
            "dropout": 0.1,
        },
        xgboost={
            "n_estimators": 100,
            "max_depth": 6,
            "learning_rate": 0.1,
        },
    )

    # Extract embeddings
    if "ft_transformer" in results:
        print("\n" + "=" * 80)
        print("EXTRACTING EMBEDDINGS")
        print("=" * 80)

        model = results["ft_transformer"]["model"]
        model.eval()

        import torch
        X_tensor = torch.FloatTensor(X[:10])

        with torch.no_grad():
            embeddings = model(X_tensor, return_embedding=True)

        print(f"\nEmbedding shape: {embeddings.shape}")
        print(f"Embedding dimension: {embeddings.shape[1]}")

    return results


def example_classification():
    """Example: Binary classification on crypto price direction."""
    print("\n\n" + "=" * 80)
    print("TABULAR CLASSIFICATION BENCHMARK")
    print("=" * 80)

    # Generate synthetic classification data
    # Simulate predicting if price will go up or down
    np.random.seed(42)
    n_samples = 5000
    n_features = 20

    X, y = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=15,
        n_redundant=3,
        n_classes=2,
        class_sep=1.0,
        random_state=42,
    )

    print(f"\nDataset:")
    print(f"  Samples: {n_samples}")
    print(f"  Features: {n_features}")
    print(f"  Class distribution: {np.bincount(y)}")

    # Initialize benchmark
    benchmark = TabularBenchmark(
        task_type="classification",
        random_state=42,
        device="cpu",
    )

    # Run benchmark
    results = benchmark.run_benchmark(
        X, y,
        models_to_run=["ft_transformer", "xgboost"],
        ft_transformer={
            "n_epochs": 50,
            "batch_size": 256,
            "lr": 1e-3,
            "early_stopping_patience": 10,
            "label_smoothing": 0.1,  # Regularization
            "d_token": 32,
            "n_blocks": 3,
            "attention_n_heads": 4,
            "dropout": 0.15,
        },
        xgboost={
            "n_estimators": 100,
            "max_depth": 6,
            "learning_rate": 0.1,
        },
    )

    return results


def example_with_real_features():
    """Example: Using real crypto features from pipeline."""
    print("\n\n" + "=" * 80)
    print("BENCHMARK WITH REAL CRYPTO FEATURES")
    print("=" * 80)

    try:
        from pipeline import CcxtDataSource, ohlcv_to_df, build_feature_set
        from pipeline import AdvancedPreprocessor

        print("\nFetching crypto data...")

        # Fetch data
        source = CcxtDataSource()
        ohlcv = source.fetch_historical_range(
            symbol="BTC/USDT",
            timeframe="1h",
            start_date="2024-01-01",
            end_date="2024-03-01",
        )

        df = ohlcv_to_df(ohlcv)

        # Build features
        features = build_feature_set(df)

        # Create target: 1 if price goes up in next 4 hours, 0 otherwise
        features["target"] = (
            features["close"].shift(-4) > features["close"]
        ).astype(int)

        # Drop NaN
        features = features.dropna()

        print(f"\nDataset:")
        print(f"  Samples: {len(features)}")
        print(f"  Features: {len(features.columns) - 1}")
        print(f"  Target distribution: {features['target'].value_counts().to_dict()}")

        # Preprocess
        preprocessor = AdvancedPreprocessor(target_col="target")
        features_processed = preprocessor.fit_transform(features)

        # Extract X, y
        X = features_processed.drop("target", axis=1).values
        y = features_processed["target"].values

        # Benchmark
        benchmark = TabularBenchmark(
            task_type="classification",
            random_state=42,
            device="cpu",
        )

        results = benchmark.run_benchmark(
            X, y,
            models_to_run=["ft_transformer", "xgboost"],
            ft_transformer={
                "n_epochs": 100,
                "batch_size": 128,
                "lr": 5e-4,
                "early_stopping_patience": 15,
                "label_smoothing": 0.1,
                "d_token": 64,
                "n_blocks": 4,
                "attention_n_heads": 8,
                "dropout": 0.2,
            },
            xgboost={
                "n_estimators": 200,
                "max_depth": 8,
                "learning_rate": 0.05,
            },
        )

        return results

    except Exception as e:
        print(f"\nCould not run with real data: {e}")
        print("Make sure CCXT is configured and internet connection is available.")
        return None


def main():
    """Run all benchmark examples."""
    print("=" * 80)
    print("TABULAR MODEL BENCHMARKS - EXAMPLES")
    print("=" * 80)
    print("\nThis script demonstrates:")
    print("  1. Regression benchmark on synthetic data")
    print("  2. Classification benchmark on synthetic data")
    print("  3. (Optional) Benchmark with real crypto features")
    print("\n")

    # Example 1: Regression
    regression_results = example_regression()

    # Example 2: Classification
    classification_results = example_classification()

    # Example 3: Real features (optional)
    real_results = example_with_real_features()

    print("\n" + "=" * 80)
    print("ALL EXAMPLES COMPLETE")
    print("=" * 80)

    return {
        "regression": regression_results,
        "classification": classification_results,
        "real": real_results,
    }


if __name__ == "__main__":
    # Set random seeds
    np.random.seed(42)

    results = main()
