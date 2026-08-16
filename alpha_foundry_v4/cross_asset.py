from __future__ import annotations

from typing import Tuple

import pandas as pd


def rolling_beta(asset_return: pd.Series, market_return: pd.Series, window: int = 300) -> pd.Series:
    a = pd.to_numeric(asset_return, errors="coerce")
    m = pd.to_numeric(market_return, errors="coerce")
    cov = a.rolling(window, min_periods=window).cov(m)
    var = m.rolling(window, min_periods=window).var(ddof=1)
    return cov / var.where(var.abs() > 1e-12)


def residual_return(asset_return: pd.Series, market_return: pd.Series, beta: pd.Series) -> pd.Series:
    a = pd.to_numeric(asset_return, errors="coerce")
    m = pd.to_numeric(market_return, errors="coerce")
    b = pd.to_numeric(beta, errors="coerce")
    return a - b * m


def lead_lag_pair(leader_innovation: pd.Series, follower_residual: pd.Series, steps: int) -> Tuple[float, int]:
    if steps <= 0:
        raise ValueError("steps must be positive")
    x = pd.to_numeric(leader_innovation, errors="coerce")
    y = pd.to_numeric(follower_residual, errors="coerce").shift(-steps)
    valid = x.notna() & y.notna()
    if int(valid.sum()) < 3:
        return float("nan"), int(valid.sum())
    return float(x[valid].corr(y[valid], method="spearman")), int(valid.sum())
