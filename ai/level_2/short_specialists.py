"""
ai/level_2/short_specialists.py — FLOTTÉE TRM SHORT (spécialistes par contexte)
================================================================================

TRMShortFleet — architecture symétrique à TRMFleet (tiny_specialists.py) mais
orientée SHORT et guidée par les contextes sémantiques définis dans
level_1/short_rules.py plutôt que par des contextes techniques.

Contextes (7 spécialistes) :
    crowded_longs    → foule extrêmement longée : fade de la crowd
    breakdown        → structure cassée : continuation baissière post-cassure
    failed_breakout  → faux breakout haussier : retournement rapide
    liquidity_stress → stress de liquidité : cascades de liquidation longs
    bear_continuation→ trend baissier établi : continuation momentum
    macro_riskoff    → régime risk-off global : macro force baissière
    general_short    → contexte générique (catch-all)

Routage :
    p_final = 0.70 × p_ctx + 0.30 × p_general
    Si 2 contextes actifs :
      p_final = 0.50 × p_top1 + 0.25 × p_top2 + 0.25 × p_general

Apprentissage récursif (hard examples) :
    Round 1 : entraînement normal sur les barres du contexte
    Round 2 : sample_weight ×3 sur les barres difficiles (hard examples)
    Round 3 : optionnel si val AUC s'améliore > 0.005

Calibration :
    Platt (LogisticRegression C=1.0) si n_val >= 200
    Isotonic sinon
    Spécialiste désactivé si n_train < 50 (pas assez de données de contexte)

Note :
    HistGradientBoostingClassifier ne supporte pas class_weight directement
    dans la version standard de sklearn — on utilise sample_weight au lieu.
    Les classes déséquilibrées sont compensées par un sample_weight calculé
    sur le ratio neg/pos, puis ×3 sur les hard examples au round 2.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression as PlattLR
from sklearn.metrics import roc_auc_score


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

CONTEXTS = [
    "crowded_longs",
    "breakdown",
    "failed_breakout",
    "liquidity_stress",
    "bear_continuation",
    "macro_riskoff",
    "general_short",
]

# Colonnes ctx_* correspondantes (générées par short_rules.compute_short_permission_context)
_CTX_COL: Dict[str, str] = {
    "crowded_longs":    "ctx_crowded_longs",
    "breakdown":        "ctx_breakdown",
    "failed_breakout":  "ctx_failed_breakout",
    "liquidity_stress": "ctx_liquidity_stress",
    "bear_continuation":"ctx_bear_continuation",
    "macro_riskoff":    "ctx_macro_riskoff",
    "general_short":    "ctx_general_short",
}

# Poids du routage
_W_CTX     = 0.70
_W_GEN     = 0.30
_W_CTX2    = 0.50    # si 2 contextes actifs : top-1
_W_CTX2_2  = 0.25    # top-2
_W_GEN2    = 0.25    # général

_MIN_N_TRAIN    = 50    # seuil minimal pour activer un spécialiste
_HARD_THRESHOLD_HI = 0.55  # y=0 mais p > 0.55 → hard
_HARD_THRESHOLD_LO = 0.45  # y=1 mais p < 0.45 → hard
_HARD_WEIGHT    = 3.0
_AUC_MIN_IMPROVE = 0.005   # amélioration minimale pour le round 3


# ─────────────────────────────────────────────────────────────────────────────
# Spécialiste individuel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortSpecialist:
    """
    Modèle spécialiste short entraîné sur UN contexte de marché.

    Attributs publics
    -----------------
    name        : identifiant du contexte (ex. "crowded_longs")
    context_col : colonne ctx_* correspondante dans le DataFrame de contexte
    model       : HistGradientBoostingClassifier entraîné (None si désactivé)
    calibrator  : calibrateur Platt ou Isotonic (None si non calibré)
    threshold   : seuil de décision (défaut 0.65)
    enabled     : False si n_train < _MIN_N_TRAIN
    val_auc     : AUC sur val (0.0 si non mesuré)
    n_train     : nombre de barres d'entraînement
    """
    name:        str
    context_col: str
    model:       Optional[HistGradientBoostingClassifier] = field(default=None, repr=False)
    calibrator:  Optional[Any]                            = field(default=None, repr=False)
    threshold:   float = 0.65
    enabled:     bool  = True
    val_auc:     float = 0.0
    n_train:     int   = 0


# ─────────────────────────────────────────────────────────────────────────────
# Flottée principale
# ─────────────────────────────────────────────────────────────────────────────

class TRMShortFleet:
    """
    Flottée de spécialistes SHORT orientée par contexte sémantique.

    Usage minimal
    -------------
    >>> fleet = TRMShortFleet(features=FEATURES_SHORT)
    >>> fleet.fit(df_train, y_train, df_val, y_val, context_df_train)
    >>> result = fleet.predict_short_with_context(df_test, context_df_test)

    context_df doit contenir les colonnes ctx_* générées par
    level_1.short_rules.compute_short_permission_context().
    """

    CONTEXTS = CONTEXTS

    def __init__(
        self,
        features: List[str],
        n_iter: int = 500,
        learning_rate: float = 0.03,
        max_leaf_nodes: int = 31,
        hard_example_rounds: int = 2,
    ) -> None:
        self.features            = features
        self.n_iter              = n_iter
        self.learning_rate       = learning_rate
        self.max_leaf_nodes      = max_leaf_nodes
        self.hard_example_rounds = hard_example_rounds

        self.specialists: Dict[str, ShortSpecialist] = {
            name: ShortSpecialist(name=name, context_col=_CTX_COL[name])
            for name in CONTEXTS
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
        context_df: pd.DataFrame,
    ) -> None:
        """
        Entraîne chaque spécialiste sur les barres de son contexte.

        Arguments
        ---------
        df_train   : DataFrame train avec colonnes features
        y          : labels train (0/1, -1 = ignore)
        df_val     : DataFrame val avec colonnes features
        y_val      : labels val
        context_df : DataFrame des ctx_* (même index que df_train)
                     — généré par compute_short_permission_context()

        Algorithme
        ----------
        Round 1 : entraînement normal avec sample_weight basé sur ratio neg/pos
        Round 2 : hard examples (y=0 & p>0.55 OU y=1 & p<0.45) → weight ×3
                  ré-entraîner si val AUC ne régresse pas
        Round 3 (optionnel) : uniquement si val AUC s'améliore > 0.005
        """
        t0 = time.time()

        X_train = self._get_X(df_train)
        y_arr   = np.asarray(y, dtype=np.int32)
        valid   = y_arr >= 0
        X_train = X_train[valid]
        y_arr   = y_arr[valid]
        # Aligner context_df sur valid
        ctx_valid = context_df.iloc[np.where(valid)[0]] if isinstance(context_df.index, pd.RangeIndex) \
                    else context_df.loc[df_train.index[valid]]

        X_val_full = self._get_X(df_val)
        y_val_arr  = np.asarray(y_val, dtype=np.int32)
        val_valid  = y_val_arr >= 0
        X_val_c    = X_val_full[val_valid]
        y_val_c    = y_val_arr[val_valid]

        for name, spec in self.specialists.items():
            ctx_col = spec.context_col
            is_gen  = (name == "general_short")

            # ── Masque de contexte ────────────────────────────────────────────
            if is_gen:
                ctx_mask = np.ones(len(X_train), dtype=bool)
            elif ctx_col in ctx_valid.columns:
                ctx_mask = ctx_valid[ctx_col].values.astype(bool)
            else:
                ctx_mask = np.ones(len(X_train), dtype=bool)

            n_ctx = int(ctx_mask.sum())
            spec.n_train = n_ctx

            if n_ctx < _MIN_N_TRAIN:
                spec.enabled = False
                continue

            X_ctx  = X_train[ctx_mask]
            y_ctx  = y_arr[ctx_mask]
            sw_base = self._compute_sample_weight(y_ctx)

            # ── Round 1 ───────────────────────────────────────────────────────
            clf = self._make_clf()
            clf.fit(X_ctx, y_ctx, sample_weight=sw_base)
            auc_r1 = self._val_auc(clf, X_val_c, y_val_c)

            # ── Round 2 : hard examples ───────────────────────────────────────
            if self.hard_example_rounds >= 2:
                p_r1    = clf.predict_proba(X_ctx)[:, 1]
                hard    = (
                    ((y_ctx == 0) & (p_r1 > _HARD_THRESHOLD_HI))
                    | ((y_ctx == 1) & (p_r1 < _HARD_THRESHOLD_LO))
                )
                sw_r2   = sw_base.copy()
                sw_r2[hard] *= _HARD_WEIGHT

                clf2 = self._make_clf()
                clf2.fit(X_ctx, y_ctx, sample_weight=sw_r2)
                auc_r2 = self._val_auc(clf2, X_val_c, y_val_c)

                if auc_r2 >= auc_r1 - 0.001:   # tolérance de 0.1 point AUC
                    clf    = clf2
                    auc_r1 = auc_r2

            # ── Round 3 optionnel : si val AUC améliore > 0.005 ───────────────
            if self.hard_example_rounds >= 3:
                p_r2   = clf.predict_proba(X_ctx)[:, 1]
                hard3  = (
                    ((y_ctx == 0) & (p_r2 > _HARD_THRESHOLD_HI))
                    | ((y_ctx == 1) & (p_r2 < _HARD_THRESHOLD_LO))
                )
                sw_r3  = sw_base.copy()
                sw_r3[hard3] *= _HARD_WEIGHT

                clf3   = self._make_clf()
                clf3.fit(X_ctx, y_ctx, sample_weight=sw_r3)
                auc_r3 = self._val_auc(clf3, X_val_c, y_val_c)

                if auc_r3 > auc_r1 + _AUC_MIN_IMPROVE:
                    clf    = clf3
                    auc_r1 = auc_r3

            spec.model   = clf
            spec.val_auc = auc_r1

            # ── Calibration ───────────────────────────────────────────────────
            spec.calibrator = self._calibrate(clf, X_val_c, y_val_c)

        dt = time.time() - t0
        self._print_summary(dt)

    # ─────────────────────────────────────────────────────────────────────────
    # Prédiction
    # ─────────────────────────────────────────────────────────────────────────

    def predict_short_proba(
        self,
        df:         pd.DataFrame,
        context_df: pd.DataFrame,
    ) -> np.ndarray:
        """
        Retourne un array de probabilités SHORT de shape (n,).

        Routage par ligne (vectorisé par contexte) :
            1 contexte actif  : p = 0.70 × p_ctx + 0.30 × p_gen
            2 contextes actifs: p = 0.50 × p_top1 + 0.25 × p_top2 + 0.25 × p_gen
            Aucun contexte    : p = p_gen  (ctx_general_short est toujours actif)
        """
        n = len(df)
        X = self._get_X(df)

        p_gen = self._predict_specialist("general_short", X)

        # Probabilités de chaque spécialiste (shape n × n_ctx)
        ctx_names  = [c for c in CONTEXTS if c != "general_short"]
        p_matrix   = np.zeros((n, len(ctx_names)), dtype=np.float64)
        ctx_active = np.zeros((n, len(ctx_names)), dtype=bool)

        for j, name in enumerate(ctx_names):
            spec    = self.specialists[name]
            ctx_col = spec.context_col
            if ctx_col in context_df.columns:
                ctx_active[:, j] = context_df[ctx_col].values.astype(bool)
            # Prédiction même si context inactif (routage peut l'ignorer)
            p_matrix[:, j] = self._predict_specialist(name, X)

        n_active = ctx_active.sum(axis=1)  # nombre de contextes actifs par barre

        p_out = np.empty(n, dtype=np.float64)

        # ── 0 ou 1 contexte actif ─────────────────────────────────────────────
        mask_0or1 = n_active <= 1
        if mask_0or1.any():
            # Récupérer l'indice du contexte actif (−1 si aucun)
            first_ctx = np.full(n, -1, dtype=np.int32)
            for j in range(len(ctx_names)):
                # Premier contexte actif : on écrase seulement si pas encore set
                is_first = ctx_active[:, j] & (first_ctx == -1)
                first_ctx[is_first] = j

            p_spec = np.where(
                first_ctx >= 0,
                p_matrix[np.arange(n), np.maximum(first_ctx, 0)],
                p_gen,
            )
            # Si premier_ctx >= 0 → blend 70/30 ; sinon → p_gen pur
            use_ctx = (first_ctx >= 0)
            p_out[mask_0or1] = np.where(
                use_ctx[mask_0or1],
                _W_CTX * p_spec[mask_0or1] + _W_GEN * p_gen[mask_0or1],
                p_gen[mask_0or1],
            )

        # ── 2+ contextes actifs ───────────────────────────────────────────────
        mask_2plus = ~mask_0or1
        if mask_2plus.any():
            # Identifier top-1 et top-2 par score de probabilité
            p_mat_2 = p_matrix[mask_2plus]         # shape (m, n_ctx)
            active_2 = ctx_active[mask_2plus]       # shape (m, n_ctx)

            # Mettre à −inf les contextes inactifs
            p_ranked = np.where(active_2, p_mat_2, -np.inf)
            idx_top1 = np.argmax(p_ranked, axis=1)
            # Masquer top-1 pour trouver top-2
            p_ranked2 = p_ranked.copy()
            p_ranked2[np.arange(len(idx_top1)), idx_top1] = -np.inf
            idx_top2 = np.argmax(p_ranked2, axis=1)

            m = mask_2plus.sum()
            p_top1 = p_mat_2[np.arange(m), idx_top1]
            p_top2 = p_mat_2[np.arange(m), idx_top2]
            p_g2   = p_gen[mask_2plus]

            p_out[mask_2plus] = (
                _W_CTX2   * p_top1
                + _W_CTX2_2 * p_top2
                + _W_GEN2   * p_g2
            )

        return p_out

    def predict_short_with_context(
        self,
        df:         pd.DataFrame,
        context_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Retourne un DataFrame avec les colonnes :
            p_short   : probabilité short finale (après routage)
            context   : nom du contexte dominant actif
            p_context : probabilité brute du spécialiste dominant
            p_general : probabilité du spécialiste général
            squeeze_risk : squeeze_risk_score si disponible, sinon 0.0
            no_short  : gate booléenne (True = signal bloqué par la gate)
            reason    : explication textuelle du contexte
        """
        n = len(df)
        X = self._get_X(df)

        p_short  = self.predict_short_proba(df, context_df)
        p_gen    = self._predict_specialist("general_short", X)

        # Contexte dominant : premier contexte actif dans l'ordre CONTEXTS
        context_arr  = np.full(n, "general_short", dtype=object)
        p_ctx_arr    = p_gen.copy()
        ctx_names    = [c for c in CONTEXTS if c != "general_short"]

        for name in reversed(ctx_names):   # reversed → premier contexte a priorité
            spec    = self.specialists[name]
            ctx_col = spec.context_col
            if ctx_col in context_df.columns:
                active = context_df[ctx_col].values.astype(bool)
                if active.any():
                    p_spec = self._predict_specialist(name, X)
                    context_arr[active] = name
                    p_ctx_arr[active]   = p_spec[active]

        # squeeze_risk
        if "squeeze_risk_score" in df.columns:
            squeeze = df["squeeze_risk_score"].fillna(0.0).values
        else:
            squeeze = np.zeros(n, dtype=np.float64)

        # no_short depuis context_df
        if "no_short" in context_df.columns:
            no_short = context_df["no_short"].values.astype(bool)
        else:
            no_short = np.zeros(n, dtype=bool)

        # Raison textuelle
        _reason_map = {
            "crowded_longs":     "Foule extrêmement longée — fade",
            "breakdown":         "Structure cassée — continuation baissière",
            "failed_breakout":   "Faux breakout — retournement rapide",
            "liquidity_stress":  "Stress de liquidité — cascades",
            "bear_continuation": "Tendance baissière établie",
            "macro_riskoff":     "Risk-off macro — régime baissier global",
            "general_short":     "Contexte générique (catch-all)",
        }
        reason_arr = np.array(
            [_reason_map.get(c, "Inconnu") for c in context_arr], dtype=object
        )

        return pd.DataFrame(
            {
                "p_short":      p_short,
                "context":      context_arr,
                "p_context":    p_ctx_arr,
                "p_general":    p_gen,
                "squeeze_risk": squeeze,
                "no_short":     no_short,
                "reason":       reason_arr,
            },
            index=df.index,
        )

    def get_summary(self) -> Dict[str, Dict]:
        """
        Résumé de chaque spécialiste.

        Retourne
        --------
        dict {name: {n_train, val_auc, threshold, enabled, calibrated}}
        """
        return {
            name: {
                "n_train":    spec.n_train,
                "val_auc":    round(spec.val_auc, 4),
                "threshold":  spec.threshold,
                "enabled":    spec.enabled,
                "calibrated": spec.calibrator is not None,
            }
            for name, spec in self.specialists.items()
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers privés
    # ─────────────────────────────────────────────────────────────────────────

    def _get_X(self, df: pd.DataFrame) -> np.ndarray:
        """
        Extrait la matrice features en respectant l'ordre self.features.
        Colonnes manquantes → remplies par 0 silencieusement.
        """
        n = len(df)
        cols_present = [f for f in self.features if f in df.columns]
        cols_missing = [f for f in self.features if f not in df.columns]

        if cols_present:
            X = df[cols_present].fillna(0.0).values.astype(np.float32)
        else:
            X = np.zeros((n, 0), dtype=np.float32)

        if cols_missing:
            pad = np.zeros((n, len(cols_missing)), dtype=np.float32)
            X   = np.hstack([X, pad])

        return X

    def _make_clf(self) -> HistGradientBoostingClassifier:
        """Instancie un HistGBT avec les hyperparamètres de la flottée."""
        return HistGradientBoostingClassifier(
            max_iter=self.n_iter,
            learning_rate=self.learning_rate,
            max_leaf_nodes=self.max_leaf_nodes,
            random_state=42,
            # Pas de class_weight : non supporté par HistGBT → sample_weight
        )

    @staticmethod
    def _compute_sample_weight(y: np.ndarray) -> np.ndarray:
        """
        Calcule des sample_weights qui compensent le déséquilibre de classes.
        Ratio = n_neg / n_pos — clampé à 80 pour éviter les explosions.
        """
        n_pos = max(int((y == 1).sum()), 1)
        n_neg = max(int((y == 0).sum()), 1)
        pos_w = min(n_neg / n_pos, 80.0)
        sw    = np.where(y == 1, pos_w, 1.0).astype(np.float64)
        return sw

    def _predict_specialist(self, name: str, X: np.ndarray) -> np.ndarray:
        """
        Retourne P(SHORT=1) pour toutes les barres via le spécialiste `name`.
        Si le spécialiste est désactivé ou non entraîné → retourne 0.5.
        Calibration appliquée si disponible.
        """
        spec = self.specialists[name]
        n    = len(X)

        if not spec.enabled or spec.model is None:
            return np.full(n, 0.5, dtype=np.float64)

        p_raw = spec.model.predict_proba(X)[:, 1].astype(np.float64)

        if spec.calibrator is None:
            return p_raw

        cal = spec.calibrator
        if isinstance(cal, PlattLR):
            return cal.predict_proba(p_raw.reshape(-1, 1))[:, 1]
        if isinstance(cal, IsotonicRegression):
            return cal.predict(p_raw).astype(np.float64)
        # Fallback
        return p_raw

    @staticmethod
    def _val_auc(clf: HistGradientBoostingClassifier,
                 X_val: np.ndarray, y_val: np.ndarray) -> float:
        """AUC sur la validation — retourne 0.0 si non calculable."""
        if len(X_val) < 10 or y_val.sum() < 2:
            return 0.0
        try:
            p = clf.predict_proba(X_val)[:, 1]
            return float(roc_auc_score(y_val, p))
        except Exception:
            return 0.0

    def _calibrate(
        self,
        clf:   HistGradientBoostingClassifier,
        X_val: np.ndarray,
        y_val: np.ndarray,
    ) -> Optional[Any]:
        """
        Calibre les probabilités du modèle sur la val.

        Platt (LogisticRegression C=1.0) si n_val >= 200.
        IsotonicRegression si 30 <= n_val < 200.
        None si n_val < 30 (pas assez de données).
        """
        n_val = len(X_val)
        if n_val < 30:
            return None

        p_raw = clf.predict_proba(X_val)[:, 1]

        if n_val >= 200:
            cal = PlattLR(C=1.0, random_state=42, max_iter=500)
            cal.fit(p_raw.reshape(-1, 1), y_val)
        else:
            cal = IsotonicRegression(out_of_bounds="clip")
            cal.fit(p_raw, y_val)

        return cal

    def _print_summary(self, elapsed: float) -> None:
        """Affiche un résumé de l'entraînement."""
        enabled = [n for n, s in self.specialists.items() if s.enabled]
        aucs    = {n: round(s.val_auc, 3)
                   for n, s in self.specialists.items() if s.enabled}
        n_trains = {n: s.n_train
                    for n, s in self.specialists.items() if s.enabled}

        print(
            f"\n   TRMShortFleet : {len(enabled)}/{len(CONTEXTS)} spécialistes actifs  "
            f"rounds={self.hard_example_rounds}  t={elapsed:.1f}s"
        )
        print(
            "   n_train : "
            + "  ".join(f"{k[:8]}={v:,}" for k, v in n_trains.items())
        )
        print(
            "   AUC val : "
            + "  ".join(f"{k[:8]}={v:.3f}" for k, v in aucs.items())
        )
        disabled = [n for n, s in self.specialists.items() if not s.enabled]
        if disabled:
            print(f"   Désactivés (n_train < {_MIN_N_TRAIN}) : {disabled}")
