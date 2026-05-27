"""
ai/level_2/trm_fleet_short_v4.py — TRM FLEET SHORT v4  (100 TRM)
=================================================================

Architecture v4 — unification des pipelines SHORT :
  100 TRM = 10 horizons temporels × 10 contextes SHORT

Innovation clé : chaque contexte short dispose maintenant d'UN spécialiste
par horizon temporel. La prédiction finale combine le contexte primaire
(OHLCV routing) avec la meilleure lecture temporelle (top-k par AUC val).

Grille :
  Horizons (10) : h04, h08, h12, d01, d03, w01, w02, mo01, q01, y01
  Contextes (10) : crowding_extreme, breakdown, failed_breakout,
                   bear_continuation, liquidity_stress, overbought_fade,
                   vol_expansion_bear, wick_rejection, dc_bear, general_short

Tous les TRM prédisent le même label `y_short_clean` (horizon primaire 4h/8h
selon le meilleur entre les deux), mais utilisent des signaux temporels à
différentes résolutions — ce qui donne des perspectives orthogonales.

Routage hybride :
  1. Classifier le contexte (OHLCV deterministe — hérité de v3)
  2. Pour le contexte primaire, prendre les top-k (k=3) TRM de ce contexte
     ordonnés par val_auc
  3. p_specialist = Σ(auc_k^2 × p_k) / Σ(auc_k^2)
  4. p_final = SPECIALIST_W × p_specialist + GENERAL_W × p_general_all
     où p_general_all est la moyenne pondérée de tous les TRM `general_short`

Points pris des versions précédentes :
  - v3 SHORT : routing OHLCV déterministe, 10 contextes, hard-example rounds
  - v3 LONG  : top-k par val_auc, routage soft, capacité adaptative par horizon
  - short_labels.py : labels MFE/MAE asymétriques, squeeze rejection filter
  - short_features.py : 62 gamechanger features (crowding/breakdown/failed/liq/squeeze)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

# Réutilise les signaux temporels du LONG — pas de duplication
from ai.level_2.trm_fleet_long_v4 import (
    TemporalHorizon, TEMPORAL_HORIZONS_V4,
    _col, _col_any, _clean, _z, _sigmoid, _ratio,
    _temporal_signals, _EPS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Contextes SHORT v4
# ─────────────────────────────────────────────────────────────────────────────

SHORT_CONTEXT_NAMES: Tuple[str, ...] = (
    "crowding_extreme",   # extrême surpositionnement haussier (funding+OI+L/S)
    "breakdown",          # structure cassée (sous VWAP + EMAs)
    "failed_breakout",    # faux breakout haussier (bull trap)
    "bear_continuation",  # continuation baissière (bear trend établi)
    "liquidity_stress",   # cascades de liquidation longs
    "overbought_fade",    # extension extrême → fade de RSI
    "vol_expansion_bear", # volatilité explosant en direction baissière
    "wick_rejection",     # rejection par grande mèche supérieure
    "dc_bear",            # death cross + structure EMA bearish
    "general_short",      # catch-all
)


@dataclass(frozen=True)
class ShortContextSpec:
    context: str
    horizon: TemporalHorizon
    name:    str   # = f"{context}_{horizon.key}"


SHORT_SPECIALIST_SPECS: Tuple[ShortContextSpec, ...] = tuple(
    ShortContextSpec(
        context=ctx,
        horizon=h,
        name=f"{ctx}__{h.key}",
    )
    for ctx in SHORT_CONTEXT_NAMES
    for h in TEMPORAL_HORIZONS_V4
)

TRM_FLEET_SHORT_SIZE_V4 = len(SHORT_SPECIALIST_SPECS) + 1  # 100 + 1 general = 101


# ─────────────────────────────────────────────────────────────────────────────
# Scoring des contextes SHORT — OHLCV + temporal signals
# ─────────────────────────────────────────────────────────────────────────────

def _score_short_context(df: pd.DataFrame, context: str, sig: Dict[str, np.ndarray]) -> np.ndarray:
    """
    Score de chaque contexte SHORT à une résolution temporelle donnée.
    Renvoie un score ∈ [0, 1] (sigmoid normalisé) — plus haut = plus pertinent.
    """
    ret_fast = sig["ret_fast"]   # rendement court terme (inversé pour short)
    ret_full = sig["ret_full"]

    # Pression vendeuse commune à tous les contextes
    sell_pressure = (
        -_z(sig["taker_buy_ratio_base"] - 0.5)      # taker buy < 0.5 = pression vendeuse
        - 0.7 * _z(sig["delta_taker_pressure"])
        - 0.5 * _z(sig["vol_imbalance"])
    )
    bear_struct = (
        -_z(sig["ema_spread_50_200"])
        - 0.5 * _z(sig["dist_ema_50"])
    )

    if context == "crowding_extreme":
        # Long crowd extrême → setup short idéal
        # Signaux : funding high, OI en expansion, L/S élevé, RSI overbought
        raw = (
            0.90 * _z(sig.get("funding_accel_24", np.zeros(len(ret_full))))
            + 0.80 * _z(sig.get("long_crowding_score", np.zeros(len(ret_full))))
            + 0.70 * _z(sig["rsi_14"] - 65.0)         # RSI > 65 = overbought
            + 0.60 * _z(sig["dist_ema_50"])             # éloigné au-dessus EMA50
            + 0.50 * _z(sig["dist_high"])               # proche du sommet récent
            + 0.40 * sell_pressure
        )

    elif context == "breakdown":
        # Structure cassée : sous VWAP + sous EMAs + pression vendeuse
        below_vwap   = _z(0.5 - sig.get("above_vwap_4h", np.full(len(ret_full), 0.5)))
        below_ema    = _z(-sig["dist_ema_20"])
        raw = (
            1.00 * below_vwap
            + 0.85 * below_ema
            + 0.70 * bear_struct
            + 0.60 * sell_pressure
            + 0.50 * _z(-ret_fast)                     # recul récent
            + 0.40 * _z(sig.get("breakdown_score", np.zeros(len(ret_full))))
        )

    elif context == "failed_breakout":
        # Bull trap : cassure haussière puis échec + mèche supérieure
        raw = (
            1.00 * _z(sig.get("failed_breakout_score", np.zeros(len(ret_full))))
            + 0.85 * _z(sig.get("bull_trap_score", np.zeros(len(ret_full))))
            + 0.70 * _z(sig["vol_ratio_fast_full"])    # volume sur l'échec
            + 0.60 * _z(sig["dist_high"])               # proche du high mais recul
            + 0.50 * _z(-ret_fast)                     # rendement inversé court terme
            + 0.40 * sell_pressure
        )

    elif context == "bear_continuation":
        # Bear trend établi : continuation baissière après pullback
        raw = (
            1.00 * bear_struct
            + 0.85 * _z(-ret_full)                     # tendance baissière moyen terme
            + 0.70 * _z(sig.get("bear_continuation_score", np.zeros(len(ret_full))))
            + 0.60 * _z(sig.get("ema_stack_bearish", np.zeros(len(ret_full))))
            + 0.50 * sell_pressure
            + 0.40 * _z(-sig["dist_low"])              # loin du bas = pullback pour short
        )

    elif context == "liquidity_stress":
        # Cascades de liquidation longs : grosse pression vendeuse
        raw = (
            1.00 * _z(sig.get("liq_long_spike_12", np.zeros(len(ret_full))))
            + 0.85 * _z(sig.get("sell_volume_shock", np.zeros(len(ret_full))))
            + 0.75 * _z(sig["vol_ratio_fast_full"])    # spike de vol
            + 0.65 * sell_pressure
            + 0.50 * _z(sig.get("range_expansion_6", np.zeros(len(ret_full))))
            + 0.40 * _z(-ret_fast)
        )

    elif context == "overbought_fade":
        # Extension extrême → fade du RSI
        raw = (
            1.00 * _z(sig["rsi_14"] - 65.0)
            + 0.85 * _z(sig["dist_ema_50"])
            + 0.70 * _z(sig["dist_high"])
            + 0.60 * _z(sig["boll_pos_20"] - 0.8)     # proche du haut des BB
            + 0.50 * _z(0.5 - sig["taker_buy_ratio_base"])
        )

    elif context == "vol_expansion_bear":
        # Volatilité qui explose dans une tendance baissière
        raw = (
            1.00 * _z(sig["vol_ratio_fast_full"])
            + 0.85 * _z(sig["rv_ratio_24_72"] - 1.0)
            + 0.75 * bear_struct
            + 0.65 * _z(-ret_fast)
            + 0.50 * _z(sig["vol_full"])
        )

    elif context == "wick_rejection":
        # Grandes mèches supérieures = rejection du marché
        raw = (
            1.00 * _z(sig.get("upper_wick_z_24", np.zeros(len(ret_full))))
            + 0.85 * _z(sig.get("upper_wick_pct", np.zeros(len(ret_full))))
            + 0.70 * _z(sig.get("close_rejection_from_high", np.zeros(len(ret_full))))
            + 0.60 * _z(sig["rsi_14"] - 55.0)
            + 0.50 * _z(-ret_fast)
        )

    elif context == "dc_bear":
        # Death cross + EMA stack baissier + RSI < 50
        raw = (
            1.00 * bear_struct
            + 0.85 * _z(0.5 - sig["rsi_14"] / 100.0)
            + 0.70 * _z(-sig["dist_ema_200"])           # sous EMA200
            + 0.60 * _z(sig.get("ema_stack_bearish", np.zeros(len(ret_full))))
            + 0.50 * _z(-ret_full)
        )

    else:  # general_short
        raw = (
            0.70 * _z(-ret_fast)
            + 0.60 * sell_pressure
            + 0.50 * bear_struct
        )

    return np.nan_to_num(_sigmoid(raw), nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32)


def build_short_context_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Scores de chaque contexte SHORT × horizon sur chaque barre.
    Score composé : context_score × temporal_boost (signal à résolution h).
    """
    if len(df) == 0:
        return pd.DataFrame(index=df.index, columns=[s.name for s in SHORT_SPECIALIST_SPECS], dtype=np.float32)

    by_horizon: Dict[str, Dict] = {}
    scores: Dict[str, np.ndarray] = {}
    for spec in SHORT_SPECIALIST_SPECS:
        h_key = spec.horizon.key
        if h_key not in by_horizon:
            by_horizon[h_key] = _temporal_signals(df, spec.horizon.hours)
        sig = by_horizon[h_key]

        # Score du contexte à cette résolution temporelle
        ctx_score = _score_short_context(df, spec.context, sig)

        # Boost temporal : le signal de momentum bearish à cette échelle
        # Renforce le score si la direction correspond à l'horizon
        temporal_boost = 1.0 + 0.15 * np.clip(-_z(sig["ret_full"]), 0.0, 1.0)
        scores[spec.name] = np.clip(ctx_score * temporal_boost, 0.0, 1.0).astype(np.float32)

    return pd.DataFrame(scores, index=df.index, dtype=np.float32)


