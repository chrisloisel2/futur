from __future__ import annotations
from typing import Iterator, List, Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd

from .io_s3 import read_year_df
from .labels import future_path_stats, rms_vol, compute_regime

def compute_future_arrays(ret: np.ndarray, rv: np.ndarray, i: int, L: int, H: int):
    fut_ret = ret[i+L:i+L+H]
    fut_rv  = rv[i+L:i+L+H]
    return fut_ret.astype(np.float32, copy=False), fut_rv.astype(np.float32, copy=False)

def iter_windows_common(
    base: str, symbol: str, quote: str, interval: str, years: List[int],
    feature_keys: List[str],
    ret_key: str, rv_key: str, close_key: str,
    lookback: int, horizon: int,
    scaler,  # RobustScaler fitted
    start: int, end: int,
    stride: int = 1,
    n_regimes: int = 4,
    score_cap: float = 50.0,
    mag_cap: float = 8.0,
) -> Iterator[Dict[str, Any]]:
    bridge = lookback + horizon
    tail = None
    gi = 0

    for y in years:
        cols = ["datetime"] + feature_keys + [ret_key, rv_key, close_key]
        df = read_year_df(base, symbol, quote, interval, y, cols)
        if tail is not None:
            df = pd.concat([tail, df], ignore_index=True)

        Xraw = df[feature_keys].values.astype(np.float32, copy=False)
        Xn = scaler.transform(Xraw)

        ret = df[ret_key].values.astype(np.float32, copy=False)
        rv  = df[rv_key].values.astype(np.float32, copy=False)
        close = df[close_key].values.astype(np.float32, copy=False)

        T = len(df)
        max_i = max(0, T - lookback - horizon)

        for i in range(0, max_i, stride):
            if gi >= end:
                return

            if gi >= start:
                Xw = Xn[i:i+lookback].astype(np.float32, copy=False)
                fut_ret, fut_rv = compute_future_arrays(ret, rv, i, lookback, horizon)

                R, DD = future_path_stats(fut_ret)
                RV = rms_vol(fut_rv)
                RV = max(RV, 1e-8)

                edge = float(np.clip(R / RV, -score_cap, score_cap))
                mag = float(min(abs(edge), mag_cap))

                regime = int(compute_regime(close[i:i+lookback], fut_ret, fut_rv, n_regimes=n_regimes))

                yield {
                    "Xw": Xw,                      # [L,F]
                    "edge": np.float32(edge),       # scalar
                    "RV": np.float32(RV),           # scalar
                    "DD": np.float32(DD),           # scalar
                    "R": np.float32(R),             # scalar
                    "mag": np.float32(mag),         # scalar
                    "regime": np.int32(regime),     # class
                }

            gi += 1

        tail = df.iloc[-bridge:].copy() if T >= bridge else df.copy()
