"""
level_3/specialist.py — ENTRAÎNEMENT D'UN EXPERT PAR CONTEXTE
=============================================================

Chaque expert est un XGBoost entraîné uniquement sur les barres de son contexte.
La spécialisation est la clé : un expert TREND_LONG ne voit que des barres
en tendance haussière — ses features et poids sont ajustés pour ce régime.

Principes
---------
  - Entraînement sur train_mask ∩ context_mask (intersection)
  - Évaluation sur val_mask ∩ context_mask
  - Features adaptées au contexte (pas les mêmes pour trend vs mean-reversion)
  - Rejet automatique si n_train < 300 ou AUC val < min_auc
  - Calibration isotonique sur val avant enregistrement dans le routeur

Architecture par contexte
-------------------------
  TREND_LONG     : FEATURES_LONG (momentum fort, EMA, trend features)
  TREND_SHORT    : FEATURES_SHORT (reversal, surachat, pression vendeuse)
  MEAN_REVERSION : features spécifiques MR (oscillateurs, autocorr, extremes)
  BREAKOUT       : features breakout (eff_ratio, boll_expansion, vol)
  HIGH_VOL       : FEATURES_COMMON + vol features (pas de trend features)
  NEUTRAL        : FEATURES_LONG (généraliste — fallback)
"""
from __future__ import annotations

import json
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    precision_recall_fscore_support,
)
from sklearn.preprocessing import StandardScaler

from ai.level_0.features import (
    FEATURES_LONG, FEATURES_SHORT, FEATURES_COMMON,
)
from ai.level_0.live_features import MACRO_BUNDLE_COLS
from ai.level_0.preprocessing import get_X
from ai.level_3.contexts import MarketContext


# ─────────────────────────────────────────────────────────────────────────────
# Features spécialisées par contexte
# ─────────────────────────────────────────────────────────────────────────────

# Mean reversion : oscillateurs + structure d'extension
FEATURES_MR: List[str] = list(dict.fromkeys([
    # Oscillateurs (cœur du MR)
    "rsi_14", "cci_20", "boll_pos_20",
    # Autocorrélation et structure de retournement
    "ret_neg_autocorr_12", "ret_pos_autocorr_12",
    # Extension prix
    "dist_from_local_high_24", "dist_from_local_low_24",
    "dist_from_local_high_168", "dist_from_local_low_168",
    "zscore_close_24", "zscore_ret_24",
    # Skew (asymétrie des queues)
    "skew_ret_12", "skew_ret_24",
    # Vol asymétrique
    "downside_vol_ratio_24", "upside_vol_ratio_24",
    # Momentum épuisé (retournement imminent)
    "mom_sharpe_6", "mom_sharpe_12", "mom_logret_6",
    # Pression directionnelle (divergence avec le trend)
    "taker_buy_ratio_base", "delta_taker_pressure",
    "sell_vol_ratio_6", "buy_vol_ratio_6",
    # Structure de barre
    "close_in_bar", "intrabar_range_pct",
    # Commun
    "rv_24", "rv_ratio_24_72", "atr_pct_14",
    "hour_sin", "hour_cos",
    # Macro / sentiment — signal contra pour MR (foule extrême → retournement)
    "fear_greed_value_z_24",
    "taker_ls_imbalance",
    "global_ls_longShortRatio_z_24",
]))

# Breakout : efficience directionnelle + expansion de range
FEATURES_BREAKOUT: List[str] = list(dict.fromkeys([
    # Efficience et direction
    "eff_ratio_12", "eff_ratio_24",
    # Expansion Bollinger
    "boll_expansion_6", "boll_width_20", "boll_pos_20",
    # Momentum de cassure
    "mom_logret_6", "mom_logret_12", "mom_logret_24",
    "momentum_accel_6",
    # Breakout structure
    "breakout_strength_24",
    "dist_from_local_high_24", "dist_from_local_low_24",
    # Pression directionnelle
    "taker_buy_ratio_base", "delta_taker_pressure",
    "taker_buy_cumul_12",
    "vol_ratio_24", "trades_ratio_24",
    # Vol
    "rv_12", "rv_24", "rv_ratio_24_72", "atr_pct_14",
    "intrabar_range_pct",
    # Temporel
    "hour_sin", "hour_cos",
    # Macro — OI + funding confirment la cassure (positions en expansion)
    "oihist_sumOpenInterest_z_24",
    "funding_rate_z_24",
]))

