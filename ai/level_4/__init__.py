"""
level_4 — PAIRWISE COMPARATOR (COHÉRENCE DES SIGNAUX)
======================================================

Ce niveau évalue la cohérence entre les prédictions des experts du niveau 3.
Il évite d'exécuter des trades dont les signaux se contredisent.

Sorties :
  "consistent"   → les experts sont d'accord → go
  "weak"         → accord partiel → signal réduit
  "contradict"   → désaccord → bloquer le trade

Status : STUB — non encore implémenté dans le pipeline sklearn.
Les implémentations sont dans :
  trading-system/src/pipeline/models/comparator/
    disagreement.py : mesure de désaccord entre experts
    novelty.py      : détection de nouveauté / OOD

Pour implémenter :
  1. Collecter les distributions de prédictions de tous les experts actifs
  2. Mesurer l'accord (KL divergence, variance entre experts)
  3. Retourner coherence ∈ {"consistent", "weak", "contradict"}
"""

__all__: list = []
