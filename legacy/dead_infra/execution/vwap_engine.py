"""
execution/vwap_engine.py — VWAP Engine (Volume-Weighted Average Price)

Découpe la commande proportionnellement au volume attendu par heure.
Utilise le profil de volume historique (volume moyen par heure UTC).

Si pas de profil historique → fallback vers TWAP uniforme.

Usage:
  vwap = VWAPEngine()
  vwap.fit_volume_profile(df)           # historique avec vol_ratio_24, hour_sin, hour_cos
  schedule = vwap.schedule(1.0, 8)      # 1 BTC sur 8 barres
  for slice_order in schedule:
      execute(slice_order)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class VWAPSlice:
    qty:        float
    bar_offset: int       # barre relative depuis maintenant
    weight:     float     # part du volume total attendu


class VWAPEngine:
    """
    VWAP basé sur un profil de volume horaire.

    Le profil est calculé depuis les features existantes:
    - vol_ratio_24 (ratio de volume par rapport à la moyenne 24h)
    - hour_sin / hour_cos (encodage cyclique de l'heure)
    """

    N_HOURS = 24

    def __init__(self):
        self._profile: Optional[np.ndarray] = None   # (24,) poids par heure UTC
        self._fitted  = False

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit_volume_profile(
        self,
        df: pd.DataFrame,
        vol_col:      str = "vol_ratio_24",
        hour_sin_col: str = "hour_sin",
        hour_cos_col: str = "hour_cos",
    ) -> "VWAPEngine":
        """
        Calcule le profil de volume moyen par heure UTC.

        Utilise hour_sin + hour_cos pour reconstruire l'heure.
        """
        if vol_col not in df.columns:
            return self
        if hour_sin_col not in df.columns or hour_cos_col not in df.columns:
            return self

        df = df.dropna(subset=[vol_col, hour_sin_col, hour_cos_col])
        if len(df) < 100:
            return self

        profile = np.zeros(self.N_HOURS)
        counts  = np.zeros(self.N_HOURS)

        for _, row in df.iterrows():
            hour = self._decode_hour(float(row[hour_sin_col]), float(row[hour_cos_col]))
            profile[hour] += float(row[vol_col])
            counts[hour]  += 1

        # Éviter les heures sans données
        counts = np.maximum(counts, 1)
        profile = profile / counts

        # Normaliser → les poids somment à 1
        total = profile.sum()
        if total > 0:
            self._profile = profile / total
        else:
            self._profile = np.full(self.N_HOURS, 1.0 / self.N_HOURS)

        self._fitted = True
        return self

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule(
        self,
        quantity:     float,
        horizon_bars: int,
        start_hour:   int = 0,    # heure UTC de départ
    ) -> list[VWAPSlice]:
        """
        Génère le schedule VWAP pour horizon_bars barres.

        Chaque slice a une quantité proportionnelle au volume attendu
        sur l'heure correspondante.
        """
        if self._profile is None:
            return self._uniform_schedule(quantity, horizon_bars)

        # Extraire les poids pour les heures correspondantes
        hours   = [(start_hour + i) % self.N_HOURS for i in range(horizon_bars)]
        weights = self._profile[hours]
        total_w = weights.sum()

        if total_w < 1e-9:
            return self._uniform_schedule(quantity, horizon_bars)

        weights = weights / total_w
        return [
            VWAPSlice(
                qty        = round(quantity * w, 8),
                bar_offset = i,
                weight     = round(float(w), 6),
            )
            for i, w in enumerate(weights)
        ]

    def is_fitted(self) -> bool:
        return self._fitted

    def peak_hours(self, top_n: int = 6) -> list[int]:
        """Heures UTC avec le plus de volume."""
        if self._profile is None:
            return list(range(top_n))
        return list(np.argsort(self._profile)[-top_n:][::-1])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _uniform_schedule(self, quantity: float, horizon_bars: int) -> list[VWAPSlice]:
        qty_per = quantity / max(horizon_bars, 1)
        return [
            VWAPSlice(qty=round(qty_per, 8), bar_offset=i, weight=round(1.0 / horizon_bars, 6))
            for i in range(horizon_bars)
        ]

    def _decode_hour(self, sin_h: float, cos_h: float) -> int:
        angle = math.atan2(sin_h, cos_h)
        hour  = (angle / (2 * math.pi) * 24) % 24
        return int(hour) % self.N_HOURS
