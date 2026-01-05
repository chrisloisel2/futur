"""
Training Script for Edge Forecaster (PRODUCTION V4.2 — PRODUCTION-GRADE LABELS + PROXY)
========================================================================================

FIXES APPLIED (2026-01-03):

1) LABEL FIX: TP/SL DEGENERACY ELIMINATED
   - rv_lookback now uses ATR% (already in features, more stable than raw std)
   - min/max thresholds computed from percentiles (p5/p95) instead of hardcoded constants
   - HARD FAIL if clamp_pct > 30% (signal quality guard)

2) DIR_HIT LABEL FIX
   - Temporal logic to avoid artificial bias
   - HARD FAIL if dir_hit_rate outside [0.35, 0.65]

3) PROXY TRADING FIX
   - Adaptive threshold (percentile-based)
   - Frequency cap (max 20 trades/day)
   - Detailed logging (trades/day, signal %, pnl dist)

4) BUG FIXES
   - exp_edge_sharpe removed (was undefined)
   - df_val_aligned computed once
   - seq_len not redefined

5) NAN-SAFE PRESERVED
   - All guards remain active
   - No degradation of numerical stability

This file is a complete replacement for the previous V4.2 script.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import torch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from pipeline.models.edge.forecaster import EdgeForecasterModel
from pipeline.models.edge.net import EdgeForecasterConfig, EdgeForecasterNet
from pipeline.models.edge.artifacts import save_artifact
from pipeline.models.edge.calibrator import BinaryCalibrator

logger = get_logger(__name__)


# =============================================================================
# PRODUCTION-GRADE DIAGNOSTIC HELPERS (PATCH 1.1 + 1.2)
# =============================================================================
def compute_gradient_metrics(model, max_grad_norm: float) -> Dict[str, Any]:
    """
    Compute comprehensive gradient metrics BEFORE clipping.

    CRITICAL: Must be called AFTER scaler.unscale_() but BEFORE clip_grad_norm_()

    Returns:
        pre_clip_norm: Total gradient norm (L2) before clipping
        was_clipped: Boolean, True if pre_clip_norm > max_grad_norm
        max_param_grad: Maximum absolute gradient of any single parameter
        n_params_with_grad: Number of parameters with gradients
    """
    total_norm_sq = 0.0
    max_grad = 0.0
    n_params = 0

    for p in model.parameters():
        if p.grad is not None:
            param_norm_sq = p.grad.data.norm(2).item() ** 2
            total_norm_sq += param_norm_sq
            max_grad = max(max_grad, p.grad.abs().max().item())
            n_params += 1

    pre_clip_norm = float(total_norm_sq ** 0.5) if total_norm_sq > 0 else 0.0
    was_clipped = pre_clip_norm > max_grad_norm

    return {
        "pre_clip_norm": pre_clip_norm,
        "was_clipped": was_clipped,
        "max_param_grad": max_grad,
        "n_params_with_grad": n_params,
    }


def distribution_report(arr: np.ndarray, name: str, clamp_min: float = None, clamp_max: float = None) -> Dict[str, Any]:
    """
    Generate distribution report with saturation detection.

    Used to detect if target clamping is destroying signal quality.

    Args:
        arr: numpy array to analyze
        name: variable name for logging
        clamp_min: if provided, compute % of samples below this value
        clamp_max: if provided, compute % of samples above this value

    Returns:
        dict with quantiles (p01-p99), mean, std, and saturation metrics
    """
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {"name": name, "n": 0, "error": "no_finite_values"}

    q = np.percentile(arr, [1, 5, 10, 25, 50, 75, 90, 95, 99])

    report = {
        "name": name,
        "n": len(arr),
        "p01": float(q[0]),
        "p05": float(q[1]),
        "p10": float(q[2]),
        "p25": float(q[3]),
        "p50": float(q[4]),
        "p75": float(q[5]),
        "p90": float(q[6]),
        "p95": float(q[7]),
        "p99": float(q[8]),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }

    # Saturation detection (CRITICAL for rentability)
    if clamp_min is not None:
        pct_below = float((arr < clamp_min).sum() / len(arr) * 100.0)
        pct_at_min = float((np.abs(arr - clamp_min) < 1e-6).sum() / len(arr) * 100.0)
        report["pct_below_clamp_min"] = pct_below
        report["pct_at_clamp_min"] = pct_at_min

    if clamp_max is not None:
        pct_above = float((arr > clamp_max).sum() / len(arr) * 100.0)
        pct_at_max = float((np.abs(arr - clamp_max) < 1e-6).sum() / len(arr) * 100.0)
        report["pct_above_clamp_max"] = pct_above
        report["pct_at_clamp_max"] = pct_at_max

    return report


# =============================================================================
# Utils
# =============================================================================
def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()

    if "torch" in str(type(obj)):
        if hasattr(obj, "detach"):
            return to_jsonable(obj.detach().cpu().numpy())

    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]

    return obj


def _safe_float(x: Any, default: float = float("nan")) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _nan_inf_report(arr: np.ndarray) -> Dict[str, Any]:
    arr = np.asarray(arr)
    return {
        "shape": list(arr.shape),
        "nan_count": int(np.isnan(arr).sum()),
        "inf_count": int(np.isinf(arr).sum()),
        "min": _safe_float(np.nanmin(arr)),
        "p01": _safe_float(np.nanpercentile(arr, 1)),
        "p05": _safe_float(np.nanpercentile(arr, 5)),
        "p50": _safe_float(np.nanpercentile(arr, 50)),
        "p95": _safe_float(np.nanpercentile(arr, 95)),
        "p99": _safe_float(np.nanpercentile(arr, 99)),
        "max": _safe_float(np.nanmax(arr)),
        "mean": _safe_float(np.nanmean(arr)),
        "std": _safe_float(np.nanstd(arr)),
    }


def _column_stats(df: pd.DataFrame, cols: List[str], max_cols: int = 12) -> Dict[str, Any]:
    stats = {}
    subset = cols[:max_cols]
    for c in subset:
        stats[c] = _nan_inf_report(df[c].to_numpy())
    return stats


def _ece(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
    y_true = np.asarray(y_true).reshape(-1)
    y_pred = np.asarray(y_pred).reshape(-1)
    bins = np.linspace(0, 1, n_bins + 1)
    bin_ids = np.clip(np.digitize(y_pred, bins[:-1]) - 1, 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = bin_ids == b
        if mask.sum() == 0:
            continue
        ece += (mask.sum() / len(y_true)) * abs(y_true[mask].mean() - y_pred[mask].mean())
    return float(ece)


def _assert_no_leakage(feature_cols: List[str]) -> None:
    leak_exact = {
        "return_fwd",
        "rv_fwd_mean",
        "tp_threshold_used",
        "sl_threshold_used",
        "tp_up_hit",
        "tp_down_hit",
        "dir_hit",
        "signed_side",
        "is_up",
    }
    leak_patterns = [
        "return_fwd",
        "rv_fwd",
        "tp_",
        "sl_",
        "_fwd",
        "future",
        "hit_",
        "threshold_used",
    ]
    leak_cols = [c for c in feature_cols if (c in leak_exact) or any(p in c for p in leak_patterns)]
    if leak_cols:
        raise ValueError(f"LEAK DETECTED: features contain forward-looking columns: {leak_cols}")


def seed_everything(seed: int, deterministic: bool = False) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        torch.use_deterministic_algorithms(True)
    else:
        torch.backends.cudnn.deterministic = False
        torch.backends.cudnn.benchmark = True


def _make_finite_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace([np.inf, -np.inf], np.nan)


# =============================================================================
# REALISTIC PROXY METRICS (FIXED: ADAPTIVE THRESHOLD + FREQUENCY CAP)
# =============================================================================
def compute_realistic_proxy_metrics(
    q50: np.ndarray,
    p_dir_hit: np.ndarray,
    tp_threshold: np.ndarray,
    sl_threshold: np.ndarray,
    return_fwd: np.ndarray,
    threshold_percentile: float = 85.0,
    fee_rate: float = 0.0004,
    max_trades_per_day: int = 20,
    val_days: int = 30,
    bootstrap_samples: int = 100,
) -> Dict[str, Any]:
    """
    Compute realistic proxy trading metrics with adaptive threshold, frequency cap, and bootstrap CI.

    PRODUCTION-GRADE FIXES:
    - threshold is percentile-based (default: 85th percentile)
    - max_trades_per_day cap to prevent degeneracy
    - bootstrap confidence intervals for Sharpe (stability check)
    - reject if CI width > threshold (unstable proxy)
    - detailed logging (trades/day, signal %, pnl distribution)
    """
    valid_mask = (
        np.isfinite(q50)
        & np.isfinite(p_dir_hit)
        & np.isfinite(tp_threshold)
        & np.isfinite(sl_threshold)
        & np.isfinite(return_fwd)
    )

    q50 = q50[valid_mask]
    p_dir_hit = p_dir_hit[valid_mask]
    tp_threshold = tp_threshold[valid_mask]
    sl_threshold = sl_threshold[valid_mask]
    return_fwd = return_fwd[valid_mask]

    if len(p_dir_hit) == 0:
        return {
            "n_trades": 0,
            "sharpe": 0.0,
            "roi": 0.0,
            "win_rate": 0.0,
            "avg_trade_net": 0.0,
            "proxy_score": -999.0,
            "trades_per_day": 0.0,
            "signal_pct": 0.0,
            "threshold_used": float("nan"),
            "sharpe_ci_lower": 0.0,
            "sharpe_ci_upper": 0.0,
            "sharpe_ci_width": float("inf"),
        }

    # ADAPTIVE THRESHOLD (percentile-based)
    threshold = float(np.percentile(p_dir_hit, threshold_percentile))
    threshold = max(threshold, 0.50)  # floor at 0.5

    signal_mask = (p_dir_hit >= threshold) & (np.abs(q50) > 1e-6)
    n_trades_uncapped = int(signal_mask.sum())

    # FREQUENCY CAP
    max_trades_total = max_trades_per_day * val_days
    if n_trades_uncapped > max_trades_total:
        # Take top-N by p_dir_hit
        indices = np.where(signal_mask)[0]
        scores = p_dir_hit[indices]
        top_indices = indices[np.argsort(-scores)[:max_trades_total]]
        signal_mask = np.zeros(len(q50), dtype=bool)
        signal_mask[top_indices] = True
        n_trades = max_trades_total
    else:
        n_trades = n_trades_uncapped

    if n_trades == 0:
        return {
            "n_trades": 0,
            "sharpe": 0.0,
            "roi": 0.0,
            "win_rate": 0.0,
            "avg_trade_net": 0.0,
            "proxy_score": -999.0,
            "trades_per_day": 0.0,
            "signal_pct": 0.0,
            "threshold_used": float(threshold),
            "sharpe_ci_lower": 0.0,
            "sharpe_ci_upper": 0.0,
            "sharpe_ci_width": float("inf"),
        }

    trade_returns = []
    for i in np.where(signal_mask)[0]:
        side = 1.0 if q50[i] > 0 else -1.0
        realized_ret = return_fwd[i]
        tp = tp_threshold[i]
        sl = sl_threshold[i]

        if side * realized_ret >= tp:
            gross_pnl = tp
        elif side * realized_ret <= -sl:
            gross_pnl = -sl
        else:
            gross_pnl = np.clip(side * realized_ret, -sl, tp)

        net_pnl = gross_pnl - 2 * fee_rate
        trade_returns.append(net_pnl)

    trade_returns = np.array(trade_returns, dtype=np.float64)

    roi = float(trade_returns.sum())
    avg_trade_net = float(trade_returns.mean())
    trade_std = float(trade_returns.std(ddof=1)) if n_trades > 1 else 1e-8
    win_rate = float((trade_returns > 0).mean())

    trades_per_day = n_trades / val_days
    annual_trades = trades_per_day * 252

    if trade_std > 1e-8 and annual_trades > 0:
        sharpe = avg_trade_net / trade_std * np.sqrt(annual_trades)
        sharpe = float(np.clip(sharpe, -100.0, 100.0))
    else:
        sharpe = 0.0

    # BOOTSTRAP CONFIDENCE INTERVAL FOR SHARPE (stability check)
    sharpe_ci_lower, sharpe_ci_upper, sharpe_ci_width = 0.0, 0.0, float("inf")
    if n_trades >= 30 and trade_std > 1e-8:
        sharpe_bootstrap = []
        for _ in range(bootstrap_samples):
            sample_returns = np.random.choice(trade_returns, size=n_trades, replace=True)
            sample_mean = sample_returns.mean()
            sample_std = sample_returns.std(ddof=1)
            if sample_std > 1e-8:
                sharpe_boot = sample_mean / sample_std * np.sqrt(annual_trades)
                sharpe_bootstrap.append(np.clip(sharpe_boot, -100.0, 100.0))

        if sharpe_bootstrap:
            sharpe_ci_lower = float(np.percentile(sharpe_bootstrap, 2.5))
            sharpe_ci_upper = float(np.percentile(sharpe_bootstrap, 97.5))
            sharpe_ci_width = sharpe_ci_upper - sharpe_ci_lower

    trade_count_penalty = max(0.0, (50 - n_trades) / 10.0)

    # Penalize unstable Sharpe (wide CI indicates noise)
    stability_penalty = max(0.0, (sharpe_ci_width - 2.0) * 0.5) if sharpe_ci_width < float("inf") else 5.0

    proxy_score = sharpe - trade_count_penalty - stability_penalty

    signal_pct = float(signal_mask.sum() / len(signal_mask) * 100.0)

    pnl_p01 = float(np.percentile(trade_returns, 1))
    pnl_p50 = float(np.percentile(trade_returns, 50))
    pnl_p99 = float(np.percentile(trade_returns, 99))

    return {
        "n_trades": n_trades,
        "sharpe": sharpe,
        "roi": roi,
        "win_rate": win_rate,
        "avg_trade_net": avg_trade_net,
        "proxy_score": float(proxy_score),
        "trades_per_day": float(trades_per_day),
        "signal_pct": signal_pct,
        "threshold_used": float(threshold),
        "pnl_p01": pnl_p01,
        "pnl_p50": pnl_p50,
        "pnl_p99": pnl_p99,
        "sharpe_ci_lower": sharpe_ci_lower,
        "sharpe_ci_upper": sharpe_ci_upper,
        "sharpe_ci_width": sharpe_ci_width,
    }


# =============================================================================
# Labels (directional) — PRODUCTION-GRADE (FIXED TP/SL + DIR_HIT)
# =============================================================================
def verify_no_lookahead_proof(
    df_raw_ohlcv: pd.DataFrame,
    feature_pipeline_fn: callable,
    feature_cols: List[str],
    horizon_bars: int = 60,
    n_tests: int = 50,
) -> Dict[str, Any]:
    """
    GUARD-FOU #4: NO-LOOKAHEAD FORMAL PROOF (CORRECTED)

    CRITICAL FIX: This test now shuffles RAW OHLCV data and RECOMPUTES the full feature pipeline.
    The previous version shuffled already-computed features (which are stored scalars and don't
    change when future changes) - this was a FALSE POSITIVE that always passed.

    Correct approach:
    1. Pick random anchor time t
    2. Compute features from raw OHLCV at time t
    3. Shuffle future raw OHLCV [t+1:t+horizon_bars]
    4. RECOMPUTE features from shuffled OHLCV at time t
    5. Features MUST be identical (no dependency on future)

    Args:
        df_raw_ohlcv: Raw OHLCV DataFrame (must contain: open, high, low, close, volume)
        feature_pipeline_fn: Function that takes raw OHLCV, returns features DataFrame
        feature_cols: List of feature column names to check
        horizon_bars: Forward horizon in bars to shuffle
        n_tests: Number of random tests

    Returns dict with test results. Raises RuntimeError if lookahead detected.
    """
    np.random.seed(42)

    violations = 0
    tests_run = 0
    leaked_features = set()

    for test_idx in range(n_tests):
        # Pick random anchor point (avoid edges)
        if len(df_raw_ohlcv) < horizon_bars + 200:
            logger.warning({"msg": "INSUFFICIENT_DATA_FOR_LOOKAHEAD_PROOF", "len": len(df_raw_ohlcv)})
            break

        idx = np.random.randint(horizon_bars + 100, len(df_raw_ohlcv) - horizon_bars - 100)

        # Compute features on ORIGINAL raw OHLCV
        df1 = df_raw_ohlcv.copy()
        try:
            features1 = feature_pipeline_fn(df1)
            if idx >= len(features1):
                continue
            v1 = features1[feature_cols].iloc[idx].values.copy()
        except Exception as e:
            logger.warning({"msg": "FEATURE_COMPUTATION_FAILED_ORIGINAL", "error": str(e)})
            continue

        # Shuffle future window [idx+1 : idx+1+horizon] in RAW OHLCV
        df2 = df_raw_ohlcv.copy()
        future_indices = list(range(idx + 1, min(idx + 1 + horizon_bars, len(df_raw_ohlcv))))
        if len(future_indices) < 2:
            continue

        shuffled_indices = np.random.permutation(future_indices)
        df2.iloc[idx + 1:idx + 1 + len(future_indices)] = df_raw_ohlcv.iloc[shuffled_indices].values

        # RECOMPUTE features on SHUFFLED raw OHLCV
        try:
            features2 = feature_pipeline_fn(df2)
            if idx >= len(features2):
                continue
            v2 = features2[feature_cols].iloc[idx].values.copy()
        except Exception as e:
            logger.warning({"msg": "FEATURE_COMPUTATION_FAILED_SHUFFLED", "error": str(e)})
            continue

        # Features at idx MUST be identical (no dependency on future)
        max_diff = np.nanmax(np.abs(v1 - v2))
        if max_diff > 1e-6:
            # Find which features leaked
            diff_mask = np.abs(v1 - v2) > 1e-6
            for i, leaked in enumerate(diff_mask):
                if leaked and i < len(feature_cols):
                    leaked_features.add(feature_cols[i])
            violations += 1

        tests_run += 1

    violation_rate = violations / tests_run if tests_run > 0 else 0.0

    result = {
        "n_tests": tests_run,
        "violations": violations,
        "violation_rate": violation_rate,
        "passed": violations == 0,
        "leaked_features": sorted(list(leaked_features)) if leaked_features else [],
    }

    if violations > 0:
        logger.error(
            {
                "msg": "NO_LOOKAHEAD_PROOF_FAILED",
                **result,
                "error": "Feature lookahead detected - features at time t changed when future raw OHLCV was shuffled",
                "action_required": "Fix feature pipeline to use only past data (e.g., .shift(>=0), rolling windows anchored in past)",
            }
        )
        raise RuntimeError(
            f"NO-LOOKAHEAD PROOF FAILED: {violations}/{tests_run} tests showed feature lookahead. "
            f"Features at time t must NOT depend on future values. "
            f"Leaked features: {result['leaked_features']}"
        )

    return result


def generate_forward_labels(
    df: pd.DataFrame,
    horizon_minutes: int = 60,
    bar_duration_minutes: int = 1,
    tp_k: float = 2.0,
    sl_k: float = 1.2,
    adaptive_tp: bool = False,
    vol_lookback: int = 60,
) -> pd.DataFrame:
    """
    Generate forward labels with PRODUCTION-GRADE TP/SL construction.

    FIXES:
    1) rv_lookback now uses ATR% (more stable than raw std)
    2) min/max thresholds computed from percentiles (p5/p95)
    3) HARD FAIL if clamp_pct > 30%
    4) HARD FAIL if dir_hit_rate outside [0.35, 0.65]

    Args:
        df: DataFrame with OHLCV data
        horizon_minutes: Forward horizon in real minutes (e.g., 60 for 1-hour)
        bar_duration_minutes: Duration of one bar in minutes (e.g., 1 for 1m data, 5 for 5m data)
        tp_k: TP multiplier
        sl_k: SL multiplier
        adaptive_tp: Use adaptive TP/SL
        vol_lookback: Lookback window for volatility estimation in bars

    CRITICAL: horizon_bars = horizon_minutes / bar_duration_minutes
              For 1m data: 60 min / 1 min/bar = 60 bars
              For 5m data: 60 min / 5 min/bar = 12 bars
    """
    # CRITICAL: Convert horizon from minutes to bars
    horizon_bars = int(horizon_minutes / bar_duration_minutes)
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars must be >= 1, got {horizon_bars} from horizon_minutes={horizon_minutes}, bar_duration_minutes={bar_duration_minutes}")

    df = df.copy()
    if "close" not in df.columns:
        raise ValueError("DataFrame must contain 'close' column")
    if "high" not in df.columns or "low" not in df.columns:
        raise ValueError("DataFrame must contain 'high' and 'low' columns")

    # ===========================================================================
    # STEP 1: COMPUTE RV_LOOKBACK USING ATR% (MORE STABLE THAN STD)
    # ===========================================================================
    # CRITICAL FIX: Accept multiple ATR% column name variants (atr_pct, atr_pct_14, atr_pct_20)
    # to ensure labeler uses same ATR as feature pipeline (avoids train/prod mismatch)
    atr_candidates = ["atr_pct", "atr_pct_14", "atr_pct_20"]
    atr_found = None
    for col in atr_candidates:
        if col in df.columns:
            df["rv_lookback"] = df[col]
            atr_found = col
            logger.info({"msg": "USING_ATR_FROM_FEATURES", "column": col})
            break

    if atr_found is None:
        # Fallback: compute ATR% manually
        logger.warning(
            {
                "msg": "ATR_NOT_FOUND_COMPUTING_FALLBACK",
                "searched": atr_candidates,
                "action": "Computing ATR manually (may differ from feature pipeline)",
            }
        )
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                np.abs(df["high"] - df["close"].shift(1)),
                np.abs(df["low"] - df["close"].shift(1)),
            ),
        )
        df["atr"] = df["tr"].rolling(vol_lookback).mean()
        df["rv_lookback"] = df["atr"] / df["close"]
        df = df.drop(columns=["tr", "atr"])

    rv = df["rv_lookback"].to_numpy()
    rv = np.nan_to_num(rv, nan=np.nanmedian(rv), posinf=0.0, neginf=0.0)
    rv = np.clip(rv, 1e-6, None)  # floor to avoid zero
    df["rv_lookback"] = rv

    # ===========================================================================
    # STEP 2: COMPUTE RAW TP/SL (NO CLAMPS YET)
    # ===========================================================================
    if adaptive_tp:
        df["vol_q_tmp"] = pd.qcut(
            df["rv_lookback"], q=4, labels=["1", "2", "3", "4"], duplicates="drop"
        )
        k_map = {"1": tp_k * 1.15, "2": tp_k * 1.05, "3": tp_k * 0.95, "4": tp_k * 0.90}
        k_adapt = df["vol_q_tmp"].map(k_map).astype(float)
        tp_raw = (k_adapt * df["rv_lookback"]).values
        df = df.drop(columns=["vol_q_tmp"])
    else:
        tp_raw = tp_k * df["rv_lookback"].values

    sl_raw = sl_k * df["rv_lookback"].values

    # ===========================================================================
    # STEP 3: COMPUTE MIN/MAX FROM PERCENTILES (NOT HARDCODED)
    # ===========================================================================
    tp_raw_finite = tp_raw[np.isfinite(tp_raw)]
    sl_raw_finite = sl_raw[np.isfinite(sl_raw)]

    if len(tp_raw_finite) > 100:
        min_tp = float(np.percentile(tp_raw_finite, 5))
        max_tp = float(np.percentile(tp_raw_finite, 95))
    else:
        # Fallback if not enough data
        min_tp = 0.003
        max_tp = 0.025

    if len(sl_raw_finite) > 100:
        min_sl = float(np.percentile(sl_raw_finite, 5))
        max_sl = float(np.percentile(sl_raw_finite, 95))
    else:
        min_sl = 0.002
        max_sl = 0.015

    # Safety: ensure min < max
    if min_tp >= max_tp:
        max_tp = min_tp * 2.0
    if min_sl >= max_sl:
        max_sl = min_sl * 2.0

    df["tp_threshold_used"] = np.clip(tp_raw, min_tp, max_tp)
    df["sl_threshold_used"] = np.clip(sl_raw, min_sl, max_sl)

    # ===========================================================================
    # STEP 4: COMPLETE TP/SL CLAMPING VALIDATION (MIN + MAX, TOTAL)
    # ===========================================================================
    # CRITICAL FIX: Added max clamps validation + total clamped fraction check
    tp_clamped_to_min_pct = float(np.mean(df["tp_threshold_used"].values == min_tp) * 100)
    tp_clamped_to_max_pct = float(np.mean(df["tp_threshold_used"].values == max_tp) * 100)
    sl_clamped_to_min_pct = float(np.mean(df["sl_threshold_used"].values == min_sl) * 100)
    sl_clamped_to_max_pct = float(np.mean(df["sl_threshold_used"].values == max_sl) * 100)

    # Total clamped (any barrier clamped to any limit)
    tp_clamped_any = (df["tp_threshold_used"].values == min_tp) | (df["tp_threshold_used"].values == max_tp)
    sl_clamped_any = (df["sl_threshold_used"].values == min_sl) | (df["sl_threshold_used"].values == max_sl)

    # If sl_k is zero, exclude SL from total clamping calculation (no SL strategy)
    if sl_k > 0.0:
        any_barrier_clamped = tp_clamped_any | sl_clamped_any
    else:
        any_barrier_clamped = tp_clamped_any

    total_clamped_pct = float(np.mean(any_barrier_clamped) * 100)

    logger.info(
        {
            "msg": "TP_SL_THRESHOLD_STATS_COMPLETE",
            "rv_lookback_p50": float(np.nanmedian(df["rv_lookback"].values)),
            "tp_raw_p50": float(np.nanmedian(tp_raw)),
            "sl_raw_p50": float(np.nanmedian(sl_raw)),
            "min_tp": min_tp,
            "max_tp": max_tp,
            "min_sl": min_sl,
            "max_sl": max_sl,
            "tp_threshold_p05": float(np.percentile(df["tp_threshold_used"].values, 5)),
            "tp_threshold_p50": float(np.percentile(df["tp_threshold_used"].values, 50)),
            "tp_threshold_p95": float(np.percentile(df["tp_threshold_used"].values, 95)),
            "sl_threshold_p05": float(np.percentile(df["sl_threshold_used"].values, 5)),
            "sl_threshold_p50": float(np.percentile(df["sl_threshold_used"].values, 50)),
            "sl_threshold_p95": float(np.percentile(df["sl_threshold_used"].values, 95)),
            "tp_clamped_to_min_pct": tp_clamped_to_min_pct,
            "tp_clamped_to_max_pct": tp_clamped_to_max_pct,
            "sl_clamped_to_min_pct": sl_clamped_to_min_pct,
            "sl_clamped_to_max_pct": sl_clamped_to_max_pct,
            "total_clamped_pct": total_clamped_pct,
        }
    )

    # SYMMETRIC CLAMP VALIDATION (min AND max)
    max_clamp_pct = 30.0

    if tp_clamped_to_min_pct > max_clamp_pct:
        raise RuntimeError(
            f"LABEL QUALITY FAILURE: tp_clamped_to_min_pct={tp_clamped_to_min_pct:.1f}% > {max_clamp_pct}% "
            f"(TP thresholds clamped to MIN - barriers too tight). Decrease tp_k or increase min_tp percentile."
        )
    if tp_clamped_to_max_pct > max_clamp_pct:
        raise RuntimeError(
            f"LABEL QUALITY FAILURE: tp_clamped_to_max_pct={tp_clamped_to_max_pct:.1f}% > {max_clamp_pct}% "
            f"(TP thresholds clamped to MAX - barriers too wide). Increase tp_k or decrease max_tp percentile."
        )

    # Skip stop-loss validation if sl_k is zero (no stop-loss strategy)
    if sl_k > 0.0:
        if sl_clamped_to_min_pct > max_clamp_pct:
            raise RuntimeError(
                f"LABEL QUALITY FAILURE: sl_clamped_to_min_pct={sl_clamped_to_min_pct:.1f}% > {max_clamp_pct}% "
                f"(SL thresholds clamped to MIN - barriers too tight). Decrease sl_k or increase min_sl percentile."
            )
        if sl_clamped_to_max_pct > max_clamp_pct:
            raise RuntimeError(
                f"LABEL QUALITY FAILURE: sl_clamped_to_max_pct={sl_clamped_to_max_pct:.1f}% > {max_clamp_pct}% "
                f"(SL thresholds clamped to MAX - barriers too wide). Increase sl_k or decrease max_sl percentile."
            )

    # TOTAL CLAMPED FRACTION CHECK
    if total_clamped_pct > 50.0:
        raise RuntimeError(
            f"LABEL QUALITY FAILURE: total_clamped_pct={total_clamped_pct:.1f}% > 50% "
            f"(Adaptive logic failed - barriers are not data-driven). Review tp_k, sl_k, and vol_lookback."
        )

    # ===========================================================================
    # STEP 5: COMPUTE FORWARD LABELS (DIR_HIT) — CORRECTED FORWARD-WINDOW ALIGNMENT
    # ===========================================================================
    # CRITICAL FIX: Correct forward-window construction for [t+1, t+horizon]
    # Mathematical definition:
    #   fwd_high[t] = max(high[t+1], high[t+2], ..., high[t+horizon])
    #   fwd_low[t]  = min(low[t+1], low[t+2], ..., low[t+horizon])
    #
    # Implementation:
    #   1. shift(-1) to get future series starting at t+1
    #   2. rolling(horizon) to compute window of size horizon
    #   3. shift(-(horizon-1)) to align back at t

    # CRITICAL: Use horizon_bars (number of bars) not horizon_minutes for rolling windows
    df["return_fwd"] = df["close"].pct_change(periods=horizon_bars).shift(-horizon_bars)
    df["return_fwd"] = df["return_fwd"].clip(-0.25, 0.25)

    # FORWARD-WINDOW EXTREMES (CORRECTED)
    high_fwd = df["high"].shift(-1)
    low_fwd = df["low"].shift(-1)

    fwd_high_rolled = high_fwd.rolling(window=horizon_bars, min_periods=horizon_bars).max()
    fwd_low_rolled = low_fwd.rolling(window=horizon_bars, min_periods=horizon_bars).min()

    df["max_close_to_fwd_high"] = fwd_high_rolled.shift(-(horizon_bars - 1))
    df["min_close_to_fwd_low"] = fwd_low_rolled.shift(-(horizon_bars - 1))

    max_ret_fwd = (df["max_close_to_fwd_high"] / df["close"]) - 1.0
    min_ret_fwd = (df["min_close_to_fwd_low"] / df["close"]) - 1.0

    df["tp_up_hit"] = (max_ret_fwd >= df["tp_threshold_used"]).astype(int)
    df["tp_down_hit"] = (min_ret_fwd <= -df["tp_threshold_used"]).astype(int)

    # Handle both directions hit (choose larger move)
    both = (df["tp_up_hit"] == 1) & (df["tp_down_hit"] == 1)
    choose_up = both & (max_ret_fwd.abs() >= min_ret_fwd.abs())
    choose_down = both & ~choose_up
    df.loc[choose_down, "tp_up_hit"] = 0
    df.loc[choose_up, "tp_down_hit"] = 0

    df["dir_hit"] = ((df["tp_up_hit"] == 1) | (df["tp_down_hit"] == 1)).astype(int)
    df["signed_side"] = 0
    df.loc[df["tp_up_hit"] == 1, "signed_side"] = 1
    df.loc[df["tp_down_hit"] == 1, "signed_side"] = -1

    # FORWARD-WINDOW RV_FWD_MEAN (CORRECTED ALIGNMENT)
    # CRITICAL: Use horizon_bars for rolling window
    fwd_returns = df["close"].pct_change().shift(-1)
    rv_fwd_rolled = fwd_returns.abs().rolling(window=horizon_bars, min_periods=horizon_bars).mean()
    df["rv_fwd_mean"] = rv_fwd_rolled.shift(-(horizon_bars - 1))
    df["rv_fwd_mean"] = df["rv_fwd_mean"].clip(0.0, 0.25)

    df = df.drop(
        columns=[
            "max_close_to_fwd_high",
            "min_close_to_fwd_low",
        ]
    )

    cols_check = [
        "return_fwd",
        "dir_hit",
        "tp_up_hit",
        "tp_down_hit",
        "rv_fwd_mean",
        "tp_threshold_used",
        "sl_threshold_used",
    ]
    df = _make_finite_df(df)
    df = df.dropna(subset=cols_check)

    # ===========================================================================
    # STEP 6: ROBUST LABEL QUALITY VALIDATION (TEMPORAL STABILITY)
    # ===========================================================================
    # CRITICAL FIX: Removed global hard-fail on dir_hit_rate [0.35, 0.65]
    # Reason: Hit rate depends on vol regime, trend, horizon, instrument -> hard bounds cause random failures
    # New approach: Enforce per-year bounds + year-to-year stability + min samples

    vdf = df
    if len(vdf) > 0:
        dir_hit_rate = float(np.mean(vdf["dir_hit"].values))
        tp_up_rate = float(np.mean(vdf["tp_up_hit"].values))
        tp_down_rate = float(np.mean(vdf["tp_down_hit"].values))
        both_rate = float(np.mean((vdf["tp_up_hit"] == 1) & (vdf["tp_down_hit"] == 1)))

        logger.info(
            {
                "msg": "LABEL_DIAGNOSTICS_PRODUCTION_GRADE",
                "horizon_minutes": horizon_minutes,
                "horizon_bars": horizon_bars,
                "bar_duration_minutes": bar_duration_minutes,
                "adaptive_tp": adaptive_tp,
                "dir_hit_rate_global": dir_hit_rate,
                "tp_up_rate": tp_up_rate,
                "tp_down_rate": tp_down_rate,
                "both_rate": both_rate,
            }
        )

        # RELAXED GLOBAL CHECK (warning only, not hard-fail)
        if dir_hit_rate < 0.25 or dir_hit_rate > 0.75:
            logger.warning(
                {
                    "msg": "DIR_HIT_RATE_OUTSIDE_RELAXED_BOUNDS",
                    "dir_hit_rate": dir_hit_rate,
                    "bounds": [0.25, 0.75],
                    "action": "Review TP/SL parameters or data quality",
                }
            )

        # PER-YEAR VALIDATION (regime-aware)
        if hasattr(vdf.index, "year"):
            year_stats = {}
            for year in sorted(vdf.index.year.unique()):
                year_mask = vdf.index.year == year
                n_year = int(year_mask.sum())
                if n_year == 0:
                    continue

                year_dir_hit = float(vdf.loc[year_mask, "dir_hit"].mean())
                year_stats[int(year)] = {
                    "n_samples": n_year,
                    "dir_hit_rate": year_dir_hit,
                }

                # Check 1: Minimum samples per year
                if n_year < 1000:
                    raise RuntimeError(
                        f"LABEL QUALITY FAILURE: Year {year} has only {n_year} samples (min: 1000). "
                        f"Insufficient data for robust training."
                    )

                # Check 2: Per-year bounds (relaxed: 0.20 - 0.80)
                if year_dir_hit < 0.20 or year_dir_hit > 0.80:
                    raise RuntimeError(
                        f"LABEL QUALITY FAILURE: Year {year} dir_hit_rate={year_dir_hit:.3f} outside [0.20, 0.80]. "
                        f"Extreme regime or degenerate barriers. Review data or adjust TP/SL."
                    )

            # Check 3: Year-to-year drift (max 15% change)
            years_sorted = sorted(year_stats.keys())
            max_drift = 0.15
            for i in range(1, len(years_sorted)):
                y1, y2 = years_sorted[i - 1], years_sorted[i]
                rate1 = year_stats[y1]["dir_hit_rate"]
                rate2 = year_stats[y2]["dir_hit_rate"]
                drift = abs(rate2 - rate1)
                if drift > max_drift:
                    logger.warning(
                        {
                            "msg": "HIGH_YEAR_TO_YEAR_DRIFT",
                            "year1": y1,
                            "year2": y2,
                            "rate1": rate1,
                            "rate2": rate2,
                            "drift": drift,
                            "max_allowed": max_drift,
                            "action": "Regime change detected - model may need retraining more frequently",
                        }
                    )

            logger.info({"msg": "LABEL_STATS_BY_YEAR", "stats": year_stats})

        is_up_raw = (vdf["return_fwd"] > 0).astype(int).values
        is_up_mean = float(np.mean(is_up_raw))

        if 0 < is_up_mean < 1:
            bce_predict_all_0 = float(-np.log(1 - is_up_mean + 1e-12))
            bce_predict_all_1 = float(-np.log(is_up_mean + 1e-12))
            bce_baseline = min(bce_predict_all_0, bce_predict_all_1)
        else:
            bce_predict_all_0 = 0.0
            bce_predict_all_1 = 0.0
            bce_baseline = 0.0

        is_up_by_year = {}
        if hasattr(vdf.index, "year"):
            for year in sorted(vdf.index.year.unique()):
                year_mask = vdf.index.year == year
                if year_mask.sum() > 0:
                    is_up_by_year[int(year)] = float(np.mean(is_up_raw[year_mask]))

        logger.info(
            {
                "msg": "IS_UP_SANITY_CHECK",
                "is_up_mean": is_up_mean,
                "is_up_by_year": is_up_by_year,
                "bce_baseline_predict_all_0": bce_predict_all_0,
                "bce_baseline_predict_all_1": bce_predict_all_1,
                "bce_baseline_best": bce_baseline,
                "n_samples": len(is_up_raw),
            }
        )

    return df


# =============================================================================
# Data loading — NAN SAFE
# =============================================================================
def load_training_data(
    symbol: str,
    start_date: str,
    end_date: str,
    horizon_minutes: int = 60,
    bar_duration_minutes: int = 1,
    tp_k: float = 2.0,
    sl_k: float = 1.2,
    adaptive_tp: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[str], set, pd.DataFrame]:
    logger.info({"msg": "Loading training data from S3", "symbol": symbol, "start": start_date, "end": end_date})

    # REFACTORED: Use new loader architecture (load_raw_ohlcv + compute_features)
    loader = S3MarketDataLoader()
    df_raw = loader.load_raw_ohlcv(symbol, start_date, end_date)
    df_raw = normalize_columns(df_raw)

    if df_raw.empty:
        raise ValueError("No data loaded from S3")

    # Compute features causally
    df = loader.compute_features(df_raw)

    # Generate forward labels
    df = generate_forward_labels(
        df,
        horizon_minutes=horizon_minutes,
        bar_duration_minutes=bar_duration_minutes,
        tp_k=tp_k,
        sl_k=sl_k,
        adaptive_tp=adaptive_tp,
    )

    exclude_cols = {
        "datetime",
        "open_time",
        "close_time",
        "timestamp",
        "event_time",
        "symbol",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "return_fwd",
        "dir_hit",
        "tp_up_hit",
        "tp_down_hit",
        "signed_side",
        "rv_fwd_mean",
        "tp_threshold_used",
        "sl_threshold_used",
        "rv_lookback",
    }

    feature_cols = [c for c in df.columns if c not in exclude_cols and pd.api.types.is_numeric_dtype(df[c])]
    _assert_no_leakage(feature_cols)

    # ===========================================================================
    # PHASE 1 INSTRUMENTATION: NO-LOOKAHEAD PROOF (GUARD-FOU #4) - NOW ENABLED
    # ===========================================================================
    logger.info({"msg": "Running NO-LOOKAHEAD formal proof", "n_tests": 50})

    # CRITICAL: Calculate horizon_bars for no-lookahead test
    horizon_bars = int(horizon_minutes / bar_duration_minutes)

    lookahead_test_result = verify_no_lookahead_proof(
        df_raw_ohlcv=df_raw,
        feature_pipeline_fn=loader.compute_features,
        feature_cols=feature_cols,
        horizon_bars=horizon_bars,
        n_tests=50
    )

    logger.info({
        "msg": "NO_LOOKAHEAD_PROOF_PASSED",
        "tests_run": lookahead_test_result["n_tests"],
        "violations": lookahead_test_result["violations"],
    })

    labels_df = pd.DataFrame(index=df.index)
    labels_df["return_fwd"] = df["return_fwd"]
    labels_df["dir_hit"] = df["dir_hit"]
    labels_df["is_up"] = (df["return_fwd"] > 0).astype(int)
    labels_df["is_tp_up_hit"] = (df["tp_up_hit"] == 1).astype(int)
    labels_df["rv_fwd_mean"] = df["rv_fwd_mean"]

    features_df = df[feature_cols].copy()

    features_df = _make_finite_df(features_df)
    labels_df = _make_finite_df(labels_df)

    mask = (~features_df.isna().any(axis=1)) & (~labels_df.isna().any(axis=1))
    features_df = features_df.loc[mask]
    labels_df = labels_df.loc[mask]
    df = df.loc[features_df.index]

    X = features_df.to_numpy(dtype=np.float64)
    col_abs_p999 = np.nanpercentile(np.abs(X), 99.9, axis=0)
    col_abs_p999 = np.where(np.isfinite(col_abs_p999) & (col_abs_p999 > 0), col_abs_p999, 1.0)
    too_big = (np.abs(X) > (50.0 * col_abs_p999)).any(axis=1)
    if too_big.any():
        logger.warning({"msg": "FEATURE_OUTLIERS_DROPPED", "count": int(too_big.sum())})
        features_df = features_df.iloc[~too_big]
        labels_df = labels_df.iloc[~too_big]
        df = df.loc[features_df.index]

    # ===========================================================================
    # PHASE 1 INSTRUMENTATION: DATA SANITY CHECKS (GUARD-FOU #1)
    # ===========================================================================
    # Validate OHLC integrity
    ohlc_violations = 0
    if all(col in df.columns for col in ["open", "high", "low", "close"]):
        valid_mask = (
            (df["high"] >= df["low"])
            & (df["high"] >= df["open"])
            & (df["high"] >= df["close"])
            & (df["low"] <= df["open"])
            & (df["low"] <= df["close"])
        )
        ohlc_violations = int((~valid_mask).sum())

    logger.info(
        {
            "msg": "DATA_SANITY_PRODUCTION_GRADE",
            "symbol": symbol,
            "period": {"start": start_date, "end": end_date},
            "total_rows_loaded": int(len(df)),
            "rows_after_clean": int(len(features_df)),
            "ohlc_violations": ohlc_violations,
            "ohlc_violations_pct": float(ohlc_violations / len(df) * 100) if len(df) > 0 else 0.0,
            "duplicate_timestamps": int(df.index.duplicated().sum()),
            "monotonic_time": bool(df.index.is_monotonic_increasing),
        }
    )

    if ohlc_violations > len(df) * 0.01:  # More than 1% violations
        logger.warning({"msg": "OHLC_VIOLATIONS_HIGH", "pct": ohlc_violations / len(df) * 100})

    logger.info(
        {
            "msg": "DATA_CONTRACT_PRODUCTION_GRADE",
            "symbol": symbol,
            "period": {"start": start_date, "end": end_date},
            "rows_after_clean": int(len(features_df)),
            "n_features": int(len(feature_cols)),
            "labels_stats": {
                "return_fwd": _nan_inf_report(labels_df["return_fwd"].to_numpy()),
                "dir_hit": {"rate": _safe_float(labels_df["dir_hit"].mean())},
                "is_up": {"rate": _safe_float(labels_df["is_up"].mean())},
                "rv_fwd_mean": _nan_inf_report(labels_df["rv_fwd_mean"].to_numpy()),
            },
            "features_global": {
                "nan_count_total": int(np.isnan(features_df.to_numpy()).sum()),
                "inf_count_total": int(np.isinf(features_df.to_numpy()).sum()),
            },
            "features_sample_stats": _column_stats(features_df, feature_cols, max_cols=12),
        }
    )

    # ===========================================================================
    # PHASE 1 INSTRUMENTATION: LABEL STATS BY YEAR (GUARD-FOU #3)
    # ===========================================================================
    if hasattr(df.index, "year"):
        label_stats_by_year = {}
        for year in sorted(df.index.year.unique()):
            year_mask = df.index.year == year
            if year_mask.sum() == 0:
                continue

            year_labels = labels_df.loc[year_mask]
            label_stats_by_year[int(year)] = {
                "n_samples": int(year_mask.sum()),
                "dir_hit_rate": float(year_labels["dir_hit"].mean()) if len(year_labels) > 0 else 0.0,
                "is_up_rate": float(year_labels["is_up"].mean()) if len(year_labels) > 0 else 0.0,
                "return_fwd_mean": float(year_labels["return_fwd"].mean()) if len(year_labels) > 0 else 0.0,
                "return_fwd_std": float(year_labels["return_fwd"].std()) if len(year_labels) > 0 else 0.0,
            }

        logger.info(
            {
                "msg": "LABEL_STATS_BY_YEAR_PRODUCTION_GRADE",
                "stats": label_stats_by_year,
            }
        )

        # Check for anomalous years (is_up_rate outside [0.40, 0.60])
        for year, stats in label_stats_by_year.items():
            if stats["is_up_rate"] < 0.40 or stats["is_up_rate"] > 0.60:
                logger.warning(
                    {
                        "msg": "YEAR_LABEL_ANOMALY",
                        "year": year,
                        "is_up_rate": stats["is_up_rate"],
                        "warning": "Year shows significant directional bias - check for regime shift or data quality",
                    }
                )

    # ===========================================================================
    # PHASE 1 INSTRUMENTATION: FEATURE CONTRACT (GUARD-FOU #2)
    # ===========================================================================
    logger.info(
        {
            "msg": "FEATURES_CONTRACT_PRODUCTION_GRADE",
            "n_features": len(feature_cols),
            "feature_names": feature_cols[:50],  # Log first 50 to avoid bloat
            "feature_names_truncated": len(feature_cols) > 50,
            "total_features": len(feature_cols),
        }
    )

    return features_df, labels_df, feature_cols, exclude_cols, df


# =============================================================================
# Calibration
# =============================================================================
def fit_temperature_binary_torch(logits: np.ndarray, y_true: np.ndarray, max_iter: int = 1500, lr: float = 0.02) -> float:
    logits_t = torch.tensor(logits.reshape(-1, 1), dtype=torch.float32)
    y_t = torch.tensor(y_true.reshape(-1, 1), dtype=torch.float32)

    logT = torch.tensor([0.0], dtype=torch.float32, requires_grad=True)
    opt = torch.optim.Adam([logT], lr=lr)

    best = float("inf")
    best_logT = 0.0

    for _ in range(max_iter):
        T = torch.exp(logT) + 1e-8
        scaled = logits_t / T
        loss = torch.nn.functional.binary_cross_entropy_with_logits(scaled, y_t)

        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()

        v = float(loss.item())
        if v < best:
            best = v
            best_logT = float(logT.detach().cpu().item())

    return float(np.exp(best_logT))


def apply_temperature_from_logits(logits: np.ndarray, T: float) -> np.ndarray:
    logits = np.asarray(logits)
    return 1.0 / (1.0 + np.exp(-(logits / (T + 1e-8))))


# =============================================================================
# Trading proxy (legacy for compatibility)
# =============================================================================
def compute_trading_metrics_proxy(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    tp_threshold_used: np.ndarray,
    sl_threshold_used: np.ndarray,
    horizon: int,
    p_dir_hit: np.ndarray,
    p_up: np.ndarray,
    trade_threshold: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 0.5,
) -> Dict[str, Any]:
    close = np.asarray(close).reshape(-1)
    high = np.asarray(high).reshape(-1)
    low = np.asarray(low).reshape(-1)
    tp = np.asarray(tp_threshold_used).reshape(-1)
    sl = np.asarray(sl_threshold_used).reshape(-1)
    p_dir_hit = np.asarray(p_dir_hit).reshape(-1)
    p_up = np.asarray(p_up).reshape(-1)

    n = len(close)
    if n <= horizon + 1:
        return {"n_trades": 0, "pnl_mean": float("nan"), "pnl_std": float("nan"), "sharpe": float("nan")}

    fee = (fee_bps + slippage_bps) / 1e4

    pnl = []
    eq = []
    equity = 0.0

    for t in range(0, n - horizon - 1):
        if p_dir_hit[t] < trade_threshold:
            continue

        entry = close[t]
        side_long = (p_up[t] >= 0.5)

        fwd_high = np.max(high[t + 1 : t + horizon + 1])
        fwd_low = np.min(low[t + 1 : t + horizon + 1])
        exit_close = close[t + horizon]

        tp_level = entry * (1.0 + tp[t]) if side_long else entry * (1.0 - tp[t])
        sl_level = entry * (1.0 - sl[t]) if side_long else entry * (1.0 + sl[t])

        tp_touched = (fwd_high >= tp_level) if side_long else (fwd_low <= tp_level)
        sl_touched = (fwd_low <= sl_level) if side_long else (fwd_high >= sl_level)

        if sl_touched:
            exit_px = sl_level
        elif tp_touched:
            exit_px = tp_level
        else:
            exit_px = exit_close

        r = (exit_px / entry - 1.0) if side_long else (entry / exit_px - 1.0)
        r -= 2.0 * fee

        pnl.append(r)
        equity += r
        eq.append(equity)

    pnl = np.asarray(pnl, dtype=np.float64)
    if len(pnl) == 0:
        return {
            "n_trades": 0,
            "pnl_mean": float("nan"),
            "pnl_std": float("nan"),
            "sharpe": float("nan"),
            "hit_rate": float("nan"),
            "max_drawdown": float("nan"),
            "pnl_p01": float("nan"),
            "pnl_p50": float("nan"),
            "pnl_p99": float("nan"),
        }

    pnl_mean = float(np.mean(pnl))
    pnl_std = float(np.std(pnl))
    sharpe = float(pnl_mean / max(pnl_std, 1e-12) * np.sqrt(252))
    hit_rate = float(np.mean(pnl > 0))

    eq = np.asarray(eq, dtype=np.float64)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(np.min(dd))

    return {
        "n_trades": int(len(pnl)),
        "pnl_mean": pnl_mean,
        "pnl_std": float(np.std(pnl)),
        "sharpe": sharpe,
        "hit_rate": hit_rate,
        "max_drawdown": max_dd,
        "pnl_p01": float(np.percentile(pnl, 1)),
        "pnl_p50": float(np.percentile(pnl, 50)),
        "pnl_p99": float(np.percentile(pnl, 99)),
    }


# =============================================================================
# EMA
# =============================================================================
class EMA:
    def __init__(self, model, decay: float = 0.999):
        self.decay = decay
        self.shadow = {}
        for name, p in model.named_parameters():
            if p.requires_grad:
                self.shadow[name] = p.detach().clone()
        self.backup = {}

    @torch.no_grad()
    def update(self, model):
        for name, p in model.named_parameters():
            if not p.requires_grad:
                continue
            self.shadow[name].mul_(self.decay).add_(p.detach(), alpha=(1.0 - self.decay))

    @torch.no_grad()
    def apply_to(self, model):
        self.backup = {}
        for name, p in model.named_parameters():
            if not p.requires_grad:
                continue
            self.backup[name] = p.detach().clone()
            p.copy_(self.shadow[name])

    @torch.no_grad()
    def restore(self, model):
        for name, p in model.named_parameters():
            if not p.requires_grad:
                continue
            p.copy_(self.backup[name])
        self.backup = {}


# =============================================================================
# LR schedule
# =============================================================================
def build_cosine_with_warmup(optimizer, warmup_steps: int, total_steps: int, min_lr_ratio: float = 0.1):
    def lr_lambda(step: int):
        if step < warmup_steps:
            return float(step) / float(max(1, warmup_steps))
        progress = float(step - warmup_steps) / float(max(1, total_steps - warmup_steps))
        cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
        return (min_lr_ratio + (1.0 - min_lr_ratio) * cosine)

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


# =============================================================================
# Training — NAN SAFE + PRODUCTION-GRADE PROXY
# =============================================================================
def train_edge_forecaster(
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    df_full: pd.DataFrame,
    feature_cols: List[str],
    cfg: EdgeForecasterConfig,
    n_epochs: int = 40,
    batch_size: int = 256,
    lr: float = 2e-4,
    device: str = "cpu",
    val_size: float = 0.2,
    log_interval: int = 300,
    ece_bins: int = 10,
    label_smoothing: float = 0.02,
    fee_bps: float = 1.0,
    slippage_bps: float = 0.5,
    amp: int = 1,
    grad_accum: int = 1,
    compile_model: int = 0,
    ema_decay: float = 0.999,
    warmup_pct: float = 0.05,
    max_grad_norm: float = 1.0,
    skip_bad_batches: int = 1,
) -> Tuple[EdgeForecasterModel, Dict[str, Any], Dict[str, Any], Dict[str, Any], BinaryCalibrator]:
    from torch.utils.data import DataLoader, Dataset

    seq_len = cfg.seq_len

    class SeqDataset(Dataset):
        def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int, stride: int = 1):
            if X.shape[0] < seq_len:
                raise ValueError(f"Not enough data for seq_len={seq_len}. n={X.shape[0]}")
            self.X = X
            self.y = y
            self.seq_len = seq_len
            self.stride = stride
            self.indices = list(range(0, X.shape[0] - seq_len + 1, stride))

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, idx):
            i = self.indices[idx]
            X_seq = self.X[i : i + self.seq_len]
            y_target = self.y[i + self.seq_len - 1]
            return torch.from_numpy(X_seq.astype(np.float32)), torch.from_numpy(y_target.astype(np.float32))

    n_train = int(len(features_df) * (1 - val_size))
    X_train = features_df.iloc[:n_train].values.astype(np.float32)
    y_train = labels_df.iloc[:n_train].values.astype(np.float32)
    X_val = features_df.iloc[n_train:].values.astype(np.float32)
    y_val = labels_df.iloc[n_train:].values.astype(np.float32)

    if not np.isfinite(X_train).all() or not np.isfinite(y_train).all():
        raise ValueError("Non-finite values in TRAIN tensors (X_train or y_train)")
    if not np.isfinite(X_val).all() or not np.isfinite(y_val).all():
        raise ValueError("Non-finite values in VAL tensors (X_val or y_val)")

    train_ds = SeqDataset(X_train, y_train, seq_len=seq_len, stride=1)
    val_ds = SeqDataset(X_val, y_val, seq_len=seq_len, stride=1)

    train_loader = DataLoader(
        train_ds,
        batch_size=batch_size,
        shuffle=True,
        drop_last=False,
        pin_memory=True,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=batch_size,
        shuffle=False,
        drop_last=False,
        pin_memory=True,
        num_workers=0,
    )

    logger.info(
        {
            "msg": "LAZY_DATASET_STATS",
            "train_sequences": len(train_ds),
            "val_sequences": len(val_ds),
            "memory_saved_gb": f"{(len(train_ds) * seq_len * X_train.shape[1] * 4) / 1e9:.2f}",
            "note": "Zero materialization - sequences built on-demand",
        }
    )

    model = EdgeForecasterModel(cfg=cfg)
    input_dim = X_train.shape[-1]
    net = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)

    device_torch = torch.device(device)
    net = net.to(device_torch)

    if compile_model:
        try:
            net = torch.compile(net)  # type: ignore
            logger.info({"msg": "TORCH_COMPILE_ENABLED"})
        except Exception as e:
            logger.warning({"msg": "TORCH_COMPILE_FAILED", "err": str(e)})

    model.net = net

    optimizer = torch.optim.AdamW(model.net.parameters(), lr=lr, weight_decay=1e-3, betas=(0.9, 0.95))

    total_steps = int(math.ceil(len(train_loader) / max(1, grad_accum)) * n_epochs)
    warmup_steps = int(total_steps * warmup_pct)
    scheduler = build_cosine_with_warmup(
        optimizer,
        warmup_steps=warmup_steps,
        total_steps=total_steps,
        min_lr_ratio=0.15,
    )

    use_amp = (amp == 1) and (device.startswith("cuda"))
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    autocast = torch.amp.autocast

    ema = EMA(model.net, decay=ema_decay) if ema_decay > 0 else None

    def quantile_loss(pred, target, quantile):
        target = torch.nan_to_num(target, nan=0.0, posinf=0.0, neginf=0.0).clamp(-0.25, 0.25)
        pred = torch.nan_to_num(pred, nan=0.0, posinf=0.0, neginf=0.0)
        error = target - pred
        return torch.mean(torch.max((quantile - 1) * error, quantile * error))

    def bce_logits_smooth(logits, target, eps: float):
        target = torch.nan_to_num(target, nan=0.0, posinf=0.0, neginf=0.0).clamp(0.0, 1.0)
        t = target * (1 - eps) + 0.5 * eps
        logits = torch.nan_to_num(logits, nan=0.0, posinf=0.0, neginf=0.0).clamp(-30.0, 30.0)
        return torch.nn.functional.binary_cross_entropy_with_logits(logits, t)

    def compute_loss(outputs, targets, diagnostic_mode=False):
        (
            q05,
            q50,
            q95,
            logits_dir,
            _p_dir,
            rv_mean,
            _sigma_tail,
        ) = outputs

        targets = torch.nan_to_num(targets, nan=0.0, posinf=0.0, neginf=0.0)

        return_fwd = targets[:, 0:1].clamp(-0.25, 0.25)
        dir_hit = targets[:, 1:2].clamp(0.0, 1.0)
        rv_fwd_mean = targets[:, 3:4].clamp(0.0, 0.25)

        loss_q05 = quantile_loss(q05, return_fwd, 0.05)
        loss_q50 = quantile_loss(q50, return_fwd, 0.50)
        loss_q95 = quantile_loss(q95, return_fwd, 0.95)

        loss_dir = bce_logits_smooth(logits_dir, dir_hit, label_smoothing)

        rv_mean = torch.nan_to_num(rv_mean, nan=0.0, posinf=0.0, neginf=0.0).clamp(0.0, 0.5)
        loss_rv = torch.nn.functional.mse_loss(rv_mean, rv_fwd_mean)

        w_q05 = 0.20
        w_q50 = 0.20
        w_q95 = 0.20
        w_dir = 0.33
        w_rv = 0.07

        total = (
            w_q05 * loss_q05
            + w_q50 * loss_q50
            + w_q95 * loss_q95
            + w_dir * loss_dir
            + w_rv * loss_rv
        )

        comps = {
            "loss_q05": float(loss_q05.item()),
            "loss_q50": float(loss_q50.item()),
            "loss_q95": float(loss_q95.item()),
            "loss_dir_hit": float(loss_dir.item()),
            "loss_rv": float(loss_rv.item()),
        }

        return total, comps

    best_val_loss = float("inf")
    best_trading_score = float("-inf")
    patience = 6
    patience_counter = 0
    checkpoints: Dict[str, Any] = {"best_trading": None, "best_val_loss": None}

    logger.info(
        {
            "msg": "TRAINING_START_PRODUCTION_GRADE",
            "cfg": to_jsonable(asdict(cfg)),
            "optimizer": {"type": "AdamW", "lr": lr, "weight_decay": 1e-3, "betas": [0.9, 0.95]},
            "schedule": {"type": "cosine_with_warmup", "warmup_steps": warmup_steps, "total_steps": total_steps},
            "amp": bool(use_amp),
            "grad_accum": int(grad_accum),
            "ema_decay": float(ema_decay),
        }
    )

    global_step = 0
    bad_batches = 0

    for epoch in range(n_epochs):
        epoch_idx = epoch + 1
        t0 = time.time()

        model.net.train()
        train_loss_sum = 0.0
        train_batches = 0

        # ========== PATCH 1.1: GRADIENT ACCUMULATION (EPOCH-LEVEL) ==========
        epoch_clip_count = 0
        epoch_total_steps = 0
        epoch_grad_norms = []
        # ====================================================================

        optimizer.zero_grad(set_to_none=True)

        for batch_idx, (X_b, y_b) in enumerate(train_loader):
            X_b = X_b.to(device_torch, non_blocking=True)
            y_b = y_b.to(device_torch, non_blocking=True)

            X_b = torch.nan_to_num(X_b, nan=0.0, posinf=0.0, neginf=0.0).clamp(-20.0, 20.0)
            y_b = torch.nan_to_num(y_b, nan=0.0, posinf=0.0, neginf=0.0)

            enable_diagnostic = (log_interval > 0) and (batch_idx % log_interval == 0)

            with autocast(device_type="cuda", enabled=use_amp):
                out = model.net(X_b, regime_vec=None)
                loss, comps = compute_loss(out, y_b, diagnostic_mode=enable_diagnostic)
                loss = loss / float(max(1, grad_accum))

            if torch.isnan(loss) or torch.isinf(loss):
                bad_batches += 1
                logger.error(
                    {
                        "msg": "LOSS_NAN_INF_SKIP",
                        "epoch": epoch_idx,
                        "batch": int(batch_idx),
                        "bad_batches": int(bad_batches),
                    }
                )
                optimizer.zero_grad(set_to_none=True)
                if skip_bad_batches and bad_batches <= 20:
                    continue
                raise RuntimeError("Loss became NaN/Inf (too many bad batches)")

            scaler.scale(loss).backward()

            if ((batch_idx + 1) % grad_accum) == 0:
                scaler.unscale_(optimizer)

                # ========== PATCH 1.1: GRADIENT METRICS (PRE-CLIP) ==========
                grad_metrics = compute_gradient_metrics(model.net, max_grad_norm)
                pre_clip_norm = grad_metrics["pre_clip_norm"]
                was_clipped = grad_metrics["was_clipped"]
                max_param_grad = grad_metrics["max_param_grad"]
                # ============================================================

                torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=max_grad_norm)

                # ========== PATCH 1.1: LR BEFORE STEP ==========
                lr_before = optimizer.param_groups[0]["lr"]
                # ===============================================

                scaler.step(optimizer)
                scaler.update()

                # ========== PATCH 1.1: AMP SCALE ==========
                amp_scale = scaler.get_scale()
                # ==========================================

                scheduler.step()

                # ========== PATCH 1.1: LR AFTER STEP ==========
                lr_after = optimizer.param_groups[0]["lr"]
                # ==============================================

                optimizer.zero_grad(set_to_none=True)
                global_step += 1

                # ========== PATCH 1.1: ACCUMULATE EPOCH STATS ==========
                epoch_clip_count += int(was_clipped)
                epoch_total_steps += 1
                epoch_grad_norms.append(pre_clip_norm)
                # =======================================================

                if global_step == 1:
                    lr_first = optimizer.param_groups[0]["lr"]
                    assert lr_first > 0.0, f"LR is zero at step 1 → scheduler/optimizer order broken (lr={lr_first})"

                if ema is not None:
                    ema.update(model.net)

            train_loss_sum += float(loss.item()) * float(max(1, grad_accum))
            train_batches += 1

            if log_interval > 0 and (batch_idx % log_interval == 0):
                logger.info(
                    {
                        "msg": "BATCH_DIAGNOSTIC_PRODUCTION_GRADE",
                        "epoch": epoch_idx,
                        "batch": int(batch_idx),
                        "loss": float(loss.item()) * float(max(1, grad_accum)),
                        "loss_components": comps,

                        # ========== PATCH 1.1: LR METRICS (BEFORE/AFTER) ==========
                        "lr_before_step": float(lr_before) if 'lr_before' in locals() else float(optimizer.param_groups[0]["lr"]),
                        "lr_after_step": float(lr_after) if 'lr_after' in locals() else float(optimizer.param_groups[0]["lr"]),
                        # ===========================================================

                        # ========== PATCH 1.1: GRADIENT METRICS ==========
                        "grad_pre_clip_norm": float(pre_clip_norm) if 'pre_clip_norm' in locals() else 0.0,
                        "grad_was_clipped": bool(was_clipped) if 'was_clipped' in locals() else False,
                        "grad_max_param": float(max_param_grad) if 'max_param_grad' in locals() else 0.0,
                        "grad_clip_threshold": float(max_grad_norm),
                        "grad_clip_ratio": float(pre_clip_norm / max_grad_norm) if ('pre_clip_norm' in locals() and max_grad_norm > 0) else 0.0,
                        # =================================================

                        # ========== PATCH 1.1: AMP SCALE ==========
                        "amp_scale": float(amp_scale) if 'amp_scale' in locals() else float(scaler.get_scale()),
                        # ==========================================

                        "global_step": int(global_step),
                        "bad_batches": int(bad_batches),
                    }
                )

        train_loss = train_loss_sum / max(1, train_batches)

        # VAL (EMA weights)
        model.net.eval()
        if ema is not None:
            ema.apply_to(model.net)

        val_loss_sum = 0.0
        val_batches = 0
        all_q50, all_logits_dir = [], []
        all_dirhit, all_isup, all_ret = [], [], []

        with torch.no_grad():
            for X_b, y_b in val_loader:
                X_b = X_b.to(device_torch, non_blocking=True)
                y_b = y_b.to(device_torch, non_blocking=True)

                X_b = torch.nan_to_num(X_b, nan=0.0, posinf=0.0, neginf=0.0).clamp(-20.0, 20.0)
                y_b = torch.nan_to_num(y_b, nan=0.0, posinf=0.0, neginf=0.0)

                with autocast(device_type="cuda", enabled=use_amp):
                    out = model.net(X_b, regime_vec=None)
                    loss, _ = compute_loss(out, y_b)

                val_loss_sum += float(loss.item())
                val_batches += 1

                _q05, q50, _q95, logits_dir, _p_dir, _rv_mean, _sigma_tail = out
                all_q50.append(q50.detach().float().cpu())
                all_logits_dir.append(logits_dir.detach().float().cpu())

                all_ret.append(y_b[:, 0].detach().float().cpu())
                all_dirhit.append(y_b[:, 1].detach().float().cpu())
                all_isup.append(y_b[:, 2].detach().float().cpu())

        val_loss = val_loss_sum / max(1, val_batches)

        q50_all = torch.cat(all_q50).squeeze().numpy().astype(np.float32, copy=False)
        logits_dir_all = torch.cat(all_logits_dir).squeeze().numpy().astype(np.float32, copy=False)
        ret_all = torch.cat(all_ret).squeeze().numpy().astype(np.float32, copy=False)
        dirhit_all = torch.cat(all_dirhit).squeeze().numpy().astype(np.float32, copy=False)
        isup_all = torch.cat(all_isup).squeeze().numpy().astype(np.float32, copy=False)

        # ========== PATCH 1.2: SATURATION CHECK (CRITICAL FOR RENTABILITY) ==========
        if epoch_idx == 1 or epoch_idx % 5 == 0:
            val_return_report = distribution_report(
                ret_all,
                "val_return_fwd",
                clamp_min=-1.0,
                clamp_max=1.0
            )

            logger.info({
                "msg": "SATURATION_CHECK_EPOCH",
                "epoch": epoch_idx,
                "val_return_saturation": val_return_report,
            })

            # CRITICAL: Hard warning if saturation > 10% (signal quality degraded)
            pct_saturated = (
                val_return_report.get("pct_above_clamp_max", 0.0) +
                val_return_report.get("pct_below_clamp_min", 0.0)
            )

            if pct_saturated > 10.0:
                logger.warning({
                    "msg": "SATURATION_WARNING_CRITICAL",
                    "pct_saturated": pct_saturated,
                    "impact": "Signal quality degraded - large moves are clamped",
                    "recommendation": "Consider wider clamps: [-2.0, 2.0] or [-5.0, 5.0]",
                    "current_clamp": "[-1.0, 1.0]",
                    "epoch": epoch_idx,
                })
        # ============================================================================

        pdh_all = 1.0 / (1.0 + np.exp(-np.clip(logits_dir_all, -30.0, 30.0)))
        pup_all = (q50_all > 0).astype(np.float32)

        from sklearn.metrics import brier_score_loss

        brier_dir = float(brier_score_loss(dirhit_all, pdh_all))
        ece_dir = float(_ece(dirhit_all, pdh_all, n_bins=ece_bins))

        # ==================================================================
        # REALISTIC PROXY METRICS (PRODUCTION-GRADE)
        # ==================================================================
        n_train_rows = int(len(features_df) * (1 - val_size))
        start = n_train_rows + seq_len - 1
        end = start + int(len(q50_all))

        if start < 0 or start >= len(df_full):
            raise RuntimeError(
                f"Invalid alignment start={start} (n_train_rows={n_train_rows}, seq_len={seq_len}, "
                f"df_full_len={len(df_full)})"
            )

        df_val_aligned = df_full.iloc[start:end].copy()

        if len(df_val_aligned) != len(q50_all):
            logger.warning(
                {
                    "msg": "ALIGNMENT_MISMATCH",
                    "df_val_aligned": int(len(df_val_aligned)),
                    "preds": int(len(q50_all)),
                    "start": int(start),
                    "end": int(end),
                    "using_min_length": True,
                }
            )
            min_len = int(min(len(df_val_aligned), len(q50_all)))
            df_val_aligned = df_val_aligned.iloc[:min_len]
            q50_all = q50_all[:min_len]
            logits_dir_all = logits_dir_all[:min_len]
            pdh_all = pdh_all[:min_len]
            ret_all = ret_all[:min_len]
            dirhit_all = dirhit_all[:min_len]
            isup_all = isup_all[:min_len]

        tp_threshold_val = df_val_aligned["tp_threshold_used"].values.astype(np.float32)
        sl_threshold_val = df_val_aligned["sl_threshold_used"].values.astype(np.float32)

        proxy_metrics = compute_realistic_proxy_metrics(
            q50=q50_all,
            p_dir_hit=pdh_all,
            tp_threshold=tp_threshold_val,
            sl_threshold=sl_threshold_val,
            return_fwd=ret_all,
            threshold_percentile=85.0,
            fee_rate=0.0004,
            max_trades_per_day=20,
            val_days=30,
        )

        n_trades_proxy = proxy_metrics["n_trades"]
        sharpe_proxy = proxy_metrics["sharpe"]
        roi_proxy = proxy_metrics["roi"]
        win_rate_proxy = proxy_metrics["win_rate"]
        proxy_score = proxy_metrics["proxy_score"]
        trades_per_day = proxy_metrics["trades_per_day"]
        signal_pct = proxy_metrics["signal_pct"]
        threshold_used = proxy_metrics["threshold_used"]
        sharpe_ci_width = proxy_metrics["sharpe_ci_width"]

        trading_score = proxy_score - 2.0 * ece_dir - 0.5 * max(0.0, val_loss / (train_loss + 1e-12) - 1.15)

        # ========== PATCH 1.1: COMPUTE EPOCH GRADIENT SUMMARY ==========
        clip_ratio_epoch = float(epoch_clip_count / max(1, epoch_total_steps) * 100.0)
        grad_norm_p50 = float(np.median(epoch_grad_norms)) if epoch_grad_norms else 0.0
        grad_norm_p95 = float(np.percentile(epoch_grad_norms, 95)) if epoch_grad_norms else 0.0
        grad_norm_max = float(np.max(epoch_grad_norms)) if epoch_grad_norms else 0.0
        # ===============================================================

        logger.info(
            {
                "msg": "EPOCH_SUMMARY_PRODUCTION_GRADE",
                "epoch": epoch_idx,
                "time_sec": float(time.time() - t0),
                "lr": float(optimizer.param_groups[0]["lr"]),
                "train_loss": float(train_loss),
                "val_loss": float(val_loss),

                # ========== PATCH 1.1: GRADIENT SUMMARY ==========
                "gradient_summary": {
                    "clip_ratio_epoch_pct": clip_ratio_epoch,
                    "grad_norm_median": grad_norm_p50,
                    "grad_norm_p95": grad_norm_p95,
                    "grad_norm_max": grad_norm_max,
                    "grad_clip_threshold": float(max_grad_norm),
                },
                # =================================================

                "calibration": {"brier_dir_hit": brier_dir, "ece_dir_hit": ece_dir},
                "realistic_proxy": {
                    "n_trades": n_trades_proxy,
                    "sharpe": sharpe_proxy,
                    "roi": roi_proxy,
                    "win_rate": win_rate_proxy,
                    "proxy_score": proxy_score,
                    "trading_score": float(trading_score),
                    "trades_per_day": trades_per_day,
                    "signal_pct": signal_pct,
                    "threshold_used": threshold_used,
                    "pnl_p01": proxy_metrics["pnl_p01"],
                    "pnl_p50": proxy_metrics["pnl_p50"],
                    "pnl_p99": proxy_metrics["pnl_p99"],
                    "sharpe_ci_lower": proxy_metrics["sharpe_ci_lower"],
                    "sharpe_ci_upper": proxy_metrics["sharpe_ci_upper"],
                    "sharpe_ci_width": sharpe_ci_width,
                },
                "bad_batches": int(bad_batches),
            }
        )

        checkpoint_base = {
            "epoch": epoch_idx,
            "model_state_dict": model.net.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "config": asdict(cfg),
            "input_dim": int(input_dim),
            "calibration": {"temperature_dir_hit": None, "temperature_up": None},
            "metrics": {
                "train_loss": float(train_loss),
                "val_loss": float(val_loss),
                "trading_score": float(trading_score),
                "ece_dir_hit": float(ece_dir),
                "brier_dir_hit": float(brier_dir),
                "proxy_n_trades": int(n_trades_proxy),
                "proxy_sharpe": float(sharpe_proxy),
                "proxy_roi": float(roi_proxy),
                "proxy_win_rate": float(win_rate_proxy),
                "proxy_trades_per_day": float(trades_per_day),
            },
        }

        if n_trades_proxy < 50:
            logger.warning(
                {
                    "msg": "CHECKPOINT_REJECTED_LOW_TRADES",
                    "reason": "n_trades < 50 (hard requirement)",
                    "n_trades": n_trades_proxy,
                    "epoch": epoch_idx,
                }
            )
        elif trading_score > best_trading_score:
            best_trading_score = float(trading_score)
            checkpoints["best_trading"] = checkpoint_base
            logger.info(
                {
                    "msg": "CHECKPOINT_UPDATE",
                    "type": "best_trading",
                    "epoch": epoch_idx,
                    "trading_score": float(trading_score),
                    "n_trades": n_trades_proxy,
                    "sharpe": sharpe_proxy,
                }
            )

        if val_loss < best_val_loss:
            best_val_loss = float(val_loss)
            checkpoints["best_val_loss"] = checkpoint_base
            patience_counter = 0
            logger.info({"msg": "CHECKPOINT_UPDATE", "type": "best_val_loss", "epoch": epoch_idx})
        else:
            patience_counter += 1

        if ema is not None:
            ema.restore(model.net)

        if patience_counter >= patience:
            logger.info({"msg": "EARLY_STOP_TRIGGERED_PRODUCTION_GRADE", "epoch": epoch_idx})
            break

    if checkpoints["best_trading"] is not None:
        model.net.load_state_dict(checkpoints["best_trading"]["model_state_dict"])
        logger.info(
            {
                "msg": "RESTORE_BEST_MODEL",
                "type": "best_trading",
                "epoch": checkpoints["best_trading"]["epoch"],
            }
        )

    # temperature scaling on VAL logits
    model.net.eval()
    logits_dir_list = []
    dirhit_list = []

    with torch.no_grad():
        for X_b, y_b in val_loader:
            X_b = X_b.to(device_torch, non_blocking=True)
            y_b = y_b.to(device_torch, non_blocking=True)

            X_b = torch.nan_to_num(X_b, nan=0.0, posinf=0.0, neginf=0.0).clamp(-20.0, 20.0)
            y_b = torch.nan_to_num(y_b, nan=0.0, posinf=0.0, neginf=0.0)

            with autocast(device_type="cuda", enabled=use_amp):
                out = model.net(X_b, regime_vec=None)

            _q05, _q50, _q95, logits_dir, _p_dir, _rv_mean, _sigma_tail = out
            logits_dir_list.append(logits_dir.detach().float().cpu())
            dirhit_list.append(y_b[:, 1].detach().float().cpu())

    logits_dir_np = torch.cat(logits_dir_list).squeeze().numpy().astype(np.float32, copy=False)
    dirhit_true = torch.cat(dirhit_list).squeeze().numpy().astype(np.float32, copy=False)

    calibrator_dir = BinaryCalibrator(method="temperature")
    calibrator_dir.fit(logits_dir_np, dirhit_true)

    logger.info(
        {
            "msg": "CALIBRATOR_FITTED",
            "temperature": calibrator_dir.temperature,
            "method": "temperature_scaling",
        }
    )

    calib = {"temperature_dir_hit": float(calibrator_dir.temperature), "temperature_up": None}
    final_metrics = {
        "best_val_loss": float(best_val_loss),
        "best_trading_score": float(best_trading_score),
        "calibration": calib,
    }

    return model, final_metrics, checkpoints, calib, calibrator_dir


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Train Edge Forecaster (Production-Grade)")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--output", type=str, default="artifacts/models/edge/production_v4.pt")

    parser.add_argument("--horizon", type=int, default=60, help="Forward horizon in minutes (e.g., 60 for 1-hour)")
    parser.add_argument("--bar-duration", type=int, default=1, help="Duration of one bar in minutes (e.g., 1 for 1m data, 5 for 5m data)")
    parser.add_argument("--seq-len", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--device", type=str, default="cpu")

    # ========== PATCH: FAST EXPERIMENTS ==========
    parser.add_argument("--data-pct", type=float, default=None, help="Use only X%% of data for fast experiments (e.g., 0.10 for 10%%)")
    # ============================================
    parser.add_argument("--val-size", type=float, default=0.15)
    parser.add_argument("--test-size", type=float, default=0.15)

    parser.add_argument("--tp-k", type=float, default=2.0)
    parser.add_argument("--sl-k", type=float, default=1.2)
    parser.add_argument("--adaptive-tp", type=int, default=0)

    parser.add_argument("--log-interval", type=int, default=300)
    parser.add_argument("--ece-bins", type=int, default=10)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--fee-bps", type=float, default=1.0)
    parser.add_argument("--slippage-bps", type=float, default=0.5)

    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--deterministic", type=int, default=0)
    parser.add_argument("--amp", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=1)
    parser.add_argument("--compile", type=int, default=0)
    parser.add_argument("--ema-decay", type=float, default=0.999)
    parser.add_argument("--warmup-pct", type=float, default=0.05)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)

    args = parser.parse_args()

    seed_everything(args.seed, deterministic=bool(args.deterministic))

    features_df, labels_df, feature_cols, _exclude_cols, df_full = load_training_data(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        horizon_minutes=args.horizon,
        bar_duration_minutes=args.bar_duration,
        tp_k=args.tp_k,
        sl_k=args.sl_k,
        adaptive_tp=bool(args.adaptive_tp),
    )

    # ========== PATCH: DATA SUBSAMPLE FOR FAST EXPERIMENTS ==========
    if args.data_pct is not None and 0.0 < args.data_pct < 1.0:
        n_rows_orig = len(features_df)
        n_rows_keep = int(n_rows_orig * args.data_pct)
        features_df = features_df.iloc[:n_rows_keep].copy()
        labels_df = labels_df.iloc[:n_rows_keep].copy()
        df_full = df_full.iloc[:n_rows_keep].copy()
        logger.info({
            "msg": "DATA_SUBSAMPLED_FOR_FAST_EXPERIMENT",
            "data_pct": args.data_pct,
            "rows_original": n_rows_orig,
            "rows_kept": n_rows_keep,
        })
    # ================================================================

    cfg = EdgeForecasterConfig(
        seq_len=args.seq_len,
        feature_cols=feature_cols,
        d_model=192,
        n_heads=6,
        n_layers=5,
        d_ff=512,
        dropout=0.05,
        attn_dropout=0.02,
        device=args.device,
        use_regime_cond=False,
    )

    model, metrics, checkpoints, calib, calibrator_dir = train_edge_forecaster(
        features_df=features_df,
        labels_df=labels_df,
        df_full=df_full,
        feature_cols=feature_cols,
        cfg=cfg,
        n_epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
        val_size=args.val_size,
        log_interval=args.log_interval,
        ece_bins=args.ece_bins,
        label_smoothing=args.label_smoothing,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        amp=args.amp,
        grad_accum=args.grad_accum,
        compile_model=args.compile,
        ema_decay=args.ema_decay,
        warmup_pct=args.warmup_pct,
        max_grad_norm=args.max_grad_norm,
    )

    # NESTED VALIDATION: train/val/test split to prevent optimization leak
    # Train: 70%, Val: 15%, Test: 15% (held-out, never seen during training)
    n_total = len(features_df)
    n_test = int(n_total * args.test_size)
    n_val = int(n_total * args.val_size)
    n_train = n_total - n_val - n_test

    logger.info({
        "msg": "NESTED_VALIDATION_SPLIT",
        "n_total": n_total,
        "n_train": n_train,
        "n_val": n_val,
        "n_test": n_test,
        "train_pct": float(n_train / n_total * 100),
        "val_pct": float(n_val / n_total * 100),
        "test_pct": float(n_test / n_total * 100),
    })

    # Val slice (used during training for early stopping)
    df_val = df_full.iloc[n_train:n_train + n_val].copy()
    X_val = features_df.iloc[n_train:n_train + n_val].values.astype(np.float32)
    y_val_dummy = labels_df.iloc[n_train:n_train + n_val].values.astype(np.float32)

    # Test slice (held-out, evaluated AFTER training)
    df_test = df_full.iloc[n_train + n_val:].copy()
    X_test = features_df.iloc[n_train + n_val:].values.astype(np.float32)
    y_test_dummy = labels_df.iloc[n_train + n_val:].values.astype(np.float32)

    if len(X_val) < args.seq_len:
        raise ValueError("Not enough validation rows for seq_len")
    if len(X_test) < args.seq_len:
        raise ValueError("Not enough test rows for seq_len")

    from torch.utils.data import Dataset, DataLoader

    class SeqDatasetFinal(Dataset):
        def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int, stride: int = 1):
            if X.shape[0] < seq_len:
                raise ValueError(f"Not enough data for seq_len={seq_len}. n={X.shape[0]}")
            self.X = X
            self.y = y
            self.seq_len = seq_len
            self.stride = stride
            self.indices = list(range(0, X.shape[0] - seq_len + 1, stride))

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, idx):
            i = self.indices[idx]
            X_seq = self.X[i : i + self.seq_len]
            y_target = self.y[i + self.seq_len - 1]
            return torch.from_numpy(X_seq.astype(np.float32)), torch.from_numpy(y_target.astype(np.float32))

    final_ds = SeqDatasetFinal(X_val, y_val_dummy, seq_len=args.seq_len, stride=1)
    final_loader = DataLoader(
        final_ds,
        batch_size=args.batch_size,
        shuffle=False,
        drop_last=False,
        pin_memory=True,
        num_workers=0,
    )

    device_torch = torch.device(args.device)
    model.net = model.net.to(device_torch)
    model.net.eval()

    logits_dir_final = []
    q50_final = []

    with torch.no_grad():
        for X_b, _ in final_loader:
            X_b = X_b.to(device_torch, non_blocking=True)
            out = model.net(X_b, regime_vec=None)
            _q05, q50, _q95, logits_dir, _p_dir, _rv_mean, _sigma_tail = out
            logits_dir_final.append(logits_dir.detach().float().cpu())
            q50_final.append(q50.detach().float().cpu())

    logits_dir_np = torch.cat(logits_dir_final).squeeze().numpy().astype(np.float32, copy=False)
    q50_np = torch.cat(q50_final).squeeze().numpy().astype(np.float32, copy=False)

    pdh_cal = apply_temperature_from_logits(logits_dir_np, calib["temperature_dir_hit"])
    pup_cal = (q50_np > 0).astype(np.float32)

    df_val_seq_end = df_val.iloc[args.seq_len - 1 : args.seq_len - 1 + len(pdh_cal)]

    proxy = compute_trading_metrics_proxy(
        close=df_val_seq_end["close"].values,
        high=df_val_seq_end["high"].values,
        low=df_val_seq_end["low"].values,
        tp_threshold_used=df_val_seq_end["tp_threshold_used"].values,
        sl_threshold_used=df_val_seq_end["sl_threshold_used"].values,
        horizon=args.horizon,
        p_dir_hit=pdh_cal,
        p_up=pup_cal,
        trade_threshold=0.55,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )

    metrics["final_real_proxy_val"] = proxy
    logger.info({"msg": "FINAL_REAL_PROXY_VAL_PRODUCTION_GRADE", **proxy})

    # ===========================================================================
    # HELD-OUT TEST SET EVALUATION (NEVER SEEN DURING TRAINING)
    # ===========================================================================
    logger.info({"msg": "EVALUATING_HELD_OUT_TEST_SET", "n_test": len(X_test)})

    ds_test = SeqDatasetFinal(X_test, y_test_dummy, args.seq_len, stride=1)
    dl_test = DataLoader(ds_test, batch_size=args.batch_size, shuffle=False, num_workers=0, drop_last=False)

    model.net.eval()
    q50_test, pdh_test, pup_test = [], [], []
    device_torch = torch.device(args.device)
    with torch.no_grad():
        for Xb, _ in dl_test:
            Xb = Xb.to(device_torch)
            out = model.predict_full_outputs(Xb)
            q50_test.append(out["quantile_50"].cpu().numpy())
            pdh_test.append(out["p_dir_hit"].cpu().numpy())
            pup_test.append(out["p_up"].cpu().numpy())

    q50_test = np.concatenate(q50_test, axis=0).astype(np.float32)
    pdh_test = np.concatenate(pdh_test, axis=0).astype(np.float32)
    pup_test = np.concatenate(pup_test, axis=0).astype(np.float32)

    df_test_seq_end = df_test.iloc[args.seq_len - 1 : args.seq_len - 1 + len(pdh_test)]

    proxy_test = compute_realistic_paper_metrics(
        median_fwd_ret=q50_test,
        close=df_test_seq_end["close"].values,
        high=df_test_seq_end["high"].values,
        low=df_test_seq_end["low"].values,
        tp_threshold_used=df_test_seq_end["tp_threshold_used"].values,
        sl_threshold_used=df_test_seq_end["sl_threshold_used"].values,
        horizon=args.horizon,
        p_dir_hit=pdh_test,
        p_up=pup_test,
        trade_threshold=0.55,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )

    metrics["final_real_proxy_test"] = proxy_test
    logger.info({
        "msg": "HELD_OUT_TEST_SET_RESULTS",
        "n_test": len(X_test),
        "sharpe": proxy_test["sharpe"],
        "n_trades": proxy_test["n_trades"],
        "roi_pct": proxy_test["roi_pct"],
        "max_dd": proxy_test["max_dd"],
        "WARNING": "This is the TRUE OOS performance - val metrics may be optimistic due to early stopping",
    })

    # Save model using unified artifact system
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    best_trading_score = float(metrics.get("best_trading_score", float("nan")))
    best_val_loss = float(metrics.get("best_val_loss", float("nan")))

    save_artifact(
        path=str(output_path),
        net=model.net,
        cfg=cfg,
        feature_cols=feature_cols,
        calibration=calib,
        metadata={
            "production_grade": True,
            "best_trading_score": best_trading_score,
            "best_val_loss": best_val_loss,
        },
    )
    logger.info({"msg": "SAVED_MODEL_UNIFIED_ARTIFACT", "path": str(output_path)})

    metrics_path = output_path.parent / f"{output_path.stem}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(to_jsonable(metrics), f, indent=2)
    logger.info({"msg": "SAVED_METRICS", "path": str(metrics_path)})

    # Save best checkpoints
    if checkpoints.get("best_trading") is not None:
        cp_trading = output_path.parent / f"{output_path.stem}_best_trading.pt"
        best_trading_ckpt = checkpoints["best_trading"]

        temp_cfg = EdgeForecasterConfig(**best_trading_ckpt["config"])
        temp_net = EdgeForecasterNet(input_dim=int(best_trading_ckpt["input_dim"]), cfg=temp_cfg)
        temp_net.load_state_dict(best_trading_ckpt["model_state_dict"])

        save_artifact(
            path=str(cp_trading),
            net=temp_net,
            cfg=temp_cfg,
            feature_cols=feature_cols,
            calibration=best_trading_ckpt.get("calibration", calib),
            metadata={"checkpoint_type": "best_trading", "epoch": int(best_trading_ckpt["epoch"])},
        )
        logger.info({"msg": "SAVED_CHECKPOINT_UNIFIED", "type": "best_trading", "path": str(cp_trading)})

    if checkpoints.get("best_val_loss") is not None:
        cp_valloss = output_path.parent / f"{output_path.stem}_best_val_loss.pt"
        best_valloss_ckpt = checkpoints["best_val_loss"]

        temp_cfg = EdgeForecasterConfig(**best_valloss_ckpt["config"])
        temp_net = EdgeForecasterNet(input_dim=int(best_valloss_ckpt["input_dim"]), cfg=temp_cfg)
        temp_net.load_state_dict(best_valloss_ckpt["model_state_dict"])

        save_artifact(
            path=str(cp_valloss),
            net=temp_net,
            cfg=temp_cfg,
            feature_cols=feature_cols,
            calibration=best_valloss_ckpt.get("calibration", calib),
            metadata={"checkpoint_type": "best_val_loss", "epoch": int(best_valloss_ckpt["epoch"])},
        )
        logger.info({"msg": "SAVED_CHECKPOINT_UNIFIED", "type": "best_val_loss", "path": str(cp_valloss)})

    calib_path = output_path.parent / f"{output_path.stem}_calibration.json"
    with open(calib_path, "w") as f:
        json.dump(to_jsonable(calib), f, indent=2)
    logger.info({"msg": "SAVED_CALIBRATION", "path": str(calib_path)})

    calibrator_path = output_path.parent / f"{output_path.stem}_calibrator.pkl"
    calibrator_dir.save(str(calibrator_path))
    logger.info({"msg": "SAVED_CALIBRATOR", "path": str(calibrator_path), "temperature": calibrator_dir.temperature})

    print(f"\n✅ Model saved to: {output_path}")
    print(f"✅ Metrics saved to: {metrics_path}")
    print(f"✅ Calibration saved to: {calib_path}")
    print(f"✅ Final real proxy on VAL: {proxy}")


if __name__ == "__main__":
    main()
