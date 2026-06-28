"""
ai/level_0/return_predictor.py — Prédiction du rendement forward (régression)
==============================================================================

Entraîné en parallèle du classificateur TRM sur les mêmes features.
Prédit log(C[t+8] / C[t]) — le rendement brut sur 8h.

Utilisation :
  - En sizing : signal_quality = p_long × boost(pred_ret_zscore)
    Un signal fort + rendement prédit élevé = taille plus grande
  - En filtrage : si pred_ret < 0 mais p_long > thr → WATCH (pas LONG)
  - En meta-apprentissage : expose la "confiance en amplitude" aux couches supérieures

Architecture :
  Ridge regression avec :
    - StandardScaler sur les features
    - alpha calibré par cross-validation rapide (4 folds)
    - Clip du output à [-0.10, +0.10] (10% max pred sur 8h = raisonnable)

Interface :
  pred = ReturnPredictor()
  pred.fit(df_train, features, train_mask)
  z = pred.predict_zscore(df_bar, rv_24)   # → float ∈ [-3, +3]
  boost = pred.size_boost(z)               # → multiplicateur ∈ [0.5, 2.0]
"""
from __future__ import annotations

import warnings
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler

from ai.level_0.constants import TARGET_COL, COST_PCT

warnings.filterwarnings("ignore")

_CLIP_RET = 0.10          # clip les prédictions à ±10%
_ALPHAS   = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]


class ReturnPredictor:
    """
    Régression Ridge : features → rendement forward 8h.

    La prédiction est normalisée par rv_24 pour produire un z-score
    interprétable comme "combien de vol annuelles ce mouvement représente".
    """

    def __init__(self) -> None:
        self.scaler_:  Optional[StandardScaler] = None
        self.model_:   Optional[RidgeCV]        = None
        self.features_: List[str]               = []
        self.train_ret_std_: float              = 0.02   # std des rendements train
        self.fitted_: bool                      = False

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(
        self,
        df:         pd.DataFrame,
        features:   List[str],
        train_mask: np.ndarray,
    ) -> "ReturnPredictor":
        """
        Entraîne la régression sur les barres de train_mask.

        df doit contenir TARGET_COL (future_ret_8h) et toutes les features.
        """
        if TARGET_COL not in df.columns:
            return self

        avail = [f for f in features if f in df.columns]
        if len(avail) < 5:
            return self

        self.features_ = avail

        df_tr  = df.iloc[np.where(train_mask)[0]]
        y_mask = df_tr[TARGET_COL].notna()
        df_tr  = df_tr[y_mask]

        if len(df_tr) < 200:
            return self

        X = df_tr[avail].fillna(0.0).values.astype(np.float64)
        y = df_tr[TARGET_COL].values.astype(np.float64)
        y = np.clip(y, -_CLIP_RET, _CLIP_RET)

        self.train_ret_std_ = float(np.std(y)) or 0.02

        scaler = StandardScaler()
        X_sc   = scaler.fit_transform(X)

        model = RidgeCV(alphas=_ALPHAS, cv=4, scoring="neg_mean_squared_error")
        model.fit(X_sc, y)

        self.scaler_ = scaler
        self.model_  = model
        self.fitted_ = True

        train_pred = model.predict(X_sc)
        r2 = float(1.0 - np.var(y - train_pred) / max(np.var(y), 1e-9))
        print(f"   ReturnPredictor : α={model.alpha_:.2f}  R²={r2:.4f}  "
              f"n={len(y):,}  ret_std={self.train_ret_std_:.4f}")

        return self

    # ── Inférence ─────────────────────────────────────────────────────────────

    def predict_return(self, df: pd.DataFrame) -> np.ndarray:
        """Prédit le rendement forward 8h pour chaque row de df."""
        if not self.fitted_ or self.model_ is None:
            return np.zeros(len(df))

        avail = [f for f in self.features_ if f in df.columns]
        if not avail:
            return np.zeros(len(df))

        X = df[avail].fillna(0.0).values.astype(np.float64)
        X_sc = self.scaler_.transform(X)
        pred = self.model_.predict(X_sc)
        return np.clip(pred, -_CLIP_RET, _CLIP_RET)

    def predict_zscore(self, df: pd.DataFrame, rv_24: float = 0.02) -> np.ndarray:
        """
        Rendement prédit normalisé par la volatilité : combien de σ de vol 24h.

        z > 0 : mouvement prédit positif
        |z| < 1 : mouvement "normal"
        |z| > 2 : mouvement exceptionnel
        """
        pred = self.predict_return(df)
        vol  = max(rv_24, 1e-4)
        z    = pred / vol
        return np.clip(z, -3.0, 3.0)

    def size_boost(self, z_score: float) -> float:
        """
        Multiplicateur de taille basé sur le z-score du rendement prédit.

        z ≤ 0   → 0.5  (signal qualitativement correct mais retour prédit négatif)
        z = 0.5 → 1.0  (neutre)
        z = 1.0 → 1.25 (bon signal)
        z = 2.0 → 1.75 (très fort signal)
        z ≥ 3.0 → 2.0  (cap)
        """
        if z_score <= 0.0:
            # Réduire si rendement prédit négatif (garde-fou)
            return max(0.5, 1.0 + z_score * 0.25)
        # Croissance concave : évite de sur-lever les gros z
        boost = 1.0 + min(z_score, 3.0) * 0.33
        return min(2.0, boost)

    def single_zscore(self, bar: "pd.Series", rv_24: float = 0.02) -> float:
        """Z-score pour une seule barre (inférence live)."""
        df_single = pd.DataFrame([bar.to_dict()])
        zs = self.predict_zscore(df_single, rv_24=rv_24)
        return float(zs[0])
