"""
ai — Pipeline ML Trading (Architecture 7 niveaux)
==================================================

Point d'entrée unique pour tout le pipeline.

Architecture :
  Level 0  — Global Gating     : features, labels, filtre tradeable
  Level 1  — Event Classifier  : régimes de marché, méta-modèle bear
  Level 2  — Edge Scorer       : modèles directionnels long / short
  Level 3  — Specialists       : experts par régime              [stub]
  Level 4  — Pairwise          : cohérence inter-modèles         [stub]
  Level 5  — Decision Gate     : CONFIRM / DELAY / INVALIDATE    [stub]
  Level 6  — Meta Scaler       : scale ∈ [0, 1]                  [proxy → level_2]
  Level 7  — Risk Controller   : action, qty, stop, take_profit

Usage rapide
------------
    from ai import pipeline, data, regime, risk

    # Charger et préparer les données
    df = data.load_csv("data/BTCUSD_1h_features.csv")

    # Entraîner le pipeline complet
    results = pipeline.train(df, mode="combined")

    # Accéder directement à un niveau
    from ai.level_2 import train_long_model, LongModelConfig

Modules
-------
    ai.data      — Chargement / features / labels (Level 0)
    ai.regime    — Régimes de marché (Level 1)
    ai.edge      — Modèles directionnels (Level 2)
    ai.risk      — Risk controller (Level 7)
    ai.pipeline  — Orchestration bout-en-bout
"""
from __future__ import annotations

# ── Sous-modules disponibles directement ────────────────────────────────────
from ai import level_0, level_1, level_2, level_3, level_7

# ── Namespaces sémantiques ───────────────────────────────────────────────────
# Utilisez ces alias pour un code plus lisible :
#   from ai import data, regime, edge, risk, pipeline

from ai import level_0 as data      # features, labels, preprocessing, filter
from ai import level_1 as regime    # régimes + bear meta-model
from ai import level_2 as edge      # modèles long / short + calibration
from ai import level_3 as specialists  # experts par contexte
from ai import level_7 as risk         # risk controller

# ── Imports de commodité ─────────────────────────────────────────────────────
# Level 0 — données
from ai.level_0 import (
    # Constantes globales
    HORIZON_BARS, HORIZON_MINUTES, BAR_FREQUENCY,
    COST_PCT, COST_PCT_STRESS,
    TRADEABLE_QUANTILE_LONG, TRADEABLE_QUANTILE_SHORT,
    TRAIN_END_YEAR, VAL_YEAR, TEST_FROM_YEAR,
    TARGET_COL, CLOSE_COL, DATETIME_COL,
    REGIME_COL, REGIME_COL_LONG,
    # Features
    FEATURES_COMMON, FEATURES_LONG, FEATURES_SHORT,
    FEATURES_FILTER, FEATURES_REGIME,
    validate_features,
    # Labels
    build_labels, build_bear_regime_label,
    compute_regime_col, compute_long_regime_col,
    compute_short_reversal_col, compute_long_reversal_col,
    # Preprocessing
    chronological_split, get_X, fit_scaler, load_csv,
    # Feature engineering
    compute_long_features, compute_short_features,
    # Filtre tradeable
    train_filter_model,
    calibrate_filter_threshold, threshold_sweep,
)

# Level 1 — régimes
from ai.level_1 import (
    RegimeFilter,
    apply_regime_filter,
    diagnose_regime_distribution,
    REGIME_NO_SHORT, REGIME_SHORTABLE, REGIME_NEUTRAL,
    train_bear_regime_model,
)

# Level 2 — edge scoring
from ai.level_2 import (
    LongModelConfig, ShortModelConfig,
    train_long_model, train_short_model,
    calibrate_long_model, calibrate_short_model,
    check_short_stability, diagnose_short_failure,
)

# Level 3 — specialists
from ai.level_3 import (
    MarketContext, ALL_CONTEXTS, assign_context,
    ContextRouter, RouterConfig, RoutingResult,
    SpecialistConfig, train_specialist, train_specialists,
    SpecialistPredictor,
    CONTEXT_FEATURES, CONTEXT_SIDE,
)

# Level 7 — risk
from ai.level_7 import (
    RiskConfig,
    make_long_risk_config, make_short_risk_config,
    load_or_create_risk_controller, save_risk_state,
)

# ── Pipeline module (import lazy pour éviter les dépendances circulaires) ────
def _get_pipeline():
    """Retourne le module pipeline (import différé)."""
    from ai import _pipeline as _p
    return _p

# ── __all__ ──────────────────────────────────────────────────────────────────
__all__ = [
    # Namespaces
    "data", "regime", "edge", "specialists", "risk",
    "level_0", "level_1", "level_2", "level_3", "level_7",
    # Level 0
    "HORIZON_BARS", "HORIZON_MINUTES", "BAR_FREQUENCY",
    "COST_PCT", "COST_PCT_STRESS",
    "FEATURES_COMMON", "FEATURES_LONG", "FEATURES_SHORT",
    "FEATURES_FILTER", "FEATURES_REGIME",
    "validate_features",
    "build_labels", "build_bear_regime_label",
    "compute_regime_col", "compute_long_regime_col",
    "compute_short_reversal_col", "compute_long_reversal_col",
    "chronological_split", "get_X", "fit_scaler", "load_csv",
    "compute_long_features", "compute_short_features",
    "train_filter_model", "calibrate_filter_threshold", "threshold_sweep",
    # Level 1
    "RegimeFilter", "apply_regime_filter", "diagnose_regime_distribution",
    "REGIME_NO_SHORT", "REGIME_SHORTABLE", "REGIME_NEUTRAL",
    "train_bear_regime_model",
    # Level 2
    "LongModelConfig", "ShortModelConfig",
    "train_long_model", "train_short_model",
    "calibrate_long_model", "calibrate_short_model",
    "check_short_stability", "diagnose_short_failure",
    # Level 3
    "MarketContext", "ALL_CONTEXTS", "assign_context",
    "ContextRouter", "RouterConfig", "RoutingResult",
    "SpecialistConfig", "train_specialist", "train_specialists",
    "SpecialistPredictor",
    "CONTEXT_FEATURES", "CONTEXT_SIDE",
    # Level 7
    "RiskConfig", "make_long_risk_config", "make_short_risk_config",
    "load_or_create_risk_controller", "save_risk_state",
]
