"""
Binary Regime Label Builder with Gray Zone
===========================================

Build clean calm/reversal labels with a gray zone to avoid noisy boundaries.

Label Logic:
- calm: Low forward RV + small forward drawdown
- reversal: Significant forward drawdown OR (trend flip + excursion)
- gray: Everything else (dropped from training)

This ensures we only train on clear examples, reducing label noise.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Tuple, Dict
from dataclasses import dataclass


@dataclass
class LabelConfig:
    """Configuration for binary regime labels."""
    horizon: int = 60  # minutes, consistent with Edge forecaster
    rv_calm_quantile: float = 0.40  # RV below this = calm candidate
    dd_small_threshold: float = -0.003  # drawdown threshold for calm (0.3%)
    dd_big_threshold: float = -0.010  # drawdown threshold for reversal (1.0%)
    excursion_threshold: float = 0.015  # 1.5% excursion for reversal
    min_gray_prop_warning: float = 0.25  # warn if gray < 25%
    max_gray_prop_warning: float = 0.60  # warn if gray > 60%


def compute_forward_rv(df: pd.DataFrame, horizon: int = 60) -> np.ndarray:
    """
    Compute forward realized volatility over horizon.

    RV_fwd[t] = sqrt(sum((ret[t+i])^2 for i in 1..horizon))
    """
    ret = df['ret'].values if 'ret' in df.columns else np.zeros(len(df))
    rv_fwd = np.full(len(df), np.nan)

    for i in range(len(df) - horizon):
        window_ret = ret[i+1:i+1+horizon]
        rv_fwd[i] = np.sqrt(np.sum(window_ret**2))

    return rv_fwd


def compute_forward_drawdown(df: pd.DataFrame, horizon: int = 60) -> np.ndarray:
    """
    Compute forward drawdown over horizon.

    dd_fwd[t] = min(cumret[t+1:t+horizon+1])

    This captures the worst intra-period drop.
    """
    close = df['close'].values
    dd_fwd = np.full(len(df), np.nan)

    for i in range(len(df) - horizon):
        future_close = close[i+1:i+1+horizon]
        entry_price = close[i]
        cumret = (future_close / entry_price) - 1.0
        dd_fwd[i] = np.min(cumret)

    return dd_fwd


def compute_forward_excursion(df: pd.DataFrame, horizon: int = 60) -> np.ndarray:
    """
    Compute forward excursion (max absolute deviation from entry).

    excursion_fwd[t] = max(abs(ret[t+1:t+horizon+1]))
    """
    close = df['close'].values
    exc_fwd = np.full(len(df), np.nan)

    for i in range(len(df) - horizon):
        future_close = close[i+1:i+1+horizon]
        entry_price = close[i]
        cumret = (future_close / entry_price) - 1.0
        exc_fwd[i] = np.max(np.abs(cumret))

    return exc_fwd


def detect_trend_flip(df: pd.DataFrame, horizon: int = 60, ema_col: str = 'ema_50') -> np.ndarray:
    """
    Detect if price crosses EMA in forward window (potential reversal).

    Returns 1 if flip detected, 0 otherwise.
    """
    close = df['close'].values
    ema = df[ema_col].values if ema_col in df.columns else close.copy()

    flip = np.zeros(len(df), dtype=bool)

    for i in range(len(df) - horizon):
        current_above = close[i] > ema[i]
        future_close = close[i+1:i+1+horizon]
        future_ema = ema[i+1:i+1+horizon]

        if current_above:
            # check if any future point goes below
            flip[i] = np.any(future_close < future_ema)
        else:
            # check if any future point goes above
            flip[i] = np.any(future_close > future_ema)

    return flip.astype(int)


def build_binary_regime_labels(
    df: pd.DataFrame,
    config: LabelConfig = None,
    fit_on_train_only: bool = True,
) -> Tuple[np.ndarray, Dict[str, float]]:
    """
    Build binary regime labels with gray zone.

    Args:
        df: DataFrame with OHLCV + features
        config: Label configuration
        fit_on_train_only: If True, compute thresholds only on first 80% of data

    Returns:
        labels: Array of shape (n,) with values:
            0 = calm
            1 = reversal
            -1 = gray (to be filtered out)
        stats: Dict with label statistics
    """
    if config is None:
        config = LabelConfig()

    # Compute forward features
    rv_fwd = compute_forward_rv(df, config.horizon)
    dd_fwd = compute_forward_drawdown(df, config.horizon)
    exc_fwd = compute_forward_excursion(df, config.horizon)
    trend_flip = detect_trend_flip(df, config.horizon)

    # Determine training set for threshold estimation (avoid leakage)
    if fit_on_train_only:
        train_size = int(0.8 * len(df))
        train_mask = np.arange(len(df)) < train_size
    else:
        train_mask = np.ones(len(df), dtype=bool)

    # Estimate RV threshold on training set only
    rv_valid = rv_fwd[train_mask & ~np.isnan(rv_fwd)]
    rv_threshold = np.quantile(rv_valid, config.rv_calm_quantile) if len(rv_valid) > 0 else 0.01

    # Build labels
    labels = np.full(len(df), -1, dtype=int)  # start with all gray

    # Calm: low RV + small drawdown
    calm_mask = (
        (rv_fwd < rv_threshold) &
        (dd_fwd > config.dd_small_threshold) &
        ~np.isnan(rv_fwd) &
        ~np.isnan(dd_fwd)
    )
    labels[calm_mask] = 0

    # Reversal: big drawdown OR (trend flip + excursion)
    reversal_mask = (
        (
            (dd_fwd < config.dd_big_threshold) |
            ((trend_flip == 1) & (exc_fwd > config.excursion_threshold))
        ) &
        ~np.isnan(dd_fwd) &
        ~np.isnan(exc_fwd)
    )
    labels[reversal_mask] = 1

    # If both calm and reversal (edge case), mark as gray
    labels[(calm_mask & reversal_mask)] = -1

    # Compute statistics
    n_total = len(labels)
    n_calm = np.sum(labels == 0)
    n_reversal = np.sum(labels == 1)
    n_gray = np.sum(labels == -1)

    stats = {
        'n_total': n_total,
        'n_calm': n_calm,
        'n_reversal': n_reversal,
        'n_gray': n_gray,
        'prop_calm': n_calm / n_total if n_total > 0 else 0,
        'prop_reversal': n_reversal / n_total if n_total > 0 else 0,
        'prop_gray': n_gray / n_total if n_total > 0 else 0,
        'rv_threshold': float(rv_threshold),
        'dd_small_threshold': config.dd_small_threshold,
        'dd_big_threshold': config.dd_big_threshold,
    }

    return labels, stats


def validate_label_quality(stats: Dict[str, float], config: LabelConfig = None) -> Tuple[bool, str]:
    """
    Validate label distribution quality.

    Returns:
        (is_valid, warning_message)
    """
    if config is None:
        config = LabelConfig()

    warnings = []

    # Check gray zone proportion
    if stats['prop_gray'] < config.min_gray_prop_warning:
        warnings.append(f"Gray zone too small ({stats['prop_gray']:.1%}), labels may be noisy")
    if stats['prop_gray'] > config.max_gray_prop_warning:
        warnings.append(f"Gray zone too large ({stats['prop_gray']:.1%}), labels may be too strict")

    # Check class balance
    if stats['n_calm'] < 100 or stats['n_reversal'] < 100:
        warnings.append(f"Insufficient samples: calm={stats['n_calm']}, reversal={stats['n_reversal']}")

    # Check extreme imbalance
    if stats['n_calm'] > 0 and stats['n_reversal'] > 0:
        ratio = max(stats['n_calm'], stats['n_reversal']) / min(stats['n_calm'], stats['n_reversal'])
        if ratio > 10:
            warnings.append(f"Extreme class imbalance: ratio={ratio:.1f}:1")

    is_valid = len(warnings) == 0
    warning_msg = "; ".join(warnings) if warnings else "OK"

    return is_valid, warning_msg


if __name__ == "__main__":
    # Example usage
    print("Binary Regime Label Builder - Example")
    print("=" * 60)

    # Create synthetic data
    np.random.seed(42)
    n = 10000
    df = pd.DataFrame({
        'close': 100 + np.cumsum(np.random.randn(n) * 0.01),
        'ret': np.random.randn(n) * 0.01,
        'ema_50': 100 + np.cumsum(np.random.randn(n) * 0.008),
    })

    config = LabelConfig(horizon=60)
    labels, stats = build_binary_regime_labels(df, config)

    print(f"\nLabel Statistics:")
    print(f"  Total samples: {stats['n_total']}")
    print(f"  Calm:     {stats['n_calm']:5d} ({stats['prop_calm']:6.2%})")
    print(f"  Reversal: {stats['n_reversal']:5d} ({stats['prop_reversal']:6.2%})")
    print(f"  Gray:     {stats['n_gray']:5d} ({stats['prop_gray']:6.2%})")
    print(f"\nThresholds:")
    print(f"  RV threshold: {stats['rv_threshold']:.6f}")
    print(f"  DD small:     {stats['dd_small_threshold']:.4f}")
    print(f"  DD big:       {stats['dd_big_threshold']:.4f}")

    is_valid, msg = validate_label_quality(stats, config)
    print(f"\nValidation: {'✅ OK' if is_valid else f'⚠️  {msg}'}")
