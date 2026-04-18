"""
level_5 — DECISION GATE (CONFIRM / DELAY / INVALIDATE)
=======================================================

Ce niveau prend la décision finale d'exécution basée sur tous les niveaux
précédents.

Entrées :
  - p_long / p_short (niveau 2)
  - régime (niveau 1)
  - cohérence (niveau 4)
  - contexte temporel (heure, jour)

Sorties :
  "CONFIRM"     → exécuter le trade
  "DELAY"       → attendre une barre de plus
  "INVALIDATE"  → annuler le signal

Status : STUB — non encore implémenté.

Pour implémenter :
  1. Définir les règles d'invalidation (ex: signal fort mais régime adverse)
  2. Implémenter la logique de délai (signal faible en attente de confirmation)
  3. Exposer une API simple : decide(signal_dict) → "CONFIRM" | "DELAY" | "INVALIDATE"
"""

__all__: list = []
