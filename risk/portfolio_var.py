"""
risk/portfolio_var.py — Portfolio VaR + CVaR (Expected Shortfall)

Méthodes:
  Historical Simulation: empirique sur les 250 dernières barres
  Paramétrique:          distribution normale (rapide, moins précis)

Métriques:
  VaR 95%  : perte maximale 95% du temps (1 bar horizon)
  VaR 99%  : perte maximale 99% du temps
  CVaR 95% : Expected Shortfall — moyenne des pertes au-delà du VaR 95%
  CVaR 99% : idem à 99%

Usage:
  var_engine = PortfolioVaR(window=250)
  var_engine.update(pnl_series)         # pd.Series de PnL historique
  report = var_engine.report()
  if var_engine.var_breach(realized_pnl):
      # déclencher kill switch
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class VaRReport:
    var_95:  float    # positive number = max expected loss
    var_99:  float
    cvar_95: float    # Expected Shortfall
    cvar_99: float
    n_obs:   int
    method:  str      # "historical" | "parametric"

    def summary(self) -> dict:
        return {
            "var_95_pct":  round(self.var_95  * 100, 4),
            "var_99_pct":  round(self.var_99  * 100, 4),
            "cvar_95_pct": round(self.cvar_95 * 100, 4),
            "cvar_99_pct": round(self.cvar_99 * 100, 4),
            "n_obs":       self.n_obs,
            "method":      self.method,
        }

    def is_valid(self, min_obs: int = 30) -> bool:
        return self.n_obs >= min_obs


class PortfolioVaR:
    """
    VaR et CVaR historiques sur un portefeuille de positions.

    Les PnL sont en fraction du capital (ex: 0.01 = +1%, -0.02 = -2%).
    """

    def __init__(self, window: int = 250):
        self._window = window
        self._pnl:   deque[float] = deque(maxlen=window)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, pnl: float) -> None:
        self._pnl.append(float(pnl))

    def update_series(self, series: pd.Series | list) -> None:
        for p in series:
            self._pnl.append(float(p))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def var(self, confidence: float = 0.95, method: str = "historical") -> float:
        """
        VaR pour un horizon de 1 barre.

        Returns: VaR positif (ex: 0.02 = 2% de perte max à ce niveau de confiance)
        """
        if len(self._pnl) < 10:
            return float("inf")
        arr = np.array(self._pnl)
        if method == "historical":
            return float(-np.percentile(arr, (1 - confidence) * 100))
        # Paramétrique (normal)
        mu, sigma = arr.mean(), arr.std()
        from scipy import stats
        return float(-(mu + sigma * stats.norm.ppf(1 - confidence)))

    def cvar(self, confidence: float = 0.95, method: str = "historical") -> float:
        """
        CVaR = Expected Shortfall (moyenne des pertes au-delà du VaR).
        """
        if len(self._pnl) < 10:
            return float("inf")
        arr   = np.array(self._pnl)
        var_q = np.percentile(arr, (1 - confidence) * 100)
        tail  = arr[arr <= var_q]
        if len(tail) == 0:
            return -float(var_q)
        return float(-tail.mean())

    def var_breach(self, realized_pnl: float, confidence: float = 0.99) -> bool:
        """
        True si le PnL réalisé est pire que le VaR (breach).
        realized_pnl < -VaR → breach.
        """
        v = self.var(confidence)
        return realized_pnl < -v

    def report(self) -> VaRReport:
        n = len(self._pnl)
        method = "historical" if n >= 30 else "parametric"
        return VaRReport(
            var_95  = self.var(0.95, method),
            var_99  = self.var(0.99, method),
            cvar_95 = self.cvar(0.95, method),
            cvar_99 = self.cvar(0.99, method),
            n_obs   = n,
            method  = method,
        )

    def empty_report(self) -> VaRReport:
        return VaRReport(var_95=0.02, var_99=0.04, cvar_95=0.03, cvar_99=0.05,
                         n_obs=0, method="default")

    @property
    def n_obs(self) -> int:
        return len(self._pnl)
