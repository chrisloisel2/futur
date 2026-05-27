"""
ai — Pipeline ML Trading
========================
Modules actifs : level_0 (features/labels), level_2 (TRM Fleet LONG), meta, regime.
SHORT désactivé. Level_1/3/7 archivés.
"""
from ai import level_0, level_2
from ai import level_0 as data
from ai import level_2 as edge

from ai.level_0 import (
    HORIZON_BARS, COST_PCT, TARGET_COL, CLOSE_COL,
    FEATURES_COMMON, FEATURES_LONG,
    build_labels, compute_regime_col, compute_long_regime_col,
    chronological_split, get_X, fit_scaler, load_csv,
    compute_long_features,
)

__all__ = [
    "data", "edge", "level_0", "level_2",
    "HORIZON_BARS", "COST_PCT", "TARGET_COL", "CLOSE_COL",
    "FEATURES_COMMON", "FEATURES_LONG",
    "build_labels", "compute_regime_col", "compute_long_regime_col",
    "chronological_split", "get_X", "fit_scaler", "load_csv",
    "compute_long_features",
]
