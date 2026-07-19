"""
src/alpha20/execution/smart_router.py — routage d'ordres (étape 7+).

SQUELETTE VOLONTAIRE : le routage réel (maker vs taker, découpage, venue)
exige la couche TCA alimentée par des fills mesurés — pas encore disponibles.
Toute implémentation avant les données serait une hypothèse de plus à
falsifier. Interface figée pour les adaptateurs.
"""
from __future__ import annotations

from typing import Dict


def route(decision: Dict) -> Dict:
    raise NotImplementedError(
        "smart_router : bloqué jusqu'à la couche TCA (étape 7) — "
        "utiliser hedge_coordinator.PairPlan pour la planification atomique")
