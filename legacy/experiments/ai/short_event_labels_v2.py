"""
Event-driven SHORT v2 labels.

The v2 target is not "price goes down over one fixed horizon". A positive
label requires an observable short event at entry time plus a profitable
multi-horizon outcome after stressed execution costs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ai.level_0.short_v2_data_contract import normalize_short_v2_columns


SHORT_V2_HORIZON_BARS = 8
SHORT_V2_DEFAULT_HORIZONS = (2, 4, 8, 12, 24)
SHORT_V2_NORMAL_COST = 0.0010
SHORT_V2_STRESS_FEE = 0.0015
SHORT_V2_BASE_SLIPPAGE = 0.0002
SHORT_V2_STRESS_COST = SHORT_V2_STRESS_FEE + 2.0 * SHORT_V2_BASE_SLIPPAGE
SHORT_V2_SQUEEZE_LIMIT = 0.025
SHORT_V2_ENTRY_MAE_LIMIT = 0.018
SHORT_V2_SQUEEZE_GATE = 0.55
SHORT_V2_LATE_ENTRY_GATE = 0.60
SHORT_V2_EVENT_QUANTILE = 0.95
SHORT_V2_TAKE_PROFIT = 0.030
SHORT_V2_STOP_LOSS = 0.018

LABEL_COL = "y_short_v2_event"
CONTEXT_COL = "short_v2_context"
ACTIVE_COL = "short_v2_event_active"
NO_SHORT_COL = "short_v2_no_short"
RET_COL = "short_v2_ret_active_h"
MFE_COL = "short_v2_mfe_active_h"
MAE_COL = "short_v2_mae_active_h"

EVENT_CONTEXTS: Tuple[str, ...] = (
    "liquidity_stress",
    "breakdown",
    "failed_breakout",
)

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "short_rebuild_v2"


def short_v2_horizon_columns(horizon: int) -> Dict[str, str]:
    suffix = f"{int(horizon)}h"
    return {
        "ret": f"short_v2_ret_{suffix}",
        "mfe": f"short_v2_mfe_{suffix}",
        "mae": f"short_v2_mae_{suffix}",
        "net_stress": f"short_v2_net_ret_stress_{suffix}",
        "net_normal": f"short_v2_net_ret_normal_{suffix}",
    }


def short_v2_exit_params(horizon: int) -> Tuple[float, float]:
    scale = max(float(horizon), 1.0) ** 0.5
    base_scale = SHORT_V2_HORIZON_BARS ** 0.5
    take_profit = min(0.060, SHORT_V2_TAKE_PROFIT * scale / base_scale)
    stop_loss = min(0.035, SHORT_V2_STOP_LOSS * scale / base_scale)
    return float(take_profit), float(stop_loss)


def _col(df: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(default, index=df.index, dtype=np.float64)


def _positive_z(s: pd.Series, cap: float = 3.0) -> pd.Series:
    return (s.clip(lower=0.0, upper=cap) / cap).fillna(0.0)


def _fit_thresholds(
    scores: Dict[str, pd.Series],
    train_mask: np.ndarray,
    quantile: float,
) -> Dict[str, float]:
    thresholds: Dict[str, float] = {}
    for name, score in scores.items():
        arr = score.values.astype(np.float64)
        train_arr = arr[train_mask & np.isfinite(arr)]
        if len(train_arr) == 0:
            thresholds[name] = 1.0
            continue
        thresholds[name] = float(np.quantile(train_arr, quantile))
    return thresholds


def _forward_short_outcome(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    horizon: int,
    take_profit: float = SHORT_V2_TAKE_PROFIT,
    stop_loss: float = SHORT_V2_STOP_LOSS,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    close_arr = close.values.astype(np.float64)
    high_arr = high.values.astype(np.float64)
    low_arr = low.values.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.where(close_arr > 0, np.log(close_arr), np.nan)
        log_high = np.where(high_arr > 0, np.log(high_arr), np.nan)
        log_low = np.where(low_arr > 0, np.log(low_arr), np.nan)

    n = len(log_close)
    ret = np.full(n, np.nan, dtype=np.float64)
    mfe = np.full(n, np.nan, dtype=np.float64)
    mae = np.full(n, np.nan, dtype=np.float64)
    if n <= horizon:
        return ret, mfe, mae

    future_high = sliding_window_view(log_high[1:], window_shape=horizon)
    future_low = sliding_window_view(log_low[1:], window_shape=horizon)
    valid = future_high.shape[0]
    ref = log_close[:valid]
    mfe[:valid] = np.clip(ref - np.nanmin(future_low, axis=1), 0.0, None)
    mae[:valid] = np.clip(np.nanmax(future_high, axis=1) - ref, 0.0, None)

    for i in range(valid):
        if not np.isfinite(ref[i]):
            continue
        exit_ret = np.nan
        for j in range(horizon):
            adverse = future_high[i, j] - ref[i]
            favorable = ref[i] - future_low[i, j]
            if not np.isfinite(adverse) or not np.isfinite(favorable):
                continue
            # Conservative ordering: if both barriers are touched in the same
            # hourly bar, assume the stop was hit first.
            if adverse >= stop_loss:
                exit_ret = -stop_loss
                break
            if favorable >= take_profit:
                exit_ret = take_profit
                break
        if np.isfinite(exit_ret):
            ret[i] = exit_ret
        elif i + horizon < n and np.isfinite(log_close[i + horizon]):
            ret[i] = ref[i] - log_close[i + horizon]
    return ret, mfe, mae


def compute_short_v2_event_contexts(
    df: pd.DataFrame,
    train_mask: np.ndarray | None = None,
    *,
    quantile: float = SHORT_V2_EVENT_QUANTILE,
) -> pd.DataFrame:
    """
    Compute event contexts from current/past information only.

    Thresholds are fitted on train_mask. If train_mask is None, all rows are
    used, which is acceptable for diagnostics but not for walk-forward training.
    """
    df = normalize_short_v2_columns(df)
    n = len(df)
    if train_mask is None:
        train_mask = np.ones(n, dtype=bool)
    else:
        train_mask = np.asarray(train_mask, dtype=bool)
        if len(train_mask) != n:
            raise ValueError("train_mask length must match df length")

    crowding = (
        0.30 * _positive_z(_col(df, "funding_rate_z_24"))
        + 0.25 * _positive_z(_col(df, "global_ls_longShortRatio_z_24"))
        + 0.20 * _positive_z(_col(df, "oihist_sumOpenInterest_z_24"))
        + 0.15 * _positive_z(_col(df, "funding_x_global_ls"))
        + 0.10 * _col(df, "funding_extreme_positive").fillna(0.0)
    )

    breakdown = (
        0.30 * _col(df, "breakdown_score").fillna(0.0)
        + 0.20 * _col(df, "below_vwap_4h").fillna(0.0)
        + 0.20 * _col(df, "local_low_break_24").fillna(0.0)
        + 0.15 * _positive_z(_col(df, "taker_sell_pressure"))
        + 0.15 * _col(df, "ema_stack_bearish").fillna(0.0)
    )

    failed_breakout = (
        0.35 * _col(df, "failed_breakout_score").fillna(0.0)
        + 0.25 * _positive_z(_col(df, "upper_wick_z_24"))
        + 0.20 * _col(df, "failed_high_12").fillna(0.0)
        + 0.10 * _col(df, "volume_exhaustion_high").fillna(0.0)
        + 0.10 * _col(df, "taker_buy_exhaustion").fillna(0.0)
    )

    liquidity_stress = (
        0.30 * _positive_z(_col(df, "liq_long_spike_12"))
        + 0.25 * _positive_z(_col(df, "sell_volume_shock"))
        + 0.20 * _positive_z(_col(df, "range_expansion_6") - 1.0)
        + 0.15 * _positive_z(_col(df, "taker_sell_pressure"))
        + 0.10 * _positive_z(_col(df, "liq_imbalance_short"))
    )

    bear_continuation = (
        0.45 * _col(df, "bear_continuation_score").fillna(0.0)
        + 0.20 * _col(df, "ema_stack_bearish").fillna(0.0)
        + 0.20 * _positive_z(-_col(df, "mom_logret_72"), cap=0.12)
        + 0.15 * _col(df, "below_vwap_12h").fillna(0.0)
    )

    macro_riskoff = (
        0.35 * _positive_z(-_col(df, "fear_greed_value_z_24"))
        + 0.25 * _positive_z(-_col(df, "funding_rate_z_24"))
        + 0.20 * _positive_z(-_col(df, "oihist_sumOpenInterest_z_24"))
        + 0.20 * _positive_z(_col(df, "global_bear_breadth"))
    )

    scores: Dict[str, pd.Series] = {
        "crowded_longs": crowding,
        "breakdown": breakdown,
        "failed_breakout": failed_breakout,
        "liquidity_stress": liquidity_stress,
        "bear_continuation": bear_continuation,
        "macro_riskoff": macro_riskoff,
    }
    thresholds = _fit_thresholds(scores, train_mask, quantile)

    funding_z24 = _col(df, "funding_rate_z_24")
    global_ls_z24 = _col(df, "global_ls_longShortRatio_z_24")
    oi_z24 = _col(df, "oihist_sumOpenInterest_z_24")
    mom4 = _col(df, "mom_logret_4")
    mom24 = _col(df, "mom_logret_24")
    mom72 = _col(df, "mom_logret_72")
    rsi = _col(df, "rsi_14", 50.0).fillna(50.0)
    squeeze = _col(df, "squeeze_risk_score").fillna(0.0)
    late_risk = _col(df, "short_late_entry_risk").fillna(0.0)
    sell_pressure = _col(df, "taker_sell_pressure")
    sell_shock = _col(df, "sell_volume_shock")
    range_exp = _col(df, "range_expansion_6", 1.0)
    upper_wick_z = _col(df, "upper_wick_z_24")
    rejection = _col(df, "close_rejection_from_high")
    liq_spike = _col(df, "liq_long_spike_12")
    ema_spread = _col(df, "ema_spread_50_200").fillna(0.0)

    crowding_gate = (
        (funding_z24 > 1.25)
        & (global_ls_z24 > 0.75)
        & ((oi_z24 > 0.50) | (_col(df, "open_interest_expansion") > 0.0))
        & (
            (_col(df, "failed_high_6") > 0.0)
            | (upper_wick_z > 1.0)
            | (sell_pressure > 0.15)
        )
        & (late_risk <= SHORT_V2_LATE_ENTRY_GATE)
    )
    breakdown_gate = (
        (_col(df, "below_vwap_4h") > 0.0)
        & (_col(df, "below_ema20") > 0.0)
        & ((_col(df, "local_low_break_24") > 0.0) | (_col(df, "local_low_break_168") > 0.0))
        & ((sell_pressure > 0.10) | (sell_shock > 1.0))
        & (mom24 < 0.0)
        & (late_risk <= SHORT_V2_LATE_ENTRY_GATE)
    )
    failed_breakout_gate = (
        ((_col(df, "failed_high_6") > 0.0) | (_col(df, "failed_high_12") > 0.0))
        & (upper_wick_z > 1.0)
        & (rejection > 0.003)
        & ((sell_pressure > 0.0) | (_col(df, "taker_buy_exhaustion") > 0.0))
        & (late_risk <= SHORT_V2_LATE_ENTRY_GATE)
    )
    liquidity_gate = (
        ((liq_spike > 2.0) | (sell_shock > 2.0))
        & (range_exp > 1.15)
        & ((sell_pressure > 0.10) | (_col(df, "liq_imbalance_short") > 0.20))
        & (mom4 < 0.0)
    )
    bear_continuation_gate = (
        (_col(df, "ema_stack_bearish") > 0.0)
        & (_col(df, "below_vwap_12h") > 0.0)
        & (mom72 < 0.0)
        & ((sell_pressure > 0.0) | (_col(df, "weak_bounce_score") > 0.35))
        & (late_risk <= SHORT_V2_LATE_ENTRY_GATE)
    )
    structural_gates: Dict[str, pd.Series] = {
        "crowded_longs": crowding_gate,
        "breakdown": breakdown_gate,
        "failed_breakout": failed_breakout_gate,
        "liquidity_stress": liquidity_gate,
        "bear_continuation": bear_continuation_gate,
        "macro_riskoff": pd.Series(False, index=df.index),
    }

    event_masks: Dict[str, np.ndarray] = {}
    for name, score in scores.items():
        score_col = f"short_v2_{name}_score"
        mask_col = f"short_v2_ctx_{name}"
        df[score_col] = score.replace([np.inf, -np.inf], np.nan)
        structural_gate = structural_gates.get(name, pd.Series(True, index=df.index)).fillna(False).values
        event_masks[name] = (
            (df[score_col].values >= thresholds[name])
            & structural_gate
            & np.isfinite(df[score_col].values)
        )
        df[mask_col] = event_masks[name].astype(np.int8)

    context = np.full(n, "none", dtype=object)
    active = np.zeros(n, dtype=bool)
    for name in EVENT_CONTEXTS:
        mask = event_masks[name] & ~active
        context[mask] = name
        active |= event_masks[name]

    bull_trend = (ema_spread > 0.0) & (mom72 > 0.0) & (rsi > 50.0)
    true_stress_exception = event_masks["failed_breakout"] | event_masks["liquidity_stress"]
    no_short = (
        (squeeze > SHORT_V2_SQUEEZE_GATE)
        | (late_risk > 0.75)
        | (bull_trend & ~true_stress_exception)
    )

    df[CONTEXT_COL] = context
    df[ACTIVE_COL] = active.astype(np.int8)
    df[NO_SHORT_COL] = no_short.astype(np.int8)
    df.attrs["short_v2_event_thresholds"] = {
        k: round(v, 6) for k, v in thresholds.items()
    }
    return df


def build_short_v2_event_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    *,
    cost_stress: float = SHORT_V2_STRESS_COST,
    horizon: int = SHORT_V2_HORIZON_BARS,
    take_profit: float | None = None,
    stop_loss: float | None = None,
) -> pd.DataFrame:
    """
    Build y_short_v2_event with train-only event thresholds.

    Label meanings:
      1: event present, no hard block, short net return survives stress cost
      0: no event or event failure
     -1: invalid or ambiguous outcome
    """
    df = compute_short_v2_event_contexts(df, train_mask)
    close = pd.to_numeric(
        df["Close"] if "Close" in df.columns else df["close"],
        errors="coerce",
    )
    high = pd.to_numeric(df["High"] if "High" in df.columns else df["high"], errors="coerce")
    low = pd.to_numeric(df["Low"] if "Low" in df.columns else df["low"], errors="coerce")
    if take_profit is None or stop_loss is None:
        tp_default, sl_default = short_v2_exit_params(horizon)
        take_profit = tp_default if take_profit is None else take_profit
        stop_loss = sl_default if stop_loss is None else stop_loss
    ret, mfe, mae = _forward_short_outcome(
        close,
        high,
        low,
        horizon,
        take_profit=float(take_profit),
        stop_loss=float(stop_loss),
    )

    df[RET_COL] = ret
    df[MFE_COL] = mfe
    df[MAE_COL] = mae
    df["short_v2_net_ret_stress"] = ret - cost_stress
    df["short_v2_net_ret_normal"] = ret - SHORT_V2_NORMAL_COST
    horizon_cols = short_v2_horizon_columns(horizon)
    df[horizon_cols["ret"]] = ret
    df[horizon_cols["mfe"]] = mfe
    df[horizon_cols["mae"]] = mae
    df[horizon_cols["net_stress"]] = ret - cost_stress
    df[horizon_cols["net_normal"]] = ret - SHORT_V2_NORMAL_COST
    df.attrs["short_v2_horizon_bars"] = int(horizon)
    df.attrs["short_v2_take_profit"] = float(take_profit)
    df.attrs["short_v2_stop_loss"] = float(stop_loss)

    active = df[ACTIVE_COL].values.astype(bool)
    no_short = df[NO_SHORT_COL].values.astype(bool)
    squeeze_risk = _col(df, "squeeze_risk_score").fillna(0.0).values
    late_risk = _col(df, "short_late_entry_risk").fillna(0.0).values

    valid = np.isfinite(ret) & np.isfinite(mfe) & np.isfinite(mae)
    net = ret - cost_stress

    positive = (
        valid
        & active
        & ~no_short
        & (net > cost_stress)
        & (mfe > cost_stress * 2.0)
        & (mae < SHORT_V2_ENTRY_MAE_LIMIT)
        & (squeeze_risk <= SHORT_V2_SQUEEZE_GATE)
        & (late_risk <= SHORT_V2_LATE_ENTRY_GATE)
    )

    failed_event = (
        valid
        & active
        & (
            (net < 0.0)
            | (mae >= SHORT_V2_ENTRY_MAE_LIMIT)
            | no_short
            | (squeeze_risk > SHORT_V2_SQUEEZE_GATE)
        )
    )
    no_event = valid & ~active
    ambiguous = valid & active & ~positive & ~failed_event

    y = np.zeros(len(df), dtype=np.int8)
    y[positive] = 1
    y[failed_event | no_event] = 0
    y[ambiguous | ~valid] = -1

    df[LABEL_COL] = y
    df["short_v2_gray"] = (y == -1).astype(np.int8)
    return df


def audit_short_v2_event_labels(df: pd.DataFrame, path: Path | None = None) -> Dict:
    """Return and optionally persist label quality metrics."""
    if LABEL_COL not in df.columns:
        raise RuntimeError("build_short_v2_event_labels must be called first")

    y = df[LABEL_COL].values.astype(np.int8)
    active = df[ACTIVE_COL].values.astype(bool) if ACTIVE_COL in df.columns else np.zeros(len(df), bool)
    ret = df[RET_COL].values.astype(np.float64) if RET_COL in df.columns else np.full(len(df), np.nan)
    mae = df[MAE_COL].values.astype(np.float64) if MAE_COL in df.columns else np.full(len(df), np.nan)

    mean_ret_active = round(float(np.nanmean(ret[active])) if active.any() else 0.0, 6)
    mean_mae_active = round(float(np.nanmean(mae[active])) if active.any() else 0.0, 6)
    stats = {
        "n_total": int(len(df)),
        "positive_rate": round(float((y == 1).mean()), 6),
        "negative_rate": round(float((y == 0).mean()), 6),
        "gray_rate": round(float((y == -1).mean()), 6),
        "event_active_rate": round(float(active.mean()), 6),
        "mean_ret_active": mean_ret_active,
        "mean_mae_active": mean_mae_active,
        "mean_ret_8h_active": mean_ret_active,
        "mean_mae_8h_active": mean_mae_active,
        "stress_cost": SHORT_V2_STRESS_COST,
        "horizon_bars": int(df.attrs.get("short_v2_horizon_bars", SHORT_V2_HORIZON_BARS)),
        "take_profit": float(df.attrs.get("short_v2_take_profit", SHORT_V2_TAKE_PROFIT)),
        "stop_loss": float(df.attrs.get("short_v2_stop_loss", SHORT_V2_STOP_LOSS)),
        "contexts": {
            name: int((df[CONTEXT_COL].values == name).sum())
            for name in ("none", *EVENT_CONTEXTS)
            if CONTEXT_COL in df.columns
        },
    }

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, indent=2)

    return stats
