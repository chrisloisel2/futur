"""
Regime Classifier - Feature Engineering
========================================

CRITICAL FEATURES pour discriminer impulse vs calm vs reversal.

Basé sur la confusion matrix observée:
- calm → reversal (71,039 confusions)
- impulse → reversal (70,196 confusions)
- impulse recall catastrophique (29,313 / 150,369 = 19.5%)

SOLUTION: Features capturant la VIOLENCE du mouvement (pas juste direction).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


def create_regime_discriminant_features(df: pd.DataFrame) -> np.ndarray:
    """
    Create 5 CRITICAL features to discriminate impulse vs calm.

    Ces features sont le MINIMUM VITAL pour que le modèle apprenne impulse.

    Args:
        df: DataFrame with OHLCV + computed indicators

    Returns:
        Feature matrix (n_samples, 5)
    """
    features = []

    # ========================================================================
    # FEATURE 1: abs_ret_1 (instant velocity)
    # ========================================================================
    # Impulse = mouvement violent IMMÉDIAT
    # Calm = mouvement faible
    # Reversal = changement de direction (mais pas forcément violent)

    abs_ret_1 = df['ret'].abs().values.reshape(-1, 1)
    features.append(abs_ret_1)

    # ========================================================================
    # FEATURE 2: abs_ret_5 (momentum cumulé court terme)
    # ========================================================================
    # Impulse = accumulation de mouvement unidirectionnel rapide
    # Sum des 5 derniers returns en valeur absolue

    ret_5_sum = df['ret'].rolling(5).sum().abs().fillna(0).values.reshape(-1, 1)
    features.append(ret_5_sum)

    # ========================================================================
    # FEATURE 3: range_1 (amplitude normalisée)
    # ========================================================================
    # Impulse = large range relative au prix
    # Calm = range faible
    # Reversal = peut avoir range élevé mais avec wicks

    range_norm = ((df['High'] - df['Low']) / (df['Close'] + 1e-8)).fillna(0).values.reshape(-1, 1)
    features.append(range_norm)

    # ========================================================================
    # FEATURE 4: vol_z (volume anomaly)
    # ========================================================================
    # Impulse = souvent accompagné de volume anormal
    # Calm = volume normal/faible
    # Reversal = volume spike mais avec indécision

    vol_mean = df['Volume'].rolling(60).mean()
    vol_std = df['Volume'].rolling(60).std()
    vol_z = ((df['Volume'] - vol_mean) / (vol_std + 1e-8)).fillna(0).values.reshape(-1, 1)
    features.append(vol_z)

    # ========================================================================
    # FEATURE 5: rv_ratio (volatility regime shift)
    # ========================================================================
    # Impulse = RV court terme >> RV long terme
    # Calm = RV stable
    # Reversal = RV élevé mais pas forcément ratio élevé

    rv_5 = df['ret'].rolling(5).std().fillna(0)
    rv_60 = df['ret'].rolling(60).std().fillna(0)
    rv_ratio = (rv_5 / (rv_60 + 1e-9)).fillna(0).values.reshape(-1, 1)
    features.append(rv_ratio)

    # Concatenate all
    X = np.hstack(features)

    return X


def create_full_regime_features(df: pd.DataFrame) -> np.ndarray:
    """
    Create FULL feature set for regime classification.

    Includes:
    - 5 critical features (impulse discrimination)
    - Additional context features (trend, momentum, microstructure)

    Total: ~15-20 features (not 200, focused)
    """
    features = []

    # ========================================================================
    # CRITICAL FEATURES (5)
    # ========================================================================
    critical_features = create_regime_discriminant_features(df)
    features.append(critical_features)

    # ========================================================================
    # ADDITIONAL CONTEXT FEATURES
    # ========================================================================

    # Multi-scale returns (direction context)
    ret_1 = df['ret'].values.reshape(-1, 1)
    ret_10 = df['ret'].rolling(10).mean().fillna(0).values.reshape(-1, 1)
    ret_30 = df['ret'].rolling(30).mean().fillna(0).values.reshape(-1, 1)
    features.extend([ret_1, ret_10, ret_30])

    # Multi-scale RV (volatility context)
    rv_15 = df['ret'].rolling(15).std().fillna(0).values.reshape(-1, 1)
    rv_60 = df['ret'].rolling(60).std().fillna(0).values.reshape(-1, 1)
    features.extend([rv_15, rv_60])

    # EMA trend strength
    if 'ema_20' in df.columns and 'ema_50' in df.columns:
        ema_slope = ((df['ema_20'] - df['ema_50']) / (df['ema_50'] + 1e-8)).fillna(0).values.reshape(-1, 1)
        features.append(ema_slope)

    # ATR normalized (structural volatility)
    if 'atr_14' in df.columns:
        atr_norm = (df['atr_14'] / (df['Close'] + 1e-8)).fillna(0).values.reshape(-1, 1)
        features.append(atr_norm)

    # RSI deviation (overbought/oversold)
    if 'rsi_14' in df.columns:
        rsi_dev = (df['rsi_14'] - 50).values.reshape(-1, 1)
        features.append(rsi_dev)

    # Body ratio (candle shape)
    body = abs(df['Close'] - df['Open'])
    total_range = df['High'] - df['Low'] + 1e-8
    body_ratio = (body / total_range).fillna(0).values.reshape(-1, 1)
    features.append(body_ratio)

    # Momentum acceleration (2nd derivative)
    ret_diff = df['ret'].diff().fillna(0).values.reshape(-1, 1)
    features.append(ret_diff)

    # Realized skewness (asymmetry)
    ret_skew = df['ret'].rolling(20).skew().fillna(0).values.reshape(-1, 1)
    features.append(ret_skew)

    # Concatenate all
    X = np.hstack(features)

    return X


def validate_feature_matrix(X: np.ndarray, feature_names: Optional[list] = None):
    """
    Validate feature matrix before training.

    Checks:
    - No NaN
    - No Inf
    - No constant features
    - Reasonable ranges
    """
    n_samples, n_features = X.shape

    # Check NaN
    nan_mask = np.isnan(X)
    nan_ratio = nan_mask.sum() / X.size
    if nan_ratio > 0:
        print(f"⚠️  WARNING: {nan_ratio:.2%} NaN values detected")
        # Replace with 0
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Check Inf
    inf_mask = np.isinf(X)
    inf_ratio = inf_mask.sum() / X.size
    if inf_ratio > 0:
        print(f"⚠️  WARNING: {inf_ratio:.2%} Inf values detected")
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Check constant features
    for i in range(n_features):
        std = X[:, i].std()
        if std < 1e-10:
            name = feature_names[i] if feature_names else f"feature_{i}"
            print(f"⚠️  WARNING: Feature '{name}' is constant (std={std:.2e})")

    # Print summary
    print(f"\n✅ Feature matrix validation:")
    print(f"   Shape: {X.shape}")
    print(f"   NaN: {nan_ratio:.2%}")
    print(f"   Inf: {inf_ratio:.2%}")
    print(f"   Min: {X.min():.4f}")
    print(f"   Max: {X.max():.4f}")
    print(f"   Mean: {X.mean():.4f}")
    print(f"   Std: {X.std():.4f}")

    return X


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Create synthetic data
    np.random.seed(42)

    n_samples = 10000
    df = pd.DataFrame({
        'Open': np.random.randn(n_samples).cumsum() + 100,
        'High': np.random.randn(n_samples).cumsum() + 101,
        'Low': np.random.randn(n_samples).cumsum() + 99,
        'Close': np.random.randn(n_samples).cumsum() + 100,
        'Volume': np.random.randint(1000, 10000, n_samples),
        'ret': np.random.randn(n_samples) * 0.01,
        'ema_20': np.random.randn(n_samples).cumsum() + 100,
        'ema_50': np.random.randn(n_samples).cumsum() + 100,
        'atr_14': np.abs(np.random.randn(n_samples)) * 2,
        'rsi_14': np.random.rand(n_samples) * 100,
    })

    print("Creating critical features...")
    X_critical = create_regime_discriminant_features(df)
    print(f"Critical features shape: {X_critical.shape}")

    print("\nCreating full features...")
    X_full = create_full_regime_features(df)
    print(f"Full features shape: {X_full.shape}")

    print("\nValidating features...")
    X_full = validate_feature_matrix(X_full)

    print("\n✅ Feature engineering complete")
