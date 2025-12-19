#!/usr/bin/env python3
"""
Test complet du pipeline de bout en bout
Vérifie: S3 → Windows → Dataset → Model → Training step
"""

import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import sys
sys.path.insert(0, '/Users/christopher/Desktop/futur')

import numpy as np
import tensorflow as tf
from collections import Counter

from ai.s3_parquet_loader import S3ParquetLoader, compute_features, prepare_model_data
from ai.data_pipeline import create_windows_for_year, StreamingRobustScaler
from ai.models.model import TinyRecursiveMarketModel, TRMConfig, FEATURE_KEYS, RunningRobustScaler

print("=" * 80)
print("TEST COMPLET DU PIPELINE")
print("=" * 80)
print()

# 1. Test S3 Loader
print("1. TEST S3 LOADER")
print("-" * 80)

try:
    loader = S3ParquetLoader(
        bucket="qbia",
        base_prefix="bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT"
    )

    # Load one year
    year_data = loader.load_year(2024, verbose=False)
    print(f"   ✅ Loaded year 2024: {len(year_data.df):,} rows")

except Exception as e:
    print(f"   ⚠️  S3 not accessible: {e}")
    print("   (Normal si pas sur serveur distant)")
    print()

    # Use synthetic data
    print("   Using synthetic data for testing...")
    import pandas as pd
    from dataclasses import dataclass

    @dataclass
    class YearData:
        year: int
        df: pd.DataFrame
        n_rows: int
        date_range: tuple

    # Create synthetic dataframe
    n = 10000
    df = pd.DataFrame({
        'Open': np.random.randn(n) * 100 + 50000,
        'High': np.random.randn(n) * 100 + 50100,
        'Low': np.random.randn(n) * 100 + 49900,
        'Close': np.random.randn(n) * 100 + 50000,
        'Volume': np.abs(np.random.randn(n)) * 1000,
        'Quote_Volume': np.abs(np.random.randn(n)) * 50000000,
        'Trades': np.random.randint(100, 1000, n),
        'Taker_Buy_Base': np.abs(np.random.randn(n)) * 500,
        'Taker_Buy_Quote': np.abs(np.random.randn(n)) * 25000000,
    })

    year_data = YearData(
        year=2024,
        df=df,
        n_rows=n,
        date_range=(0, n)
    )
    print(f"   ✅ Created synthetic data: {n:,} rows")

print()

# 2. Test Feature Engineering
print("2. TEST FEATURE ENGINEERING")
print("-" * 80)

df_with_features = compute_features(year_data.df, verbose=False)
print(f"   Features shape: {df_with_features.shape}")
print(f"   ✅ Computed features")
print()

# 3. Test Data Preparation
print("3. TEST DATA PREPARATION")
print("-" * 80)

X, y_ret, y_rv = prepare_model_data(df_with_features, FEATURE_KEYS)
print(f"   X shape: {X.shape}")
print(f"   y_ret shape: {y_ret.shape}")
print(f"   y_rv shape: {y_rv.shape}")
print(f"   ✅ Prepared model data")
print()

# 4. Test Scaler
print("4. TEST SCALER")
print("-" * 80)

# Use RunningRobustScaler directly from model.py
scaler_raw = RunningRobustScaler(feature_dim=len(FEATURE_KEYS))
for i in range(min(1000, len(X))):
    scaler_raw.update(X[i])
scaler_raw.finalize()

X_scaled = scaler_raw.transform(X)
print(f"   Scaled X: mean={np.mean(X_scaled):.6f}, std={np.std(X_scaled):.6f}")
print(f"   ✅ Scaler works")

# Wrap in StreamingRobustScaler for create_windows_for_year
scaler = StreamingRobustScaler(feature_dim=len(FEATURE_KEYS))
scaler.scaler = scaler_raw  # Use already fitted scaler
scaler.is_fitted = True
print()

# 5. Test Window Creation
print("5. TEST WINDOW CREATION (CRITICAL)")
print("-" * 80)

windows_data = create_windows_for_year(
    year_data=year_data,
    scaler=scaler,
    lookback=256,
    horizon=12,
    stride=12,
    verbose=False
)

print(f"   Xw shape: {windows_data.Xw.shape}")
print(f"   y_ret shape: {windows_data.y_ret.shape}")
print(f"   y_dir shape: {windows_data.y_dir.shape}")
print(f"   y_rv shape: {windows_data.y_rv.shape}")
print()

