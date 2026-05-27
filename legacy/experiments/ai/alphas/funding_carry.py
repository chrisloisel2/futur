"""
ai/alphas/funding_carry.py — Funding Carry Alpha

Edge: quand le funding rate est élevé (longs payent les shorts), les longs sont
      sur-endettés → mean-reversion probable sur 4-8h.

Conditions:
  1. funding_rate > 0.05% / 8h  (longs payent cher)
  2. OI en hausse sur 4 barres  (accumulation de leverage)
  3. RSI > 60                   (marché étiré à la hausse)

Signal: SHORT (harvest carry + anticipation de déleveraging)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class FundingCarryAlpha(AlphaBase):
    name            = "funding_carry"
    max_allocation  = 0.015    # 1.5% du capital max
    valid_regimes   = ["EXPANSION", "COMPRESSION", "UNKNOWN", None]
    default_horizon = 6        # 6h

    def __init__(
        self,
        funding_threshold: float = 0.0005,   # 0.05%/8h
        rsi_min:           float = 58.0,
        oi_z_min:          float = 0.3,      # OI en accélération
    ):
        self._funding_thr = funding_threshold
        self._rsi_min     = rsi_min
        self._oi_z_min    = oi_z_min

    def is_valid(self, bar: pd.Series) -> bool:
        return "funding_rate" in bar.index and "rsi_14" in bar.index

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        funding = float(bar.get("funding_rate", 0.0))
        rsi     = float(bar.get("rsi_14", 50.0))
        oi_z    = float(bar.get("oi_acceleration_z", 0.0))

        if funding < self._funding_thr:
            return None
        if rsi < self._rsi_min:
            return None

        # Conviction: plus le funding est élevé + OI en hausse → plus fort
        conviction = min(1.0, (funding / self._funding_thr - 1.0) * 0.4 + 0.4)
        if oi_z > self._oi_z_min:
            conviction = min(1.0, conviction + 0.15)

        return AlphaSignal(
            name             = self.name,
            side             = "short",
            conviction       = round(conviction, 4),
            horizon_bars     = self.default_horizon,
            regime_condition = None,
            metadata         = {"funding_rate": funding, "rsi": rsi, "oi_z": oi_z},
        )
