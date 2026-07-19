"""
risk/dynamic_sizing.py — Dynamic Position Sizing v2

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


# ─── Portfolio-level correlation cap (v2) ────────────────────────────────────

# Corrélations historiques crypto approximatives (bull market)
_DEFAULT_CRYPTO_CORR: dict = {
    ("BTCUSDT", "ETHUSDT"):  0.88,
    ("BTCUSDT", "BNBUSDT"):  0.82,
    ("BTCUSDT", "SOLUSDT"):  0.80,
    ("BTCUSDT", "XRPUSDT"):  0.75,
    ("BTCUSDT", "ADAUSDT"):  0.78,
    ("BTCUSDT", "AVAXUSDT"): 0.79,
    ("BTCUSDT", "DOGEUSDT"): 0.72,
    ("BTCUSDT", "DOTUSDT"):  0.80,
    ("BTCUSDT", "LINKUSDT"): 0.78,
    ("ETHUSDT", "BNBUSDT"):  0.85,
    ("ETHUSDT", "SOLUSDT"):  0.82,
    ("ETHUSDT", "ADAUSDT"):  0.83,
    ("ETHUSDT", "AVAXUSDT"): 0.84,
}


def _get_corr(a: str, b: str) -> float:
    """Retourne la corrélation estimée entre deux assets."""
    key = tuple(sorted([a, b]))
    return _DEFAULT_CRYPTO_CORR.get(key, 0.75)   # 0.75 par défaut


def apply_portfolio_correlation_cap(
    open_positions:   dict,          # {symbol: {"size": float, "rv_24": float}}
    new_symbol:       str,
    new_size:         float,
    max_portfolio_vol: float = 0.20, # 20% vol annuelle portfolio cible
    target_vol:       float = 0.15,
) -> float:
    """
    Réduit new_size si l'ajout de cette position fait dépasser la vol portfolio cible.

    open_positions : dict des positions déjà ouvertes (excl. la nouvelle)
    new_symbol     : symbol qu'on veut ouvrir
    new_size       : taille souhaitée (fraction du capital, ex : 0.25)
    max_portfolio_vol : seuil de vol annuelle portfolio (défaut 20%)

    Retourne la taille ajustée (≤ new_size).
    """
    if not open_positions:
        return new_size  # première position : pas de corrélation

    # Construire la matrice de covariance approximative
    all_syms = list(open_positions.keys()) + [new_symbol]
    n = len(all_syms)
    sizes = [open_positions[s]["size"] for s in all_syms[:-1]] + [new_size]
    vols  = [open_positions[s].get("rv_24", 0.02) for s in all_syms[:-1]] + [0.02]

    # rv_24 = realized_volatility_20 = std(log_ret, 20) × √20
    # → vol_hourly = rv_24 / √20
    # → vol_annual = vol_hourly × √8760 = rv_24 × √(8760/20)
    _ANN = math.sqrt(8760 / 20)   # ≈ 20.93
    vols_annual = [v * _ANN for v in vols]

    # Matrice de covariance (diagonale = var, off-diag = corr × σi × σj)
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                cov[i][j] = vols_annual[i] ** 2
            else:
                rho = _get_corr(all_syms[i], all_syms[j])
                cov[i][j] = rho * vols_annual[i] * vols_annual[j]

    # Vol portfolio = sqrt(w' Σ w)
    w = sizes[:]
    port_var = sum(w[i] * w[j] * cov[i][j] for i in range(n) for j in range(n))
    port_vol = math.sqrt(max(port_var, 0.0))

    if port_vol <= max_portfolio_vol:
        return new_size  # pas de problème

    # Résoudre pour la taille max de la nouvelle position
    # Vol portfolio = f(new_size) — on fait une recherche binaire
    lo, hi = 0.0, new_size
    for _ in range(20):
        mid = (lo + hi) / 2
        w[-1] = mid
        pv = sum(w[i] * w[j] * cov[i][j] for i in range(n) for j in range(n))
        if math.sqrt(max(pv, 0.0)) <= max_portfolio_vol:
            lo = mid
        else:
            hi = mid

    adjusted = round(lo, 4)
    if adjusted < new_size * 0.05:
        adjusted = 0.0  # trop corrélé → ne pas ouvrir

    return adjusted
