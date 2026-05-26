"""
ai/alphas/sentiment_extreme.py — Sentiment Extreme Alpha

Edge: Fear & Greed Index aux extrêmes est un signal contrarian.
      - Extrême peur (< 20) → accumulation → LONG
      - Extrême euphorie (> 80) → distribution → SHORT

Note: signal lent (72h lookback), ne pas combiner avec des alphas rapides.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class SentimentExtremeAlpha(AlphaBase):
    name            = "sentiment_extreme"
    max_allocation  = 0.015
    valid_regimes   = None
    default_horizon = 24   # 24h — signal lent

    def __init__(
        self,
        fear_threshold:    float = 22.0,   # F&G < 22 → extrême peur
        greed_threshold:   float = 78.0,   # F&G > 78 → extrême cupidité
        z_min:             float = 1.5,    # z-score minimum pour valider l'extrême
    ):
        self._fear_thr  = fear_threshold
        self._greed_thr = greed_threshold
        self._z_min     = z_min

    def is_valid(self, bar: pd.Series) -> bool:
        return "fear_greed_value_z_72" in bar.index

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        fg_z   = float(bar.get("fear_greed_value_z_72", 0.0))
        fg_raw = float(bar.get("fear_greed_value",     50.0))

        # Extrême peur → LONG (contrarian)
        if fg_z < -self._z_min and fg_raw < self._fear_thr:
            conviction = min(1.0, abs(fg_z) / (self._z_min * 2) * 0.6 + 0.3)
            return AlphaSignal(
                name         = self.name,
                side         = "long",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"fg_z": fg_z, "fg_raw": fg_raw, "type": "fear"},
            )

        # Extrême cupidité → SHORT (contrarian)
        if fg_z > self._z_min and fg_raw > self._greed_thr:
            conviction = min(1.0, abs(fg_z) / (self._z_min * 2) * 0.6 + 0.3)
            return AlphaSignal(
                name         = self.name,
                side         = "short",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = None,
                metadata     = {"fg_z": fg_z, "fg_raw": fg_raw, "type": "greed"},
            )

        return None
