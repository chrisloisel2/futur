"""
level_6 — META SCALER (SCALE ∈ [0, 1])
========================================

Ce niveau convertit le signal brut (probabilités calibrées) en un scale
de position normalisé entre 0 et 1.

Rôle :
  - Transformer p_long/p_short en scale ∈ [0, 1]
  - Appliquer la Kelly fraction de manière dynamique
  - Réduire l'exposition quand la confiance est faible ou incertaine

Formule typique :
  scale = kelly_fraction × (p - min_edge) / (1 - min_edge)
  scale = clip(scale, 0, 1)

Status : partiellement implémenté (calibration dans level_2).
La calibration des probabilités (ECE) est dans level_2/long_calibrate.py
et level_2/short_calibrate.py.
Le scaling de position sera ajouté ici quand level_3–5 seront actifs.

Pour implémenter :
  1. Lire p_long/p_short du niveau 2
  2. Lire la cohérence du niveau 4
  3. Calculer scale = f(p, coherence, kelly_fraction)
  4. Exposer scale ∈ [0, 1] au niveau 7 (Risk Controller)

Liens vers level_2 (calibration existante) :
"""
from ai.level_2.long_calibrate import calibrate_direction_model as calibrate_long
from ai.level_2.short_calibrate import calibrate_direction_model as calibrate_short

__all__ = ["calibrate_long", "calibrate_short"]
