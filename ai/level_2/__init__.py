"""
level_2 — EDGE SCORER (MODÈLES DIRECTIONNELS LONG/SHORT)
=========================================================

Ce niveau prédit la direction du marché pour chaque barre tradeable.
Il est responsable de :
  1. Entraîner le modèle LONG (y_long → p_long)
  2. Entraîner le modèle SHORT (y_short → p_short)
  3. Calibrer les probabilités (ECE, threshold sweep)
  4. Valider la stabilité inter-années du short (obligatoire)

Asymétrie structurelle long / short :
  - Features différentes (FEATURES_LONG vs FEATURES_SHORT)
  - Hyperparamètres différents (LongModelConfig vs ShortModelConfig)
  - Calibration différente (isotonic long / platt short)
  - Validation inter-années obligatoire pour le short uniquement

Optimiser ce niveau pour :
  - Améliorer l'AUC sur val (target : > 0.65)
  - Réduire l'ECE après calibration (target : < 0.05)
  - Améliorer la stabilité inter-années du short (target : ≤ 1 bad year)

API publique
------------
"""
from ai.level_2.long_config import LongModelConfig
from ai.level_2.short_config import ShortModelConfig
from ai.level_2.long import train_long_model
from ai.level_2.short import train_short_model
from ai.level_2.long_calibrate import calibrate_direction_model as calibrate_long_model
from ai.level_2.short_calibrate import calibrate_direction_model as calibrate_short_model
from ai.level_2.short_validate import check_short_stability, diagnose_short_failure

__all__ = [
    # Configurations
    "LongModelConfig",
    "ShortModelConfig",
    # Entraînement
    "train_long_model",
    "train_short_model",
    # Calibration
    "calibrate_long_model",
    "calibrate_short_model",
    # Validation
    "check_short_stability",
    "diagnose_short_failure",
]
