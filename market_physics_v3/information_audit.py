from __future__ import annotations

from typing import Dict, Mapping

import numpy as np
import pandas as pd


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Return pandas-compatible average ranks for a finite/non-NaN vector."""
    values = np.asarray(values)
    n = int(values.size)
    if n == 0:
        return np.asarray([], dtype=float)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    starts = np.r_[0, np.flatnonzero(sorted_values[1:] != sorted_values[:-1]) + 1]
    stops = np.r_[starts[1:], n]
    averages = 0.5 * ((starts + 1).astype(float) + stops.astype(float))
    ranks_sorted = np.repeat(averages, stops - starts)
    ranks = np.empty(n, dtype=float)
    ranks[order] = ranks_sorted
    return ranks


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size < 3 or b.size != a.size:
        return float("nan")
    ac = a - float(np.mean(a))
    bc = b - float(np.mean(b))
    denom = float(np.sqrt(np.dot(ac, ac) * np.dot(bc, bc)))
    if not np.isfinite(denom) or denom <= 0.0:
        return float("nan")
    return float(np.dot(ac, bc) / denom)


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    """Exact pairwise Spearman IC with substantially lower pandas overhead."""
    xs = pd.Series(x)
    ys = pd.Series(y)
    v = xs.notna() & ys.notna()
    if int(v.sum()) < 3:
        return float("nan")
    xa = xs[v].to_numpy()
    ya = ys[v].to_numpy()
    return _pearson(_average_ranks(xa), _average_ranks(ya))


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


def _lagged_corr_from_sums(
    cross_sum: float,
    left_sum: float,
    right_sum: float,
    left_sq_sum: float,
    right_sq_sum: float,
    count: int,
) -> float:
    if count < 2:
        return float("nan")
    n = float(count)
    cov = float(cross_sum) - float(left_sum) * float(right_sum) / n
    var_left = float(left_sq_sum) - float(left_sum) * float(left_sum) / n
    var_right = float(right_sq_sum) - float(right_sum) * float(right_sum) / n
    if var_left <= 0.0 or var_right <= 0.0:
        return float("nan")
    return float(cov / np.sqrt(var_left * var_right))


def effective_sample_size(x: pd.Series, max_lag: int = 100) -> float:
    """Same positive-autocorrelation ESS rule as before, using FFT cross-products.

    The previous implementation called pandas autocorr separately for every lag.
    This version computes all lagged cross-products in one FFT, while prefix
    sums reproduce the per-lag Pearson centering used by Series.autocorr().
    NaNs are dropped first exactly as in the original implementation.
    """
    values = pd.Series(x).dropna().to_numpy(dtype=float)
    n = int(values.size)
    if n < 3:
        return float(n)
    limit = min(int(max_lag), n - 1)
    if limit <= 0:
        return float(n)

    fft_size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(values, n=fft_size)
    cross = np.fft.irfft(spectrum * np.conjugate(spectrum), n=fft_size)[: limit + 1]

    prefix = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    prefix_sq = np.concatenate(([0.0], np.cumsum(values * values, dtype=float)))
    acfs = []
    for lag in range(1, limit + 1):
        count = n - lag
        left_sum = prefix[count]
        right_sum = prefix[n] - prefix[lag]
        left_sq = prefix_sq[count]
        right_sq = prefix_sq[n] - prefix_sq[lag]
        rho = _lagged_corr_from_sums(
            cross[lag], left_sum, right_sum, left_sq, right_sq, count
        )
        if not np.isfinite(rho) or rho <= 0.0:
            break
        acfs.append(float(rho))
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
