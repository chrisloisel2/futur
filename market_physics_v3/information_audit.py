from __future__ import annotations

from typing import Dict, Mapping

import numpy as np
import pandas as pd


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    v = x.notna() & y.notna()
    if v.sum() < 3:
        return float("nan")
    return float(x[v].rank().corr(y[v].rank()))


def ic_decay(feature: pd.Series, future_returns: Mapping[str, pd.Series]) -> Dict[str, float]:
    return {str(h): spearman_ic(feature, target) for h, target in future_returns.items()}


def reverse_causality(feature: pd.Series, returns: pd.Series, lag: int = 1) -> Dict[str, float]:
    return {
        "feature_to_future_return": spearman_ic(feature, returns.shift(-lag)),
        "return_to_future_feature": spearman_ic(returns, feature.shift(-lag)),
    }


def cross_sectional_rank_ic(frame: pd.DataFrame, feature: str, target: str, time_col: str = "timestamp") -> pd.Series:
    values = {}
    for key, g in frame.groupby(time_col, sort=True):
        values[key] = spearman_ic(g[feature], g[target])
    return pd.Series(values, dtype=float)


def effective_sample_size(x: pd.Series, max_lag: int = 100) -> float:
    s = pd.Series(x).dropna()
    n = len(s)
    if n < 3:
        return float(n)
    acfs = []
    for lag in range(1, min(max_lag, n - 1) + 1):
        rho = s.autocorr(lag=lag)
        if not np.isfinite(rho) or rho <= 0:
            break
        acfs.append(rho)
    denom = 1.0 + 2.0 * sum(acfs)
    return float(n / denom) if denom > 0 else float(n)


def block_shuffle_null(feature: pd.Series, target: pd.Series, block: int = 288, repeats: int = 100, seed: int = 17) -> Dict[str, float]:
    x = pd.Series(feature).reset_index(drop=True)
    y = pd.Series(target).reset_index(drop=True)
    observed = spearman_ic(x, y)
    rng = np.random.default_rng(seed)
    starts = list(range(0, len(y), block))
    blocks = [y.iloc[s:s + block].to_numpy() for s in starts]
    null = []
    for _ in range(repeats):
        order = rng.permutation(len(blocks))
        shuffled = np.concatenate([blocks[i] for i in order])[:len(y)]
        null.append(spearman_ic(x, pd.Series(shuffled)))
    null_arr = np.array([v for v in null if np.isfinite(v)], dtype=float)
    if not np.isfinite(observed) or len(null_arr) == 0:
        return {"observed": observed, "null_mean": float("nan"), "p_two_sided": float("nan")}
    p = (1.0 + np.sum(np.abs(null_arr) >= abs(observed))) / (len(null_arr) + 1.0)
    return {"observed": observed, "null_mean": float(np.mean(null_arr)), "p_two_sided": float(p)}
