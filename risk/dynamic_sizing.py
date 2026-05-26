"""
risk/dynamic_sizing.py — Dynamic Position Sizing

Extension du RiskController avec:
  1. Volatility targeting — ajuste la taille pour cibler une vol annuelle fixe
  2. Liquidity-aware sizing — réduit si le marché est peu liquide
  3. Regime-adjusted sizing — multiplicateurs de régime (PANIC=0, EXPANSION=1)
  4. Kelly fraction — sizing optimisé selon win rate / ratio gain/perte

Usage:
  sizer = DynamicSizer(target_annual_vol=0.15)
  size = sizer.compute_size(
      base_size     = 100.0,          # taille de base (units/$ du RiskController)
      vol_24h       = 0.025,          # volatilité réalisée 24h (fraction)
      liquidity_mult= 0.65,           # depuis LiquidityStressEngine
      regime_mult   = 0.70,           # depuis CompositeRegime.sizing_multipliers()
      side          = "long",
  )
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class SizingResult:
    final_size:        float
    base_size:         float
    vol_multiplier:    float
    regime_multiplier: float
    liquidity_mult:    float
    kelly_fraction:    Optional[float]
    cap_applied:       bool
    breakdown:         dict


class DynamicSizer:
    """
    Sizer dynamique multi-niveaux.

    Pipeline:
      base_size
        × vol_multiplier        (volatility targeting)
        × regime_multiplier     (régime macro)
        × liquidity_multiplier  (stress de liquidité)
        × kelly_fraction        (si stats disponibles, optionnel)
        → clamp to [min_size, max_size]
    """

    def __init__(
        self,
        target_annual_vol: float = 0.15,      # 15% vol annuelle cible
        bars_per_year:     int   = 8760,      # barres 1h par an
        min_size:          float = 0.0,
        max_size:          float = float("inf"),
        kelly_fraction:    float = 0.25,      # fraction Kelly (prudent)
        max_kelly_mult:    float = 1.5,
    ):
        self._target_vol  = target_annual_vol
        self._bars_yr     = bars_per_year
        self._min_size    = min_size
        self._max_size    = max_size
        self._kelly_frac  = kelly_fraction
        self._max_kelly   = max_kelly_mult

    # ------------------------------------------------------------------
    # Main sizing
    # ------------------------------------------------------------------

    def compute_size(
        self,
        base_size:          float,
        vol_24h:            float,
        liquidity_mult:     float = 1.0,
        regime_mult:        float = 1.0,
        win_rate:           Optional[float] = None,
        avg_win_pct:        Optional[float] = None,
        avg_loss_pct:       Optional[float] = None,
    ) -> SizingResult:
        """
        Calcule la taille finale après tous les ajustements.

        Args:
            base_size       : taille de base (unité arbitraire, ex: $)
            vol_24h         : vol réalisée sur 24h (fraction, ex: 0.025 = 2.5%)
            liquidity_mult  : [0, 1] depuis LiquidityStressEngine
            regime_mult     : [0, 1] depuis CompositeRegime.sizing_multipliers()[side]
            win_rate        : optionnel pour Kelly
            avg_win_pct     : optionnel pour Kelly
            avg_loss_pct    : optionnel pour Kelly
        """
        # 1. Volatility targeting
        annual_vol = self._annualize_vol(vol_24h)
        if annual_vol > 1e-6:
            vol_mult = self._target_vol / annual_vol
            vol_mult = min(2.0, max(0.10, vol_mult))   # cap [10%, 200%]
        else:
            vol_mult = 1.0

        # 2. Kelly (si disponible)
        kelly_f = None
        kelly_mult = 1.0
        if win_rate is not None and avg_win_pct is not None and avg_loss_pct is not None:
            kelly_f    = self.kelly_fraction(win_rate, avg_win_pct, avg_loss_pct)
            kelly_mult = min(self._max_kelly, kelly_f / self._kelly_frac)

        # 3. Appliquer tous les multiplicateurs
        final = base_size * vol_mult * regime_mult * liquidity_mult * kelly_mult

        # 4. Clamp
        cap_applied = final > self._max_size or final < self._min_size
        final = max(self._min_size, min(self._max_size, final))

        return SizingResult(
            final_size        = round(final, 6),
            base_size         = base_size,
            vol_multiplier    = round(vol_mult, 4),
            regime_multiplier = round(regime_mult, 4),
            liquidity_mult    = round(liquidity_mult, 4),
            kelly_fraction    = round(kelly_f, 4) if kelly_f else None,
            cap_applied       = cap_applied,
            breakdown         = {
                "after_vol":        round(base_size * vol_mult, 6),
                "after_regime":     round(base_size * vol_mult * regime_mult, 6),
                "after_liquidity":  round(base_size * vol_mult * regime_mult * liquidity_mult, 6),
                "annual_vol_est":   round(annual_vol, 4),
                "target_vol":       self._target_vol,
            },
        )

    # ------------------------------------------------------------------
    # Kelly criterion
    # ------------------------------------------------------------------

    def kelly_fraction(
        self,
        win_rate:     float,
        avg_win_pct:  float,
        avg_loss_pct: float,
    ) -> float:
        """
        Kelly fraction = (p × b - q) / b
        où:
          p = win_rate, q = 1 - p
          b = avg_win / avg_loss (payoff ratio)

        Retourne la fraction fractionnelle (× self._kelly_frac).
        """
        if avg_loss_pct < 1e-6:
            return self._kelly_frac
        b     = avg_win_pct / avg_loss_pct
        q     = 1.0 - win_rate
        kelly = (win_rate * b - q) / b
        kelly = max(0.0, kelly)
        return min(1.0, kelly * self._kelly_frac)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _annualize_vol(self, vol_24h: float) -> float:
        """vol_24h (fraction sur 24h) → vol annuelle approximative."""
        bars_per_day = 24
        return vol_24h * math.sqrt(365 * bars_per_day / self._bars_yr * self._bars_yr)
