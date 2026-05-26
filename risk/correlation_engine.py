"""
risk/correlation_engine.py — Portfolio Correlation Engine

Tracks la corrélation entre les retours des différentes positions/alphas.
Détecte le régime de corrélation (stress = corrélations tendent vers 1.0).

Usage:
  ce = CorrelationEngine()
  ce.update({"btc_long": 0.01, "eth_long": 0.008, "funding_carry": -0.002})
  matrix = ce.correlation_matrix()
  net_exp = ce.net_exposure({"btc_long": 1.0, "eth_long": 0.5})
  is_stress = ce.is_correlation_stress()
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class CorrelationReport:
    matrix:           pd.DataFrame
    mean_correlation: float
    max_correlation:  float
    is_stress:        bool
    n_positions:      int
    window:           int


class CorrelationEngine:
    """
    Tracks rolling correlations entre les PnL des positions/alphas.

    Régime de stress: corrélation moyenne > stress_threshold (défaut 0.70).
    En stress, toutes les positions deviennent corrélées → diversification illusion.
    """

    def __init__(
        self,
        window:           int   = 60,
        stress_threshold: float = 0.70,
        long_window:      int   = 252,
    ):
        self._window    = window
        self._stress_thr = stress_threshold
        self._pnl:      dict[str, deque] = defaultdict(lambda: deque(maxlen=long_window))
        self._last_corr: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, pnl_dict: dict[str, float]) -> None:
        """Enregistre un vecteur de PnL pour une barre."""
        for name, pnl in pnl_dict.items():
            self._pnl[name].append(float(pnl))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def correlation_matrix(self, window: Optional[int] = None) -> pd.DataFrame:
        w     = window or self._window
        names = [n for n, d in self._pnl.items() if len(d) >= max(w // 2, 5)]
        if len(names) < 2:
            return pd.DataFrame()

        data    = {n: list(self._pnl[n])[-w:] for n in names}
        min_len = min(len(v) for v in data.values())
        df      = pd.DataFrame({n: v[-min_len:] for n, v in data.items()})
        corr    = df.corr()
        self._last_corr = corr
        return corr

    def mean_correlation(self, window: Optional[int] = None) -> float:
        corr = self.correlation_matrix(window)
        if corr.empty or len(corr) < 2:
            return 0.0
        # Uniquement les paires (triangle supérieur, sans la diagonale)
        n   = len(corr)
        vals = []
        for i in range(n):
            for j in range(i + 1, n):
                v = corr.iloc[i, j]
                if not np.isnan(v):
                    vals.append(abs(v))
        return float(np.mean(vals)) if vals else 0.0

    def net_exposure(self, positions: dict[str, float]) -> float:
        """
        Score de concentration du risque [0, 1].

        positions = {alpha_name: notional_fraction}
        0 = parfaitement diversifié | 1 = concentré dans un seul actif.
        """
        total = sum(abs(v) for v in positions.values())
        if total < 1e-9:
            return 0.0
        weights = np.array([abs(v) / total for v in positions.values()])
        # HHI (Herfindahl-Hirschman Index) normalisé
        n   = len(weights)
        hhi = float(np.sum(weights ** 2))
        hhi_min = 1.0 / n if n > 0 else 1.0
        return float(np.clip((hhi - hhi_min) / (1.0 - hhi_min), 0.0, 1.0))

    def is_correlation_stress(self, window: Optional[int] = None) -> bool:
        return self.mean_correlation(window) > self._stress_thr

    def report(self, window: Optional[int] = None) -> CorrelationReport:
        corr = self.correlation_matrix(window)
        mean_c = self.mean_correlation(window)
        max_c  = 0.0
        if not corr.empty:
            n = len(corr)
            for i in range(n):
                for j in range(i + 1, n):
                    v = abs(corr.iloc[i, j])
                    if not np.isnan(v):
                        max_c = max(max_c, v)
        return CorrelationReport(
            matrix           = corr,
            mean_correlation = round(mean_c, 4),
            max_correlation  = round(max_c, 4),
            is_stress        = mean_c > self._stress_thr,
            n_positions      = len(self._pnl),
            window           = window or self._window,
        )

    def regime_adjusted_multiplier(self, base_multiplier: float = 1.0) -> float:
        """Réduit la taille en régime de corrélation élevée."""
        mean_c = self.mean_correlation()
        if mean_c > self._stress_thr:
            reduction = (mean_c - self._stress_thr) / (1.0 - self._stress_thr)
            return base_multiplier * (1.0 - 0.5 * reduction)
        return base_multiplier