# CRITICAL CHECKS
assert windows_data.y_dir.ndim == 1, "y_dir must be 1D!"
assert windows_data.y_rv.ndim == 1, "y_rv must be 1D (scalar)!"

unique_dirs = np.unique(windows_data.y_dir)
assert set(unique_dirs) == {0, 1}, f"Direction must be binary {{0,1}}, got {set(unique_dirs)}"

print(f"   ✅ Direction is binary: {unique_dirs}")
print(f"   ✅ RV is scalar: {windows_data.y_rv.shape}")

# Check balance
counts = Counter(windows_data.y_dir)
print(f"   Direction balance: DOWN={counts[0]} ({100*counts[0]/len(windows_data.y_dir):.1f}%), "
      f"UP={counts[1]} ({100*counts[1]/len(windows_data.y_dir):.1f}%)")
print()

# 6. Test Model
print("6. TEST MODEL")
print("-" * 80)

cfg = TRMConfig(
    lookback=256, horizon=12, stride=12,
    d_model=128, n_heads=4, d_ff=256, dropout=0.15,
    batch_size=32, lr=0.0003,
    w_ret=1.0, w_dir=0.8, w_rv=0.3
)

model = TinyRecursiveMarketModel(cfg=cfg, feature_dim=len(FEATURE_KEYS))

# Forward pass
sample = windows_data.Xw[:10]
outputs = model(sample, training=False)

print(f"   Model outputs:")
print(f"     ret: {outputs['ret'].shape} (expected: [10, 12])")
print(f"     dir: {outputs['dir'].shape} (expected: [10, 2])")
print(f"     rv:  {outputs['rv'].shape} (expected: [10,])")
print()

assert outputs['ret'].shape == (10, 12), f"Bad ret shape: {outputs['ret'].shape}"
assert outputs['dir'].shape == (10, 2), f"Bad dir shape: {outputs['dir'].shape}"
assert outputs['rv'].shape == (10,), f"Bad rv shape: {outputs['rv'].shape}"

print(f"   ✅ Model outputs correct")
print()

# 7. Test Training Step
print("7. TEST TRAINING STEP (CRITICAL)")
print("-" * 80)

# Create dataset
ds = tf.data.Dataset.from_tensor_slices((
    windows_data.Xw[:64],
    {
        "ret": windows_data.y_ret[:64],
        "dir": windows_data.y_dir[:64],
        "rv": windows_data.y_rv[:64]  # NOW SCALAR!
    }
)).batch(32)

# Compile
optimizer = tf.keras.optimizers.AdamW(learning_rate=0.0003)

losses = {
    "ret": tf.keras.losses.Huber(delta=1.0),
    "dir": tf.keras.losses.SparseCategoricalCrossentropy(),
    "rv": tf.keras.losses.Huber(delta=0.01),
}

metrics = {
    "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    "dir": [tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
    "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
}

model.compile(optimizer=optimizer, loss=losses, metrics=metrics)

# Training step
print("   Running 1 epoch...")
history = model.fit(ds, epochs=1, verbose=0)

print()
print("   Results after 1 epoch:")
for key, value in history.history.items():
    print(f"     {key}: {value[0]:.6f}")

# Check for NaN/Inf
all_finite = all(np.isfinite(v[0]) for v in history.history.values())
assert all_finite, "NaN/Inf detected in losses!"

# Check direction accuracy
dir_acc = history.history['dir_acc'][0]
print()
if dir_acc >= 0.48:  # Allow some variance
    print(f"   ✅ Direction accuracy: {dir_acc:.1%} (>= baseline)")
else:
    print(f"   ⚠️  Direction accuracy: {dir_acc:.1%} (low, but may be due to synthetic data)")

print()

# Final summary
print("=" * 80)
print("✅ ALL PIPELINE TESTS PASSED!")
print("=" * 80)
print()
print("Le pipeline complet fonctionne:")
print("  ✓ S3 Loader (ou synthetic data)")
print("  ✓ Feature engineering")
print("  ✓ Window creation (direction binaire, RV scalaire)")
print("  ✓ Model architecture (2 classes dir, scalar RV)")
print("  ✓ Training step (pas de NaN/Inf)")
print()
print("🚀 Prêt pour l'entraînement complet!")
print()
