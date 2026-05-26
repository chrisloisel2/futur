"""
ai/alphas/vol_expansion.py — Volatility Expansion Alpha

Edge: compression de la volatilité (Bollinger étroit) précède les breakouts.
      Quand le prix sort de la compression → momentum burst.

Conditions LONG:
  boll_width_20 < seuil bas (compression)
  boll_pos_20 > 0.7 (prix proche de la bande haute)
  eff_ratio_12 > 0.4 (mouvement directionnel)

Conditions SHORT:
  boll_width_20 < seuil bas
  boll_pos_20 < 0.3 (prix proche de la bande basse)
  eff_ratio_12 > 0.4
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class VolExpansionAlpha(AlphaBase):
    name            = "vol_expansion"
    max_allocation  = 0.02
    valid_regimes   = ["COMPRESSION", "EXPANSION", "UNKNOWN", None]
    default_horizon = 8

    def __init__(
        self,
        compression_pct:   float = 0.25,   # boll_width dans le bas P25%
        breakout_pos_long: float = 0.70,   # boll_pos > 0.70 → breakout haut
        breakout_pos_short:float = 0.30,   # boll_pos < 0.30 → breakout bas
        eff_ratio_min:     float = 0.35,   # efficacité directionnelle
    ):
        self._compress = compression_pct
        self._bp_long  = breakout_pos_long
        self._bp_short = breakout_pos_short
        self._eff_min  = eff_ratio_min
        # Rolling percentile de boll_width (calibré à la volée)
        self._boll_history: list[float] = []
        self._history_max = 500

    def is_valid(self, bar: pd.Series) -> bool:
        return "boll_width_20" in bar.index and "boll_pos_20" in bar.index

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        boll_w  = float(bar.get("boll_width_20",  0.02))
        boll_p  = float(bar.get("boll_pos_20",    0.5))
        eff_r   = float(bar.get("eff_ratio_12",   0.0))
        rv_24   = float(bar.get("rv_24",          0.02))

        # Mise à jour de l'historique pour le percentile
        self._boll_history.append(boll_w)
        if len(self._boll_history) > self._history_max:
            self._boll_history = self._boll_history[-self._history_max:]

        if len(self._boll_history) < 50:
            return None  # Pas assez d'historique

        # Seuil de compression: bas P25% de l'historique
        thresh = float(np.percentile(self._boll_history, 25))
        in_compression = boll_w < thresh

        if not in_compression or eff_r < self._eff_min:
            return None

        # LONG breakout
        if boll_p > self._bp_long:
            conviction = min(1.0, (boll_p - self._bp_long) / (1.0 - self._bp_long) * 0.5
                             + eff_r * 0.5)
            return AlphaSignal(
                name         = self.name,
                side         = "long",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"boll_w": boll_w, "boll_p": boll_p, "thresh": thresh},
            )

        # SHORT breakout
        if boll_p < self._bp_short:
            conviction = min(1.0, (self._bp_short - boll_p) / self._bp_short * 0.5
                             + eff_r * 0.5)
            return AlphaSignal(
                name         = self.name,
                side         = "short",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"boll_w": boll_w, "boll_p": boll_p, "thresh": thresh},
            )

        return None
