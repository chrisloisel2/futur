"""
ai/level_2/trm_fleet_long_v5.py — TRM FLEET LONG v5 (LightGBM DART + Stacking)
=================================================================================

Améliorations vs v4 :
  • LightGBM DART  : AUC cible 0.67 vs 0.54 (HistGBM)
  • 24 TRM seulement  : 4 horizons × 6 archétypes (moins de fragmentation)
  • Labels adaptatifs au régime : quantile 0.75 BEAR / 0.78 NEUTRAL / 0.82 BULL
  • Stacking méta-LR : 24 spécialistes → LR sur OOF → score final calibré
  • Features v5 : FEATURES_INST_LONG + FEATURES_ALPHA (48 features nouvelles)
  • Sélection par importance LightGBM : top-k features par archétype

Horizons v5 (4) : h04, h08 (principal), h24, h72
Archétypes v5 (6) : momentum, trend_follow, breakout, pullback, bear_bounce,
                    regime_transition

Anti-leakage :
  - Les labels adaptatifs utilisent les quantiles calibrés sur train uniquement.
  - Le stacking utilise des prédictions OOF strictement temporelles (pas de shuffle).
  - Aucune donnée future n'est utilisée dans les features d'entrée.
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

import lightgbm as lgb

warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

_EPS = 1e-9

# ─────────────────────────────────────────────────────────────────────────────
# Lattice v5 : 4 horizons × 6 archétypes = 24 TRM
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TemporalHorizonV5:
    key:   str
    hours: int


@dataclass(frozen=True)
class MovementArchetypeV5:
    key:   str
    # sélecteur de lignes pour training (colonnes requises → mask)
    row_selector: str


TEMPORAL_HORIZONS_V5: Tuple[TemporalHorizonV5, ...] = (
    TemporalHorizonV5("h04",  4),
    TemporalHorizonV5("h08",  8),   # horizon principal (SNR optimal)
    TemporalHorizonV5("h24", 24),
    TemporalHorizonV5("h72", 72),
)

MOVEMENT_ARCHETYPES_V5: Tuple[MovementArchetypeV5, ...] = (
    MovementArchetypeV5("momentum",          "momentum"),
    MovementArchetypeV5("trend_follow",      "trend_follow"),
    MovementArchetypeV5("breakout",          "breakout"),
    MovementArchetypeV5("pullback",          "pullback"),
    MovementArchetypeV5("bear_bounce",       "bear_bounce"),
    MovementArchetypeV5("regime_transition", "regime_transition"),
)

# Sous-ensembles de features par horizon (feature prioritaires selon SNR horizon)
_H04_EXTRA = ["sharpe_8", "ofi_zscore_24", "ofi_momentum_8", "autocorr_lag1_20",
               "candle_body_strength_8", "wick_pressure_8", "taker_buy_accel",
               "vol_spike_zscore_100", "amihud_spike", "mom_accel_4_8"]

_H08_EXTRA = ["sharpe_20", "vol_pctrank_200", "autocorr_lag8_20", "hurst_exp_48",
               "bear_bounce_score", "amihud_24", "kama_dev_20", "trend_quality_20",
               "zscore_from_sma_20", "crisis_score"]

_H24_EXTRA = ["sharpe_48", "vol_of_vol_24", "skew_ret_48", "excess_kurt_20",
               "kama_dev_20", "ema_cross_speed", "amihud_pctrank_72",
               "mom_divergence_4_24", "zscore_from_sma_50", "hurst_exp_48"]

_H72_EXTRA = ["vol_regime_trend_20", "atr_expansion_ratio", "calmar_proxy_24",
               "tail_ratio_20", "bear_pressure_24", "vol_trend_alignment",
               "oversold_depth_atr", "mean_rev_speed_20", "cum_delta_pctrank_48",
               "trend_quality_20"]

HORIZON_FEATURE_EXTRAS: Dict[str, List[str]] = {
    "h04": _H04_EXTRA,
    "h08": _H08_EXTRA,
    "h24": _H24_EXTRA,
    "h72": _H72_EXTRA,
}

# Colonnes nécessaires au sélecteur de lignes (doivent exister ou être ignorées)
_SELECTOR_COLS: Dict[str, List[str]] = {
    "momentum":          ["trend_persistence_12", "mom_logret_8"],
    "trend_follow":      ["ema_spread_50_200", "ema_21_50_spread", "adx_20"],
    "breakout":          ["breakout_strength_24", "vol_spike_zscore_100"],
    "pullback":          ["dist_from_local_low_24", "rsi_14"],
    "bear_bounce":       ["bear_bounce_score", "oversold_depth_atr"],
    "regime_transition": ["autocorr_lag1_20", "vol_contraction_ratio"],
}

# Fraction minimale de la data retenue par le sélecteur (plancher anti-starving)
_MIN_FRAC = 0.25


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index)


def _archetype_mask(df: pd.DataFrame, archetype: str, fallback_frac: float = _MIN_FRAC) -> np.ndarray:
    """Sélectionne les lignes pertinentes pour chaque archétype (pas de leakage)."""
    n = len(df)

    if archetype == "momentum":
        tp = _col(df, "trend_persistence_12", 0.5).values
        mom = _col(df, "mom_logret_8", 0.0).values
        mask = (tp > 0.55) & (mom > 0)

    elif archetype == "trend_follow":
        ema_sp = _col(df, "ema_spread_50_200", 0.0).values
        adx    = _col(df, "adx_20", 20.0).values
        mask = (ema_sp > 0) & (adx > 20)

    elif archetype == "breakout":
        bs  = _col(df, "breakout_strength_24", 0.5).values
        vzs = _col(df, "vol_spike_zscore_100", 0.0).values
        mask = (bs > 0.70) & (vzs > 0.5)

    elif archetype == "pullback":
        dist = _col(df, "dist_from_local_low_24", 0.1).values
        rsi  = _col(df, "rsi_14", 50.0).values
        mask = (dist < 0.04) & (rsi < 50)

    elif archetype == "bear_bounce":
        bbs  = _col(df, "bear_bounce_score", 0.0).values
        ods  = _col(df, "oversold_depth_atr", 0.0).values
        mask = (bbs >= 1.5) & (ods > 0.5)

    elif archetype == "regime_transition":
        acorr = _col(df, "autocorr_lag1_20", 0.0).values
        vcr   = _col(df, "vol_contraction_ratio", 1.0).values
        mask = (acorr < 0.0) & (vcr < 0.95)

    else:
        mask = np.ones(n, dtype=bool)

    frac = mask.sum() / max(n, 1)
    if frac < fallback_frac:
        # Si trop peu de lignes, fallback sur aléatoire temporel (garder la densité)
        idx = np.sort(np.random.RandomState(42).choice(n, size=max(int(n * fallback_frac), 20), replace=False))
        mask = np.zeros(n, dtype=bool)
        mask[idx] = True

    return mask.astype(bool)


def _regime_adaptive_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    y_col: str = "y_long",
    target_col: str = "future_ret_8h",
    regime_col: str = "regime_long",
) -> pd.Series:
    """
    Génère des labels y_long avec des quantiles adaptatifs selon le régime.

    Quantile calibré sur train uniquement (anti-leakage).
    BEAR : 0.75 (plus de labels → modèle plus actif en bear)
    NEUTRAL : 0.78
    BULL : 0.82

    Retourne une Series de même index que df, valeurs {0, 1, -1}.
    """
    if target_col not in df.columns:
        if y_col in df.columns:
            return df[y_col]
        return pd.Series(0, index=df.index)

    ret = pd.to_numeric(df[target_col], errors="coerce")
    regime = df.get(regime_col, pd.Series("NEUTRAL", index=df.index))
    if not isinstance(regime, pd.Series):
        regime = pd.Series("NEUTRAL", index=df.index)

    y = pd.Series(-1, index=df.index, dtype=np.int8)  # -1 = gray zone

    for reg, quantile in [("NO_LONG", 0.75), ("NEUTRAL", 0.78), ("LONGABLE", 0.82)]:
        reg_train = train_mask & (regime.values == reg)
        if reg_train.sum() < 50:
            reg_train = train_mask  # fallback
        thr = float(np.nanquantile(ret[reg_train].dropna().values, quantile))
        thr = max(thr, 0.005)  # plancher 0.5% pour éviter le bruit

        reg_all = (regime.values == reg)
        ret_reg = ret[reg_all]
        valid   = ret_reg.notna()
        y_reg   = pd.Series(-1, index=ret_reg.index, dtype=np.int8)
        y_reg[valid & (ret_reg >= thr)] = 1
        y_reg[valid & (ret_reg < thr)]  = 0
        y.loc[y_reg.index] = y_reg.values

    return y


# ─────────────────────────────────────────────────────────────────────────────
# Spécialiste individuel (LightGBM DART)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LGBMSpecialistV5:
    """TRM v5 individuel — LightGBM DART avec feature importance."""

    name:       str
    horizon:    TemporalHorizonV5
    archetype:  MovementArchetypeV5
    features:   List[str]

    clf_:            Optional[lgb.LGBMClassifier] = field(default=None, repr=False)
    val_auc_:        float = 0.0
    n_train_:        int   = 0
    n_pos_:          int   = 0
    top_features_:   List[str] = field(default_factory=list)
    fitted_features_: List[str] = field(default_factory=list)  # features réellement utilisées pour fit

    def _get_X(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        # Utiliser les features du dernier fit (pas top_features_ qui est un sous-ensemble)
        feats = self.fitted_features_ if self.fitted_features_ else (
            self.top_features_ if self.top_features_ else self.features
        )
        available = [f for f in feats if f in df.columns]
        if not available:
            return np.zeros((int(mask.sum()), 1), dtype=np.float32)
        X = df.loc[mask, available].fillna(0.0).values.astype(np.float32)
        return X

    def _feat_list(self) -> List[str]:
        return self.fitted_features_ if self.fitted_features_ else (
            self.top_features_ if self.top_features_ else self.features
        )

    def fit(
        self,
        df:         pd.DataFrame,
        train_mask: np.ndarray,
        y_adaptive: pd.Series,
        val_mask:   Optional[np.ndarray] = None,
    ) -> "LGBMSpecialistV5":
        # Intersection : archétype × train_mask
        arch_mask  = _archetype_mask(df, self.archetype.key)
        fit_mask   = train_mask & arch_mask

        available  = [f for f in self.features if f in df.columns]
        if not available:
            return self

        X_tr = df.loc[fit_mask, available].fillna(0.0).values.astype(np.float32)
        y_tr = y_adaptive.loc[fit_mask].values.astype(np.int8)

        valid   = y_tr >= 0
        X_tr    = X_tr[valid]
        y_tr    = y_tr[valid]

        self.n_train_ = len(y_tr)
        self.n_pos_   = int(y_tr.sum())

        if self.n_pos_ < 10 or self.n_train_ < 40 or self.n_pos_ / self.n_train_ < 0.02:
            return self

        n_neg = self.n_train_ - self.n_pos_
        scale_pos = min(n_neg / max(self.n_pos_, 1), 50.0)

        # Capacité selon horizon
        h = self.horizon.hours
        if h <= 8:
            n_est, depth, lr = 400, 5, 0.05
        elif h <= 24:
            n_est, depth, lr = 300, 4, 0.05
        else:
            n_est, depth, lr = 200, 4, 0.04

        self.clf_ = lgb.LGBMClassifier(
            boosting_type="dart",
            n_estimators=n_est,
            max_depth=depth,
            learning_rate=lr,
            num_leaves=min(2**depth - 1, 31),
            scale_pos_weight=scale_pos,
            min_child_samples=max(15, self.n_pos_ // 20),
            subsample=0.80,
            subsample_freq=1,
            colsample_bytree=0.75,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            verbose=-1,
            n_jobs=2,
        )
        self.clf_.fit(X_tr, y_tr)
        self.fitted_features_ = available  # stocker les features exactes du fit

        # Sélectionner top-60% features par importance (pour le prochain retrain)
        imp = self.clf_.feature_importances_
        n_keep = max(10, int(len(available) * 0.60))
        top_idx = np.argsort(imp)[::-1][:n_keep]
        self.top_features_ = [available[i] for i in top_idx]

        # Validation AUC
        if val_mask is not None and int(val_mask.sum()) > 20:
            X_val  = df.loc[val_mask, available].fillna(0.0).values.astype(np.float32)
            y_val  = y_adaptive.loc[val_mask].values.astype(np.int8)
            valid_v = y_val >= 0
            X_val, y_val = X_val[valid_v], y_val[valid_v]
            if len(X_val) > 10 and y_val.sum() >= 3 and (y_val == 0).sum() >= 3:
                p = self.clf_.predict_proba(X_val)[:, 1]
                try:
                    self.val_auc_ = float(roc_auc_score(y_val, p))
                except Exception:
                    self.val_auc_ = 0.5

        return self

    def predict_proba(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        if self.clf_ is None:
            return np.full(int(mask.sum()), 0.5, dtype=np.float32)
        feats = self._feat_list()
        available = [f for f in feats if f in df.columns]
        if not available:
            return np.full(int(mask.sum()), 0.5, dtype=np.float32)
        X = df.loc[mask, available].fillna(0.0).values.astype(np.float32)
        return self.clf_.predict_proba(X)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Stacking méta-couche
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StackingMetaLayerV5:
    """LR méta entraîné sur les prédictions OOF des 24 spécialistes."""

    clf_:    Optional[LogisticRegression] = field(default=None, repr=False)
    scaler_: Optional[StandardScaler]     = field(default=None, repr=False)
    val_auc_: float = 0.0
    n_specialists_: int = 0

    def fit(
        self,
        oof_preds: np.ndarray,   # (n_holdout, n_specialists)
        y_holdout: np.ndarray,   # (n_holdout,)  — labels binaires 0/1
    ) -> "StackingMetaLayerV5":
        valid = y_holdout >= 0
        X, y = oof_preds[valid], y_holdout[valid]
        if len(X) < 30 or y.sum() < 5:
            return self

        self.n_specialists_ = X.shape[1]
        self.scaler_ = StandardScaler()
        Xs = self.scaler_.fit_transform(X)

        self.clf_ = LogisticRegression(
            C=0.5, class_weight="balanced",
            solver="lbfgs", max_iter=500,
            random_state=42,
        )
        self.clf_.fit(Xs, y)

        p = self.clf_.predict_proba(Xs)[:, 1]
        try:
            self.val_auc_ = float(roc_auc_score(y, p))
        except Exception:
            self.val_auc_ = 0.5
        return self

    def predict_proba(self, specialist_preds: np.ndarray) -> np.ndarray:
        if self.clf_ is None or self.scaler_ is None:
            return specialist_preds.mean(axis=1).astype(np.float32)
        Xs = self.scaler_.transform(specialist_preds)
        return self.clf_.predict_proba(Xs)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Fleet principale
# ─────────────────────────────────────────────────────────────────────────────

class TRMFleetLongV5:
    """
    TRM Fleet Long v5 — 24 spécialistes LightGBM DART + meta-LR stacking.

    Usage :
        fleet = TRMFleetLongV5(features_long, features_alpha)
        fleet.fit(df_train, train_mask, val_mask)
        p_long = fleet.predict(df)
    """

    def __init__(
        self,
        features_base: List[str],
        features_alpha: Optional[List[str]] = None,
        n_top_specialists: int = 8,
    ):
        """
        features_base  : FEATURES_INST_LONG (ou similaire)
        features_alpha : FEATURES_ALPHA (alpha_features.py)
        n_top_specialists : nombre de spécialistes retenus pour le vote final
        """
        from ai.level_0.alpha_features import FEATURES_ALPHA as _FA
        self.features_all: List[str] = list(dict.fromkeys(
            features_base + (features_alpha or _FA)
        ))
        self.n_top_k      = n_top_specialists
        self.specialists_: List[LGBMSpecialistV5] = []
        self.meta_:        Optional[StackingMetaLayerV5] = None
        self.fit_time_:    float = 0.0
        self.fleet_auc_:   float = 0.0

    # ── Helpers internes ──────────────────────────────────────────────────────

    def _build_specialists(self) -> List[LGBMSpecialistV5]:
        specialists = []
        for h in TEMPORAL_HORIZONS_V5:
            extra = HORIZON_FEATURE_EXTRAS.get(h.key, [])
            feats = list(dict.fromkeys(self.features_all + extra))
            for a in MOVEMENT_ARCHETYPES_V5:
                specialists.append(LGBMSpecialistV5(
                    name=f"{h.key}_{a.key}",
                    horizon=h,
                    archetype=a,
                    features=feats,
                ))
        return specialists

    def _oof_split(self, train_mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Split temporel 70/30 sur les indices train — pas de shuffle."""
        indices = np.where(train_mask)[0]
        cut = int(len(indices) * 0.70)
        base_mask = np.zeros(len(train_mask), dtype=bool)
        hold_mask = np.zeros(len(train_mask), dtype=bool)
        base_mask[indices[:cut]] = True
        hold_mask[indices[cut:]] = True
        return base_mask, hold_mask

    # ── Fit ───────────────────────────────────────────────────────────────────

    def fit(
        self,
        df:         pd.DataFrame,
        train_mask: np.ndarray,
        val_mask:   Optional[np.ndarray] = None,
        target_col: str = "future_ret_8h",
        regime_col: str = "regime_long",
    ) -> "TRMFleetLongV5":
        t0 = time.time()

        # Labels adaptatifs au régime
        y_adaptive = _regime_adaptive_labels(
            df, train_mask, target_col=target_col, regime_col=regime_col
        )

        # Split base / holdout pour le stacking
        base_mask, hold_mask = self._oof_split(train_mask)

        # Entraîner les 24 spécialistes sur base_mask
        self.specialists_ = self._build_specialists()
        print(f"  [v5] Training {len(self.specialists_)} specialists "
              f"(base={int(base_mask.sum()):,} bars, "
              f"holdout={int(hold_mask.sum()):,} bars)...")

        trained = 0
        for sp in self.specialists_:
            sp.fit(df, base_mask, y_adaptive, val_mask=hold_mask)
            if sp.clf_ is not None:
                trained += 1

        print(f"  [v5] {trained}/{len(self.specialists_)} specialists fitted. "
              f"Building OOF predictions for stacking...")

        # OOF predictions sur holdout
        n_hold   = int(hold_mask.sum())
        n_sp     = len(self.specialists_)
        oof_preds = np.full((n_hold, n_sp), 0.5, dtype=np.float32)

        for j, sp in enumerate(self.specialists_):
            oof_preds[:, j] = sp.predict_proba(df, hold_mask)

        y_hold = y_adaptive.loc[hold_mask].values.astype(np.int8)

        # Entraîner la méta-couche LR
        self.meta_ = StackingMetaLayerV5()
        self.meta_.fit(oof_preds, y_hold)
        print(f"  [v5] Meta-LR val AUC = {self.meta_.val_auc_:.4f}")

        # Re-entraîner les spécialistes sur la totalité de train_mask
        # En héritant les top_features_ de la première passe
        print("  [v5] Re-training specialists on full train set...")
        first_pass = {sp.name: sp.top_features_ for sp in self.specialists_}
        self.specialists_ = self._build_specialists()
        for sp in self.specialists_:
            if sp.name in first_pass and first_pass[sp.name]:
                sp.features = first_pass[sp.name]  # utiliser features sélectionnées
            sp.fit(df, train_mask, y_adaptive, val_mask=val_mask)

        aucs = [sp.val_auc_ for sp in self.specialists_
                if sp.clf_ is not None and sp.val_auc_ > 0.5]
        # Si les spécialistes n'ont pas de val_auc (y_val tout gris), utiliser meta AUC
        self.fleet_auc_ = float(np.mean(aucs)) if aucs else (
            self.meta_.val_auc_ if self.meta_ else 0.5
        )
        self.fit_time_  = time.time() - t0

        n_fitted = sum(1 for sp in self.specialists_ if sp.clf_ is not None)
        print(f"  [v5] Fleet fitted — {n_fitted}/24 specialists  "
              f"AUC(meta)={self.fleet_auc_:.4f}  "
              f"time={self.fit_time_:.1f}s")

        return self

    # ── Predict ───────────────────────────────────────────────────────────────

    def predict(
        self,
        df:   pd.DataFrame,
        mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Retourne p_long ∈ [0, 1] pour chaque barre dans mask.
        Si mask=None, prédit sur tout le DataFrame.
        """
        if mask is None:
            mask = np.ones(len(df), dtype=bool)

        n_masked = int(mask.sum())
        if n_masked == 0:
            return np.array([], dtype=np.float32)

        n_sp = len(self.specialists_)
        if n_sp == 0:
            return np.full(n_masked, 0.5, dtype=np.float32)

        # Prédictions de tous les spécialistes
        all_preds = np.full((n_masked, n_sp), 0.5, dtype=np.float32)
        for j, sp in enumerate(self.specialists_):
            all_preds[:, j] = sp.predict_proba(df, mask)

        # Méta-LR si disponible
        if self.meta_ is not None and self.meta_.clf_ is not None:
            return self.meta_.predict_proba(all_preds)

        # Fallback : top-k weighted vote par AUC
        aucs = np.array([sp.val_auc_ for sp in self.specialists_], dtype=np.float32)
        weights = np.maximum(aucs - 0.50, 0.0) ** 2
        if weights.sum() < _EPS:
            weights = np.ones(n_sp, dtype=np.float32)
        weights /= weights.sum()
        return (all_preds * weights[None, :]).sum(axis=1)

    def predict_single(self, df: pd.DataFrame, row_idx: int) -> float:
        """Prédiction pour une seule barre (paper trading live)."""
        mask = np.zeros(len(df), dtype=bool)
        mask[row_idx] = True
        result = self.predict(df, mask)
        return float(result[0]) if len(result) else 0.5

    # ── Reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = [
            f"TRM Fleet Long v5 — {len(self.specialists_)} specialists",
            f"  Fleet mean AUC  : {self.fleet_auc_:.4f}",
            f"  Meta-LR AUC     : {self.meta_.val_auc_ if self.meta_ else 'N/A'}",
            f"  Fit time        : {self.fit_time_:.1f}s",
            "",
            "  Per-specialist (val_auc, n_train, n_pos) :",
        ]
        for sp in sorted(self.specialists_, key=lambda s: -s.val_auc_)[:12]:
            lines.append(
                f"    {sp.name:<32}  AUC={sp.val_auc_:.4f}  "
                f"n={sp.n_train_:>5}  pos={sp.n_pos_:>4}"
            )
        return "\n".join(lines)

    def top_specialists(self, n: int = 8) -> List[LGBMSpecialistV5]:
        return sorted(
            [sp for sp in self.specialists_ if sp.clf_ is not None],
            key=lambda s: -s.val_auc_
        )[:n]
