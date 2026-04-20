from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import numpy as np

def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    if fut_ret.size == 0:
        return 0.0, 0.0
    path = np.cumsum(fut_ret.astype(np.float64))
    R = float(path[-1])
    peak = np.maximum.accumulate(path)
    dd = float(np.max(peak - path)) if path.size else 0.0
    return R, dd

def rms_vol(fut_rv: np.ndarray) -> float:
    if fut_rv.size == 0:
        return 0.0
    z = fut_rv.astype(np.float64)
    return float(np.sqrt(np.mean(z*z)))

@dataclass(frozen=True)
class LabelCfg:
    eps: float = 1e-12
    # tradeability thresholds (set after train quantiles)
    thr_absR: float = 0.0
    thr_RV_hi: float = 0.0
    thr_DD_lo: float = 1e9

def compute_tradeable(R: float, RV: float, DD: float, cfg: LabelCfg) -> int:
    RV = max(float(RV), 1e-8)
    absR = abs(float(R))
    DD = max(float(DD), 0.0)
    ok = (absR >= cfg.thr_absR) and (RV >= cfg.thr_RV_hi) and (DD <= cfg.thr_DD_lo)
    return int(ok)

def compute_regime(
    lookback_close: np.ndarray,
    fut_ret: np.ndarray,
    fut_rv: np.ndarray,
    n_regimes: int = 4
) -> int:
    # 4 régimes simples, stables:
    # 0 squeeze   : low RV + small move
    # 1 breakout  : big move with trend continuation
    # 2 reversal  : big move against lookback trend
    # 3 impulse   : vol shock / high RV
    R, DD = future_path_stats(fut_ret)
    RV = rms_vol(fut_rv)

    # lookback trend sign
    if lookback_close.size >= 8:
        tr = float(lookback_close[-1] - lookback_close[0])
    else:
        tr = 0.0
    trend = 1.0 if tr > 0 else (-1.0 if tr < 0 else 0.0)

    score = R / (RV + 1e-12)
    abs_score = abs(score)

    # vol shock
    if RV >= np.quantile(np.abs(fut_rv), 0.9) if fut_rv.size else False:
        return 3 % n_regimes

    # squeeze
    if abs_score < 0.4:
        return 0 % n_regimes

    # breakout vs reversal
    move_sign = 1.0 if R > 0 else (-1.0 if R < 0 else 0.0)
    if trend != 0.0 and move_sign == trend:
        return 1 % n_regimes
    return 2 % n_regimes
