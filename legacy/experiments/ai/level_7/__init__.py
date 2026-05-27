"""
level_7 — RISK CONTROLLER (ACTION, QTY, STOP, TAKE PROFIT)
===========================================================

Ce niveau est le dernier avant l'exécution.
Il transforme le signal de trading en ordre réel avec gestion du risque.

Responsabilités :
  1. Calculer la taille de position (Kelly fraction, max % du capital)
  2. Calculer le stop loss (ATR-adaptatif ou fixe)
  3. Calculer le take profit (RR ratio)
  4. Appliquer les gardes-fous (max positions, cooldown, DD limite)
  5. Persister l'état entre les runs (streak, peak equity, etc.)

Asymétrie long / short :
  - Configuration indépendante par côté (make_long_risk_config / make_short_risk_config)
  - Stop plus serré pour le short (1.2% vs 1.5%)
  - Kelly plus conservateur pour le short (0.20 vs 0.25)
  - Cooldown plus long après perte short (10 bars vs 6)

Optimiser ce niveau pour :
  - Ajuster les stops selon la volatilité réalisée
  - Calibrer la Kelly fraction par régime
  - Ajouter des circuit breakers (DD streak, crash detection)

Liens vers l'implémentation TF/NumPy :
  ai/models/level_7/RiskController.py

API publique
------------
"""
from ai.level_7.config import (
    RiskConfig,
    make_long_risk_config,
    make_short_risk_config,
)
from ai.level_7.state import (
    load_or_create_risk_controller,
    save_risk_state,
)

__all__ = [
    # Configuration
    "RiskConfig",
    "make_long_risk_config",
    "make_short_risk_config",
    # Persistance
    "load_or_create_risk_controller",
    "save_risk_state",
]
