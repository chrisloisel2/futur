#!/usr/bin/env python3
"""
Script de diagnostic des données S3
Vérifie colonnes, valeurs, et qualité des features
"""

import sys
import numpy as np
import pandas as pd

# Add to path
sys.path.insert(0, '/Users/christopher/Desktop/futur')

from ai.s3_parquet_loader import S3ParquetLoader, compute_features
from ai.models.model import FEATURE_KEYS

print("="*80)
print("  DIAGNOSTIC DES DONNÉES S3")
print("="*80)
print()

# Load 2017 data
loader = S3ParquetLoader(bucket="qbia")
year_data = loader.load_year(2017, verbose=True)

print("\n" + "="*80)
print("ANALYSE DES COLONNES RAW")
print("="*80)

df = year_data.df
print(f"\nColonnes présentes ({len(df.columns)}):")
for i, col in enumerate(df.columns, 1):
    print(f"  {i:2d}. {col}")

print(f"\nPremières lignes:")
print(df.head())

print(f"\nStatistiques de base:")
print(df.describe())

# Check price columns
print("\n" + "="*80)
print("ANALYSE DES PRIX")
print("="*80)

if 'Close' in df.columns:
    close = df['Close'].values
    print(f"Close price stats:")
    print(f"  Min:    {np.min(close):.2f}")
    print(f"  Max:    {np.max(close):.2f}")
    print(f"  Mean:   {np.mean(close):.2f}")
    print(f"  Std:    {np.std(close):.2f}")
    print(f"  NaN:    {np.sum(np.isnan(close))}")
    print(f"  Zero:   {np.sum(close == 0)}")

    # Check returns
    ret = np.diff(close) / close[:-1]
    print(f"\nReturns stats:")
    print(f"  Min:    {np.min(ret):.6f}")
    print(f"  Max:    {np.max(ret):.6f}")
    print(f"  Mean:   {np.mean(ret):.6f}")
    print(f"  Std:    {np.std(ret):.6f}")
    print(f"  NaN:    {np.sum(np.isnan(ret))}")
    print(f"  Zero:   {np.sum(ret == 0)} ({100*np.sum(ret == 0)/len(ret):.2f}%)")
else:
    print("❌ 'Close' column not found!")

# Compute features
print("\n" + "="*80)
print("CALCUL DES FEATURES")
print("="*80)

df_features = compute_features(df, verbose=True)

print(f"\nColonnes après feature engineering ({len(df_features.columns)}):")
for i, col in enumerate(df_features.columns, 1):
    print(f"  {i:2d}. {col}")

# Check log_ret
print("\n" + "="*80)
print("ANALYSE LOG_RET")
print("="*80)

if 'log_ret' in df_features.columns:
    log_ret = df_features['log_ret'].values
    print(f"log_ret stats:")
    print(f"  Min:    {np.nanmin(log_ret):.6f}")
    print(f"  Max:    {np.nanmax(log_ret):.6f}")
    print(f"  Mean:   {np.nanmean(log_ret):.6f}")
    print(f"  Std:    {np.nanstd(log_ret):.6f}")
    print(f"  NaN:    {np.sum(np.isnan(log_ret))} ({100*np.sum(np.isnan(log_ret))/len(log_ret):.2f}%)")
    print(f"  Zero:   {np.sum(log_ret == 0)} ({100*np.sum(log_ret == 0)/len(log_ret):.2f}%)")
    print(f"  Inf:    {np.sum(np.isinf(log_ret))}")

    # After fillna(0)
    log_ret_clean = df_features['log_ret'].fillna(0).values
    print(f"\nAprès fillna(0):")
    print(f"  Zero:   {np.sum(log_ret_clean == 0)} ({100*np.sum(log_ret_clean == 0)/len(log_ret_clean):.2f}%)")
    print(f"  Non-zero: {np.sum(log_ret_clean != 0)}")
else:
    print("❌ 'log_ret' not found!")

# Check FEATURE_KEYS
print("\n" + "="*80)
print("VÉRIFICATION FEATURE_KEYS")
print("="*80)

print(f"\nFeatures attendues par le modèle ({len(FEATURE_KEYS)}):")
missing = []
present = []

for key in FEATURE_KEYS:
    if key in df_features.columns:
        present.append(key)
    else:
        missing.append(key)

print(f"\n✓ Présentes ({len(present)}):")
for key in present:
    print(f"  - {key}")

if missing:
    print(f"\n❌ Manquantes ({len(missing)}):")
    for key in missing:
        print(f"  - {key}")

# Sample windows
print("\n" + "="*80)
print("TEST WINDOWING")
print("="*80)

from ai.s3_parquet_loader import prepare_model_data
from ai.models.model import make_windows

X, y_ret, y_rv = prepare_model_data(df_features, FEATURE_KEYS)

print(f"\nX shape: {X.shape}")
print(f"y_ret shape: {y_ret.shape}")
print(f"y_rv shape: {y_rv.shape}")

print(f"\ny_ret sample (premiers 100):")
print(y_ret[:100])
print(f"  Non-zero: {np.sum(y_ret[:100] != 0)}")

# Create windows with stride=1 (pour test)
Xw, y_ret_h, y_dir, y_rv_h = make_windows(X, y_ret, y_rv, lookback=256, horizon=12, stride=1)

print(f"\nWindows créées:")
print(f"  Xw shape: {Xw.shape}")
print(f"  y_ret_h shape: {y_ret_h.shape}")
print(f"  y_dir shape: {y_dir.shape}")

print(f"\ny_ret_h sample (premiers 10 windows, premier step):")
print(y_ret_h[:10, 0])
print(f"  Non-zero: {np.sum(y_ret_h[:10, 0] != 0)}")

print(f"\ny_dir distribution:")
unique, counts = np.unique(y_dir, return_counts=True)
for val, count in zip(unique, counts):
    label = ['DOWN', 'FLAT', 'UP'][val]
    print(f"  {label} ({val}): {count} ({100*count/len(y_dir):.2f}%)")

print("\n" + "="*80)
print("DIAGNOSTIC COMPLET")
print("="*80)
