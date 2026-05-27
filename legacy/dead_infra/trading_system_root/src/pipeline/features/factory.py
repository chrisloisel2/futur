from __future__ import annotations

import pandas as pd

from pipeline.features.fast import compute_fast_features
from pipeline.features.mid import compute_mid_features
from pipeline.features.slow import compute_slow_features


class FeatureFactory:
    """
    Builds fast/mid/slow feature sets from clean events.

    FIXED: Proper NaN handling to prevent model prediction failures.
    """

    def __init__(self, feature_set: str = "v1", ffill_limit: int = 5) -> None:
        self.feature_set = feature_set
        self.ffill_limit = ffill_limit  # Max forward fill steps

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        # Compute features
        fast = compute_fast_features(df)
        mid = compute_mid_features(df)
        slow = compute_slow_features(df)

        # Join with outer to preserve all timestamps
        out = fast.join(mid, how="outer").join(slow, how="outer")

        # CRITICAL FIX: Handle NaN values
        # Strategy:
        # 1. Forward fill for up to 5 periods (handles temporary gaps)
        # 2. Fill remaining NaN with 0 (safer than dropping)
        # 3. Log excessive NaN rates

        # Track NaN rates before filling
        nan_rates = out.isna().mean()
        high_nan_cols = nan_rates[nan_rates > 0.3].index.tolist()

        if high_nan_cols:
            from common.logging.setup import get_logger
            logger = get_logger(__name__)
            logger.warning({
                "msg": "High NaN rates detected",
                "columns": high_nan_cols,
                "rates": {col: f"{nan_rates[col]*100:.1f}%" for col in high_nan_cols[:5]},
            })

        # Forward fill (causal, up to limit)
        out = out.ffill(limit=self.ffill_limit)

        # CRITICAL FIX: Use median fill for volatility features, not zero
        # Zero volatility = false "calm" signal
        volatility_cols = [col for col in out.columns if any(x in col.lower() for x in ['vol', 'rv', 'atr', 'std'])]

        for col in volatility_cols:
            if col in out.columns and out[col].isna().any():
                median_val = out[col].median()
                if pd.notna(median_val) and median_val > 0:
                    out[col] = out[col].fillna(median_val)

        # Fill remaining NaN with 0 (for non-volatility features)
        out = out.fillna(0.0)

        out["feature_set"] = self.feature_set
        return out.reset_index()
