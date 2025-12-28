from __future__ import annotations

import numpy as np
import pandas as pd


class VaREngine:
    def __init__(self, method: str = "parametric"):
        self.method = method

    def compute(self, returns: pd.Series, alpha: float = 0.95) -> tuple[float, float]:
        if returns.empty:
            return 0.0, 0.0
        if self.method == "historical":
            var = float(np.quantile(returns, 1 - alpha))
            cvar = float(returns[returns <= var].mean()) if (returns <= var).any() else var
        else:
            # Parametric VaR/CVaR using normal distribution
            mu = float(returns.mean())
            sigma = float(returns.std() or 0.0)
            # Use correct z-score for alpha (1.65 for 95%, 2.33 for 99%)
            from scipy import stats
            z = stats.norm.ppf(1 - alpha)
            var = mu - z * sigma
            # CVaR (Expected Shortfall) = mean of tail beyond VaR
            # For normal dist: CVaR = mu - sigma * phi(z) / (1-alpha)
            # where phi is PDF of standard normal
            phi_z = stats.norm.pdf(z)
            cvar = mu - sigma * (phi_z / (1 - alpha))
        return abs(var), abs(cvar)


def fractional_kelly(p_hit: float, payoff_ratio: float, cap: float = 0.10, shrink: float = 0.25) -> float:
    """
    Fractional Kelly sizing with conservative defaults.

    Args:
        p_hit: Probability of winning (should be calibrated)
        payoff_ratio: Average win / average loss
        cap: Maximum Kelly fraction (default 10% - conservative)
        shrink: Shrinkage factor to apply (default 0.25 - very conservative)

    Returns:
        Position size as fraction of capital
    """
    edge = p_hit * payoff_ratio - (1 - p_hit)
    if edge <= 0:
        return 0.0
    denom = payoff_ratio
    kelly = edge / denom if denom else 0.0
    # Apply cap first, then shrink
    kelly = min(cap, kelly) * shrink
    kelly = max(0.0, kelly)
    return float(kelly)
