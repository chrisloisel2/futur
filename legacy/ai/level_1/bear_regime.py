"""
level_1/bear_regime.py — META-MODÈLE DE RÉGIME BEAR
====================================================

Architecture méta-filtre :

    [Regime Model] → p_bear_regime
          ↓
    IF p_bear > threshold (0.70 par défaut) :
        run short model
    ELSE :
        no short

Pourquoi ce modèle ?
  Le modèle short (edge model) apprend à identifier des barres individuelles
  où une baisse de 1h est probable. Il ne sait pas si on EST dans un bear market.
  Le meta-modèle de régime répond à la question : "Est-ce qu'on est dans un
  contexte macro baissier ?" Si non → pas de short même si le signal edge est fort.

Label :
  y_bear_regime[t] = label STRUCTUREL backward-looking
  = 1 si (prix < EMA50) ET (EMA50 < EMA200) ET (RSI < 48) ET (mom_72 < 0)

Features :
  FEATURES_REGIME = 12 features macro-structurelles (voir level_0/features.py)
  Délibérément MOINS de features que le modèle edge — on veut capter la macro,
  pas le signal bar-by-bar.

Usage :
    from ai.level_1.bear_regime import train_bear_regime_model

    regime_result = train_bear_regime_model(df, train_mask, val_mask, out_dir)
    # → regime_result["model"], regime_result["scaler"],
    #   regime_result["threshold"], regime_result["features"]

    # Dans le backtest :
    p_bear = regime_result["model"].predict_proba(X_regime)[:, 1]
    if p_bear[i] >= regime_result["threshold"]:
        # short autorisé
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_recall_curve
from sklearn.preprocessing import StandardScaler

from ai.level_0.features import FEATURES_REGIME
from ai.level_0.labels import build_bear_regime_label


def train_bear_regime_model(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    horizon_bars: int = 72,
    bear_threshold_pct: float = -0.02,
    activation_threshold: float = 0.70,
    features: Optional[List[str]] = None,
) -> Dict:
    """
    Entraîne le méta-modèle de régime bear.

    Arguments
    ---------
    df                    : DataFrame avec features macro et colonnes close/y_short
    train_mask            : masque booléen train
    val_mask              : masque booléen val
    out_dir               : dossier de sauvegarde
    horizon_bars          : fenêtre forward pour le label (défaut 72h = 3 jours)
    bear_threshold_pct    : seuil de baisse pour labeler "bear" (défaut -2%)
    activation_threshold  : seuil p_bear pour activer le short (défaut 0.70)
    features              : liste de features (défaut FEATURES_REGIME)

    Retourne
    --------
    dict avec :
        model, scaler, features, threshold
        val_auc, val_bear_pct, short_rate_in_bear
    """
    out_dir = Path(out_dir)
    _feats = features or FEATURES_REGIME

    missing = [f for f in _feats if f not in df.columns]
    if missing:
        print(f"   ⚠  BearRegime: features manquantes {missing} — retrait")
        _feats = [f for f in _feats if f in df.columns]

    if len(_feats) == 0:
        raise RuntimeError("BearRegime: aucune feature disponible.")

    y_bear_full = build_bear_regime_label(df, horizon_bars=horizon_bars,
                                          threshold=bear_threshold_pct)
    df = df.copy()
    df["y_bear_regime"] = y_bear_full

    train_valid = train_mask & (y_bear_full.values >= 0)
    val_valid   = val_mask   & (y_bear_full.values >= 0)

    X_train = df.loc[train_valid, _feats].fillna(0.0).values.astype(np.float32)
    y_train = df.loc[train_valid, "y_bear_regime"].values.astype(np.int32)
    X_val   = df.loc[val_valid,   _feats].fillna(0.0).values.astype(np.float32)
    y_val   = df.loc[val_valid,   "y_bear_regime"].values.astype(np.int32)

    n_bear_train = int(y_train.sum())
    n_bear_val   = int(y_val.sum())
    pct_bear_train = n_bear_train / max(len(y_train), 1)
    pct_bear_val   = n_bear_val   / max(len(y_val),   1)

    print(f"\n   [BearRegime] Label bear : train={n_bear_train:,} ({pct_bear_train:.1%})  "
          f"val={n_bear_val:,} ({pct_bear_val:.1%})")
    print(f"   [BearRegime] Features   : {len(_feats)}")

    if n_bear_train < 50:
        print("   ⚠  BearRegime: trop peu de labels bear en train — régime désactivé")
        return _null_regime_result(_feats, activation_threshold)

    scaler = StandardScaler()
    scaler.fit(X_train)
    Xs_train = scaler.transform(X_train)
    Xs_val   = scaler.transform(X_val)

    # Logistique intentionnellement : on veut des probabilités calibrées,
    # pas de surapprentissage (le régime est une macro-information lente).
    clf = LogisticRegression(
        C=0.5,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    clf.fit(Xs_train, y_train)

    p_val = clf.predict_proba(Xs_val)[:, 1]

    try:
        val_auc = float(roc_auc_score(y_val, p_val))
    except Exception:
        val_auc = float("nan")

    print(f"   [BearRegime] AUC val   : {val_auc:.4f}")

    threshold_cal = _calibrate_regime_threshold(
        df=df,
        mask=val_valid,
        p_bear=p_val,
        min_activated_pct=0.10,
        default_threshold=activation_threshold,
    )
    print(f"   [BearRegime] Seuil calibré : {threshold_cal:.2f}  "
          f"(défaut={activation_threshold:.2f})")

    if "y_short" in df.columns:
        y_short_val = df.loc[val_valid, "y_short"].values.astype(np.int32)
        bear_active = p_val >= threshold_cal
        bear_inactive = ~bear_active
        if bear_active.sum() > 0:
            sr_active   = float((y_short_val[bear_active]   == 1).mean())
            sr_inactive = float((y_short_val[bear_inactive] == 1).mean()) if bear_inactive.sum() > 0 else 0.0
            sr_overall  = float((y_short_val >= 1).mean())
            print(f"   [BearRegime] short_rate dans bear_activé={sr_active:.1%}  "
                  f"inactivé={sr_inactive:.1%}  global={sr_overall:.1%}")
            print(f"   [BearRegime] Barres activées : {bear_active.sum():,} / {len(y_val):,} "
                  f"({bear_active.mean():.1%})")
        else:
            sr_active = sr_inactive = sr_overall = float("nan")
    else:
        sr_active = sr_inactive = sr_overall = float("nan")

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "bear_regime_model.pkl",  "wb") as f: pickle.dump(clf, f)
    with open(out_dir / "bear_regime_scaler.pkl", "wb") as f: pickle.dump(scaler, f)
    with open(out_dir / "model.pkl", "wb") as f: pickle.dump(clf, f)
    with open(out_dir / "scaler.pkl", "wb") as f: pickle.dump(scaler, f)
    metrics = {
        "features":              _feats,
        "n_features":            len(_feats),
        "horizon_bars":          horizon_bars,
        "bear_threshold_pct":    bear_threshold_pct,
        "activation_threshold":  threshold_cal,
        "threshold":             threshold_cal,
        "pct_bear_train":        round(pct_bear_train, 4),
        "pct_bear_val":          round(pct_bear_val, 4),
        "val_auc":               round(val_auc, 4),
        "short_rate_in_bear_active":   round(sr_active, 4)   if not np.isnan(sr_active)   else None,
        "short_rate_in_bear_inactive": round(sr_inactive, 4) if not np.isnan(sr_inactive) else None,
        "short_rate_overall":          round(sr_overall, 4)  if not np.isnan(sr_overall)  else None,
    }
    with open(out_dir / "bear_regime_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    with open(out_dir / "metadata.json", "w") as f:
        json.dump({
            **metrics,
            "schema_version": 2,
            "component": "regime",
            "model_file": "model.pkl",
            "scaler_file": "scaler.pkl",
            "enabled_for_inference": True,
        }, f, indent=2)

    return {
        "model":     clf,
        "scaler":    scaler,
        "features":  _feats,
        "threshold": threshold_cal,
        "val_auc":   val_auc,
        "metrics":   metrics,
    }


def _calibrate_regime_threshold(
    df: pd.DataFrame,
    mask: np.ndarray,
    p_bear: np.ndarray,
    min_activated_pct: float = 0.10,
    default_threshold: float = 0.70,
) -> float:
    """
    Calibre le seuil d'activation du régime bear sur la val.

    Critère : maximiser short_rate dans les barres activées
    tout en gardant min_activated_pct de barres actives.

    Si y_short absent ou signal faible : retourne default_threshold.
    """
    if "y_short" not in df.columns:
        return default_threshold

    y_short = df.loc[mask, "y_short"].values.astype(np.int32)
    n_total = len(p_bear)

    if n_total < 100:
        return default_threshold

    best_thr   = default_threshold
    best_score = 0.0

    for thr in np.arange(0.40, 0.90, 0.02):
        active = p_bear >= thr
        n_active = int(active.sum())

        if n_active < max(n_total * min_activated_pct, 20):
            break

        sr = float((y_short[active] == 1).mean()) if n_active > 0 else 0.0

        # Critère : short_rate × sqrt(n_active) — qualité × couverture
        score = sr * np.sqrt(n_active)
        if score > best_score:
            best_score = score
            best_thr   = thr

    return float(best_thr)


def _null_regime_result(features: List[str], threshold: float) -> Dict:
    """Retourne un résultat neutre quand le régime ne peut pas être entraîné."""
    return {
        "model":     None,
        "scaler":    None,
        "features":  features,
        "threshold": threshold,
        "val_auc":   float("nan"),
        "metrics":   {"disabled": True},
    }
