"""
execution/slippage_model.py — Slippage Prediction Model

Modèle linéaire: slippage_bps = α + β₁×(qty/ADV) + β₂×atr_pct + β₃×intrabar_range

Features utilisées (disponibles dans les parquets existants):
  - vol_ratio_24   (proxy de l'ADV — plus c'est haut, plus liquide)
  - atr_pct_14     (volatilité ATR)
  - intrabar_range_pct (proxy bid-ask spread)

Sans données historiques de trades réels → paramètres prudents par défaut.
Avec données réelles → fit() sur historical_trades_df.

Usage:
  model = SlippageModel()
  bps = model.predict(quantity_frac=0.001, atr_pct=0.025, spread_proxy=0.01)
  adj_threshold = model.adjust_threshold(raw_threshold=0.54, slippage_bps=bps)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class SlippageEstimate:
    bps:           float    # slippage estimé en bps
    pct:           float    # slippage en fraction (bps / 10000)
    market_impact: float    # impact de marché seul (sans bid-ask)
    bid_ask:       float    # composante bid-ask
    is_modeled:    bool     # True si fit sur données réelles, False si par défaut


class SlippageModel:
    """
    Modèle de slippage linéaire avec paramètres par défaut conservateurs.

    slippage_bps = α + β₁×(qty_frac / vol_ratio) + β₂×atr_pct×10000 + β₃×spread_pct×10000

    Paramètres par défaut (calibrés sur crypto spot 2023-2024):
      α = 0.5 bps  (bid-ask minimum)
      β₁ = 8.0     (impact de marché: 1% ADV → 8 bps)
      β₂ = 0.8     (contribution de la vol ATR)
      β₃ = 0.5     (contribution du spread intrabar)
    """

    DEFAULT_PARAMS = {
        "alpha":       2.0,    # 2 bps constant (bid-ask floor)
        "beta_impact": 8.0,    # market impact: 1% ADV → 8 bps
        "beta_atr":    0.005,  # ATR contribution (0.005 × atr_pct × 10000 = 0.05bps per 1% ATR)
        "beta_spread": 0.05,   # spread contribution (0.05 × range × 10000 = 5bps for 1% range)
    }

    def __init__(self):
        self._params  = dict(self.DEFAULT_PARAMS)
        self._fitted  = False
        self._n_obs   = 0

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> "SlippageModel":
        """
        Calibre le modèle sur des données historiques de trades réels.

        df doit avoir les colonnes:
          actual_slippage_bps, quantity_frac, vol_ratio, atr_pct, intrabar_range_pct
        """
        required = ["actual_slippage_bps", "quantity_frac", "vol_ratio",
                    "atr_pct", "intrabar_range_pct"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return self   # impossible de fitter sans les données

        df = df.dropna(subset=required)
        if len(df) < 20:
            return self

        # Construire les features
        X = np.column_stack([
            np.ones(len(df)),
            df["quantity_frac"].values / df["vol_ratio"].values.clip(0.01),
            df["atr_pct"].values * 10000,
            df["intrabar_range_pct"].values * 10000,
        ])
        y = df["actual_slippage_bps"].values

        # OLS
        try:
            coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
            self._params = {
                "alpha":       max(0.0, coef[0]),
                "beta_impact": max(0.0, coef[1]),
                "beta_atr":    max(0.0, coef[2]),
                "beta_spread": max(0.0, coef[3]),
            }
            self._fitted = True
            self._n_obs  = len(df)
        except Exception:
            pass

        return self

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(
        self,
        quantity_frac:  float,   # fraction du volume quotidien (ex: 0.001 = 0.1%)
        atr_pct:        float,   # ATR en fraction (ex: 0.025 = 2.5%)
        spread_proxy:   float,   # intrabar_range_pct (ex: 0.01 = 1%)
        vol_ratio:      float = 1.0,
    ) -> SlippageEstimate:
        p    = self._params
        rel  = quantity_frac / max(vol_ratio, 0.01)

        market_impact = p["beta_impact"] * rel
        atr_component = p["beta_atr"] * atr_pct * 10000
        spread_comp   = p["beta_spread"] * spread_proxy * 10000
        total_bps     = p["alpha"] + market_impact + atr_component + spread_comp
        total_bps     = max(0.0, total_bps)

        return SlippageEstimate(
            bps           = round(total_bps, 4),
            pct           = round(total_bps / 10000, 6),
            market_impact = round(market_impact, 4),
            bid_ask       = round(spread_comp + p["alpha"], 4),
            is_modeled    = self._fitted,
        )

    def predict_from_bar(self, bar: dict | pd.Series, quantity_frac: float = 0.001) -> SlippageEstimate:
        atr_pct   = float(bar.get("atr_pct_14",        0.02))
        spread    = float(bar.get("intrabar_range_pct", 0.01))
        vol_ratio = float(bar.get("vol_ratio_24",       1.0))
        return self.predict(quantity_frac, atr_pct, spread, vol_ratio)

    def adjust_threshold(
        self,
        raw_threshold: float,
        slippage_bps:  float,
    ) -> float:
        """
        Ajuste le seuil de probabilité pour compenser le slippage attendu.

        Plus le slippage est élevé → le seuil doit être plus haut
        (on a besoin d'un edge plus fort pour justifier le trade).
        """
        slippage_pct   = slippage_bps / 10000
        # Règle heuristique: 1bps de slippage ≈ 0.001 d'augmentation de seuil
        adjustment     = slippage_pct * 0.04
        return min(0.70, raw_threshold + adjustment)
