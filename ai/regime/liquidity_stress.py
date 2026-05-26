"""
ai/regime/liquidity_stress.py — Liquidity Stress Engine

Score de stress de liquidité [0, 1] composé de 4 proxies:

  1. Spread proxy    → intrabar_range_pct  (écart intrabar = proxy bid-ask)
  2. Volume anomaly  → vol_ratio_24        (volume anormal = flows forcés)
  3. Funding extreme → funding_rate_z_72   (extremes = longs/shorts piégés)
  4. Liquidations    → liq_long_spike_12 + liq_short_spike_12

Régimes:
  LIQUID     → score < 0.35
  STRESSED   → score ∈ [0.35, 0.65)
  ILLIQUID   → score ≥ 0.65

Usage:
  eng = LiquidityStressEngine()
  score = eng.score(bar_dict)
  regime = eng.regime(bar_dict)
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


@dataclass
class LiquidityReport:
    score:          float
    regime:         str
    components:     dict[str, float]
    dominant_cause: str


class LiquidityStressEngine:
    def __init__(
        self,
        spread_weight:    float = 0.25,
        volume_weight:    float = 0.25,
        funding_weight:   float = 0.25,
        liq_weight:       float = 0.25,
        stressed_thr:     float = 0.35,
        illiquid_thr:     float = 0.65,
        # Paramètres de normalisation (calibrés sur données historiques)
        spread_norm:      float = 0.03,    # 3% intrabar range = score 1.0
        vol_ratio_norm:   float = 3.0,     # vol_ratio_24 = 3× normal = score 1.0
        funding_z_norm:   float = 3.0,     # |funding_z| = 3 = score 1.0
        liq_spike_norm:   float = 3.0,     # liq_spike = 3 = score 1.0
    ):
        self._w   = {
            "spread":  spread_weight,
            "volume":  volume_weight,
            "funding": funding_weight,
            "liq":     liq_weight,
        }
        self._stressed_thr  = stressed_thr
        self._illiquid_thr  = illiquid_thr
        self._spread_norm   = spread_norm
        self._vol_ratio_norm= vol_ratio_norm
        self._funding_z_norm= funding_z_norm
        self._liq_spike_norm= liq_spike_norm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, bar: dict) -> float:
        components = self._compute_components(bar)
        return float(np.clip(
            sum(self._w[k] * v for k, v in components.items()),
            0.0, 1.0
        ))

    def regime(self, bar: dict) -> str:
        s = self.score(bar)
        if s >= self._illiquid_thr:
            return "ILLIQUID"
        if s >= self._stressed_thr:
            return "STRESSED"
        return "LIQUID"

    def report(self, bar: dict) -> LiquidityReport:
        components = self._compute_components(bar)
        s = float(np.clip(
            sum(self._w[k] * v for k, v in components.items()),
            0.0, 1.0
        ))
        dominant = max(components, key=lambda k: components[k] * self._w[k])
        return LiquidityReport(
            score          = round(s, 4),
            regime         = self._to_regime(s),
            components     = {k: round(v, 4) for k, v in components.items()},
            dominant_cause = dominant,
        )

    def size_multiplier(self, bar: dict) -> float:
        s = self.score(bar)
        if s >= self._illiquid_thr:
            return 0.30
        if s >= self._stressed_thr:
            return 0.65
        return 1.00

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_components(self, bar: dict) -> dict[str, float]:
        spread   = float(bar.get("intrabar_range_pct", 0.0))
        vol_r    = float(bar.get("vol_ratio_24", 1.0))
        fund_z   = abs(float(bar.get("funding_rate_z_72", 0.0)))
        liq_l    = float(bar.get("liq_long_spike_12",  0.0))
        liq_s    = float(bar.get("liq_short_spike_12", 0.0))

        # Volume anomaly: > normal is stress; < normal is also a bit suspicious
        vol_stress = max(0.0, vol_r - 1.0) / max(self._vol_ratio_norm - 1.0, 1e-6)

        return {
            "spread":  min(1.0, spread / self._spread_norm),
            "volume":  min(1.0, vol_stress),
            "funding": min(1.0, fund_z / self._funding_z_norm),
            "liq":     min(1.0, (liq_l + liq_s) / (2 * self._liq_spike_norm)),
        }

    def _to_regime(self, score: float) -> str:
        if score >= self._illiquid_thr:
            return "ILLIQUID"
        if score >= self._stressed_thr:
            return "STRESSED"
        return "LIQUID"
