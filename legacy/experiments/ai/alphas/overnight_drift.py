"""
ai/alphas/overnight_drift.py — Overnight / Time-of-Day Drift Alpha

Edge: les cryptos présentent une asymétrie temporelle connue:
  - Session asiatique (0h-8h UTC): drift baissier en bear market
  - Session américaine (14h-20h UTC): momentum plus fiable
  - Ouverture européenne (6h-9h UTC): fréquemment volatile

Conditions LONG:
  - Session américaine (14h-18h UTC) + momentum court positif
  - Ouverture européenne avec vol faible (continuation)

Conditions SHORT:
  - Session asiatique + momentum négatif + bear regime
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from ai.alphas.base import AlphaBase, AlphaSignal


class OvernightDriftAlpha(AlphaBase):
    name            = "overnight_drift"
    max_allocation  = 0.01    # Petit size — signal faible
    valid_regimes   = None
    default_horizon = 4       # 4h (durée d'une session)

    def __init__(
        self,
        us_session_start:    int = 14,    # 14h UTC
        us_session_end:      int = 20,    # 20h UTC
        asia_session_start:  int = 0,
        asia_session_end:    int = 8,
        eu_session_start:    int = 6,
        eu_session_end:      int = 10,
        momentum_threshold:  float = 0.005,  # 0.5% sur 4h
    ):
        self._us_start   = us_session_start
        self._us_end     = us_session_end
        self._asia_start = asia_session_start
        self._asia_end   = asia_session_end
        self._eu_start   = eu_session_start
        self._eu_end     = eu_session_end
        self._mom_thr    = momentum_threshold

    def is_valid(self, bar: pd.Series) -> bool:
        return "hour_sin" in bar.index or "hour_cos" in bar.index

    def generate(self, bar: pd.Series, context: dict) -> Optional[AlphaSignal]:
        hour_sin = float(bar.get("hour_sin", 0.0))
        hour_cos = float(bar.get("hour_cos", 1.0))
        mom_4h   = float(bar.get("log_ret_4", bar.get("log_ret_1", 0.0)))

        # Reconstruire l'heure UTC depuis hour_sin/cos
        hour_utc = self._decode_hour(hour_sin, hour_cos)

        in_us_session   = self._us_start <= hour_utc < self._us_end
        in_asia_session = hour_utc < self._asia_end or hour_utc >= 22
        in_eu_session   = self._eu_start <= hour_utc < self._eu_end

        # Session US + momentum positif → LONG
        if in_us_session and mom_4h > self._mom_thr:
            conviction = min(1.0, mom_4h / (self._mom_thr * 4) * 0.6 + 0.25)
            return AlphaSignal(
                name         = self.name,
                side         = "long",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = "EXPANSION",
                metadata     = {"session": "US", "hour": hour_utc, "mom_4h": mom_4h},
            )

        # Session asiatique + momentum négatif → SHORT (faible conviction)
        if in_asia_session and mom_4h < -self._mom_thr:
            conviction = min(0.5, abs(mom_4h) / (self._mom_thr * 4) * 0.4 + 0.15)
            return AlphaSignal(
                name         = self.name,
                side         = "short",
                conviction   = round(conviction, 4),
                horizon_bars = self.default_horizon,
                regime_condition = "DELEVERAGING",
                metadata     = {"session": "ASIA", "hour": hour_utc, "mom_4h": mom_4h},
            )

        return None

    def _decode_hour(self, sin_h: float, cos_h: float) -> int:
        angle = math.atan2(sin_h, cos_h)
        hour  = (angle / (2 * math.pi) * 24) % 24
        return int(hour)
