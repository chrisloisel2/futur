#!/usr/bin/env python3
"""
Test rapide du pipeline d'entraînement
Teste avec seulement 1 année pour validation
"""

import os
import sys

print("="*80)
print("  PIPELINE TEST - Quick Validation")
print("="*80)
print()

# Test 1: Imports
print("Test 1: Checking imports...")
try:
    import tensorflow as tf
    print(f"  ✓ TensorFlow {tf.__version__}")

    import numpy as np
    print(f"  ✓ NumPy {np.__version__}")

    import pandas as pd
    print(f"  ✓ Pandas {pd.__version__}")

    import yaml
    print(f"  ✓ PyYAML")

    import boto3
    print(f"  ✓ Boto3 {boto3.__version__}")

    from ai.models.model import TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS
    print(f"  ✓ Model imported ({len(FEATURE_KEYS)} features)")

    from ai.s3_parquet_loader import S3ParquetLoader
    print(f"  ✓ S3 Parquet Loader")

    from ai.data_pipeline import fit_scaler_streaming, save_windows_to_disk
    print(f"  ✓ Data Pipeline")

    from ai.advanced_metrics import compute_all_metrics
    print(f"  ✓ Advanced Metrics")

    from ai.training_callbacks import build_callbacks
    print(f"  ✓ Training Callbacks")

except ImportError as e:
    print(f"  ✗ Import error: {e}")
    print()
    print("Please install requirements:")
    print("  pip install -r ai/requirements_training.txt")
    sys.exit(1)

print()

# Test 2: GPU
print("Test 2: Checking GPU...")
gpus = tf.config.list_physical_devices('GPU')
if gpus:
    print(f"  ✓ Found {len(gpus)} GPU(s)")
    for i, gpu in enumerate(gpus):
        print(f"    GPU {i}: {gpu.name}")
else:
    print("  ⚠  No GPU found, will use CPU")

print()

# Test 3: S3 Access
print("Test 3: Checking S3 access...")
try:
    loader = S3ParquetLoader(bucket="qbia")
    years = loader.list_years()
    print(f"  ✓ S3 accessible")
    print(f"  ✓ Found {len(years)} years: {years}")
except Exception as e:
    print(f"  ✗ S3 error: {e}")
    print()
    print("Please configure AWS credentials:")
    print("  aws configure")
    sys.exit(1)

print()

# Test 4: Model creation
print("Test 4: Testing model creation...")
try:
    config = TRMConfig()
    model = TinyRecursiveMarketModel(config, feature_dim=len(FEATURE_KEYS))
    print(f"  ✓ Model created")
    print(f"    - d_model: {config.d_model}")
    print(f"    - n_heads: {config.n_heads}")
    print(f"    - lookback: {config.lookback}")
    print(f"    - horizon: {config.horizon}")

    # Test forward pass
    import numpy as np
    test_input = np.random.randn(2, config.lookback, len(FEATURE_KEYS)).astype(np.float32)
    test_output = model(test_input, training=False)
    print(f"  ✓ Forward pass successful")
    print(f"    - ret shape: {test_output['ret'].shape}")
    print(f"    - dir shape: {test_output['dir'].shape}")
    print(f"    - rv shape: {test_output['rv'].shape}")

except Exception as e:
    print(f"  ✗ Model error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 5: Configuration
print("Test 5: Checking configuration...")
config_path = "ai/configs/train_advanced.yaml"
if os.path.exists(config_path):
    print(f"  ✓ Config file exists: {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    print(f"    - Train years: {config['data']['years_train']}")
    print(f"    - Test years: {config['data']['years_test']}")
    print(f"    - Epochs: {config['training']['epochs']}")
    print(f"    - Batch size: {config['training']['batch_size']}")
else:
    print(f"  ⚠  Config file not found: {config_path}")
    print("    Will use defaults")

print()

# Test 6: Quick data test
print("Test 6: Testing data loading (1 year)...")
try:
    test_year = years[-1] if years else 2024
    print(f"  Loading year {test_year} as test...")

    year_data = loader.load_year(test_year, verbose=False)
    print(f"  ✓ Loaded {year_data.n_rows:,} rows")
    print(f"    - Columns: {len(year_data.df.columns)}")
    print(f"    - Memory: {year_data.df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    # Test feature computation
    from ai.s3_parquet_loader import compute_features
    df_features = compute_features(year_data.df, verbose=False)
    print(f"  ✓ Features computed")
    print(f"    - Total columns: {len(df_features.columns)}")

except Exception as e:
    print(f"  ✗ Data error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 7: Metrics computation
print("Test 7: Testing metrics computation...")
try:
    # Create dummy predictions
    N = 1000
    H = 12
    y_true_dict = {
        'ret': np.random.randn(N, H) * 0.001,
        'dir': np.random.randint(0, 3, N),
        'rv': np.abs(np.random.randn(N, H)) * 0.01
    }
    y_pred_dict = {
        'ret': y_true_dict['ret'] + np.random.randn(N, H) * 0.0005,
        'dir': np.random.dirichlet([1, 1, 1], N),
        'rv': y_true_dict['rv'] + np.random.randn(N, H) * 0.002
    }

    metrics = compute_all_metrics(y_true_dict, y_pred_dict)
    print(f"  ✓ Computed {len(metrics)} metrics")
    print(f"    - Sharpe Ratio: {metrics['sharpe_ratio']:.4f}")
    print(f"    - Accuracy: {metrics['accuracy']:.2%}")
    print(f"    - MAE Returns: {metrics['mae_returns']:.6f}")

except Exception as e:
    print(f"  ✗ Metrics error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Summary
print("="*80)
print("  ALL TESTS PASSED ✓")
print("="*80)
print()
print("You can now run the full training:")
print("  ./ai/launch_training.sh")
print()
print("Or for a quick test (1 year, 5 epochs):")
print("  1. Edit ai/configs/train_advanced.yaml")
print("     - Set years_train: [2023]")
print("     - Set epochs: 5")
print("     - Set steps_per_epoch: 500")
print("  2. Run: python3 ai/train_advanced.py")
print()
print("="*80)
