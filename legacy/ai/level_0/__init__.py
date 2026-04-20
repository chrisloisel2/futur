"""
level_0 — GLOBAL GATING & DATA PIPELINE
========================================

Ce niveau est la fondation de tout le pipeline.
Il est responsable de :
  1. Définir les constantes globales (horizons, coûts, splits)
  2. Définir les listes de features par branche (long / short / régime)
  3. Calculer les features asymétriques (feature engineering)
  4. Construire les labels (tradeable, y_long, y_short, y_bear_regime)
  5. Préprocesser les données (split chronologique, scaling)
  6. Entraîner le filtre tradeable (Stage 1)

Optimiser ce niveau pour :
  - Améliorer la qualité des labels (réduire le bruit, ajuster les seuils)
  - Ajouter / retirer des features (impact mesuré par AUC backtest)
  - Ajuster les splits temporels

API publique
------------
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
    compute_short_reversal_col,
    compute_long_reversal_col,
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
    compute_short_features,
)

from ai.level_0.filter import train_filter_model
from ai.level_0.filter_calibrate import (
    calibrate_filter_threshold,
    threshold_sweep,
    check_threshold_stability,
)

__all__ = [
    # Constants
    "HORIZON_BARS", "HORIZON_MINUTES", "BAR_FREQUENCY",
    "COST_PCT", "COST_PCT_STRESS",
    "TRADEABLE_QUANTILE_LONG", "TRADEABLE_QUANTILE_SHORT",
    "TRAIN_END_YEAR", "VAL_YEAR", "TEST_FROM_YEAR",
    "TARGET_COL", "CLOSE_COL", "DATETIME_COL",
    "REGIME_COL", "REGIME_COL_LONG",
    "FILTER_BETA_LONG", "FILTER_BETA_SHORT",
    "PNL_COST_MULT",
    "assert_horizon",
    # Features
    "FEATURES_COMMON", "FEATURES_MACRO_FILTER",
    "FEATURES_LONG", "FEATURES_SHORT",
    "FEATURES_FILTER", "FEATURES_REGIME",
    "FEATURES_LONG_EXTRA", "FEATURES_SHORT_EXTRA",
    "validate_features", "get_feature_overlap",
    # Labels
    "build_labels", "build_pnl_labels", "build_bear_regime_label",
    "compute_regime_col", "compute_long_regime_col",
    "compute_short_reversal_col", "compute_long_reversal_col",
    "get_train_labels",
    # Preprocessing
    "chronological_split", "get_X", "fit_scaler", "load_csv",
    # Feature engineering
    "compute_long_features", "compute_short_features",
    # Filter
    "train_filter_model",
    "calibrate_filter_threshold", "threshold_sweep", "check_threshold_stability",
]
