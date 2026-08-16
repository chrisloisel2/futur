from __future__ import annotations

from typing import Dict, Iterable, Sequence

import numpy as np
import pandas as pd


def _future_shift(series: pd.Series, steps: int) -> pd.Series:
    if steps <= 0:
        raise ValueError("steps must be positive")
    return series.shift(-steps)


def future_log_return(price: pd.Series, steps: int) -> pd.Series:
    p = pd.to_numeric(price, errors="coerce")
    future = _future_shift(p, steps)
    return 1e4 * np.log(future / p)


def leave_one_venue_out_fair_value(frame: pd.DataFrame, excluded_venue: str, venues: Sequence[str]) -> pd.Series:
    numerator = pd.Series(0.0, index=frame.index, dtype=float)
    denominator = pd.Series(0.0, index=frame.index, dtype=float)
    for venue_raw in venues:
        venue = str(venue_raw).lower()
        if venue == str(excluded_venue).lower():
            continue
        mid_col = venue + "__price_mid"
        weight_col = venue + "__price_weight"
        if mid_col not in frame:
            continue
        mid = pd.to_numeric(frame[mid_col], errors="coerce")
        if weight_col in frame:
            weight = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
        else:
            weight = pd.Series(1.0, index=frame.index, dtype=float)
        valid = mid.notna() & (mid > 0) & weight.notna() & (weight > 0)
        numerator = numerator + mid.where(valid, 0.0) * weight.where(valid, 0.0)
        denominator = denominator + weight.where(valid, 0.0)
    return numerator / denominator.where(denominator > 0)


def next_mid_move(mid: pd.Series) -> pd.Series:
    values = pd.to_numeric(mid, errors="coerce")
    out = pd.Series(np.nan, index=values.index, dtype=float)
    arr = values.to_numpy(dtype=float)
    n = len(arr)
    for i in range(n - 1):
        if not np.isfinite(arr[i]):
            continue
        j = i + 1
        while j < n and np.isfinite(arr[j]) and arr[j] == arr[i]:
            j += 1
        if j >= n or not np.isfinite(arr[j]):
            continue
        out.iloc[i] = float(np.sign(arr[j] - arr[i]))
    return out


def time_to_next_mid_move_ms(mid: pd.Series, asof_ns: pd.Series) -> pd.Series:
    values = pd.to_numeric(mid, errors="coerce").to_numpy(dtype=float)
    ts = pd.to_numeric(asof_ns, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(len(values) - 1):
        if not np.isfinite(values[i]) or not np.isfinite(ts[i]):
            continue
        j = i + 1
        while j < len(values) and np.isfinite(values[j]) and values[j] == values[i]:
            j += 1
        if j < len(values) and np.isfinite(values[j]) and np.isfinite(ts[j]):
            out[i] = (ts[j] - ts[i]) / 1e6
    return pd.Series(out, index=mid.index, dtype=float)


def realized_volatility(return_bps: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        raise ValueError("window must exceed 1")
    r = pd.to_numeric(return_bps, errors="coerce")
    return r.rolling(window, min_periods=window).std(ddof=0)


def build_standard_targets(frame: pd.DataFrame, cadence_ms: int, horizons_ms: Iterable[int], fair_value_col: str = "price_fair_value") -> Dict[str, pd.Series]:
    if cadence_ms <= 0:
        raise ValueError("cadence_ms must be positive")
    out: Dict[str, pd.Series] = {}
    for horizon in horizons_ms:
        horizon = int(horizon)
        if horizon <= 0 or horizon % cadence_ms != 0:
            raise ValueError("horizon must be a positive multiple of cadence_ms")
        steps = horizon // cadence_ms
        out["future_fair_value_return_%sms" % horizon] = future_log_return(frame[fair_value_col], steps)
    if "price_mid" in frame:
        out["next_mid_move"] = next_mid_move(frame["price_mid"])
        if "asof_ns" in frame:
            out["time_to_next_mid_move_ms"] = time_to_next_mid_move_ms(frame["price_mid"], frame["asof_ns"])
    return out
