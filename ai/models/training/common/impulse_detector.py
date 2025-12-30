"""
Impulse Detector: Event-based detection of non-stationary market shocks.

CRITICAL DISTINCTION:
- Impulse is NOT a regime (not stationary, short-lived)
- Impulse is an EVENT / CONDITION requiring specific handling
- Detected causally at time t (no future leak)

USE CASES:
- Feature for other models (is_impulse flag, impulse_score)
- Meta-control: downscale position size during impulse
- Execution: switch MAKER → TAKER during impulse
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class ImpulseDetector:
    """
    Event-based impulse detection for non-stationary liquidity shocks.

    Impulse characteristics:
    - Violent price movement (high |returns| / realized vol)
    - Volume spike
    - Spread expansion (if available)
    - Short duration (minutes, not hours)

    NOT a regime (not stationary), so:
    - No classification metrics (accuracy/recall don't apply)
    - Event-level metrics only (frequency, conditional PnL, execution cost)
    """

    def __init__(
        self,
        threshold: float = 0.7,
        ret_weight: float = 0.5,
        vol_weight: float = 0.3,
        spread_weight: float = 0.2,
        sigma_threshold: float = 2.0,
    ):
        """
        Args:
            threshold: Binary impulse flag threshold (default 0.7)
            ret_weight: Weight for return shock component
            vol_weight: Weight for volume shock component
            spread_weight: Weight for spread expansion component
            sigma_threshold: Sigma level for raw score threshold (default 2.0)
        """
        self.threshold = threshold
        self.ret_weight = ret_weight
        self.vol_weight = vol_weight
        self.spread_weight = spread_weight
        self.sigma_threshold = sigma_threshold

        # Event log for metrics
        self.events: List[Dict] = []

    def compute_score(
        self,
        ret_1m: float,
        rv_60: float,
        volume: float,
        volume_ma: float,
        volume_std: float,
        spread_z: float = 0.0,
    ) -> float:
        """
        Compute impulse score at time t (causal).

        Args:
            ret_1m: 1-minute log return
            rv_60: 60-minute realized volatility
            volume: Current volume
            volume_ma: Volume moving average (e.g., 120-period)
            volume_std: Volume standard deviation
            spread_z: Spread z-score (optional, default 0)

        Returns:
            Impulse score ∈ [0, 1]

        Formula:
            z_ret = |ret_1m| / rv_60
            z_vol = (volume - volume_ma) / volume_std
            raw_score = w1*z_ret + w2*z_vol + w3*spread_z
            impulse_score = sigmoid(raw_score - sigma_threshold)
        """
        # Return shock (normalized by realized volatility)
        z_ret = abs(ret_1m) / (rv_60 + 1e-6)

        # Volume shock (z-score)
        z_vol = (volume - volume_ma) / (volume_std + 1e-6)

        # Composite raw score
        raw_score = (
            self.ret_weight * z_ret +
            self.vol_weight * z_vol +
            self.spread_weight * spread_z
        )

        # Sigmoid activation with threshold
        score = 1.0 / (1.0 + np.exp(-(raw_score - self.sigma_threshold)))

        return float(score)

    def detect(
        self,
        timestamp: pd.Timestamp,
        ret_1m: float,
        rv_60: float,
        volume: float,
        volume_ma: float,
        volume_std: float,
        spread_z: float = 0.0,
        regime: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """
        Detect impulse event and log if triggered.

        Args:
            timestamp: Current timestamp
            ret_1m: 1-minute log return
            rv_60: 60-minute realized volatility
            volume: Current volume
            volume_ma: Volume moving average
            volume_std: Volume standard deviation
            spread_z: Spread z-score (optional)
            regime: Current regime (for logging)

        Returns:
            (is_impulse, impulse_score)
        """
        score = self.compute_score(
            ret_1m=ret_1m,
            rv_60=rv_60,
            volume=volume,
            volume_ma=volume_ma,
            volume_std=volume_std,
            spread_z=spread_z,
        )

        is_impulse = score > self.threshold

        if is_impulse:
            self.events.append({
                'timestamp': timestamp,
                'score': score,
                'ret_1m': ret_1m,
                'z_ret': abs(ret_1m) / (rv_60 + 1e-6),
                'z_vol': (volume - volume_ma) / (volume_std + 1e-6),
                'spread_z': spread_z,
                'regime': regime,
                'pnl_during': None,  # to be filled post-event
                'execution_cost': None,
                'duration_seconds': None,
            })

        return is_impulse, score

    def compute_features(
        self,
        df: pd.DataFrame,
        ret_col: str = 'ret_1m',
        rv_col: str = 'rv_60',
        volume_col: str = 'volume',
        spread_z_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Compute impulse features for a full DataFrame (vectorized).

        Args:
            df: DataFrame with OHLCV data
            ret_col: Column name for 1m returns
            rv_col: Column name for 60m realized volatility
            volume_col: Column name for volume
            spread_z_col: Optional column name for spread z-score

        Returns:
            DataFrame with added columns: impulse_score, is_impulse
        """
        df = df.copy()

        # Volume statistics
        df['volume_ma'] = df[volume_col].rolling(120, min_periods=60).mean()
        df['volume_std'] = df[volume_col].rolling(120, min_periods=60).std()

        # Return shock
        z_ret = np.abs(df[ret_col]) / (df[rv_col] + 1e-6)

        # Volume shock
        z_vol = (df[volume_col] - df['volume_ma']) / (df['volume_std'] + 1e-6)

        # Spread (if available)
        if spread_z_col and spread_z_col in df.columns:
            spread_z = df[spread_z_col]
        else:
            spread_z = 0.0

        # Composite score
        raw_score = (
            self.ret_weight * z_ret +
            self.vol_weight * z_vol +
            self.spread_weight * spread_z
        )

        df['impulse_score'] = 1.0 / (1.0 + np.exp(-(raw_score - self.sigma_threshold)))
        df['is_impulse'] = df['impulse_score'] > self.threshold

        return df

    def get_event_metrics(self, total_days: float) -> Dict[str, float]:
        """
        Compute event-level metrics (NOT classification metrics).

        Args:
            total_days: Total number of days in the period

        Returns:
            Dictionary of event metrics:
            - impulse_frequency_per_day
            - avg_score
            - avg_return_magnitude
            - max_score
            - regime_distribution (if regime was logged)
        """
        if not self.events:
            return {
                'impulse_frequency_per_day': 0.0,
                'avg_score': 0.0,
                'avg_return_magnitude': 0.0,
                'max_score': 0.0,
            }

        metrics = {
            'impulse_frequency_per_day': len(self.events) / max(total_days, 1),
            'avg_score': float(np.mean([e['score'] for e in self.events])),
            'avg_return_magnitude': float(np.mean([abs(e['ret_1m']) for e in self.events])),
            'max_score': float(max([e['score'] for e in self.events])),
            'avg_z_ret': float(np.mean([e['z_ret'] for e in self.events])),
            'avg_z_vol': float(np.mean([e['z_vol'] for e in self.events])),
        }

        # Regime distribution (if logged)
        regimes = [e['regime'] for e in self.events if e['regime'] is not None]
        if regimes:
            unique, counts = np.unique(regimes, return_counts=True)
            metrics['regime_distribution'] = {
                str(k): int(v) for k, v in zip(unique, counts)
            }

        # PnL during impulse (if filled)
        pnls = [e['pnl_during'] for e in self.events if e['pnl_during'] is not None]
        if pnls:
            metrics['avg_pnl_during_impulse'] = float(np.mean(pnls))
            metrics['total_impulse_pnl'] = float(np.sum(pnls))

        # Execution cost (if filled)
        costs = [e['execution_cost'] for e in self.events if e['execution_cost'] is not None]
        if costs:
            metrics['avg_execution_cost_impulse'] = float(np.mean(costs))

        return metrics

    def reset_events(self):
        """Clear event log."""
        self.events = []

    def __repr__(self) -> str:
        return (
            f"ImpulseDetector(threshold={self.threshold}, "
            f"events_logged={len(self.events)})"
        )


def create_impulse_features_batch(
    df: pd.DataFrame,
    detector: Optional[ImpulseDetector] = None,
) -> pd.DataFrame:
    """
    Convenience function to add impulse features to a DataFrame.

    Args:
        df: DataFrame with OHLCV data
        detector: Optional ImpulseDetector instance (creates default if None)

    Returns:
        DataFrame with impulse_score and is_impulse columns

    Example:
        >>> df = pd.DataFrame(...)
        >>> df = create_impulse_features_batch(df)
        >>> print(df[['timestamp', 'impulse_score', 'is_impulse']].head())
    """
    if detector is None:
        detector = ImpulseDetector()

    return detector.compute_features(df)