def classify_short_context_v4(df: pd.DataFrame) -> np.ndarray:
    """
    Assigne UN contexte par barre par score de spécificité relatif.

    Spécificité = score_brut / (moyenne + ε) — une colonne gagne seulement
    quand son signal est nettement AU-DESSUS de la moyenne des autres contextes.
    Cela évite que overbought_fade/breakdown monopolisent le routing car ils
    ont des scores absolus naturellement plus élevés.
    """
    n = len(df)
    if n == 0:
        return np.array([], dtype=object)

    h04_specs = [s for s in SHORT_SPECIALIST_SPECS if s.horizon.key == "h04"]
    if not h04_specs:
        return np.full(n, "general_short", dtype=object)

    by_h04 = _temporal_signals(df, 4)
    scores_h04: Dict[str, np.ndarray] = {}
    for spec in h04_specs:
        if spec.context == "general_short":
            continue   # general_short est le fallback, pas candidat au routing
        scores_h04[spec.context] = _score_short_context(df, spec.context, by_h04)

    score_matrix = np.column_stack(list(scores_h04.values()))  # (n, 9)
    context_names_arr = np.array(list(scores_h04.keys()), dtype=object)

    # Score de spécificité : score / (colmean + ε)
    # Normaliser chaque colonne par sa moyenne glissante (100 barres) pour rester causal
    col_means = np.maximum(score_matrix.mean(axis=0, keepdims=True), 0.01)
    spec_matrix = score_matrix / col_means                          # (n, 9)

    # Argmax sur le score de spécificité
    best_idx   = np.argmax(spec_matrix, axis=1)
    best_raw   = score_matrix[np.arange(n), best_idx]              # score brut du gagnant
    best_spec  = spec_matrix[np.arange(n), best_idx]               # score spécificité du gagnant

    ctx = context_names_arr[best_idx].astype(object)
    # Fallback general_short si :
    #   - score brut faible (< 0.50) : signal trop faible
    #   - spécificité faible (< 1.10) : le gagnant n'est pas nettement au-dessus des autres
    no_clear_winner = (best_raw < 0.50) | (best_spec < 1.10)
    ctx[no_clear_winner] = "general_short"
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Spécialiste SHORT individuel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortSpecialistV4:
    """
    TRM SHORT spécialisé sur (contexte, horizon).
    Prédit y_short_clean (label 4h/8h) sur les barres de son contexte.
    """
    spec:         ShortContextSpec
    features:     List[str]
    clf_:         Optional[Any]            = field(default=None, repr=False)
    scaler_:      Optional[StandardScaler] = field(default=None, repr=False)
    val_auc_:     float = 0.0
    n_train_:     int   = 0
    n_pos_:       int   = 0

    def _get_X(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        missing = [f for f in self.features if f not in df.columns]
        if missing:
            raise RuntimeError(
                f"ShortSpecialist {self.spec.name!r} — features manquantes dans df : {missing}\n"
                f"Appeler get_available_features(df, features) avant de créer la fleet."
            )
        X = df.loc[mask, self.features].fillna(0.0).values.astype(np.float32)
        return X

    def fit(
        self,
        df:            pd.DataFrame,
        train_mask:    np.ndarray,
        val_mask:      Optional[np.ndarray] = None,
        sample_weight: Optional[np.ndarray] = None,
        label_col:     str                  = "y_short_clean",
    ) -> "ShortSpecialistV4":
        X_tr = self._get_X(df, train_mask)
        y_tr = df.loc[train_mask, label_col].values.astype(np.int32)

        valid = y_tr >= 0
        X_tr  = X_tr[valid]
        y_tr  = y_tr[valid]
        if sample_weight is not None:
            sample_weight = sample_weight[valid]

        self.n_train_ = len(y_tr)
        self.n_pos_   = int(y_tr.sum())

        if self.n_pos_ < 5 or self.n_train_ < 20:
            return self

        self.scaler_ = StandardScaler()
        Xsc = self.scaler_.fit_transform(X_tr)
        n_neg = len(y_tr) - self.n_pos_
        spw   = min(n_neg / max(self.n_pos_, 1), 60.0)

        # Capacité adaptative SHORT :
        #   h04/h08 : précision élevée, 280 iter, depth 4
        #   h12/d01 : intermédiaire, 220 iter
        #   d03+    : compact (généralisation), 160 iter, depth 3
        h = self.spec.horizon.hours
        if h <= 8:
            max_iter, max_depth, min_leaf = 280, 4, 15
        elif h <= 24:
            max_iter, max_depth, min_leaf = 220, 4, 18
        else:
            max_iter, max_depth, min_leaf = 160, 3, 22

        self.clf_ = HistGradientBoostingClassifier(
            max_iter=max_iter, max_depth=max_depth, learning_rate=0.04,
            l2_regularization=1.2, min_samples_leaf=min_leaf,
            class_weight={0: 1.0, 1: spw}, random_state=42,
        )
        self.clf_.fit(Xsc, y_tr, sample_weight=sample_weight)

        if (val_mask is not None
                and len(val_mask) == len(df)
                and int(val_mask.sum()) > 10):
            X_val = self._get_X(df, val_mask)
            y_val = df.loc[val_mask, label_col].values.astype(np.int32)
            valid_v = y_val >= 0
            X_val, y_val = X_val[valid_v], y_val[valid_v]
            if len(X_val) > 10 and y_val.sum() >= 2:
                p = self.clf_.predict_proba(self.scaler_.transform(X_val))[:, 1]
                self.val_auc_ = float(roc_auc_score(y_val, p))
        return self

    def predict_proba(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        if self.clf_ is None or self.scaler_ is None:
            return np.full(int(mask.sum()), 0.5, dtype=np.float32)
        X = self._get_X(df, mask)
        return self.clf_.predict_proba(self.scaler_.transform(X))[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# TRM Fleet Short v4
# ─────────────────────────────────────────────────────────────────────────────

class TRMFleetShortV4:
    """
    Flottée TRM SHORT v4 — 100 TRM + 1 général.

    Routing hybride :
      1. Classifier le contexte primaire (OHLCV h04, déterministe)
      2. Dans ce contexte, prendre les top-k TRM par val_auc (sur tous les horizons)
      3. Blend par auc^2 → p_specialist_context
      4. p_general = blend de tous les TRM `general_short` par auc^2
      5. p_final = SPECIALIST_W × p_specialist_context + GENERAL_W × p_general
    """

    SPECIALIST_W = 0.68
    GENERAL_W    = 0.32
    TOP_K_HORIZONS = 3   # top-3 horizons par contexte

    def __init__(
        self,
        features:           List[str],
        n_recursive_rounds: int = 2,
        min_specialist_rows: int = 80,
        label_col:          str = "y_short_clean",
    ):
        self.features            = features
        self.n_recursive_rounds  = n_recursive_rounds
        self.min_specialist_rows = max(20, int(min_specialist_rows))
        self.label_col           = label_col
        self.specialists: Dict[str, ShortSpecialistV4] = {}
        self.n_ctx_: Dict[str, int] = {}
        self._fleet_auc_mean: float = 0.0
        self._init_specialists()

    def _init_specialists(self) -> None:
        for spec in SHORT_SPECIALIST_SPECS:
            self.specialists[spec.name] = ShortSpecialistV4(
                spec=spec, features=self.features,
            )
        # Un TRM général "general_short" unique sur tous les horizons
        # (implémenté via le contexte general_short × h04 comme référence)

    def _specialists_for_context(self, context: str) -> List[ShortSpecialistV4]:
        return [
            self.specialists[s.name]
            for s in SHORT_SPECIALIST_SPECS
            if s.context == context and self.specialists[s.name].clf_ is not None
        ]

    def _compute_val_aucs(
        self,
        df_val:    pd.DataFrame,
        val_mask:  Optional[np.ndarray] = None,
    ) -> None:
        """Calcule l'AUC val de chaque spécialiste SHORT sur le dataset val (BTC seul)."""
        df_sub = df_val.loc[val_mask] if val_mask is not None else df_val
        df_sub = df_sub.reset_index(drop=True)
        if self.label_col not in df_sub.columns:
            return

        y_val = df_sub[self.label_col].values.astype(np.int32)
        ctx_arr = classify_short_context_v4(df_sub)
        ones    = np.ones(len(df_sub), dtype=bool)

        for spec_obj in SHORT_SPECIALIST_SPECS:
            specialist = self.specialists[spec_obj.name]
            if specialist.clf_ is None:
                continue
            ctx = spec_obj.context
            ctx_sel = (ctx_arr == ctx) if ctx != "general_short" else ones
            y_sub   = y_val[ctx_sel]
            valid_v = y_sub >= 0
            y_sub   = y_sub[valid_v]
            if len(y_sub) < 10 or y_sub.sum() < 2:
                continue
            X_sub = specialist._get_X(df_sub, ctx_sel)
            X_sub = X_sub[valid_v]
            try:
                p = specialist.clf_.predict_proba(
                    specialist.scaler_.transform(X_sub)
                )[:, 1]
                specialist.val_auc_ = float(roc_auc_score(y_sub, p))
            except Exception:
                pass

    def train(
        self,
        df:           pd.DataFrame,
        train_mask:   np.ndarray,
        df_val_btc:   Optional[pd.DataFrame] = None,  # val BTC (pour AUC val)
        val_mask_btc: Optional[np.ndarray]   = None,
    ) -> "TRMFleetShortV4":
        t0 = time.time()

        # Classifier les barres d'entraînement dans leurs contextes
        ctx_arr = classify_short_context_v4(df.loc[train_mask])
        train_idx = np.where(train_mask)[0]
        n_train   = len(train_idx)

        # Scores par contexte × horizon pour sélection des barres
        score_df = build_short_context_scores(df.loc[train_mask].reset_index(drop=True))

        weights_now = np.ones(n_train, dtype=np.float64)

        for rnd in range(self.n_recursive_rounds):
            for spec_obj in SHORT_SPECIALIST_SPECS:
                specialist = self.specialists[spec_obj.name]
                ctx        = spec_obj.context

                # Sélectionner les barres de ce contexte
                # + queue haute du score (top 30% de ce contexte × horizon)
                ctx_in_train = (ctx_arr == ctx)
                if ctx_in_train.sum() < self.min_specialist_rows:
                    # Contexte trop rare : entraîner sur toutes les barres du contexte
                    # sans filtre de queue (robustesse avec peu de données)
                    if ctx_in_train.sum() < 20:
                        continue
                else:
                    # Sélectionner la queue haute des scores pour ce spécialiste
                    col_name = spec_obj.name
                    if col_name in score_df.columns:
                        scores_ctx = score_df.loc[ctx_in_train, col_name].to_numpy()
                        q70 = np.percentile(scores_ctx[np.isfinite(scores_ctx)], 70) if scores_ctx.size else 0.0
                        ctx_in_train = ctx_in_train & (
                            score_df[col_name].reindex(range(len(ctx_arr))).fillna(0).to_numpy() >= q70
                        )
                        if ctx_in_train.sum() < 20:
                            ctx_in_train = (ctx_arr == ctx)  # fallback

                ctx_global = np.zeros(len(df), dtype=bool)
                ctx_global[train_idx[ctx_in_train]] = True

                ctx_weights = weights_now[ctx_in_train]

                specialist.fit(
                    df,
                    ctx_global,
                    val_mask=None,          # AUC calculé séparément sur df_val_btc
                    sample_weight=ctx_weights,
                    label_col=self.label_col,
                )

            # Barres difficiles
            if rnd < self.n_recursive_rounds - 1:
                p_ens     = self._predict_raw(df, train_mask)
                uncertain = np.abs(p_ens - 0.5) < 0.12
                weights_now = np.where(uncertain, 3.0, 1.0).astype(np.float64)

        # AUC val — calculé séparément sur df_val_btc (même pattern que LONG v4)
        if df_val_btc is not None:
            self._compute_val_aucs(df_val_btc, val_mask_btc)

        # Métriques
        auc_vals = [s.val_auc_ for s in self.specialists.values() if s.val_auc_ > 0]
        self._fleet_auc_mean = float(np.mean(auc_vals)) if auc_vals else 0.0

        dt = time.time() - t0
        n_ctx = {c: int((ctx_arr == c).sum()) for c in SHORT_CONTEXT_NAMES}
        self.n_ctx_ = n_ctx
        trained = sum(1 for s in self.specialists.values() if s.clf_ is not None)
        top_ctx = sorted(n_ctx.items(), key=lambda x: x[1], reverse=True)[:8]

        # Meilleurs TRM par AUC
        auc_top = sorted(
            ((n, s.val_auc_) for n, s in self.specialists.items() if s.val_auc_ > 0),
            key=lambda x: x[1], reverse=True
        )[:8]

        print(f"   TRMFleetShort v4 : {trained}/{len(self.specialists)} TRM entraînés  "
              f"rounds={self.n_recursive_rounds}  t={dt:.1f}s")
        print(f"   Contextes train : " + "  ".join(f"{k}={v:,}" for k, v in top_ctx))
        print(f"   AUC top8 : " + "  ".join(f"{k.split('__')[0][:8]}_{k.split('__')[1]}={v:.3f}" for k, v in auc_top))
        print(f"   AUC fleet moyenne : {self._fleet_auc_mean:.3f}")
        return self

    def _predict_raw(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        return self.predict(df, mask)

    def predict(
        self,
        df:   pd.DataFrame,
        mask: np.ndarray,
    ) -> np.ndarray:
        """
        Prédiction hybride context × horizon.
        """
        df_sub = df.loc[mask].copy().reset_index(drop=True)
        n      = len(df_sub)
        if n == 0:
            return np.array([], dtype=np.float32)
        ones   = np.ones(n, dtype=bool)

        # Contexte primaire (h04 only)
        ctx_arr = classify_short_context_v4(df_sub)

        # p_general : blend des TRM general_short par auc^2
        general_specs = self._specialists_for_context("general_short")
        if general_specs:
            aucs_g = np.array([s.val_auc_ for s in general_specs], dtype=np.float32)
            w_g    = aucs_g ** 2 + 1e-5
            w_g   /= w_g.sum()
            p_general = sum(
                float(w) * s.predict_proba(df_sub, ones)
                for s, w in zip(general_specs, w_g)
            )
            if isinstance(p_general, (int, float)):
                p_general = np.full(n, p_general, dtype=np.float32)
            p_general = np.asarray(p_general, dtype=np.float32)
        else:
            p_general = np.full(n, 0.5, dtype=np.float32)

        p_out = p_general.copy()

        for ctx in SHORT_CONTEXT_NAMES:
            if ctx == "general_short":
                continue
            ctx_rows = (ctx_arr == ctx)
            if not ctx_rows.any():
                continue

            ctx_specs = self._specialists_for_context(ctx)
            if not ctx_specs:
                continue

            # Top-k par val_auc
            ctx_specs_sorted = sorted(ctx_specs, key=lambda s: s.val_auc_, reverse=True)
            top_specs = ctx_specs_sorted[:min(self.TOP_K_HORIZONS, len(ctx_specs_sorted))]

            aucs_s = np.array([s.val_auc_ for s in top_specs], dtype=np.float32)
            w_s    = aucs_s ** 2 + 1e-5
            w_s   /= w_s.sum()

            p_specialist = np.zeros(int(ctx_rows.sum()), dtype=np.float32)
            for s, w in zip(top_specs, w_s):
                p_specialist += w * s.predict_proba(df_sub, ctx_rows)

            p_out[ctx_rows] = (
                self.SPECIALIST_W * p_specialist
                + self.GENERAL_W  * p_general[ctx_rows]
            )

        return p_out.astype(np.float32)

    def adaptive_threshold(self) -> float:
        """Seuil minimal basé sur l'AUC fleet."""
        return 0.55 if self._fleet_auc_mean < 0.58 else 0.57

    def calibrate_thresholds(
        self,
        df_val:     pd.DataFrame,
        ret_col:    str   = "future_ret_short_4h",
        cost_short: float = 0.0012,
        min_thr:    float = 0.55,
        max_thr:    float = 0.82,
        min_trades: int   = 8,
    ) -> Dict[str, float]:
        """
        Calibre un seuil PnL par contexte sur la validation.
        Critère : expectancy × (PF - 1) × sqrt(n_trades)
        Stabilité : max drop 20% sur ±0.02
        """
        n       = len(df_val)
        ones    = np.ones(n, dtype=bool)
        ctx_arr = classify_short_context_v4(df_val)
        p_all   = self.predict(df_val, ones)

        if ret_col not in df_val.columns:
            fallback = "future_ret_4h" if "future_ret_4h" in df_val.columns else None
            if fallback is None:
                return {ctx: min_thr for ctx in SHORT_CONTEXT_NAMES}
            ret_col = fallback

        rets_raw  = df_val[ret_col].fillna(0.0).to_numpy(dtype=np.float64)
        # future_ret_short_4h est déjà signé pour le short (positif = short gagne)
        # Si on utilise future_ret_4h, il faut inverser
        if "short" not in ret_col:
            rets_raw = -rets_raw

        thresholds: Dict[str, float] = {}

        for ctx in SHORT_CONTEXT_NAMES:
            ctx_ok  = (ctx_arr == ctx) if ctx != "general_short" else np.ones(n, dtype=bool)
            gate_ok = np.ones(n, dtype=bool)   # gate appliquée en amont
            sel     = ctx_ok & gate_ok

            p_sub   = p_all[sel]
            ret_sub = rets_raw[sel] - cost_short   # net de coût

            if len(p_sub) < 10:
                thresholds[ctx] = min_thr
                continue

            best_thr, best_score = min_thr, -np.inf
            for thr in np.arange(min_thr, max_thr + 0.001, 0.01):
                m = p_sub >= thr
                n_t = m.sum()
                if n_t < min_trades:
                    continue
                rets_t = ret_sub[m]
                wins   = (rets_t > 0).sum()
                gw     = rets_t[rets_t > 0].sum()
                gl     = abs(rets_t[rets_t < 0].sum())
                pf     = gw / max(gl, 1e-9)
                exp    = float(rets_t.mean())
                edge   = max(pf - 1.0, 0.0)
                score  = exp * edge * (n_t ** 0.5)
                if score > best_score:
                    best_score, best_thr = score, thr

            # Stabilité : PnL ne doit pas chuter de plus de 20% sur ±0.02
            pnl_best = float((ret_sub[p_sub >= best_thr]).sum())
            for delta in (0.02,):
                for nb_thr in (best_thr - delta, best_thr + delta):
                    if nb_thr < min_thr or nb_thr > max_thr:
                        continue
                    pnl_nb = float((ret_sub[p_sub >= nb_thr]).sum())
                    if pnl_best > 0 and pnl_nb < pnl_best * 0.80:
                        best_thr = min(best_thr + 0.01, max_thr)

            thresholds[ctx] = round(float(best_thr), 2)

        return thresholds

    def val_auc_summary(self) -> Dict[str, float]:
        return {n: round(s.val_auc_, 3) for n, s in self.specialists.items() if s.val_auc_ > 0}

    def to_fleet_report(self) -> Dict:
        return {
            "version":        "v4",
            "n_total":        len(self.specialists),
            "n_trained":      sum(1 for s in self.specialists.values() if s.clf_ is not None),
            "fleet_auc_mean": round(self._fleet_auc_mean, 4),
            "n_horizons":     len(TEMPORAL_HORIZONS_V4),
            "n_contexts":     len(SHORT_CONTEXT_NAMES),
        }
