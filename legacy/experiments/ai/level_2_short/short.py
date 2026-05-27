"""
level_2/short.py — ENTRAÎNEMENT DU MODÈLE SHORT
================================================

Le short N'EST PAS une copie renommée du long.
Différences explicites :
  - features asymétriques (retournement, surachat, pression vendeuse)
  - hyperparamètres plus conservateurs
  - validation inter-années obligatoire
  - TCN désactivé par défaut (signal trop fragile pour le justifier)

Séparation stricte : ce module ne connaît PAS le long.
"""
from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    precision_recall_fscore_support, confusion_matrix,
)

from ai.level_0.features import FEATURES_SHORT, validate_features
from ai.level_0.preprocessing import get_X, fit_scaler
from ai.level_2.short_config import ShortModelConfig


def train_short_model(
    df,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    cfg: Optional[ShortModelConfig] = None,
) -> Dict:
    """
    Entraîne le modèle SHORT complet.

    Retourne un dict avec :
      side, lr, xgb, best_model, best_model_name,
      scaler, best_tabular, best_tabular_f1, all_metrics
    """
    cfg = cfg or ShortModelConfig()
    side = "short"
    label_col = "y_short"

    print("\n" + "=" * 70)
    print(f"STAGE 2 — EDGE MODEL SHORT  (label=y_short, features={len(FEATURES_SHORT)})")
    print("=" * 70)

    validate_features(df, FEATURES_SHORT, context="short/train")

    train_idx   = np.where(train_mask)[0]
    y_train_raw = df.loc[train_mask, label_col].values.astype(np.int32)
    valid_train = y_train_raw >= 0
    train_idx_clean = train_idx[valid_train]
    train_mask_clean = np.zeros(len(df), dtype=bool)
    train_mask_clean[train_idx_clean] = True

    X_train = get_X(df, train_mask_clean, FEATURES_SHORT)
    y_train = df.loc[train_mask_clean, label_col].values.astype(np.int32)
    X_val   = get_X(df, val_mask,         FEATURES_SHORT)
    y_val   = df.loc[val_mask, label_col].values.astype(np.int32)
    val_valid = y_val >= 0
    X_val, y_val = X_val[val_valid], y_val[val_valid]

    n_tr  = len(X_train)
    pos_tr = int((y_train == 1).sum())
    spw    = float((y_train == 0).sum()) / max(pos_tr, 1)

    print(f"   Train  : {n_tr:,}  (SHORT=1: {pos_tr:,} = {pos_tr/max(n_tr,1):.1%})")
    print(f"   Val    : {len(X_val):,}  "
          f"(SHORT=1: {(y_val==1).sum():,} = {(y_val==1).sum()/max(len(y_val),1):.1%})")
    print(f"   scale_pos_weight = {spw:.2f}")

    if pos_tr < 150:
        raise RuntimeError(
            f"Trop peu d'exemples SHORT ({pos_tr}) en train. "
            f"Réduire tradeable_quantile ou vérifier les données."
        )

    scaler      = fit_scaler(X_train)
    all_metrics: List[Dict] = []

    # ── Baseline A : Logistic Regression ─────────────────────────────────────
    lr = LogisticRegression(
        C=cfg.lr_C,
        class_weight="balanced",
        max_iter=cfg.lr_max_iter,
        solver=cfg.lr_solver,
        random_state=cfg.seed,
    )
    lr.fit(scaler.transform(X_train), y_train)
    m_lr = _eval_model(lr, scaler, X_val, y_val, "Logistic", side)
    all_metrics.append(m_lr)

    # ── Baseline B : XGBoost / HistGBT ───────────────────────────────────────
    xgb, xgb_name = _build_xgb_short(cfg, spw)
    xgb.fit(scaler.transform(X_train), y_train)
    m_xgb = _eval_model(xgb, scaler, X_val, y_val, xgb_name, side)
    all_metrics.append(m_xgb)

    best_tab = max(all_metrics, key=lambda m: m["macro_f1"])
    best_tab_model = lr if best_tab["model"] == "Logistic" else xgb

    print(f"\n   Meilleur tabulaire SHORT : {best_tab['model']}  "
          f"macro_F1={best_tab['macro_f1']:.4f}  AUC={best_tab['auc']:.4f}")

    if best_tab["auc"] < cfg.min_auc:
        print(f"   ⚠  AUC={best_tab['auc']:.4f} < {cfg.min_auc} — signal short faible")
        print("      → Revoir les features short ou accepter de désactiver le short")

    # ── Feature importance (top-20 gamechanger features) ─────────────────────
    _print_short_feature_importance(best_tab_model, FEATURES_SHORT)

    if cfg.tcn_enabled and best_tab["auc"] >= cfg.min_auc:
        print("   TCN SHORT : non implémenté dans cette version")
        print("   → Activer quand AUC short > 0.65 en baseline tabulaire")

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "logistic.pkl",   "wb") as f: pickle.dump(lr, f)
    with open(out_dir / "xgb.pkl",        "wb") as f: pickle.dump(xgb, f)
    with open(out_dir / "scaler.pkl",     "wb") as f: pickle.dump(scaler, f)
    with open(out_dir / "best_model.pkl", "wb") as f: pickle.dump(best_tab_model, f)

    summary = {
        "side":         side,
        "models":       all_metrics,
        "best_tabular": best_tab["model"],
        "best_final":   best_tab["model"],
    }
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(summary, f, indent=2)

    return {
        "side":              side,
        "lr":                lr,
        "xgb":               xgb,
        "best_model":        best_tab_model,
        "best_model_name":   best_tab["model"],
        "scaler":            scaler,
        "features":          list(FEATURES_SHORT),
        "best_tabular":      best_tab["model"],
        "best_tabular_f1":   best_tab["macro_f1"],
        "all_metrics":       all_metrics,
    }


