"""
src/alpha20/execution/maker_model.py — probabilité de fill maker (étape 7+).

SQUELETTE VOLONTAIRE : le modèle de fill maker doit être AJUSTÉ sur les fills
réels mesurés par la couche TCA (queue position, spread, volatilité courte).
Sans données de fills, tout modèle serait décoratif. Interface figée.
"""
from __future__ import annotations

from typing import Dict


def fill_probability(quote: Dict) -> float:
    raise NotImplementedError(
        "maker_model : à ajuster sur les fills TCA réels (étape 7) — "
        "d'ici là le simulateur suppose tout-taker (conservateur)")
