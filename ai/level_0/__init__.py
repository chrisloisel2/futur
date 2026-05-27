"""
level_0 — Features, Labels, Preprocessing (ACTIF)
==================================================
Modules actifs uniquement — SHORT et Filter désactivés (audit 2026-05).
"""
from ai.level_0.constants import (
    HORIZON_BARS, HORIZON_MINUTES, BAR_FREQUENCY,
    COST_PCT, COST_PCT_STRESS,
    TRADEABLE_QUANTILE_LONG, TRADEABLE_QUANTILE_SHORT,
    TRAIN_END_YEAR, VAL_YEAR, TEST_FROM_YEAR,
    TARGET_COL, CLOSE_COL, DATETIME_COL,
    REGIME_COL, REGIME_COL_LONG,
    FILTER_BETA_LONG, FILTER_BETA_SHORT,
    PNL_COST_MULT,
    assert_horizon,
)

from ai.level_0.features import (
    FEATURES_COMMON,
    FEATURES_MACRO_FILTER,
    FEATURES_LONG,
    FEATURES_SHORT,
    FEATURES_FILTER,
    FEATURES_REGIME,
    FEATURES_LONG_EXTRA,
    FEATURES_SHORT_EXTRA,
    validate_features,
    get_feature_overlap,
)

from ai.level_0.labels import (
    build_labels,
    build_pnl_labels,
    build_bear_regime_label,
    compute_regime_col,
    compute_long_regime_col,
    get_train_labels,
)

from ai.level_0.preprocessing import (
    chronological_split,
    get_X,
    fit_scaler,
    load_csv,
)

from ai.level_0.feature_engineering import (
    compute_long_features,
    compute_macro_cross_features,
)

__all__ = [
    "HORIZON_BARS", "HORIZON_MINUTES", "BAR_FREQUENCY",
    "COST_PCT", "COST_PCT_STRESS",
    "TRADEABLE_QUANTILE_LONG", "TRADEABLE_QUANTILE_SHORT",
    "TRAIN_END_YEAR", "VAL_YEAR", "TEST_FROM_YEAR",
    "TARGET_COL", "CLOSE_COL", "DATETIME_COL",
    "REGIME_COL", "REGIME_COL_LONG",
    "FILTER_BETA_LONG", "FILTER_BETA_SHORT",
    "PNL_COST_MULT", "assert_horizon",
    "FEATURES_COMMON", "FEATURES_MACRO_FILTER",
    "FEATURES_LONG", "FEATURES_SHORT",
    "FEATURES_FILTER", "FEATURES_REGIME",
    "FEATURES_LONG_EXTRA", "FEATURES_SHORT_EXTRA",
    "validate_features", "get_feature_overlap",
    "build_labels", "build_pnl_labels", "build_bear_regime_label",
    "compute_regime_col", "compute_long_regime_col", "get_train_labels",
    "chronological_split", "get_X", "fit_scaler", "load_csv",
    "compute_long_features", "compute_macro_cross_features",
]
