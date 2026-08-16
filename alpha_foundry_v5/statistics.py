from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from .splits import cscv_splits


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    a = pd.Series(x, dtype=float)
    b = pd.Series(y, dtype=float)
    valid = a.notna() & b.notna() & np.isfinite(a) & np.isfinite(b)
    if int(valid.sum()) < 3:
        return float("nan")
    return float(a[valid].rank(method="average").corr(b[valid].rank(method="average")))


def bh_qvalues(pvalues: Sequence[float]) -> np.ndarray:
    p = np.asarray(pvalues, dtype=float)
    out = np.full(len(p), np.nan, dtype=float)
    valid = np.flatnonzero(np.isfinite(p))
    if len(valid) == 0:
        return out
    pv = np.clip(p[valid], 0.0, 1.0)
    order = np.argsort(pv)
    ranked = pv[order]
    q = ranked * float(len(ranked)) / np.arange(1, len(ranked) + 1, dtype=float)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0.0, 1.0)
    restored = np.empty_like(q)
    restored[order] = q
    out[valid] = restored
    return out


def effective_sample_size(values: Sequence[float], max_lag: int = 500) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float(n)
    x = x - np.mean(x)
    var = float(np.dot(x, x))
    if var <= 1e-18:
        return 1.0
    rho_sum = 0.0
    for lag in range(1, min(int(max_lag), n - 1) + 1):
        a = x[:-lag]
        b = x[lag:]
        denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
        if denom <= 1e-18:
            break
        rho = float(np.dot(a, b) / denom)
        if rho <= 0:
            break
        rho_sum += rho
    return float(max(1.0, n / (1.0 + 2.0 * rho_sum)))


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0)))


def _inv_normal_cdf(p: float) -> float:
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return float("-inf")
        if p == 1.0:
            return float("inf")
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.383577518672690e2, -3.066479806614716e1, 2.506628277459239]
    b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1]
    c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0-p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


def sharpe_ratio(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd) if sd > 1e-18 else float("nan")


def probabilistic_sharpe_ratio(returns: Sequence[float], benchmark_sharpe: float = 0.0) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 3:
        return float("nan")
    sr = sharpe_ratio(r)
    if not np.isfinite(sr):
        return float("nan")
    centered = r - np.mean(r)
    sd = float(np.std(r, ddof=1))
    if sd <= 1e-18:
        return float("nan")
    skew = float(np.mean((centered / sd) ** 3))
    kurt = float(np.mean((centered / sd) ** 4))
    denom = max(1e-18, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr)
    z = (sr - float(benchmark_sharpe)) * math.sqrt(n - 1.0) / math.sqrt(denom)
    return float(_normal_cdf(z))


def deflated_sharpe_probability(returns: Sequence[float], trial_sharpes: Sequence[float]) -> float:
    trials = np.asarray(trial_sharpes, dtype=float)
    trials = trials[np.isfinite(trials)]
    if len(trials) <= 1:
        return probabilistic_sharpe_ratio(returns, 0.0)
    sigma = float(np.std(trials, ddof=1))
    if sigma <= 1e-18:
        benchmark = float(np.max(trials))
    else:
        n = float(len(trials))
        gamma = 0.5772156649015329
        benchmark = float(np.mean(trials)) + sigma * ((1.0 - gamma) * _inv_normal_cdf(1.0 - 1.0 / n) + gamma * _inv_normal_cdf(1.0 - 1.0 / (n * math.e)))
    return probabilistic_sharpe_ratio(returns, benchmark)


def cscv_pbo(strategy_returns: np.ndarray, n_blocks: int = 10) -> float:
    matrix = np.asarray(strategy_returns, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("strategy_returns must be [time, strategies] with >=2 strategies")
    logits = []
    for split in cscv_splits(matrix.shape[0], n_blocks=n_blocks):
        train = matrix[split.train_idx]
        test = matrix[split.test_idx]
        train_sr = np.asarray([sharpe_ratio(train[:, j]) for j in range(matrix.shape[1])])
        if not np.any(np.isfinite(train_sr)):
            continue
        best = int(np.nanargmax(train_sr))
        test_sr = np.asarray([sharpe_ratio(test[:, j]) for j in range(matrix.shape[1])])
        finite = np.isfinite(test_sr)
        if not finite[best] or int(finite.sum()) < 2:
            continue
        vals = test_sr[finite]
        selected = float(test_sr[best])
        rank = float(np.sum(vals <= selected)) / float(len(vals) + 1.0)
        rank = min(max(rank, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(rank / (1.0 - rank)))
    return float(np.mean(np.asarray(logits) <= 0.0)) if logits else float("nan")


def block_permutation_pvalue(x: Sequence[float], y: Sequence[float], block_size: int, repeats: int = 200, seed: int = 17) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    valid = np.isfinite(a) & np.isfinite(b)
    a = a[valid]
    b = b[valid]
    n = len(a)
    if n < 20:
        return float("nan")
    observed = abs(spearman(a, b))
    block_size = max(1, min(int(block_size), n))
    blocks = [np.arange(i, min(i + block_size, n)) for i in range(0, n, block_size)]
    rng = np.random.RandomState(int(seed))
    exceed = 0
    for _ in range(int(repeats)):
        order = rng.permutation(len(blocks))
        idx = np.concatenate([blocks[i] for i in order])
        score = abs(spearman(a, b[idx]))
        if np.isfinite(score) and score >= observed - 1e-15:
            exceed += 1
    return float((exceed + 1.0) / (int(repeats) + 1.0))
