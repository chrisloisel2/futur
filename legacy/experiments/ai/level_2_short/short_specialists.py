"""
ai/level_2/short_specialists.py — FLOTTÉE TRM SHORT v3 (10 spécialistes OHLCV)
================================================================================

v3 — routage exclusif OHLCV-only (symétrique à TRMFleet v2, tiny_specialists.py)

Problème v2 :
  - Contextes basés sur features macro (funding, L/S, OI) absentes des CSV
  - Routage multi-booléen → 5/7 spécialistes disabled (n_train < _MIN_N_TRAIN)
  - general_short absorbait 95%+ des barres

v3 design :
  1. 10 contextes exclusifs basés UNIQUEMENT sur OHLCV + indicateurs techniques
  2. Routage déterministe par escalier de priorité (classify_short_context)
  3. Chaque spécialiste entraîné sur son contexte + general_short sur toutes les barres
  4. Soft routing : p_final = 0.65 × p_specialist + 0.35 × p_general
  5. StandardScaler par spécialiste + class_weight pour déséquilibre

Contextes (10, exclusifs, priorité décroissante) :
  dc_fresh          → death cross récent (< 7 barres) : naissant bear
  vol_expand_bear   → volatilité explosant vers le bas
  dc_mature         → EMA50 < EMA200 établi (> 7 barres)
  breakdown         → structure cassée (sous VWAP + sous EMA20)
  failed_breakout   → faux breakout haussier (échec + upper wick)
  overbought_fade   → extension extrême à la hausse (RSI>65 ou dist_ema>4%)
  vol_compress_bear → compression volatile + biais baissier
  bear_momentum     → momentum fortement négatif + stack EMA bearish
  wick_rejection    → grandes mèches supérieures (rejections)
  general_short     → catch-all

Routage soft :
  p_final = SPECIALIST_W × p_specialist(ctx) + GENERAL_W × p_general
  SPECIALIST_W=0.65, GENERAL_W=0.35
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler


# ─────────────────────────────────────────────────────────────────────────────
# Contextes et routage OHLCV
# ─────────────────────────────────────────────────────────────────────────────

CONTEXT_NAMES = [
    "dc_fresh",
    "vol_expand_bear",
    "dc_mature",
    "breakdown",
    "failed_breakout",
    "overbought_fade",
    "vol_compress_bear",
    "bear_momentum",
    "wick_rejection",
    "general_short",
]

_REASON_MAP: Dict[str, str] = {
    "dc_fresh":          "Death cross récent — momentum baissier naissant",
    "vol_expand_bear":   "Expansion de volatilité baissière",
    "dc_mature":         "Bear établi — EMA50 < EMA200",
    "breakdown":         "Structure cassée — sous VWAP + sous EMA20",
    "failed_breakout":   "Faux breakout haussier — rejet + mèche",
    "overbought_fade":   "Extension extrême à la hausse — fade",
    "vol_compress_bear": "Compression volatile + biais baissier",
    "bear_momentum":     "Momentum fortement négatif",
    "wick_rejection":    "Rejection par grande mèche supérieure",
    "general_short":     "Contexte générique (catch-all)",
}


def classify_short_context(df: pd.DataFrame) -> np.ndarray:
    """
    Assigne UN contexte par barre — vectorisé, O(n), purement OHLCV.

    Priorité décroissante (dc_fresh la plus haute) :
      dc_fresh > vol_expand_bear > dc_mature > breakdown > failed_breakout
      > overbought_fade > vol_compress_bear > bear_momentum > wick_rejection
      > general_short (défaut)
    """
    n = len(df)
    ctx = np.full(n, "general_short", dtype=object)

    def _col(name: str, default: float = 0.0) -> np.ndarray:
        if name in df.columns:
            return df[name].fillna(default).values.astype(np.float64)
        return np.full(n, default, dtype=np.float64)

    ema_spread  = _col("ema_spread_50_200", 0.0)
    rv_ratio    = _col("rv_ratio_24_72",    1.0)
    below_vwap  = _col("below_vwap_4h",     0.0)
    below_ema20 = _col("below_ema20",        0.0)
    failed_high = _col("failed_high_12",     0.0)
    wick_z      = _col("upper_wick_z_24",   0.0)
    upper_wick  = _col("upper_wick_pct",    0.0)
    rsi         = _col("rsi_14",            50.0)
    dist_ema50  = _col("dist_ema_50",        0.0)
    boll_w      = _col("boll_width_20",     0.02)
    mom72       = _col("mom_logret_72",      0.0)
    mom24       = _col("mom_logret_24",      0.0)
    ema_stack   = _col("ema_stack_bearish",  0.0)

    boll_pos = boll_w[boll_w > 0]
    boll_med = float(np.nanmedian(boll_pos)) if len(boll_pos) else 0.02

    # dc_fresh : EMA spread vient de passer sous 0 dans les 7 dernières barres
    if "ema_spread_50_200" in df.columns:
        spread_7ago = df["ema_spread_50_200"].shift(7).fillna(0.0).values.astype(np.float64)
        is_dc_fresh = (ema_spread < 0) & (spread_7ago >= 0)
    else:
        is_dc_fresh = np.zeros(n, dtype=bool)

    is_vol_expand_bear   = (rv_ratio > 1.4)             & (mom24 < 0.0)
    is_dc_mature         = (ema_spread < 0)             & (~is_dc_fresh)
    is_breakdown         = (below_vwap > 0.5)           & (below_ema20 > 0.5)
    is_failed_breakout   = (failed_high > 0.5)          & (wick_z > 0.5)
    is_overbought_fade   = (rsi > 65.0)                 | (dist_ema50 > 0.04)
    is_vol_compress_bear = (boll_w < boll_med * 0.70)   & (ema_spread < 0)
    is_bear_momentum     = (mom72 < -0.04)              & (ema_stack > 0.5)
    is_wick_rejection    = (upper_wick > 0.40)          | (wick_z > 1.5)

    # Assignation priorité croissante : dc_fresh en dernier (priorité max)
    ctx[is_wick_rejection]       = "wick_rejection"
    ctx[is_bear_momentum]        = "bear_momentum"
    ctx[is_vol_compress_bear]    = "vol_compress_bear"
    ctx[is_overbought_fade]      = "overbought_fade"
    ctx[is_failed_breakout]      = "failed_breakout"
    ctx[is_breakdown]            = "breakdown"
    ctx[is_dc_mature]            = "dc_mature"
    ctx[is_vol_expand_bear]      = "vol_expand_bear"
    ctx[is_dc_fresh]             = "dc_fresh"

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Spécialiste individuel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortSpecialist:
    """
    Modèle spécialiste SHORT entraîné sur UN contexte de marché.

    Utilise TOUTES les features (pas de sélection artificielle) avec la même
    capacité que le modèle global. La spécialisation vient des DONNÉES, pas
    des features.
    """
    context_name: str
    features:     List[str]
    clf_:         Optional[HistGradientBoostingClassifier] = field(default=None, repr=False)
    scaler_:      Optional[StandardScaler]                = field(default=None, repr=False)
    val_auc_:     float = 0.0
    n_train_:     int   = 0
    n_pos_:       int   = 0

    def fit_raw(
        self,
        X:             np.ndarray,
        y:             np.ndarray,
        sample_weight: Optional[np.ndarray] = None,
    ) -> "ShortSpecialist":
        valid = y >= 0
        X, y  = X[valid], y[valid]
        if sample_weight is not None:
            sample_weight = sample_weight[valid]

        n_pos = int(y.sum())
        n_neg = len(y) - n_pos
        self.n_train_ = len(y)
        self.n_pos_   = n_pos

        if n_pos < 5 or self.n_train_ < 20:
            return self

        self.scaler_ = StandardScaler()
        Xsc = self.scaler_.fit_transform(X)

        spw = min(n_neg / max(n_pos, 1), 80.0)
        self.clf_ = HistGradientBoostingClassifier(
            max_iter=400,
            max_depth=4,
            learning_rate=0.04,
            l2_regularization=1.0,
            min_samples_leaf=15,
            class_weight={0: 1.0, 1: spw},
            random_state=42,
        )
        self.clf_.fit(Xsc, y, sample_weight=sample_weight)
        return self

    def predict_proba_raw(self, X: np.ndarray) -> np.ndarray:
        """Retourne P(SHORT=1) pour la matrice X brute."""
        if self.clf_ is None or self.scaler_ is None:
            return np.full(len(X), 0.5, dtype=np.float32)
        Xsc = self.scaler_.transform(X)
        return self.clf_.predict_proba(Xsc)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Calibration des seuils par contexte
# ─────────────────────────────────────────────────────────────────────────────

def calibrate_short_context_thresholds(
    fleet:      "TRMShortFleet",
    df_val:     pd.DataFrame,
    ret_val:    np.ndarray,
    cost_short: float = 0.0015,
    min_thr:    float = 0.55,
    max_thr:    float = 0.80,
    min_trades: int   = 5,
) -> Dict[str, float]:
    """
    Calibre UN seuil par contexte en maximisant l'expectancy nette SHORT.

    Arguments
    ---------
    fleet      : TRMShortFleet entraîné
    df_val     : DataFrame val (features + contextes)
    ret_val    : future_ret aligné sur df_val (positif = hausse = perte pour short)
    cost_short : coût aller-retour short (ex. 0.0015 = 15 bps)
    """
    ctx_arr  = classify_short_context(df_val)
    X_val    = fleet._get_X(df_val)
    p_gen    = fleet.specialists["general_short"].predict_proba_raw(X_val)

    thresholds: Dict[str, float] = {}

    for name, spec in fleet.specialists.items():
        if spec.clf_ is None:
            thresholds[name] = min_thr
            continue

        p_spec = spec.predict_proba_raw(X_val)
        if name == "general_short":
            p_ens = p_spec
            sel   = np.ones(len(df_val), dtype=bool)
        else:
            p_ens = fleet.SPECIALIST_W * p_spec + fleet.GENERAL_W * p_gen
            sel   = (ctx_arr == name)

        p_s   = p_ens[sel]
        ret_s = ret_val[sel] if len(ret_val) == len(df_val) else np.zeros(sel.sum())
        valid = np.isfinite(ret_s)
        p_s, ret_s = p_s[valid], ret_s[valid]

        if len(p_s) < min_trades:
            thresholds[name] = min_thr
            continue

        best_thr, best_exp = min_thr, -np.inf
        for thr in np.arange(min_thr, max_thr + 0.001, 0.01):
            m = p_s >= thr
            if m.sum() < min_trades:
                continue
            exp = float((-ret_s[m] - cost_short).mean())
            if exp > best_exp:
                best_exp, best_thr = exp, thr

        thresholds[name] = round(float(best_thr), 2)

    return thresholds


# ─────────────────────────────────────────────────────────────────────────────
# Flottée TRM SHORT v3
# ─────────────────────────────────────────────────────────────────────────────

class TRMShortFleet:
    """
    Flottée de 10 spécialistes SHORT — routage exclusif OHLCV v3.

    API compatible v2 : context_df ignoré (paramètre conservé pour compatibilité).

    Utilisation
    -----------
    >>> fleet = TRMShortFleet(features=FEATURES_SHORT_GAMECHANGER)
    >>> fleet.fit(df_train, y_train, df_val, y_val)
    >>> p = fleet.predict_short_proba(df_test)
    """

    SPECIALIST_W  = 0.65
    GENERAL_W     = 0.35
    CONTEXT_NAMES = CONTEXT_NAMES

    def __init__(
        self,
        features:            List[str],
        n_iter:              int   = 500,
        learning_rate:       float = 0.03,
        max_leaf_nodes:      int   = 31,   # ignoré, max_depth=4 utilisé (compat API v2)
        hard_example_rounds: int   = 2,
    ) -> None:
        self.features            = features
        self.n_iter              = n_iter
        self.learning_rate       = learning_rate
        self.hard_example_rounds = hard_example_rounds

        self.specialists: Dict[str, ShortSpecialist] = {
            name: ShortSpecialist(context_name=name, features=features)
            for name in CONTEXT_NAMES
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Entraînement
    # ─────────────────────────────────────────────────────────────────────────

    def fit(
        self,
        df_train:   pd.DataFrame,
        y:          np.ndarray,
        df_val:     pd.DataFrame,
        y_val:      np.ndarray,
        context_df: object = None,  # ignoré en v3 — conservé pour compat API
    ) -> None:
        """
        Entraîne les 10 spécialistes avec apprentissage récursif (hard examples).

        Arguments
        ---------
        df_train   : DataFrame train (déjà sous-échantillonné si besoin)
        y          : labels train (0/1, -1=ignore)
        df_val     : DataFrame val
        y_val      : labels val
        context_df : ignoré (conservé pour compatibilité API v2)
        """
        t0 = time.time()

        y_arr     = np.asarray(y,     dtype=np.int32)
        y_val_arr = np.asarray(y_val, dtype=np.int32)

        ctx_train = classify_short_context(df_train)
        X_train   = self._get_X(df_train)
        X_val     = self._get_X(df_val)

        n_train  = len(X_train)
        weights  = np.ones(n_train, dtype=np.float64)

        for rnd in range(self.hard_example_rounds):
            for name, spec in self.specialists.items():
                if name == "general_short":
                    ctx_mask = np.ones(n_train, dtype=bool)
                else:
                    ctx_mask = (ctx_train == name)

                if ctx_mask.sum() < 20:
                    continue

                X_ctx = X_train[ctx_mask]
                y_ctx = y_arr[ctx_mask]
                w_ctx = weights[ctx_mask]

                spec.fit_raw(X_ctx, y_ctx, sample_weight=w_ctx)

            # Barres difficiles : haute incertitude de l'ensemble
            if rnd < self.hard_example_rounds - 1:
                p_ens     = self._predict_ensemble_raw(X_train, ctx_train)
                uncertain = np.abs(p_ens - 0.5) < 0.12
                weights   = np.where(uncertain, 3.0, 1.0).astype(np.float64)

        # AUC val par spécialiste
        ctx_val = classify_short_context(df_val)
        self._compute_val_aucs(X_val, y_val_arr, ctx_val)

        dt = time.time() - t0
        self._print_summary(dt, ctx_train, ctx_val)

    # ─────────────────────────────────────────────────────────────────────────
    # Prédiction
    # ─────────────────────────────────────────────────────────────────────────

    def predict_short_proba(
        self,
        df:         pd.DataFrame,
        context_df: object = None,   # ignoré en v3
    ) -> np.ndarray:
        """Retourne P(SHORT=1) pour toutes les barres de df — shape (n,)."""
        ctx_arr = classify_short_context(df)
        X       = self._get_X(df)
        return self._predict_ensemble_raw(X, ctx_arr).astype(np.float32)

    def predict_short_with_context(
        self,
        df:         pd.DataFrame,
        context_df: object = None,   # ignoré en v3
    ) -> pd.DataFrame:
        """
        Retourne un DataFrame avec colonnes :
            p_short, context, p_context, p_general,
            squeeze_risk, no_short, reason
        """
        n       = len(df)
        ctx_arr = classify_short_context(df)
        X       = self._get_X(df)

        p_short = self._predict_ensemble_raw(X, ctx_arr).astype(np.float64)
        p_gen   = self.specialists["general_short"].predict_proba_raw(X).astype(np.float64)

        p_ctx_arr = p_gen.copy()
        for name, spec in self.specialists.items():
            if name == "general_short" or spec.clf_ is None:
                continue
            ctx_m = (ctx_arr == name)
            if ctx_m.any():
                p_ctx_arr[ctx_m] = spec.predict_proba_raw(X[ctx_m]).astype(np.float64)

        squeeze = (
            df["squeeze_risk_score"].fillna(0.0).values
            if "squeeze_risk_score" in df.columns
            else np.zeros(n, dtype=np.float64)
        )
        no_short = np.zeros(n, dtype=bool)

        return pd.DataFrame(
            {
                "p_short":      p_short,
                "context":      ctx_arr,
                "p_context":    p_ctx_arr,
                "p_general":    p_gen,
                "squeeze_risk": squeeze,
                "no_short":     no_short,
                "reason":       np.array([_REASON_MAP.get(c, c) for c in ctx_arr]),
            },
            index=df.index,
        )

    def get_summary(self) -> Dict[str, Dict]:
        """Résumé de chaque spécialiste : n_train, val_auc, enabled, n_pos."""
        return {
            name: {
                "n_train":  spec.n_train_,
                "n_pos":    spec.n_pos_,
                "val_auc":  round(spec.val_auc_, 4),
                "enabled":  spec.clf_ is not None,
            }
            for name, spec in self.specialists.items()
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers internes
    # ─────────────────────────────────────────────────────────────────────────

    def _get_X(self, df: pd.DataFrame) -> np.ndarray:
        """Extrait la matrice features — colonnes manquantes → 0."""
        avail   = [f for f in self.features if f in df.columns]
        missing = len(self.features) - len(avail)
        X = df[avail].fillna(0.0).values.astype(np.float32) if avail \
            else np.zeros((len(df), 0), dtype=np.float32)
        if missing:
            X = np.hstack([X, np.zeros((len(df), missing), dtype=np.float32)])
        return X

    def _predict_ensemble_raw(
        self,
        X:       np.ndarray,
        ctx_arr: np.ndarray,
    ) -> np.ndarray:
        """
        Soft routing vectorisé sur un tableau X déjà extrait.
        p_final = 0.65 × p_specialist(ctx) + 0.35 × p_general
        """
        p_gen = self.specialists["general_short"].predict_proba_raw(X).astype(np.float64)
        p_out = p_gen.copy()

        for name, spec in self.specialists.items():
            if name == "general_short" or spec.clf_ is None:
                continue
            ctx_m = (ctx_arr == name)
            if not ctx_m.any():
                continue
            p_spec = spec.predict_proba_raw(X[ctx_m]).astype(np.float64)
            p_out[ctx_m] = self.SPECIALIST_W * p_spec + self.GENERAL_W * p_gen[ctx_m]

        return p_out.astype(np.float32)

    def _compute_val_aucs(
        self,
        X_val:    np.ndarray,
        y_val:    np.ndarray,
        ctx_val:  np.ndarray,
    ) -> None:
        valid = y_val >= 0
        X_v, y_v, ctx_v = X_val[valid], y_val[valid], ctx_val[valid]

        for name, spec in self.specialists.items():
            if spec.clf_ is None:
                continue
            if name == "general_short":
                X_s, y_s = X_v, y_v
            else:
                m = (ctx_v == name)
                if m.sum() < 10 or y_v[m].sum() < 2 or (y_v[m] == 0).sum() < 2:
                    continue
                X_s, y_s = X_v[m], y_v[m]

            p = spec.predict_proba_raw(X_s)
            try:
                spec.val_auc_ = float(roc_auc_score(y_s, p))
            except Exception:
                pass

    def _print_summary(
        self,
        elapsed:   float,
        ctx_train: np.ndarray,
        ctx_val:   np.ndarray,
    ) -> None:
        enabled  = [n for n, s in self.specialists.items() if s.clf_ is not None]
        n_ctx    = {n: int((ctx_train == n).sum()) for n in CONTEXT_NAMES}
        n_ctx_v  = {n: int((ctx_val   == n).sum()) for n in CONTEXT_NAMES}
        aucs     = {n: round(s.val_auc_, 3) for n, s in self.specialists.items()
                    if s.clf_ is not None}

        print(
            f"\n   TRMShortFleet v3 : {len(enabled)}/{len(CONTEXT_NAMES)} spécialistes actifs  "
            f"rounds={self.hard_example_rounds}  t={elapsed:.1f}s"
        )
        print("   Contextes train : " +
              "  ".join(f"{k[:7]}={v:,}" for k, v in n_ctx.items()))
        print("   Contextes val   : " +
              "  ".join(f"{k[:7]}={v:,}" for k, v in n_ctx_v.items()))
        print("   AUC val         : " +
              "  ".join(f"{k[:7]}={v:.3f}" for k, v in aucs.items()))

        disabled = [n for n, s in self.specialists.items() if s.clf_ is None]
        if disabled:
            print(f"   Désactivés (n_train<20 ou n_pos<5) : {disabled}")
