"""
ai/alphas/base.py — AlphaBase ABC + AlphaSignal dataclass

Tous les micro-alphas héritent de AlphaBase et produisent des AlphaSignal.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class AlphaSignal:
    name:             str
    side:             str            # "long" | "short" | "neutral"
    conviction:       float          # [0, 1] — force du signal
    horizon_bars:     int            # durée attendue du trade
    regime_condition: Optional[str]  # régime requis (None = tous)
    metadata:         dict = field(default_factory=dict)

    def __post_init__(self):
        assert self.side in ("long", "short", "neutral"), f"Invalid side: {self.side}"
        assert 0.0 <= self.conviction <= 1.0, f"Conviction must be in [0,1]: {self.conviction}"

    def is_directional(self) -> bool:
        return self.side != "neutral"


class AlphaBase(ABC):
    """
    Interface commune pour tous les micro-alphas.

    Attributs de classe à définir dans chaque alpha:
      name            — identifiant unique (snake_case)
      max_allocation  — fraction max du capital (ex. 0.02 = 2%)
      valid_regimes   — régimes autorisés (None = tous)
      default_horizon — durée du trade en barres si non spécifié
    """
    name:            str  = "alpha_base"
    max_allocation:  float = 0.02
    valid_regimes:   Optional[list[str]] = None
    default_horizon: int   = 8

    @abstractmethod
    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        """
        Génère un signal pour la barre courante.

        Args:
            bar     : pd.Series avec les features de la barre courante
            context : dict avec des informations de contexte (régime, portfolio, etc.)

        Returns:
            AlphaSignal si un trade est justifié, None sinon
        """
        ...

    @abstractmethod
    def is_valid(self, bar: pd.Series) -> bool:
        """Vérifie que les features nécessaires sont disponibles et valides."""
        ...

    def regime_allowed(self, regime: Optional[str]) -> bool:
        """Vérifie si le régime actuel est compatible avec cet alpha."""
        if self.valid_regimes is None:
            return True
        return regime in self.valid_regimes

    def describe(self) -> dict:
        return {
            "name":           self.name,
            "max_allocation": self.max_allocation,
            "valid_regimes":  self.valid_regimes,
            "default_horizon": self.default_horizon,
        }
