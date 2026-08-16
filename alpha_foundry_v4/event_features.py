from __future__ import annotations

import math
from typing import Dict

import numpy as np
import pandas as pd


def _entropy(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    x = x[x > 0]
    if len(x) == 0:
        return float("nan")
    weights = x / x.sum()
    return float(-(weights * np.log(weights)).sum())


def aggregate_trade_window(trades: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {"trade_count": 0.0, "gross_notional": 0.0, "signed_notional": 0.0, "flow_imbalance": float("nan"), "large_trade_fraction": float("nan"), "trade_size_entropy": float("nan"), "trades_per_second": 0.0, "interarrival_cv": float("nan")}
    notional = pd.to_numeric(trades["notional"], errors="coerce").fillna(0.0).abs()
    side = pd.to_numeric(trades["side_sign"], errors="coerce").fillna(0.0).clip(-1.0, 1.0)
    gross = float(notional.sum())
    signed = float((notional * side).sum())
    threshold = float(notional.quantile(0.90)) if len(notional) else float("nan")
    large_fraction = float((notional >= threshold).mean()) if np.isfinite(threshold) else float("nan")
    tps = 0.0
    interarrival_cv = float("nan")
    if "receive_ts_ns" in trades and len(trades) >= 2:
        ts = np.sort(pd.to_numeric(trades["receive_ts_ns"], errors="coerce").dropna().to_numpy(dtype=float))
        if len(ts) >= 2:
            duration = max((ts[-1] - ts[0]) / 1e9, 1e-9)
            tps = float(len(ts) / duration)
            dt = np.diff(ts) / 1e9
            mean_dt = float(np.mean(dt))
            if mean_dt > 0:
                interarrival_cv = float(np.std(dt) / mean_dt)
    return {"trade_count": float(len(trades)), "gross_notional": gross, "signed_notional": signed, "flow_imbalance": float(signed / gross) if gross > 0 else float("nan"), "large_trade_fraction": large_fraction, "trade_size_entropy": _entropy(notional), "trades_per_second": tps, "interarrival_cv": interarrival_cv}


def flow_dynamics(signed_flow: pd.Series, lag: int = 1) -> pd.DataFrame:
    if lag <= 0:
        raise ValueError("lag must be positive")
    flow = pd.to_numeric(signed_flow, errors="coerce")
    acceleration = flow.diff(lag)
    jerk = acceleration.diff(lag)
    return pd.DataFrame({"signed_flow": flow, "flow_acceleration": acceleration, "flow_jerk": jerk, "cvd": flow.fillna(0.0).cumsum()})


def absorption_score(signed_notional: pd.Series, price_change_bps: pd.Series, eps: float = 1e-9) -> pd.Series:
    flow = pd.to_numeric(signed_notional, errors="coerce").abs()
    impact = pd.to_numeric(price_change_bps, errors="coerce").abs()
    return flow / (impact + float(eps))


def queue_event_intensities(events: pd.DataFrame, duration_seconds: float) -> Dict[str, float]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if events.empty:
        return {"add_rate": 0.0, "remove_rate": 0.0, "cancel_rate": 0.0, "trade_rate": 0.0}
    kinds = events["event_type"].astype(str).str.lower()
    return {"add_rate": float(kinds.isin(["add", "snapshot_add"]).sum() / duration_seconds), "remove_rate": float(kinds.isin(["remove", "delete"]).sum() / duration_seconds), "cancel_rate": float(kinds.eq("cancel").sum() / duration_seconds), "trade_rate": float(kinds.isin(["trade", "execute", "execution"]).sum() / duration_seconds)}


def depletion_hazard(queue_size: float, cancel_rate: float, trade_rate: float, add_rate: float) -> float:
    q = max(float(queue_size), 0.0)
    if q <= 0:
        return 1.0
    outflow = max(float(cancel_rate), 0.0) + max(float(trade_rate), 0.0)
    inflow = max(float(add_rate), 0.0)
    net = max(outflow - inflow, 0.0)
    return float(1.0 - math.exp(-net / q))
