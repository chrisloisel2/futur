"""
src/institutional/portfolio/correlation_model.py
─────────────────────────────────────────────────────────────────────────────
Modèle de corrélation : buckets statiques + matrice de corrélation glissante.

On contrôle le risque par CORRÉLATION (buckets), pas par interdiction totale :
deux longs très corrélés (BTC+ETH) comptent dans le même bucket "majors".
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from src.institutional.engines.base import DEFAULT_CORRELATION_BUCKETS, correlation_bucket_for


def bucket_for(asset: str) -> str:
    return correlation_bucket_for(asset)


class CorrelationModel:
    """Buckets statiques + corrélation empirique optionnelle (fenêtre glissante)."""

    def __init__(self, window: int = 240, high_corr: float = 0.7):
        self.window = window
        self.high_corr = high_corr
        self._corr: Optional[pd.DataFrame] = None
        self.buckets = dict(DEFAULT_CORRELATION_BUCKETS)

    def update(self, prices: Dict[str, pd.Series]) -> "CorrelationModel":
        """Recalcule la matrice de corrélation depuis un dict asset→close."""
        rets = {}
        for a, s in prices.items():
            r = s.sort_index().pct_change().tail(self.window)
            if r.notna().sum() > 10:
                rets[a] = r
        if len(rets) >= 2:
            self._corr = pd.DataFrame(rets).corr()
        return self

    def correlation(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        if self._corr is not None and a in self._corr.index and b in self._corr.columns:
            v = self._corr.loc[a, b]
            if pd.notna(v):
                return float(v)
        # fallback : même bucket → forte corrélation présumée
        return 0.8 if self.bucket(a) == self.bucket(b) else 0.2

    def bucket(self, asset: str) -> str:
        return self.buckets.get(asset, "other")

    def grouped_exposure(self, exposures: Dict[str, float]) -> Dict[str, float]:
        """Agrège les expositions par bucket de corrélation."""
        out: Dict[str, float] = {}
        for asset, exp in exposures.items():
            out[self.bucket(asset)] = out.get(self.bucket(asset), 0.0) + exp
        return out

    def correlated_group(self, asset: str, others: list) -> list:
        """Renvoie les actifs corrélés > high_corr à `asset` parmi `others`."""
        return [o for o in others if self.correlation(asset, o) >= self.high_corr]
