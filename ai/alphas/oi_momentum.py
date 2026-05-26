"""
ai/alphas/oi_momentum.py — OI Momentum Alpha

Edge: accélération de l'Open Interest en direction du momentum → confirmation
      de tendance institutionnelle.

Conditions LONG:
  oi_acceleration_z > 1.0  (OI croît rapidement)
  mom_logret_72 > 0.02     (momentum positif 3j)
  global_ls_longShortRatio_z_72 > -1.0 (pas de position extrême long)

Conditions SHORT:
  oi_acceleration_z < -1.0  (OI chute → déleveraging)
  mom_logret_72 < -0.02
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class OIMomentumAlpha(AlphaBase):
    name            = "oi_momentum"
    max_allocation  = 0.02
    valid_regimes   = None    # Tous régimes
    default_horizon = 12

    def __init__(
        self,
        oi_z_threshold: float = 1.0,
        mom_threshold:  float = 0.02,
        ls_z_cap:       float = 1.5,    # éviter les extrêmes de position
    ):
        self._oi_z  = oi_z_threshold
        self._mom   = mom_threshold
        self._ls_z  = ls_z_cap

    def is_valid(self, bar: pd.Series) -> bool:
        return "oi_acceleration_z" in bar.index or "mom_logret_72" in bar.index

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        oi_z  = float(bar.get("oi_acceleration_z",             0.0))
        mom72 = float(bar.get("mom_logret_72",                 0.0))
        ls_z  = float(bar.get("global_ls_longShortRatio_z_72", 0.0))

        # LONG: OI en hausse + momentum positif + pas surchargé côté long
        if oi_z > self._oi_z and mom72 > self._mom and ls_z < self._ls_z:
            conviction = min(1.0, oi_z / (self._oi_z * 2) * 0.6 + abs(mom72) / 0.05 * 0.4)
            return AlphaSignal(
                name         = self.name,
                side         = "long",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = "EXPANSION",
                metadata     = {"oi_z": oi_z, "mom72": mom72, "ls_z": ls_z},
            )

        # SHORT: OI en baisse + momentum négatif
        if oi_z < -self._oi_z and mom72 < -self._mom and ls_z > -self._ls_z:
            conviction = min(1.0, abs(oi_z) / (self._oi_z * 2) * 0.6 + abs(mom72) / 0.05 * 0.4)
            return AlphaSignal(
                name         = self.name,
                side         = "short",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = "DELEVERAGING",
                metadata     = {"oi_z": oi_z, "mom72": mom72},
            )

        return None