# High vol : uniquement features robustes à la volatilité (pas de momentum trend)
FEATURES_HIGH_VOL: List[str] = list(dict.fromkeys(
    FEATURES_COMMON + [
        "rv_12", "rv_48", "rv_72", "rv_168",
        "rv_ratio_24_72", "rv_ratio_12_48",
        "max_drawdown_12",
        "downside_vol_ratio_24", "upside_vol_ratio_24",
        "boll_pos_20", "close_in_bar",
        "taker_buy_ratio_base", "delta_taker_pressure",
        "skew_ret_12", "skew_ret_24",
        # Macro structurel — en high vol, le sentiment est le signal le plus stable
        "fear_greed_value_z_72",
        "funding_rate_z_288",
        "oihist_sumOpenInterest_z_72",
    ]
))

# Mapping contexte → features par défaut
CONTEXT_FEATURES: Dict[str, List[str]] = {
    MarketContext.TREND_LONG.value:     FEATURES_LONG,
    MarketContext.TREND_SHORT.value:    FEATURES_SHORT,
    MarketContext.MEAN_REVERSION.value: FEATURES_MR,
    MarketContext.BREAKOUT.value:       FEATURES_BREAKOUT,
    MarketContext.HIGH_VOL.value:       FEATURES_HIGH_VOL,
    MarketContext.NEUTRAL.value:        FEATURES_LONG,
}

