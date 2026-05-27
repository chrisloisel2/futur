from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


class CorrelationModel:
    """
    Correlation and diversification model.

    FIXED: Added methods to detect high correlation and reduce position sizes.
    """

    def __init__(self, clusters: Dict[str, str], max_correlation: float = 0.7):
        self.clusters = clusters
        self.max_correlation = max_correlation

    def diversification_score(self, targets: pd.DataFrame) -> float:
        """
        Compute diversification score [0, 1].
        1.0 = perfect diversification, 0.0 = concentrated in one cluster.
        """
        if targets.empty:
            return 1.0
        clusters = targets["symbol"].map(self.clusters).fillna("default")
        weights = abs(targets["notional_usd"]) / abs(targets["notional_usd"]).sum()
        cluster_weights = weights.groupby(clusters).sum()
        score = 1.0 - cluster_weights.max()
        return float(np.clip(score, 0.0, 1.0))

    def compute_rolling_correlation(
        self,
        returns_df: pd.DataFrame,
        window: int = 30
    ) -> Dict[tuple, float]:
        """
        Compute rolling correlation matrix for all symbol pairs.

        Args:
            returns_df: DataFrame with columns as symbols, rows as time
            window: Rolling window size (e.g., 30 periods)

        Returns:
            Dict of {(symbol1, symbol2): correlation}
        """
        if returns_df.empty or len(returns_df.columns) < 2:
            return {}

        # Compute rolling correlation
        corr_matrix = returns_df.rolling(window).corr()

        # Extract latest correlations
        latest_corr = corr_matrix.iloc[-len(returns_df.columns):]

        # Build dict of pairs
        correlations = {}
        symbols = returns_df.columns.tolist()
        for i, sym1 in enumerate(symbols):
            for sym2 in symbols[i+1:]:
                corr_value = latest_corr.loc[sym1, sym2]
                if not pd.isna(corr_value):
                    correlations[(sym1, sym2)] = float(corr_value)

        return correlations

    def check_high_correlation(
        self,
        targets: list,
        correlations: Dict[tuple, float]
    ) -> list[tuple]:
        """
        Check for high correlations between target positions.

        Returns:
            List of (symbol1, symbol2, correlation) tuples exceeding threshold
        """
        high_corr_pairs = []

        target_symbols = {t.symbol for t in targets}

        for (sym1, sym2), corr in correlations.items():
            if sym1 in target_symbols and sym2 in target_symbols:
                if abs(corr) > self.max_correlation:
                    high_corr_pairs.append((sym1, sym2, corr))

        return high_corr_pairs

    def apply_correlation_penalty(
        self,
        targets: list,
        high_corr_pairs: list[tuple],
        penalty_factor: float = 0.5
    ) -> list:
        """
        Reduce position sizes for highly correlated pairs.

        Args:
            targets: List of TargetPosition objects
            high_corr_pairs: List from check_high_correlation
            penalty_factor: Multiplier for penalized positions (0.5 = halve size)

        Returns:
            Updated targets with reduced sizes
        """
        if not high_corr_pairs:
            return targets

        # Track which symbols to penalize
        penalized_symbols = set()
        for sym1, sym2, _ in high_corr_pairs:
            penalized_symbols.add(sym1)
            penalized_symbols.add(sym2)

        # Apply penalty
        for target in targets:
            if target.symbol in penalized_symbols:
                target.notional_usd *= penalty_factor
                if not hasattr(target, 'reasons'):
                    target.reasons = []
                target.reasons.append(f"correlation_penalty_{penalty_factor}")

        return targets