def _eval_model(clf, scaler, X_val, y_val, label, side) -> Dict:
    X_sc    = scaler.transform(X_val)
    y_pred  = clf.predict(X_sc)
    y_proba = clf.predict_proba(X_sc)[:, 1] if hasattr(clf, "predict_proba") else y_pred.astype(float)
    acc  = accuracy_score(y_val, y_pred)
    mf1  = f1_score(y_val, y_pred, average="macro", zero_division=0)
    prec, recall, _, _ = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1], zero_division=0
    )
    try:
        auc = float(roc_auc_score(y_val, y_proba))
    except Exception:
        auc = float("nan")
    cm = confusion_matrix(y_val, y_pred, labels=[0, 1]).tolist()
    print(f"   [{label:>12}]  acc={acc:.4f}  macro_F1={mf1:.4f}  AUC={auc:.4f}  "
          f"prec_SHORT={prec[1]:.3f}  recall_SHORT={recall[1]:.3f}")
    return {
        "model": label, "side": side,
        "acc": round(acc, 4), "macro_f1": round(mf1, 4), "auc": round(auc, 4),
        "precision_short": round(float(prec[1]), 4),
        "recall_short":    round(float(recall[1]), 4),
        "confusion_matrix": cm,
    }


def _build_xgb_short(cfg: ShortModelConfig, spw: float):
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=cfg.xgb_n_estimators,
            max_depth=cfg.xgb_max_depth,
            learning_rate=cfg.xgb_learning_rate,
            subsample=cfg.xgb_subsample,
            colsample_bytree=cfg.xgb_colsample_bytree,
            scale_pos_weight=spw,
            reg_alpha=cfg.xgb_reg_alpha,
            reg_lambda=cfg.xgb_reg_lambda,
            min_child_weight=cfg.xgb_min_child_weight,
            use_label_encoder=False,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=cfg.seed,
        )
        return clf, "XGBoost"
    except ImportError:
        pass
    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(
        learning_rate=cfg.xgb_learning_rate,
        max_iter=cfg.xgb_n_estimators,
        max_depth=cfg.xgb_max_depth,
        min_samples_leaf=cfg.xgb_min_child_weight * 2,
        class_weight="balanced",
        random_state=cfg.seed,
    )
    return clf, "HistGBT"


def _print_short_feature_importance(clf, feature_names: list, top_n: int = 20) -> None:
    """
    Affiche les top-N features par importance pour le modele short.
    Identifie quelles categories de features (gamechanger vs baseline) dominent.
    """
    try:
        if hasattr(clf, "feature_importances_"):
            importances = clf.feature_importances_
        elif hasattr(clf, "coef_"):
            importances = np.abs(clf.coef_[0])
        else:
            return

        idx_sorted = np.argsort(importances)[::-1][:top_n]
        print(f"\n   Feature importance SHORT (top {top_n}) :")
        for rank, idx in enumerate(idx_sorted, 1):
            fname = feature_names[idx] if idx < len(feature_names) else f"feat_{idx}"
            imp   = importances[idx]
            # Categoriser la feature
            if any(k in fname for k in ("crowding", "breakdown", "trap", "squeeze",
                                         "liq_", "taker_sell", "bear_cont", "weak_bounce",
                                         "failed_", "oi_up_price", "oi_price_div",
                                         "funding_extreme", "long_short_ext", "fear_greed_ext",
                                         "funding_accel", "spread_proxy", "open_interest_exp",
                                         "sell_volume_shock", "range_exp", "vwap_loss",
                                         "below_ema", "local_low")):
                cat = "[GC]"  # Gamechanger
            elif any(k in fname for k in ("oi_x", "funding_x", "macro_conf", "crowd_lev",
                                           "macro_reg", "oi_accel")):
                cat = "[XM]"  # Cross-macro
            else:
                cat = "[BS]"  # Baseline
            print(f"     {rank:2d}. {cat} {fname:<45} {imp:.4f}")
    except Exception as e:
        print(f"   Feature importance non disponible : {e}")