# Mapping contexte → label principal (long ou short)
CONTEXT_SIDE: Dict[str, str] = {
    MarketContext.TREND_LONG.value:     "long",
    MarketContext.TREND_SHORT.value:    "short",
    MarketContext.MEAN_REVERSION.value: "both",
    MarketContext.BREAKOUT.value:       "both",
    MarketContext.HIGH_VOL.value:       "both",
    MarketContext.NEUTRAL.value:        "long",
}


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpecialistConfig:
    """Hyperparamètres pour l'entraînement des experts."""
    seed: int = 42

    # XGBoost — plus léger que level_2 (moins de données par expert)
    n_estimators: int = 400
    max_depth: int = 4
    learning_rate: float = 0.05
    subsample: float = 0.80
    colsample_bytree: float = 0.75
    reg_alpha: float = 0.10
    reg_lambda: float = 1.00
    min_child_weight: int = 10

    # Critères d'acceptation
    min_auc: float = 0.56
    min_train_samples: int = 300
    min_val_samples: int = 100

    # Calibration
    calibrate: bool = True

    # Contextes à entraîner (None = tous)
    contexts_to_train: Optional[List[str]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Entraînement d'un expert
# ─────────────────────────────────────────────────────────────────────────────

def train_specialist(
    df,
    context: MarketContext,
    context_mask: np.ndarray,     # mask des barres dans ce contexte
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    side: str,                     # "long", "short", ou "both" → label à utiliser
    out_dir: Path,
    cfg: Optional[SpecialistConfig] = None,
    features: Optional[List[str]] = None,
) -> Optional[Dict]:
    """
    Entraîne un expert spécialisé pour un contexte donné.

    Arguments
    ---------
    df           : DataFrame complet avec features + labels
    context      : contexte pour lequel entraîner l'expert
    context_mask : masque booléen des barres dans ce contexte
    train_mask   : masque booléen du split train
    val_mask     : masque booléen du split val
    side         : "long" → y_long, "short" → y_short, "both" → y_long prioritaire
    out_dir      : répertoire de sortie pour les artefacts
    cfg          : configuration XGBoost
    features     : liste de features (défaut = CONTEXT_FEATURES[context])

    Retourne
    --------
    dict avec {model, scaler, calibrator, features, metrics} ou None si rejeté
    """
    cfg  = cfg or SpecialistConfig()
    ctx_val = context.value

    if features is None:
        features = _filter_available_features(df, CONTEXT_FEATURES.get(ctx_val, FEATURES_LONG))

    # ── Sélection du label ─────────────────────────────────────────────────────
    label_col = _pick_label(df, side)
    if label_col is None:
        print(f"   [{ctx_val}] ✗ label introuvable pour side={side}")
        return None

    # ── Masques combinés (contexte ∩ split) ────────────────────────────────────
    train_ctx = train_mask & context_mask
    val_ctx   = val_mask   & context_mask

    # Exclure gray zones
    y_train_raw = df.loc[train_ctx, label_col].values.astype(np.int32)
    valid_tr    = y_train_raw >= 0
    train_clean = np.where(train_ctx)[0][valid_tr]
    train_clean_mask = np.zeros(len(df), dtype=bool)
    train_clean_mask[train_clean] = True

    y_val_raw = df.loc[val_ctx, label_col].values.astype(np.int32)
    valid_v   = y_val_raw >= 0
    val_clean = np.where(val_ctx)[0][valid_v]
    val_clean_mask = np.zeros(len(df), dtype=bool)
    val_clean_mask[val_clean] = True

    n_tr = int(train_clean_mask.sum())
    n_v  = int(val_clean_mask.sum())

    print(f"\n   Expert [{ctx_val:<18}]  label={label_col}  "
          f"train={n_tr:,}  val={n_v:,}")

    if n_tr < cfg.min_train_samples:
        print(f"   [{ctx_val}] ✗ trop peu de données train ({n_tr} < {cfg.min_train_samples})")
        return None

    if n_v < cfg.min_val_samples:
        print(f"   [{ctx_val}] ✗ trop peu de données val ({n_v} < {cfg.min_val_samples})")
        return None

    X_train = get_X(df, train_clean_mask, features)
    y_train = df.loc[train_clean_mask, label_col].values.astype(np.int32)
    X_val   = get_X(df, val_clean_mask,   features)
    y_val   = df.loc[val_clean_mask,      label_col].values.astype(np.int32)

    pos_tr = int((y_train == 1).sum())
    if pos_tr < 30:
        print(f"   [{ctx_val}] ✗ trop peu de positifs en train ({pos_tr})")
        return None

    spw = float((y_train == 0).sum()) / max(pos_tr, 1)

    # ── Normalisation ──────────────────────────────────────────────────────────
    scaler = StandardScaler()
    X_train_sc = scaler.fit_transform(X_train)
    X_val_sc   = scaler.transform(X_val)

    # ── XGBoost ────────────────────────────────────────────────────────────────
    t0 = time.time()
    model = _build_xgb(cfg, spw)
    model.fit(X_train_sc, y_train)
    dt = time.time() - t0

    # ── Évaluation ────────────────────────────────────────────────────────────
    metrics = _eval(model, X_val_sc, y_val, ctx_val, dt)
    metrics["n_train"] = n_tr
    metrics["n_val"]   = n_v
    metrics["label"]   = label_col
    metrics["context"] = ctx_val

    if metrics["auc"] < cfg.min_auc:
        print(f"   [{ctx_val}] ✗ AUC trop faible ({metrics['auc']:.4f} < {cfg.min_auc})")
        # Sauvegarder quand même pour analyse
        _save_rejected(out_dir, ctx_val, metrics)
        return None

    # ── Calibration isotonique ─────────────────────────────────────────────────
    calibrator = None
    if cfg.calibrate:
        raw_proba = model.predict_proba(X_val_sc)[:, 1]
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_proba, y_val)
        cal_proba = calibrator.predict(raw_proba)
        ece = _compute_ece(cal_proba, y_val)
        print(f"   [{ctx_val}] calibré  ECE={ece:.4f}")
        metrics["ece_after_calibration"] = round(ece, 5)

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx_dir = out_dir / ctx_val
    ctx_dir.mkdir(exist_ok=True)

    with open(ctx_dir / "model.pkl",  "wb") as f: pickle.dump(model,  f)
    with open(ctx_dir / "scaler.pkl", "wb") as f: pickle.dump(scaler, f)
    if calibrator is not None:
        with open(ctx_dir / "calibrator.pkl", "wb") as f: pickle.dump(calibrator, f)

    meta = {
        "features": features,
        "weight":   0.0,   # sera mis à jour par le routeur
        "accepted": True,
        "side":     side,
        "metrics":  metrics,
    }
    with open(ctx_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"   [{ctx_val}] ✓ sauvegardé → {ctx_dir}")

    return {
        "model":      model,
        "scaler":     scaler,
        "calibrator": calibrator,
        "features":   features,
        "metrics":    metrics,
        "side":       side,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pick_label(df, side: str) -> Optional[str]:
    """Choisit le label selon le côté."""
    if side == "long" and "y_long" in df.columns:
        return "y_long"
    if side == "short" and "y_short" in df.columns:
        return "y_short"
    if side == "both":
        if "y_long" in df.columns:
            return "y_long"
        if "y_short" in df.columns:
            return "y_short"
    return None


def _filter_available_features(df, features: List[str]) -> List[str]:
    """Filtre la liste de features pour ne garder que celles disponibles dans df."""
    available = [f for f in features if f in df.columns]
    missing = set(features) - set(available)
    if missing:
        print(f"   ⚠  Features absentes (ignorées) : {sorted(missing)}")
    return available


def _build_xgb(cfg: SpecialistConfig, spw: float):
    """Construit XGBoost avec fallback HistGBT."""
    try:
        from xgboost import XGBClassifier
        return XGBClassifier(
            n_estimators=cfg.n_estimators,
            max_depth=cfg.max_depth,
            learning_rate=cfg.learning_rate,
            subsample=cfg.subsample,
            colsample_bytree=cfg.colsample_bytree,
            scale_pos_weight=spw,
            reg_alpha=cfg.reg_alpha,
            reg_lambda=cfg.reg_lambda,
            min_child_weight=cfg.min_child_weight,
            use_label_encoder=False,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=cfg.seed,
        )
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(
            learning_rate=cfg.learning_rate,
            max_iter=cfg.n_estimators,
            max_depth=cfg.max_depth,
            min_samples_leaf=cfg.min_child_weight * 2,
            class_weight="balanced",
            random_state=cfg.seed,
        )


def _eval(model, X_val_sc, y_val, label: str, elapsed: float) -> Dict:
    """Évalue le modèle et affiche les métriques."""
    y_pred  = model.predict(X_val_sc)
    y_proba = model.predict_proba(X_val_sc)[:, 1]

    mf1  = float(f1_score(y_val, y_pred, average="macro", zero_division=0))
    acc  = float(accuracy_score(y_val, y_pred))
    prec, rec, _, _ = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1], zero_division=0
    )
    try:
        auc = float(roc_auc_score(y_val, y_proba))
    except Exception:
        auc = float("nan")

    print(f"   [{label:<18}]  AUC={auc:.4f}  macro_F1={mf1:.4f}  "
          f"acc={acc:.4f}  prec_pos={prec[1]:.3f}  rec_pos={rec[1]:.3f}  "
          f"t={elapsed:.1f}s")

    return {
        "auc":            round(auc, 4),
        "macro_f1":       round(mf1, 4),
        "acc":            round(acc, 4),
        "precision_pos":  round(float(prec[1]), 4),
        "recall_pos":     round(float(rec[1]), 4),
    }


def _compute_ece(proba: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece  = 0.0
    n    = len(y_true)
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (proba >= lo) & (proba < hi)
        if not mask.any():
            continue
        acc   = float(y_true[mask].mean())
        conf  = float(proba[mask].mean())
        ece  += (mask.sum() / n) * abs(acc - conf)
    return ece


def _save_rejected(out_dir: Path, ctx_val: str, metrics: dict) -> None:
    """Sauvegarde les métriques d'un expert rejeté pour analyse."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        rejected_path = out_dir / "rejected_experts.json"
        existing = {}
        if rejected_path.exists():
            with open(rejected_path) as f:
                existing = json.load(f)
        existing[ctx_val] = metrics
        with open(rejected_path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass
