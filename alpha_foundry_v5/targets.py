from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np
import pandas as pd


def forward_log_return(price: pd.Series, steps: int) -> pd.Series:
    if int(steps) <= 0:
        raise ValueError("steps must be positive")
    p = pd.to_numeric(price, errors="coerce")
    return 1e4 * np.log(p.shift(-int(steps)) / p)


def future_realized_vol(price: pd.Series, steps: int) -> pd.Series:
    p = pd.to_numeric(price, errors="coerce")
    r = np.log(p / p.shift(1))
    return 1e4 * r.shift(-1).rolling(int(steps), min_periods=int(steps)).std(ddof=1).shift(-(int(steps) - 1))


def leave_one_venue_out_fair_value(frame: pd.DataFrame, excluded_venue: str, venues: Sequence[str]) -> pd.Series:
    num = pd.Series(0.0, index=frame.index, dtype=float)
    den = pd.Series(0.0, index=frame.index, dtype=float)
    for venue in venues:
        v = str(venue).lower()
        if v == str(excluded_venue).lower():
            continue
        mid_col = v + "__price_mid"
        weight_col = v + "__price_weight"
        if mid_col not in frame:
            continue
        mid = pd.to_numeric(frame[mid_col], errors="coerce")
        weight = pd.to_numeric(frame[weight_col], errors="coerce").clip(lower=0.0) if weight_col in frame else pd.Series(1.0, index=frame.index)
        good = mid.notna() & weight.notna() & np.isfinite(mid) & np.isfinite(weight) & (mid > 0) & (weight > 0)
        num = num + mid.where(good, 0.0) * weight.where(good, 0.0)
        den = den + weight.where(good, 0.0)
    return num / den.where(den > 0)


def next_mid_move(mid: pd.Series, max_steps: int = 1000) -> Tuple[pd.Series, pd.Series]:
    values = pd.to_numeric(mid, errors="coerce").to_numpy(dtype=float)
    direction = np.full(len(values), np.nan, dtype=float)
    time_steps = np.full(len(values), np.nan, dtype=float)
    for i in range(len(values)):
        if not np.isfinite(values[i]):
            continue
        stop = min(len(values), i + 1 + int(max_steps))
        future = values[i + 1:stop]
        diff = future - values[i]
        changed = np.flatnonzero(np.isfinite(diff) & (np.abs(diff) > 1e-15))
        if len(changed):
            j = int(changed[0])
            direction[i] = 1.0 if diff[j] > 0 else -1.0
            time_steps[i] = float(j + 1)
    return pd.Series(direction, index=mid.index), pd.Series(time_steps, index=mid.index)


def basis_convergence_target(basis_bps: pd.Series, steps: int) -> pd.Series:
    b = pd.to_numeric(basis_bps, errors="coerce")
    return -(b.shift(-int(steps)) - b)


def make_target(frame: pd.DataFrame, name: str, horizon_steps: int, **kwargs) -> pd.Series:
    if name == "fair_value_return":
        return forward_log_return(frame[kwargs.get("price_col", "price_fair_value")], horizon_steps)
    if name == "future_realized_vol":
        return future_realized_vol(frame[kwargs.get("price_col", "price_fair_value")], horizon_steps)
    if name == "basis_convergence":
        return basis_convergence_target(frame[kwargs.get("basis_col", "basis_bps")], horizon_steps)
    if name == "loo_fair_value_return":
        fv = leave_one_venue_out_fair_value(frame, kwargs["excluded_venue"], kwargs["venues"])
        return forward_log_return(fv, horizon_steps)
    raise KeyError("unknown target: %s" % name)
