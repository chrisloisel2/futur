"""
level_0/filter.py — FILTRE TRADEABLE CLASS-BALANCED
====================================================

Entraîne le filtre Stage 1 (tradeable vs non-tradeable).

PROBLÈME RÉSOLU ICI :
  Le filtre précédent obtenait F1=0.038, recall_tradeable=2.16%.
  Cause : déséquilibre de classes 75/25 non compensé.
  Fix   : scale_pos_weight (XGBoost) / class_weight="balanced" (sklearn)
          + calibration du seuil sur métrique business, pas F1 brut.

Ce filtre est PARTAGÉ entre long et short.
Les seuils d'activation sont ensuite DIFFÉRENTS par branche
(filter_threshold_long ≠ filter_threshold_short).
"""
from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    precision_recall_fscore_support, confusion_matrix,
)
from sklearn.preprocessing import StandardScaler

from ai.level_0.constants import FILTER_BETA_LONG, FILTER_BETA_SHORT
from ai.level_0.features import FEATURES_FILTER, validate_features
from ai.level_0.preprocessing import get_X, fit_scaler
from ai.level_0.filter_calibrate import calibrate_filter_threshold


def train_filter_model(
    df,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
) -> Tuple[object, StandardScaler, Dict]:
    """
    Entraîne le filtre tradeable global.

    Paramètres
    ----------
    df         : DataFrame avec la colonne 'tradeable_net'
    train_mask : masque train
    val_mask   : masque val (calibration du seuil sur val uniquement)
    out_dir    : dossier de sauvegarde

    Retourne
    --------
    clf, scaler, metrics
    """
    print("\n" + "=" * 70)
    print("STAGE 1 — FILTRE TRADEABLE  (class-balanced, seuil calibré sur val)")
    print("=" * 70)

    validate_features(df, FEATURES_FILTER, context="filter/train")

    X_train = get_X(df, train_mask, FEATURES_FILTER)
    y_train = df.loc[train_mask, "tradeable_net"].values.astype(np.int32)
    X_val   = get_X(df, val_mask,   FEATURES_FILTER)
    y_val   = df.loc[val_mask,   "tradeable_net"].values.astype(np.int32)

    n_neg = int((y_train == 0).sum())
    n_pos = int((y_train == 1).sum())
    spw   = n_neg / max(n_pos, 1)
    print(f"   Train  : {len(y_train):,}  (tradeable: {n_pos:,} = {n_pos/len(y_train):.1%})")
    print(f"   Val    : {len(y_val):,}  (tradeable: {y_val.sum():,} = {y_val.sum()/len(y_val):.1%})")
    print(f"   scale_pos_weight = {spw:.2f}")

    clf, model_name = _build_classifier(spw)
    scaler = fit_scaler(X_train)
    clf.fit(scaler.transform(X_train), y_train)

    proba_val = (
        clf.predict_proba(scaler.transform(X_val))[:, 1]
        if hasattr(clf, "predict_proba")
        else clf.predict(scaler.transform(X_val)).astype(float)
    )

    try:
        auc = float(roc_auc_score(y_val, proba_val))
    except Exception:
        auc = float("nan")

    thr_long  = calibrate_filter_threshold(proba_val, y_val, beta=FILTER_BETA_LONG)
    thr_short = calibrate_filter_threshold(proba_val, y_val, beta=FILTER_BETA_SHORT)

    y_pred = (proba_val >= thr_long).astype(int)
    f1     = f1_score(y_val, y_pred, average="binary", zero_division=0)
    acc    = accuracy_score(y_val, y_pred)
    _, recall, _, _ = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1], zero_division=0
    )
    cm = confusion_matrix(y_val, y_pred, labels=[0, 1])

    print(f"\n   Modèle             : {model_name}")
    print(f"   Val AUC            : {auc:.4f}  (séparabilité, indépendant du seuil)")
    print(f"   Seuil calibré LONG : {thr_long:.2f}  (F1.5 — recall-favoring)")
    print(f"   Seuil calibré SHORT: {thr_short:.2f}  (F1.0 — balanced)")
    print(f"   Val F1 (thr={thr_long:.2f}) : {f1:.4f}")
    print(f"   Recall tradeable   : {recall[1]:.3f}  (cible > 0.30)")
    print(f"   Recall not_trade   : {recall[0]:.3f}")
    print(f"   Confusion (thr={thr_long:.2f}) :\n{cm}")

    if recall[1] < 0.25:
        print("   ⚠  recall_tradeable < 0.25 — filtre trop restrictif")
    if auc < 0.60:
        print("   ⚠  AUC < 0.60 — features du filtre insuffisantes")

    metrics = {
        "model":                        model_name,
        "val_auc":                      round(auc, 4),
        "val_f1":                       round(f1, 4),
        "val_acc":                      round(acc, 4),
        "recall_tradeable":             round(float(recall[1]), 4),
        "recall_not_tradeable":         round(float(recall[0]), 4),
        "confusion_matrix":             cm.tolist(),
        "scale_pos_weight":             round(spw, 3),
        "calibrated_threshold_long":    round(thr_long, 3),
        "calibrated_threshold_short":   round(thr_short, 3),
        "n_train_positive":             n_pos,
        "n_train_negative":             n_neg,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "filter_model.pkl",  "wb") as f: pickle.dump(clf, f)
    with open(out_dir / "filter_scaler.pkl", "wb") as f: pickle.dump(scaler, f)
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    return clf, scaler, metrics


def _build_classifier(scale_pos_weight: float):
    """
    Construit le classifieur avec compensation du déséquilibre de classes.
    Essaie XGBoost en premier, repli sur HistGBT.
    """
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.04,
            subsample=0.80,
            colsample_bytree=0.70,
            scale_pos_weight=scale_pos_weight,
            use_label_encoder=False,
            eval_metric="aucpr",
            min_child_weight=10,
            reg_alpha=0.05,
            reg_lambda=1.0,
            n_jobs=-1,
            random_state=42,
        )
        return clf, "XGBoost"
    except ImportError:
        pass

    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=500,
        max_depth=5,
        min_samples_leaf=20,
        class_weight="balanced",
        random_state=42,
    )
    return clf, "HistGBT"
