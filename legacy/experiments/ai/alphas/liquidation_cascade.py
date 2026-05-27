"""
ai/alphas/liquidation_cascade.py — Liquidation Cascade Alpha

Edge: les pics de liquidation forcée créent des dislocations de prix temporaires
      → mean-reversion rapide dans les 2-4 barres.

Long cascade (liq_long_spike): les longs sont liquidés → prix bas → rebond
Short cascade (liq_short_spike): les shorts sont liquidés → prix haut → retour

Conditions:
  liq_[long|short]_spike_12 > spike_threshold (en σ)
  vol_ratio_24 > 1.5  (volume anormal = liquidation réelle)
  mom_logret_72: contra-indicator (on trade contre)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class LiquidationCascadeAlpha(AlphaBase):
    name            = "liquidation_cascade"
    max_allocation  = 0.01    # Petit size: événement rare, incertitude haute
    valid_regimes   = None
    default_horizon = 3       # Mean-reversion rapide (3h)

    def __init__(
        self,
        spike_threshold: float = 1.8,   # sigmas above normal
        vol_ratio_min:   float = 1.5,
    ):
        self._spike_thr  = spike_threshold
        self._vol_min    = vol_ratio_min

    def is_valid(self, bar: pd.Series) -> bool:
        return (
            "liq_long_spike_12" in bar.index
            or "liq_short_spike_12" in bar.index
        )

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        liq_long  = float(bar.get("liq_long_spike_12",  0.0))
        liq_short = float(bar.get("liq_short_spike_12", 0.0))
        vol_ratio = float(bar.get("vol_ratio_24", 1.0))
        mom72     = float(bar.get("mom_logret_72", 0.0))

        if vol_ratio < self._vol_min:
            return None

        # Liquidation longs → prix trop bas → rebond (LONG)
        if liq_long > self._spike_thr and mom72 < -0.01:
            conviction = min(1.0, (liq_long / self._spike_thr - 1.0) * 0.5 + 0.35)
            return AlphaSignal(
                name         = self.name,
                side         = "long",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"liq_long": liq_long, "vol_ratio": vol_ratio},
            )

        # Liquidation shorts → prix trop haut → retour (SHORT)
        if liq_short > self._spike_thr and mom72 > 0.01:
            conviction = min(1.0, (liq_short / self._spike_thr - 1.0) * 0.5 + 0.35)
            return AlphaSignal(
                name         = self.name,
                side         = "short",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"liq_short": liq_short, "vol_ratio": vol_ratio},
            )

        return None
