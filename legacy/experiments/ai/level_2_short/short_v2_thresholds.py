"""
Stress-first threshold calibration for SHORT v2.

Thresholds are selected on validation net PnL under 15 bps fees plus
slippage x2. Normal-cost metrics are reported, but never used as the primary
selection objective.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from ai.level_0.short_event_labels_v2 import (
    ACTIVE_COL,
    CONTEXT_COL,
    MAE_COL,
    MFE_COL,
    NO_SHORT_COL,
    RET_COL,
    SHORT_V2_BASE_SLIPPAGE,
    SHORT_V2_NORMAL_COST,
    SHORT_V2_SQUEEZE_LIMIT,
    SHORT_V2_STRESS_FEE,
)


SHORT_V2_STRESS_COST = SHORT_V2_STRESS_FEE + 2.0 * SHORT_V2_BASE_SLIPPAGE
SHORT_V2_EXTREME_COST = 0.0020 + 3.0 * SHORT_V2_BASE_SLIPPAGE

MIN_TRADES_PER_CONTEXT = 10
MAX_TRADES_FRAC_PER_CONTEXT = 0.04
MIN_STRESS_PF_FOR_THRESHOLD = 1.30
MAX_SQUEEZE_RATE_FOR_THRESHOLD = 0.12

FOLD_OK_STRESS_PF = 1.30
FOLD_CATASTROPHIC_STRESS_PF = 0.75
FOLD_MAX_DD_PCT = 8.0
FOLD_MAX_SQUEEZE_RATE = 0.25


def _pf(net: np.ndarray) -> float:
    wins = net[net > 0.0]
    losses = net[net <= 0.0]
    return float(wins.sum()) / max(float(abs(losses.sum())), 1e-12)


def _max_dd_pct(net: np.ndarray, position_pct: float = 0.10) -> float:
    if len(net) == 0:
        return 0.0
    equity = np.cumprod(1.0 + net * position_pct)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.clip(peak, 1e-12, None)
    return float(np.nanmax(dd) * 100.0)


def evaluate_short_v2_trades(
    ret: np.ndarray,
    signal_mask: np.ndarray,
    *,
    mae: Optional[np.ndarray] = None,
    cost: float = SHORT_V2_STRESS_COST,
    position_pct: float = 0.10,
    group_id: Optional[np.ndarray] = None,
) -> Dict:
    """Evaluate selected SHORT trades. ret is positive when the short wins.

    If group_id is provided, PnL metrics are computed after splitting one
    portfolio-level short allocation equally across trades sharing a group
    (typically the same timestamp). n_trades remains the raw signal count.
    """
    ret_arr = np.asarray(ret, dtype=np.float64)
    signal_mask = np.asarray(signal_mask, dtype=bool) & np.isfinite(ret_arr)
    selected = ret_arr[signal_mask]
    if len(selected) == 0:
        return {
            "n_trades": 0,
            "n_clusters": 0,
            "pf": 0.0,
            "expectancy": 0.0,
            "wr": 0.0,
            "trade_wr": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_drawdown": 0.0,
            "squeeze_rate": 0.0,
            "total_return_pct": 0.0,
        }

    trade_net = selected - cost
    if group_id is not None:
        groups = np.asarray(group_id, dtype=object)[signal_mask]
        net = (
            pd.DataFrame({"group": groups, "net": trade_net})
            .groupby("group", sort=True)["net"]
            .mean()
            .values.astype(np.float64)
        )
    else:
        net = trade_net
    wins = net[net > 0.0]
    losses = net[net <= 0.0]
    squeeze_rate = 0.0
    if mae is not None:
        selected_mae = np.asarray(mae, dtype=np.float64)[signal_mask]
        selected_mae = selected_mae[np.isfinite(selected_mae)]
        if len(selected_mae) > 0:
            squeeze_rate = float((selected_mae >= SHORT_V2_SQUEEZE_LIMIT).mean())

    equity = np.cumprod(1.0 + net * position_pct)
    return {
        "n_trades": int(len(trade_net)),
        "n_clusters": int(len(net)),
        "pf": round(_pf(net), 4),
        "expectancy": round(float(net.mean()), 6),
        "wr": round(float((net > 0.0).mean()), 4),
        "trade_wr": round(float((trade_net > 0.0).mean()), 4),
        "avg_win": round(float(wins.mean()) if len(wins) else 0.0, 6),
        "avg_loss": round(float(losses.mean()) if len(losses) else 0.0, 6),
        "max_drawdown": round(_max_dd_pct(net, position_pct=position_pct), 4),
        "squeeze_rate": round(squeeze_rate, 4),
        "total_return_pct": round(float((equity[-1] - 1.0) * 100.0), 4),
    }


def _score(metrics_stress: Dict) -> float:
    n = max(metrics_stress["n_trades"], 1)
    pf = max(metrics_stress["pf"], 1e-6)
    exp = metrics_stress["expectancy"]
    dd = metrics_stress["max_drawdown"]
    squeeze = metrics_stress["squeeze_rate"]
    return (
        exp * math.sqrt(n)
        + 0.70 * math.log(pf)
        - 0.08 * dd
        - 0.70 * squeeze
    )


def calibrate_short_v2_thresholds(
    df_val: pd.DataFrame,
    p_short: np.ndarray,
    *,
    context_col: str = CONTEXT_COL,
    ret_col: str = RET_COL,
    active_col: str = ACTIVE_COL,
    no_short_col: str = NO_SHORT_COL,
    mae_col: str = MAE_COL,
    threshold_grid: Iterable[float] | None = None,
) -> Dict[str, Dict]:
    """
    Calibrate context thresholds on validation net PnL under stress costs.
    """
    if threshold_grid is None:
        threshold_grid = np.arange(0.50, 0.991, 0.01)

    p_short = np.asarray(p_short, dtype=np.float64)
    if len(p_short) != len(df_val):
        raise ValueError("p_short length must match df_val")
    if ret_col not in df_val.columns:
        raise KeyError(f"missing return column: {ret_col}")

    ctx = (
        df_val[context_col].astype(str).values
        if context_col in df_val.columns
        else np.full(len(df_val), "general", dtype=object)
    )
    active = (
        df_val[active_col].values.astype(bool)
        if active_col in df_val.columns
        else np.ones(len(df_val), dtype=bool)
    )
    no_short = (
        df_val[no_short_col].values.astype(bool)
        if no_short_col in df_val.columns
        else np.zeros(len(df_val), dtype=bool)
    )
    ret = df_val[ret_col].values.astype(np.float64)
    mae = df_val[mae_col].values.astype(np.float64) if mae_col in df_val.columns else None
    group_id = None
    if "datetime" in df_val.columns:
        group_id = pd.to_datetime(df_val["datetime"], utc=True).dt.floor("h").astype(str).values

    contexts = sorted(c for c in set(ctx.tolist()) if c != "none")
    result: Dict[str, Dict] = {}

    for context in contexts:
        base = (ctx == context) & active & ~no_short & np.isfinite(ret) & np.isfinite(p_short)
        n_context = int(base.sum())
        if n_context < MIN_TRADES_PER_CONTEXT:
            result[context] = _disabled("not_enough_validation_events", n_context)
            continue

        best: Dict | None = None
        best_score = -np.inf
        for thr_raw in threshold_grid:
            thr = round(float(thr_raw), 2)
            signal = base & (p_short >= thr)
            n_signal = int(signal.sum())
            if n_signal < MIN_TRADES_PER_CONTEXT:
                continue
            if n_signal > max(MIN_TRADES_PER_CONTEXT, int(n_context * MAX_TRADES_FRAC_PER_CONTEXT)):
                continue

            stress = evaluate_short_v2_trades(
                ret, signal, mae=mae, cost=SHORT_V2_STRESS_COST, group_id=group_id
            )
            if stress["pf"] < MIN_STRESS_PF_FOR_THRESHOLD:
                continue
            if stress["expectancy"] <= 0.0:
                continue
            if stress["squeeze_rate"] > MAX_SQUEEZE_RATE_FOR_THRESHOLD:
                continue

            normal = evaluate_short_v2_trades(
                ret, signal, mae=mae, cost=SHORT_V2_NORMAL_COST, group_id=group_id
            )
            extreme = evaluate_short_v2_trades(
                ret, signal, mae=mae, cost=SHORT_V2_EXTREME_COST, group_id=group_id
            )
            if extreme["pf"] < 1.05 or extreme["expectancy"] <= 0.0:
                continue
            score = _score(stress)

            candidate = {
                "enabled": True,
                "threshold": thr,
                "optimized_for": "net_pnl_stress_15bps_slippage_x2",
                "n_val_events": n_context,
                "n_val_trades": stress["n_trades"],
                "n_val_clusters": stress["n_clusters"],
                "pf_normal": normal["pf"],
                "pf_stress": stress["pf"],
                "pf_extreme": extreme["pf"],
                "expectancy_stress": stress["expectancy"],
                "wr_stress": stress["wr"],
                "max_drawdown": stress["max_drawdown"],
                "squeeze_rate": stress["squeeze_rate"],
                "score": round(score, 6),
            }

            if score > best_score:
                best_score = score
                best = candidate

        result[context] = best if best is not None else _disabled(
            "no_threshold_survived_stress_objective",
            n_context,
        )

    if not result:
        result["general"] = _disabled("no_contexts_available", 0)

    return result


def _disabled(reason: str, n_events: int) -> Dict:
    return {
        "enabled": False,
        "threshold": None,
        "optimized_for": "net_pnl_stress_15bps_slippage_x2",
        "n_val_events": int(n_events),
        "n_val_trades": 0,
        "n_val_clusters": 0,
        "pf_normal": None,
        "pf_stress": None,
        "pf_extreme": None,
        "expectancy_stress": None,
        "wr_stress": None,
        "max_drawdown": None,
        "squeeze_rate": None,
        "score": None,
        "reason": reason,
    }


def threshold_for_context(thresholds: Dict[str, Dict], context: str) -> Optional[float]:
    entry = thresholds.get(context) or thresholds.get("general")
    if not entry or not entry.get("enabled"):
        return None
    threshold = entry.get("threshold")
    return float(threshold) if threshold is not None else None


def collect_short_v2_trades(
    df_test: pd.DataFrame,
    p_short: np.ndarray,
    thresholds: Dict[str, Dict],
    *,
    cost: float = SHORT_V2_STRESS_COST,
    horizon: int | None = None,
) -> list[dict]:
    """Return the individual test trades selected by v2 thresholds."""
    if len(df_test) != len(p_short):
        raise ValueError("p_short length must match df_test")

    ctx = (
        df_test[CONTEXT_COL].astype(str).values
        if CONTEXT_COL in df_test.columns
        else np.full(len(df_test), "general", dtype=object)
    )
    active = (
        df_test[ACTIVE_COL].values.astype(bool)
        if ACTIVE_COL in df_test.columns
        else np.ones(len(df_test), dtype=bool)
    )
    no_short = (
        df_test[NO_SHORT_COL].values.astype(bool)
        if NO_SHORT_COL in df_test.columns
        else np.zeros(len(df_test), dtype=bool)
    )
    ret = df_test[RET_COL].values.astype(np.float64)
    mae = df_test[MAE_COL].values.astype(np.float64) if MAE_COL in df_test.columns else np.full(len(df_test), np.nan)
    mfe = df_test[MFE_COL].values.astype(np.float64) if MFE_COL in df_test.columns else np.full(len(df_test), np.nan)
    dt = pd.to_datetime(df_test["datetime"], utc=True) if "datetime" in df_test.columns else pd.Series(pd.NaT, index=df_test.index)
    sym = df_test["symbol"].astype(str).values if "symbol" in df_test.columns else np.full(len(df_test), "", dtype=object)

    trades: list[dict] = []
    for i, context in enumerate(ctx):
        if not active[i] or no_short[i] or context == "none" or not np.isfinite(ret[i]):
            continue
        threshold = threshold_for_context(thresholds, context)
        if threshold is None or p_short[i] < threshold:
            continue
        net_ret = float(ret[i] - cost)
        trades.append(
            {
                "datetime": dt.iloc[i].isoformat() if not pd.isna(dt.iloc[i]) else None,
                "symbol": str(sym[i]),
                "horizon": int(horizon) if horizon is not None else None,
                "context": str(context),
                "p_short": round(float(p_short[i]), 6),
                "threshold": round(float(threshold), 4),
                "gross_ret": round(float(ret[i]), 8),
                "net_ret": round(net_ret, 8),
                "mfe": round(float(mfe[i]), 8) if np.isfinite(mfe[i]) else None,
                "mae": round(float(mae[i]), 8) if np.isfinite(mae[i]) else None,
                "cost": round(float(cost), 8),
            }
        )
    return trades


def backtest_short_v2_thresholds(
    df_test: pd.DataFrame,
    p_short: np.ndarray,
    thresholds: Dict[str, Dict],
    *,
    cost: float = SHORT_V2_STRESS_COST,
    position_pct: float = 0.10,
) -> Dict:
    """Backtest a calibrated SHORT v2 signal on test data."""
    if len(df_test) != len(p_short):
        raise ValueError("p_short length must match df_test")

    ctx = (
        df_test[CONTEXT_COL].astype(str).values
        if CONTEXT_COL in df_test.columns
        else np.full(len(df_test), "general", dtype=object)
    )
    active = (
        df_test[ACTIVE_COL].values.astype(bool)
        if ACTIVE_COL in df_test.columns
        else np.ones(len(df_test), dtype=bool)
    )
    no_short = (
        df_test[NO_SHORT_COL].values.astype(bool)
        if NO_SHORT_COL in df_test.columns
        else np.zeros(len(df_test), dtype=bool)
    )
    ret = df_test[RET_COL].values.astype(np.float64)
    mae = df_test[MAE_COL].values.astype(np.float64) if MAE_COL in df_test.columns else None
    group_id = None
    if "datetime" in df_test.columns:
        group_id = pd.to_datetime(df_test["datetime"], utc=True).dt.floor("h").astype(str).values

    signal = np.zeros(len(df_test), dtype=bool)
    for i, context in enumerate(ctx):
        if not active[i] or no_short[i] or context == "none":
            continue
        thr = threshold_for_context(thresholds, context)
        if thr is not None and p_short[i] >= thr:
            signal[i] = True

    metrics = evaluate_short_v2_trades(
        ret,
        signal,
        mae=mae,
        cost=cost,
        position_pct=position_pct,
        group_id=group_id,
    )
    metrics["event_active_rate"] = round(float(active.mean()), 4)
    metrics["gate_blocked_rate"] = round(float(no_short.mean()), 4)
    metrics["deployment_mode"] = "hedge_only"
    metrics["max_short_exposure"] = min(0.10, position_pct)
    return metrics


def classify_short_v2_fold(metrics_stress: Dict) -> Dict:
    """Classify one fold using stress metrics only."""
    n = metrics_stress.get("n_trades", 0)
    pf = metrics_stress.get("pf", 0.0)
    exp = metrics_stress.get("expectancy", 0.0)
    dd = metrics_stress.get("max_drawdown", 0.0)
    squeeze = metrics_stress.get("squeeze_rate", 0.0)

    ok = (
        n >= MIN_TRADES_PER_CONTEXT
        and pf >= FOLD_OK_STRESS_PF
        and exp > 0.0
        and dd <= FOLD_MAX_DD_PCT
        and squeeze <= FOLD_MAX_SQUEEZE_RATE
    )
    catastrophic = (
        n >= MIN_TRADES_PER_CONTEXT
        and (pf < FOLD_CATASTROPHIC_STRESS_PF or dd > FOLD_MAX_DD_PCT or squeeze > 0.50)
    )
    status = "OK" if ok else "CATASTROPHIC" if catastrophic else "WEAK"
    return {"fold_ok": ok, "fold_catastrophic": catastrophic, "fold_status": status}


def save_thresholds(thresholds: Dict[str, Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(thresholds, fh, indent=2)
