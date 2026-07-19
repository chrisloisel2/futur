"""
src/institutional/engines/exit_engine/labels.py
─────────────────────────────────────────────────────────────────────────────
Spécification des features et du label de sortie (réutilise ai/level_0/exit_labels).

Label V1 :
    exit_now = 1 si sortir à t donne un meilleur résultat NET que tenir jusqu'à
    l'horizon prévu ; 0 sinon.

Les features marché sont dupliquées ici (source de vérité = ai.level_0.exit_labels)
pour éviter une dépendance d'import au chargement du moteur.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

EXIT_POSITION_FEATURES: List[str] = [
    "bars_held", "bars_remaining", "bars_frac", "unrealized_ret", "unrealized_ret_bps",
    "max_ret_so_far", "min_ret_so_far", "drawdown_from_peak", "recovery_from_trough",
    "is_profitable", "pnl_velocity_1", "pnl_velocity_3", "pnl_normalized",
    "entry_rsi", "entry_adx", "entry_trend_score", "entry_momentum_score",
    "entry_close_position_in_range",
]

EXIT_MARKET_FEATURES: List[str] = [
    "return_5", "return_10", "log_return_5", "log_return_10", "realized_vol_20",
    "atr_pct_20", "bb_width_20", "bb_percent_b_20", "close_position_in_range",
    "body_to_range", "high_low_range_pct", "distance_ema_20", "distance_ema_50",
    "ema_21_50_spread", "ema_slope_20", "macd_hist", "macd_hist_slope", "rsi_13",
    "rsi_20", "stoch_k_20", "adx_20", "di_spread_20", "choppiness_20",
    "efficiency_ratio_20", "volume_ratio_20", "cmf_20", "obv_slope_20", "trend_score",
    "momentum_score", "volatility_score", "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "return_50", "distance_ema_200", "regression_slope_50", "donchian_position_20",
    "di_plus_20", "mtf_4h_adx_20", "mtf_4h_rsi_10", "mtf_4h_return_5",
    "mtf_1d_return_5", "mtf_1d_rsi_5",
]


def label_exit_now(
    close: pd.Series,
    entry_idx: int,
    horizon: int,
    cost: float = 0.001,
) -> int:
    """1 si sortir à entry_idx bat le hold jusqu'à entry_idx+horizon (net de coût)."""
    if entry_idx + horizon >= len(close):
        return 0
    now_ret = -cost  # sortir maintenant = réaliser le PnL courant (référence 0)
    hold_ret = float(close.iloc[entry_idx + horizon] / close.iloc[entry_idx] - 1.0) - cost
    return int(now_ret > hold_ret)
