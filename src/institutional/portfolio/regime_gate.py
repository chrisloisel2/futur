"""
src/institutional/portfolio/regime_gate.py
─────────────────────────────────────────────────────────────────────────────
RegimeGate (Phase 39) — autorise/bloque les NOUVEAUX longs selon le régime BTC.

⚠️ CAUSAL UNIQUEMENT : régime calculé avec F_t (prix/vol/drawdown/momentum/EMA
passés). Aucun hindsight, aucune calibration sur le ROI futur, aucune règle
"2024 était bull donc je garde 2024".

Différence avec le Governor :
    RegimeGate = autorise l'OUVERTURE de longs (avant)
    Governor   = protège le portefeuille déjà ouvert (pendant)
    HedgeGov   = réduit le beta de l'exposition existante
    CarryGate  = autorise le carry selon le funding (indépendant du LongRegime)

Défaut : UNKNOWN → BLOCK_LONG. Le cash est l'état par défaut en régime hostile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

LONG_REGIMES = ("BULL", "RECOVERY", "NEUTRAL", "BEAR", "CRASH", "UNKNOWN")
PERMISSIONS = ("ALLOW_LONG", "REDUCE_LONG", "BLOCK_LONG")

PERMISSION_BY_REGIME = {
    "BULL": "ALLOW_LONG",
    "RECOVERY": "ALLOW_LONG",
    "NEUTRAL": "REDUCE_LONG",
    "BEAR": "BLOCK_LONG",
    "CRASH": "BLOCK_LONG",
    "UNKNOWN": "BLOCK_LONG",   # défaut prudent
}
PERMISSION_SIZE_MULT = {"ALLOW_LONG": 1.0, "REDUCE_LONG": 0.5, "BLOCK_LONG": 0.0}


@dataclass
class RegimeGateConfig:
    ema_fast: int = 50           # heures
    ema_slow: int = 200
    ret_30d_h: int = 720
    ret_14d_h: int = 336
    dd_window_h: int = 2160      # 90 jours
    dd_bear: float = 0.15        # drawdown 90j > 15% → bear/crash
    vol_window_h: int = 24
    vol_panic_q: float = 0.95    # vol au-dessus du 95e percentile glissant = panic
    crash_ret_3d: float = 0.15   # chute > 15% sur 3j → crash
    min_history_h: int = 2160


@dataclass
class RegimeGateDecision:
    timestamp: str
    btc_regime: str
    permission: str
    size_mult: float
    reason: str


class RegimeGate:
    def __init__(self, config: Optional[RegimeGateConfig] = None):
        self.config = config or RegimeGateConfig()
        self._regimes: Optional[pd.Series] = None

    def compute_regime_series(self, btc_close: pd.Series) -> pd.Series:
        """Série de régime BTC, 100% causale (trailing ewm/rolling/shift)."""
        cfg = self.config
        c = btc_close.sort_index().astype(float)
        ema_f = c.ewm(span=cfg.ema_fast, min_periods=cfg.ema_fast).mean()
        ema_s = c.ewm(span=cfg.ema_slow, min_periods=cfg.ema_slow).mean()
        ret30 = c / c.shift(cfg.ret_30d_h) - 1.0
        ret14 = c / c.shift(cfg.ret_14d_h) - 1.0
        ret3d = c / c.shift(72) - 1.0
        roll_max = c.rolling(cfg.dd_window_h, min_periods=cfg.ema_slow).max()
        dd90 = c / roll_max - 1.0
        logret = np.log(c / c.shift(1))
        rv = logret.rolling(cfg.vol_window_h, min_periods=cfg.vol_window_h).std()
        rv_thresh = rv.rolling(cfg.dd_window_h, min_periods=cfg.vol_window_h).quantile(cfg.vol_panic_q)
        panic = rv > rv_thresh

        regimes = pd.Series("UNKNOWN", index=c.index, dtype=object)
        enough = c.shift(cfg.min_history_h).notna()

        is_crash = (ret3d <= -cfg.crash_ret_3d) | (panic & (dd90 <= -cfg.dd_bear))
        is_bear = (c < ema_s) & (ema_f < ema_s) & (ret30 < 0) & (dd90 <= -cfg.dd_bear)
        is_bull = (c > ema_s) & (ema_f > ema_s) & (ret30 > 0) & (dd90 > -cfg.dd_bear) & (~panic)
        is_recovery = (c > ema_f) & (ret14 > 0) & (dd90 > -cfg.dd_bear) & (~is_bull) & (~is_crash)

        regimes[enough & is_crash] = "CRASH"
        regimes[enough & is_bear & (regimes == "UNKNOWN")] = "BEAR"
        regimes[enough & is_bull & (regimes == "UNKNOWN")] = "BULL"
        regimes[enough & is_recovery & (regimes == "UNKNOWN")] = "RECOVERY"
        regimes[enough & (regimes == "UNKNOWN")] = "NEUTRAL"
        self._regimes = regimes
        return regimes

    def decide_at(self, ts: pd.Timestamp) -> RegimeGateDecision:
        if self._regimes is None:
            return RegimeGateDecision(str(ts), "UNKNOWN", "BLOCK_LONG", 0.0, "no_regime_series")
        i = self._regimes.index.searchsorted(ts, side="right") - 1
        regime = str(self._regimes.iloc[i]) if i >= 0 else "UNKNOWN"
        perm = PERMISSION_BY_REGIME[regime]
        return RegimeGateDecision(str(ts), regime, perm, PERMISSION_SIZE_MULT[perm],
                                  f"btc_regime={regime}")
