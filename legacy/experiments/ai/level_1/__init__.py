"""
level_1 — EVENT CLASSIFIER & REGIME DETECTION
==============================================

Ce niveau décide dans quel contexte de marché on se trouve.
Il est responsable de :
  1. Classifier le régime (SHORTABLE / NEUTRAL / NO_SHORT) — règles déterministes
  2. Filtrer le short via le méta-modèle probabiliste (bear_regime)
  3. Gater l'accès aux modèles de niveau 2 selon le régime

Deux composants complémentaires :
  rules.py       : filtre déterministe (règles EMA/RSI) — zéro leakage
  bear_regime.py : méta-modèle ML (LogReg sur FEATURES_REGIME) — probabilités calibrées

Optimiser ce niveau pour :
  - Améliorer la pureté du régime (% de vrais bear dans SHORTABLE)
  - Ajuster le seuil d'activation (0.70–0.90)
  - Ajouter des features macro au modèle de régime

API publique
------------
"""
from ai.level_1.rules import (
    RegimeFilter,
    apply_regime_filter,
    diagnose_regime_distribution,
    compute_regime_stats_by_year,
    REGIME_NO_SHORT,
    REGIME_SHORTABLE,
    REGIME_NEUTRAL,
)

from ai.level_1.bear_regime import train_bear_regime_model
from ai.level_1.macro_gate import MacroGate, compute_macro_gate_series

__all__ = [
    # Règles déterministes
    "RegimeFilter",
    "apply_regime_filter",
    "diagnose_regime_distribution",
    "compute_regime_stats_by_year",
    "REGIME_NO_SHORT",
    "REGIME_SHORTABLE",
    "REGIME_NEUTRAL",
    # Méta-modèle ML
    "train_bear_regime_model",
    # Gate macro dynamique
    "MacroGate",
    "compute_macro_gate_series",
]
