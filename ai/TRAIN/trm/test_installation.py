#!/usr/bin/env python3
"""
Test script to verify TRM installation and basic functionality.

Run this to check that everything is properly set up.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")

    try:
        import torch
        print(f"  ✓ PyTorch {torch.__version__}")
    except ImportError:
        print("  ✗ PyTorch not found. Install with: pip install torch")
        return False

    try:
        import pandas as pd
        print(f"  ✓ Pandas {pd.__version__}")
    except ImportError:
        print("  ✗ Pandas not found. Install with: pip install pandas")
        return False

    try:
        import numpy as np
        print(f"  ✓ NumPy {np.__version__}")
    except ImportError:
        print("  ✗ NumPy not found. Install with: pip install numpy")
        return False

    try:
        import boto3
        print(f"  ✓ Boto3 {boto3.__version__}")
    except ImportError:
        print("  ✗ Boto3 not found. Install with: pip install boto3")
        return False

    try:
        import yaml
        print(f"  ✓ PyYAML")
    except ImportError:
        print("  ✗ PyYAML not found. Install with: pip install pyyaml")
        return False

    return True


def test_trm_modules():
    """Test that TRM modules can be imported."""
    print("\nTesting TRM modules...")

    try:
        from trm import TinyRecursiveModel
        print("  ✓ TinyRecursiveModel")
    except ImportError as e:
        print(f"  ✗ TinyRecursiveModel: {e}")
        return False

    try:
        from trm import CompositeTradingLoss
        print("  ✓ CompositeTradingLoss")
    except ImportError as e:
        print(f"  ✗ CompositeTradingLoss: {e}")
        return False

    try:
        from trm import TRMTrainer
        print("  ✓ TRMTrainer")
    except ImportError as e:
        print(f"  ✗ TRMTrainer: {e}")
        return False

    try:
        from trm import TRMBacktester
        print("  ✓ TRMBacktester")
    except ImportError as e:
        print(f"  ✗ TRMBacktester: {e}")
        return False

    try:
        from trm import S3TRMDataLoader
        print("  ✓ S3TRMDataLoader")
    except ImportError as e:
        print(f"  ✗ S3TRMDataLoader: {e}")
        return False

    return True


def test_model_creation():
    """Test that a model can be created."""
    print("\nTesting model creation...")

    try:
        import torch
        from trm import TinyRecursiveModel

        model = TinyRecursiveModel(
            num_features=10,
            latent_dim=32,
            num_iterations=5
        )

        total_params = sum(p.numel() for p in model.parameters())
        print(f"  ✓ Model created: {total_params:,} parameters")

        # Test forward pass
        x = torch.randn(2, 60, 10)  # batch=2, seq_len=60, features=10
        output = model(x)
        print(f"  ✓ Forward pass: input {x.shape} → output {output.shape}")

        return True

    except Exception as e:
        print(f"  ✗ Model creation failed: {e}")
        return False


def test_loss_function():
    """Test that loss function works."""
    print("\nTesting loss function...")

    try:
        import torch
        from trm import CompositeTradingLoss

        loss_fn = CompositeTradingLoss()

        pred = torch.randn(100)
        true = torch.randn(100)

        loss, components = loss_fn(pred, true)

        print(f"  ✓ Loss computed: {loss.item():.6f}")
        print(f"    Components: {list(components.keys())}")

        return True

    except Exception as e:
        print(f"  ✗ Loss function failed: {e}")
        return False


def test_data_features():
    """Test feature engineering."""
    print("\nTesting feature engineering...")

    try:
        import numpy as np
        import pandas as pd
        from trm.data import build_trm_features

        # Create synthetic OHLCV data
        n = 5000
        timestamps = pd.date_range('2024-01-01', periods=n, freq='1min')
        returns = np.random.normal(0.0001, 0.002, n)
        prices = 100 * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': prices,
            'high': prices * 1.001,
            'low': prices * 0.999,
            'close': prices,
            'volume': np.random.lognormal(15, 1, n),
            'symbol': 'BTCUSDT'
        })

        # Build features
        features = build_trm_features(df, normalize=True, drop_na=True)

        print(f"  ✓ Features built: {features.shape[1]-1} features, {len(features)} samples")
        print(f"    Columns: {[col for col in features.columns if col != 'timestamp']}")

        return True

    except Exception as e:
        print(f"  ✗ Feature engineering failed: {e}")
        return False


def test_s3_connection():
    """Test S3 connection (optional)."""
    print("\nTesting S3 connection (optional)...")

    try:
        from data.s3_data_source import S3DataSource

        s3 = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
        years = s3.list_available_years()

        if years:
            print(f"  ✓ S3 connection successful")
            print(f"    Available years: {years}")
            symbols = s3.list_available_symbols(years[-1])
            print(f"    Symbols in {years[-1]}: {len(symbols)}")
            return True
        else:
            print("  ⚠ S3 connection works but no data found")
            return True

    except Exception as e:
        print(f"  ⚠ S3 connection failed: {e}")
        print("    This is OK if you haven't configured AWS credentials yet")
        return True  # Don't fail on S3 issues


def main():
    """Run all tests."""
    print("=" * 60)
    print("TRM INSTALLATION TEST")
    print("=" * 60)

    tests = [
        ("Dependencies", test_imports),
        ("TRM Modules", test_trm_modules),
        ("Model Creation", test_model_creation),
        ("Loss Function", test_loss_function),
        ("Feature Engineering", test_data_features),
        ("S3 Connection", test_s3_connection),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} crashed: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{test_name:<30} {status}")

    print("=" * 60)
    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("\n✓ All tests passed! TRM is ready to use.")
        print("\nNext steps:")
        print("  1. Edit config.yaml")
        print("  2. Run: python train_trm.py")
        return 0
    else:
        print("\n✗ Some tests failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
