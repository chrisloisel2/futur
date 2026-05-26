#!/usr/bin/env python3
"""
train_institutional.py — Pipeline ML sur ohlcv_institutional_features_btcusdt
===============================================================================

Source : MongoDB ohlcv_institutional_features_btcusdt (interval=1h, BTC/USDT)
Features : 905 features pré-calculées → sélection fill ≥75% (voir institutional_features.py)
Labels   : dérivés de label_future_log_return_5 (5h forward ≈ 4h horizon)
           + label_triple_barrier_5 (version path-aware avec stops ATR)

Architecture identique au pipeline existant :
  Stage 1 — Filtre        : LogisticRegression + XGBoost (direction-agnostique)
  Stage 2 — Direction     : LogisticRegression + XGBoost séparés LONG / SHORT
  Stage 3 — Walk-forward  : fenêtres annuelles expanding, test = 2024+

Usage
-----
  python train_institutional.py [OPTIONS]

Options :
  --side          long|short|both  (défaut: both)
  --horizon       int              (défaut: 5 — barres forward dans les labels)
  --walk-forward                   activer le walk-forward annuel (défaut: off)
  --wf-step       month|quarter    pas du walk-forward (défaut: quarter)
  --use-tb                         utiliser label_triple_barrier comme y_* au lieu de log-return
  --run-dir       PATH             répertoire de sortie des artefacts (défaut: runs/institutional)
  --train-tcn                      activer le TCN si AUC suffisant (défaut: off)
  --verbose                        afficher les métriques détaillées
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    precision_recall_fscore_support,
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("train_institutional")

# ── Imports internes ──────────────────────────────────────────────────────────
from ai.level_0.institutional_loader import (
    load_institutional_data,
    build_institutional_labels,
    get_split_masks,
)
from ai.level_0.institutional_features import (
    FEATURES_INST_LONG,
    FEATURES_INST_SHORT,
    FEATURES_INST_FILTER,
    FEATURES_INST_REGIME,
    get_available_features,
)
try:
    from backtest.engine import run_backtest_side
    from backtest.metrics import print_backtest_summary
    _HAS_BACKTEST = True
except Exception:
    _HAS_BACKTEST = False


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION DE FEATURES (version institutionnelle — gère les NaN par imputation)
# ─────────────────────────────────────────────────────────────────────────────

def extract_X(
    df: pd.DataFrame,
    mask: np.ndarray,
    features: List[str],
    imputer: Optional[SimpleImputer] = None,
    fit_imputer: bool = False,
) -> Tuple[np.ndarray, SimpleImputer]:
    """
    Extrait la matrice X pour un masque. Impute les NaN résiduels par médiane.

    Si fit_imputer=True, ajuste l'imputer sur le sous-ensemble masqué.
    Sinon, applique l'imputer déjà ajusté (pas de leakage).
    """
    X = df.loc[mask, features].values.astype(np.float64)

    if imputer is None:
        imputer = SimpleImputer(strategy="median")

    if fit_imputer:
        imputer.fit(X)

    X = imputer.transform(X)

    # Remplacer les inf résiduels (colonne entièrement NaN dans train → median=nan)
    X = np.where(np.isfinite(X), X, 0.0)
    return X, imputer


def fit_scaler_inst(X_train: np.ndarray) -> StandardScaler:
    sc = StandardScaler()
    sc.fit(X_train)
    return sc


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def eval_model(clf, scaler, X_val, y_val, name: str, side: str) -> Dict:
    X_sc = scaler.transform(X_val)
    y_pred  = clf.predict(X_sc)
    y_proba = clf.predict_proba(X_sc)[:, 1] if hasattr(clf, "predict_proba") else y_pred.astype(float)

    mf1  = f1_score(y_val, y_pred, average="macro", zero_division=0)
    try:
        auc = float(roc_auc_score(y_val, y_proba))
    except Exception:
        auc = float("nan")

    prec, rec, _, _ = precision_recall_fscore_support(y_val, y_pred, labels=[0, 1], zero_division=0)
    return {
        "model":     name,
        "side":      side,
        "macro_f1":  round(mf1, 4),
        "auc":       round(auc, 4),
        "prec_pos":  round(float(prec[1]), 4),
        "rec_pos":   round(float(rec[1]),  4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENTRAÎNEMENT D'UN CÔTÉ (LONG ou SHORT)
# ─────────────────────────────────────────────────────────────────────────────

def train_side(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    features: List[str],
    label_col: str,
    side: str,
    out_dir: Path,
    verbose: bool = True,
    train_tcn: bool = False,
    use_lgbm: bool = True,
) -> Dict:
    """
    Entraîne un modèle de direction (LONG ou SHORT).

    Procédure :
      1. LogisticRegression (rapide, interprétable)
      2. XGBoost (non-linéaire, robuste)
      3. [LightGBM] si disponible et XGBoost n'est pas meilleur
      4. [TCN] uniquement si train_tcn=True et AUC > 0.58

    Retourne un dict avec les artefacts et métriques.
    """
    print(f"\n{'='*65}")
    print(f"  STAGE 2 — {side.upper()} | label={label_col} | features={len(features)}")
    print(f"{'='*65}")

    # Exclure les zones grises (-1) du training
    y_all_train = df.loc[train_mask, label_col].values.astype(np.int32)
    valid_train = y_all_train >= 0
    train_idx   = np.where(train_mask)[0][valid_train]
    mask_clean  = np.zeros(len(df), dtype=bool)
    mask_clean[train_idx] = True

    # Extraire X avec imputation
    X_tr, imputer = extract_X(df, mask_clean, features, fit_imputer=True)
    y_tr = df.loc[mask_clean, label_col].values.astype(np.int32)

    # Val : exclure les zones grises
    y_all_val = df.loc[val_mask, label_col].values.astype(np.int32)
    valid_val  = y_all_val >= 0
    val_idx    = np.where(val_mask)[0][valid_val]
    val_mask_clean = np.zeros(len(df), dtype=bool)
    val_mask_clean[val_idx] = True
    X_val, _  = extract_X(df, val_mask_clean, features, imputer=imputer)
    y_val     = df.loc[val_mask_clean, label_col].values.astype(np.int32)

    pos_tr = int((y_tr == 1).sum())
    pos_v  = int((y_val == 1).sum())
    spw    = float((y_tr == 0).sum()) / max(pos_tr, 1)

    print(f"   Train (sans zones grises) : {len(X_tr):,}  ({side}=1 : {pos_tr:,} = {pos_tr/max(len(X_tr),1):.1%})")
    print(f"   Val                       : {len(X_val):,}  ({side}=1 : {pos_v:,}  = {pos_v/max(len(X_val),1):.1%})")
    print(f"   scale_pos_weight          : {spw:.2f}")

    if pos_tr < 100:
        raise RuntimeError(
            f"Trop peu d'exemples {side.upper()} positifs ({pos_tr}) en train. "
            "Réduire tradeable_quantile ou vérifier les données."
        )

    scaler = fit_scaler_inst(X_tr)
    all_metrics: List[Dict] = []

    # ── 1. Logistic Regression ────────────────────────────────────────────────
    lr = LogisticRegression(
        C=0.1,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    lr.fit(scaler.transform(X_tr), y_tr)
    m_lr = eval_model(lr, scaler, X_val, y_val, "LogisticRegression", side)
    all_metrics.append(m_lr)
    if verbose:
        print(f"   LR   : AUC={m_lr['auc']:.4f}  F1={m_lr['macro_f1']:.4f}  "
              f"Prec={m_lr['prec_pos']:.3f}  Rec={m_lr['rec_pos']:.3f}")

    # ── 2. XGBoost / HistGBT ──────────────────────────────────────────────────
    try:
        from xgboost import XGBClassifier
        xgb = XGBClassifier(
            n_estimators=600,
            max_depth=4,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.7,
            scale_pos_weight=spw,
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=42,
            n_jobs=-1,
            verbosity=0,
        )
        xgb.fit(
            scaler.transform(X_tr), y_tr,
            eval_set=[(scaler.transform(X_val), y_val)],
            verbose=False,
        )
        m_xgb = eval_model(xgb, scaler, X_val, y_val, "XGBoost", side)
        all_metrics.append(m_xgb)
        if verbose:
            print(f"   XGB  : AUC={m_xgb['auc']:.4f}  F1={m_xgb['macro_f1']:.4f}  "
                  f"Prec={m_xgb['prec_pos']:.3f}  Rec={m_xgb['rec_pos']:.3f}")
    except ImportError:
        LOG.warning("XGBoost non disponible — utilisation du HistGBT sklearn.")
        from sklearn.ensemble import HistGradientBoostingClassifier
        xgb = HistGradientBoostingClassifier(
            max_iter=400, max_depth=4, learning_rate=0.05,
            class_weight="balanced", random_state=42,
        )
        xgb.fit(X_tr, y_tr)   # HistGBT gère les NaN nativement
        m_xgb = eval_model(xgb, scaler, X_val, y_val, "HistGBT", side)
        all_metrics.append(m_xgb)

    # ── 3. LightGBM (optionnel — plus rapide que XGB sur ce volume) ───────────
    if use_lgbm:
        try:
            import lightgbm as lgb
            lgbm = lgb.LGBMClassifier(
                n_estimators=600,
                num_leaves=48,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.7,
                scale_pos_weight=spw,
                random_state=42,
                n_jobs=-1,
                verbosity=-1,
            )
            lgbm.fit(
                scaler.transform(X_tr), y_tr,
                eval_set=[(scaler.transform(X_val), y_val)],
                callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
            )
            m_lgbm = eval_model(lgbm, scaler, X_val, y_val, "LightGBM", side)
            all_metrics.append(m_lgbm)
            if verbose:
                print(f"   LGBM : AUC={m_lgbm['auc']:.4f}  F1={m_lgbm['macro_f1']:.4f}  "
                      f"Prec={m_lgbm['prec_pos']:.3f}  Rec={m_lgbm['rec_pos']:.3f}")
        except Exception as e:
            LOG.debug("LightGBM ignoré : %s", e)

    # ── Sélection du meilleur modèle ──────────────────────────────────────────
    best_m = max(all_metrics, key=lambda m: m["macro_f1"])
    model_map = {"LogisticRegression": lr, "XGBoost": xgb}
    try:
        model_map["LightGBM"] = lgbm
    except NameError:
        pass
    best_model = model_map.get(best_m["model"], lr)

    print(f"\n   ► Meilleur {side.upper()} : {best_m['model']}  "
          f"AUC={best_m['auc']:.4f}  F1={best_m['macro_f1']:.4f}")

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(lr,         out_dir / "logistic.pkl")
    _dump(xgb,        out_dir / "xgb.pkl")
    _dump(best_model, out_dir / "best_model.pkl")
    _dump(scaler,     out_dir / "scaler.pkl")
    _dump(imputer,    out_dir / "imputer.pkl")

    summary = {
        "side":        side,
        "label_col":   label_col,
        "n_features":  len(features),
        "features":    features,
        "n_train":     int(len(X_tr)),
        "n_val":       int(len(X_val)),
        "pos_train":   int(pos_tr),
        "models":      all_metrics,
        "best":        best_m,
    }
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(summary, f, indent=2)

    return {
        "side":        side,
        "best_model":  best_model,
        "best_name":   best_m["model"],
        "scaler":      scaler,
        "imputer":     imputer,
        "features":    features,
        "metrics":     all_metrics,
        "best_metric": best_m,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENTRAÎNEMENT FILTRE (Stage 1 — tradeable gate)
# ─────────────────────────────────────────────────────────────────────────────

def train_filter(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    features: List[str],
    out_dir: Path,
    verbose: bool = True,
) -> Dict:
    """
    Filtre binaire : détecte si une barre est propice à l'entrée.
    Label = tradeable_net (|ret| > coûts).
    """
    print(f"\n{'='*65}")
    print(f"  STAGE 1 — FILTRE TRADEABLE | features={len(features)}")
    print(f"{'='*65}")

    X_tr, imputer = extract_X(df, train_mask, features, fit_imputer=True)
    y_tr  = df.loc[train_mask, "tradeable_net"].values.astype(np.int32)
    X_val, _       = extract_X(df, val_mask,   features, imputer=imputer)
    y_val = df.loc[val_mask,   "tradeable_net"].values.astype(np.int32)

    scaler = fit_scaler_inst(X_tr)
    pos = int((y_tr == 1).sum())
    print(f"   Train : {len(X_tr):,}  (tradeable=1 : {pos:,} = {pos/max(len(X_tr),1):.1%})")

    lr = LogisticRegression(C=0.5, class_weight="balanced", max_iter=500, random_state=42)
    lr.fit(scaler.transform(X_tr), y_tr)
    m_lr = eval_model(lr, scaler, X_val, y_val, "LogisticRegression", "filter")

    try:
        from xgboost import XGBClassifier
        spw = float((y_tr == 0).sum()) / max(pos, 1)
        xgb = XGBClassifier(
            n_estimators=300, max_depth=3, learning_rate=0.05,
            subsample=0.8, scale_pos_weight=spw,
            eval_metric="logloss", use_label_encoder=False,
            random_state=42, n_jobs=-1, verbosity=0,
        )
        xgb.fit(scaler.transform(X_tr), y_tr, verbose=False)
        m_xgb = eval_model(xgb, scaler, X_val, y_val, "XGBoost", "filter")
        all_m = [m_lr, m_xgb]
    except ImportError:
        all_m = [m_lr]
        xgb = lr

    best_m = max(all_m, key=lambda m: m["macro_f1"])
    best_model = lr if best_m["model"] == "LogisticRegression" else xgb
    if verbose:
        print(f"   ► Filtre : {best_m['model']}  AUC={best_m['auc']:.4f}  F1={best_m['macro_f1']:.4f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(best_model, out_dir / "best_model.pkl")
    _dump(scaler,     out_dir / "scaler.pkl")
    _dump(imputer,    out_dir / "imputer.pkl")

    return {
        "best_model": best_model,
        "scaler":     scaler,
        "imputer":    imputer,
        "features":   features,
        "metric":     best_m,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD
# ─────────────────────────────────────────────────────────────────────────────

def run_walk_forward(
    df: pd.DataFrame,
    base_train_mask: np.ndarray,
    base_val_mask: np.ndarray,
    test_mask: np.ndarray,
    features_long: List[str],
    features_short: List[str],
    features_filter: List[str],
    out_dir: Path,
    step: str = "quarter",
    side: str = "both",
    use_tb: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Walk-forward expanding-window sur la période de test (2024+).

    À chaque pas :
      - Train   : tout ce qui est avant le début de la fenêtre de test courante
      - Val     : la période précédant directement le test (≥6 mois)
      - Test    : le pas courant (trimestre ou mois)
      - Modèles : ré-entraînés sur train+val pour prédire test

    Collecte les signaux et retours pour le backtest agrégé.
    """
    test_dates = df[test_mask].index
    if len(test_dates) == 0:
        raise ValueError("test_mask vide — aucune donnée de test.")

    # Construire les fenêtres de walk-forward
    freq  = "QS" if step == "quarter" else "MS"
    steps = pd.date_range(test_dates.min(), test_dates.max(), freq=freq, tz="UTC")

    label_long  = "y_long_tb"  if use_tb and "y_long_tb"  in df.columns else "y_long"
    label_short = "y_short_tb" if use_tb and "y_short_tb" in df.columns else "y_short"

    all_signals: List[pd.DataFrame] = []

    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD ({step.upper()} steps, {len(steps)} périodes, test={test_dates.min().date()} → {test_dates.max().date()})")
    print(f"{'='*65}")

    for i, step_start in enumerate(steps):
        step_end = (step_start + pd.DateOffset(months=3 if step == "quarter" else 1))
        step_mask = (df.index >= step_start) & (df.index < step_end)
        step_mask = step_mask.values

        if step_mask.sum() == 0:
            continue

        # Expanding train = tout avant cette fenêtre
        wf_train_mask = (df.index < step_start).values & (df.index < step_start).values
        # Validation = 6 mois avant la fenêtre de test
        val_start = step_start - pd.DateOffset(months=6)
        wf_val_mask = ((df.index >= val_start) & (df.index < step_start)).values

        if wf_train_mask.sum() < 1000 or wf_val_mask.sum() < 100:
            continue

        print(f"\n  [{i+1}/{len(steps)}] Fenêtre : {step_start.date()} → {step_end.date()}"
              f"  train={wf_train_mask.sum():,}  val={wf_val_mask.sum():,}  test={step_mask.sum():,}")

        step_out = out_dir / f"wf_{step_start.strftime('%Y%m')}"

        # Ré-entraîner les modèles
        try:
            filt = train_filter(df, wf_train_mask, wf_val_mask, features_filter,
                                step_out / "filter", verbose=False)
        except Exception as e:
            LOG.warning("Filtre WF %s : %s", step_start.date(), e)
            filt = None

        side_results = {}
        for s, feats, lbl in [
            ("long",  features_long,  label_long),
            ("short", features_short, label_short),
        ]:
            if side != "both" and side != s:
                continue
            try:
                res = train_side(df, wf_train_mask, wf_val_mask, feats, lbl, s,
                                 step_out / s, verbose=False)
                side_results[s] = res
            except Exception as e:
                LOG.warning("  Side %s WF %s : %s", s, step_start.date(), e)

        # Générer les signaux sur la fenêtre de test
        step_df = df[step_mask].copy()
        signals = _generate_signals(step_df, side_results, filt, verbose=verbose)
        if signals is not None and len(signals):
            all_signals.append(signals)

    if not all_signals:
        LOG.warning("Aucun signal généré par le walk-forward.")
        return pd.DataFrame()

    return pd.concat(all_signals).sort_index()


def _generate_signals(
    df: pd.DataFrame,
    side_results: Dict,
    filt_result: Optional[Dict],
    filter_threshold: float = 0.45,
    long_threshold: float = 0.52,
    short_threshold: float = 0.55,
    verbose: bool = False,
) -> Optional[pd.DataFrame]:
    """Applique les modèles bar-à-bar et retourne un DataFrame de signaux."""
    if not side_results:
        return None

    rows = []
    for ts, row in df.iterrows():
        # Filtre
        if filt_result is not None:
            x_f = _row_to_x(row, filt_result["features"], filt_result["imputer"])
            p_f = filt_result["best_model"].predict_proba(
                filt_result["scaler"].transform(x_f)
            )[0, 1]
            if p_f < filter_threshold:
                continue

        rec = {"timestamp": ts, "future_ret_4h": row.get("future_ret_4h", np.nan)}

        for side, res in side_results.items():
            x_d = _row_to_x(row, res["features"], res["imputer"])
            p_d = res["best_model"].predict_proba(
                res["scaler"].transform(x_d)
            )[0, 1]
            rec[f"p_{side}"] = round(p_d, 4)
            thr = long_threshold if side == "long" else short_threshold
            rec[f"signal_{side}"] = int(p_d >= thr)

        rows.append(rec)

    if not rows:
        return None

    out = pd.DataFrame(rows).set_index("timestamp")
    if verbose:
        for side in side_results:
            n = int(out.get(f"signal_{side}", pd.Series(0)).sum())
            LOG.info("  %s → %d signaux sur %d bars", side, n, len(out))
    return out


def _row_to_x(row: pd.Series, features: List[str], imputer: SimpleImputer) -> np.ndarray:
    """Convertit une ligne du DataFrame en vecteur 2D pour predict_proba."""
    vals = np.array([[row.get(f, np.nan) for f in features]], dtype=np.float64)
    vals = imputer.transform(vals)
    vals = np.where(np.isfinite(vals), vals, 0.0)
    return vals


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST SIMPLE (test set complet)
# ─────────────────────────────────────────────────────────────────────────────

def run_simple_backtest(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    side_results: Dict,
    filt_result: Optional[Dict],
    side: str,
    threshold: float,
    filter_threshold: float = 0.45,
    cost_pct: float = 0.0010,
    verbose: bool = True,
) -> Dict:
    """
    Backtest bar-à-bar avec imputation correcte des NaN.
    Précompute les probabilités sur tout le test set via l'imputer puis
    calcule les métriques de performance manuellement.
    """
    if side not in side_results:
        return {}

    res  = side_results[side]
    ret_sign = 1.0 if side == "long" else -1.0

    df_test = df[test_mask].copy()
    n = len(df_test)

    # ── Probabilités filtre ────────────────────────────────────────────────────
    p_filter = np.ones(n)
    if filt_result is not None:
        X_f = df_test[filt_result["features"]].values.astype(np.float64)
        X_f = filt_result["imputer"].transform(X_f)
        X_f = np.where(np.isfinite(X_f), X_f, 0.0)
        p_filter = filt_result["best_model"].predict_proba(
            filt_result["scaler"].transform(X_f)
        )[:, 1]

    # ── Probabilités direction ─────────────────────────────────────────────────
    X_d = df_test[res["features"]].values.astype(np.float64)
    X_d = res["imputer"].transform(X_d)
    X_d = np.where(np.isfinite(X_d), X_d, 0.0)
    p_dir = res["best_model"].predict_proba(
        res["scaler"].transform(X_d)
    )[:, 1]

    # ── Backtest bar-à-bar ─────────────────────────────────────────────────────
    rets = df_test["future_ret_4h"].values
    trade_rets, trade_list = [], []

    for i in range(n):
        if p_filter[i] < filter_threshold:
            continue
        if p_dir[i] < threshold:
            continue
        raw_ret = float(rets[i]) if np.isfinite(rets[i]) else 0.0
        net_ret = raw_ret * ret_sign - cost_pct
        trade_rets.append(net_ret)
        trade_list.append({
            "ts":    str(df_test.index[i]),
            "side":  side,
            "p":     round(float(p_dir[i]), 4),
            "ret":   round(raw_ret, 6),
            "net":   round(net_ret, 6),
        })

    trade_rets_arr = np.array(trade_rets, dtype=np.float64)
    result = _compute_backtest_metrics(trade_rets_arr, cost_pct, side, trade_list)

    if verbose:
        _print_backtest(result, side)

    return result


def _compute_backtest_metrics(trade_rets: np.ndarray, cost_pct: float, side: str, trades: list) -> Dict:
    n = len(trade_rets)
    if n == 0:
        return {"side": side, "n_trades": 0, "sharpe": 0.0, "win_rate": 0.0,
                "total_ret": 0.0, "profit_factor": 0.0, "avg_ret": 0.0}
    wins     = trade_rets[trade_rets > 0]
    losses   = trade_rets[trade_rets < 0]
    sharpe   = float(trade_rets.mean() / trade_rets.std() * np.sqrt(252 * 24)) if trade_rets.std() > 0 else 0
    pf       = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    return {
        "side":          side,
        "n_trades":      n,
        "sharpe":        round(sharpe, 3),
        "win_rate":      round(float((trade_rets > 0).mean()), 4),
        "total_ret":     round(float(trade_rets.sum()), 4),
        "profit_factor": round(pf, 3),
        "avg_ret":       round(float(trade_rets.mean()), 5),
        "avg_ret_pct":   round(float(trade_rets.mean() * 100), 3),
        "trades":        trades[:100],   # échantillon
    }


def _print_backtest(result: Dict, side: str) -> None:
    print(f"   {side.upper():5s} — {result['n_trades']:4d} trades  "
          f"WR={result['win_rate']:.1%}  Sharpe={result['sharpe']:.2f}  "
          f"PF={result['profit_factor']:.2f}  avg_net={result['avg_ret_pct']:.3f}%  "
          f"total_ret={result['total_ret']*100:.1f}%")


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE COMPLET
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(args: argparse.Namespace) -> None:
    t0 = time.monotonic()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Chargement des données ─────────────────────────────────────────────
    print("\n" + "="*65)
    print("  CHARGEMENT — ohlcv_institutional_features_btcusdt (interval=1h)")
    print("="*65)

    # Projection : uniquement les colonnes nécessaires + labels + OHLCV
    # Évite de lire les 907 colonnes (10× plus rapide sur 76K lignes)
    all_feature_cols = list(dict.fromkeys(
        FEATURES_INST_LONG + FEATURES_INST_SHORT + FEATURES_INST_FILTER
    ))
    df = load_institutional_data(interval="1h", columns=all_feature_cols)

    # ── 2. Split chronologique ────────────────────────────────────────────────
    train_mask, val_mask, test_mask = get_split_masks(
        df,
        train_end_year=args.train_end_year,
        val_year=args.val_year,
        test_from_year=args.test_from_year,
    )
    print(f"   Train : {train_mask.sum():,}  Val : {val_mask.sum():,}  Test : {test_mask.sum():,}")

    # ── 3. Construction des labels ────────────────────────────────────────────
    print("\n  LABELS")
    df = build_institutional_labels(
        df,
        train_mask=train_mask,
        horizon=args.horizon,
        cost_pct=args.cost_pct,
    )

    # ── 4. Sélection des features (filtre les absentes ou fill < 75%) ─────────
    print("\n  FEATURES")
    feats_long   = get_available_features(df, FEATURES_INST_LONG,   min_fill=0.75)
    feats_short  = get_available_features(df, FEATURES_INST_SHORT,  min_fill=0.75)
    feats_filter = get_available_features(df, FEATURES_INST_FILTER, min_fill=0.75)

    print(f"   Long   : {len(feats_long)}  Short : {len(feats_short)}  Filtre : {len(feats_filter)}")

    label_long  = "y_long_tb"  if args.use_tb and "y_long_tb"  in df.columns else "y_long"
    label_short = "y_short_tb" if args.use_tb and "y_short_tb" in df.columns else "y_short"

    LOG.info("Labels : long=%s  short=%s", label_long, label_short)

    # ── 5. Stage 1 : Filtre ───────────────────────────────────────────────────
    filt_result = train_filter(
        df, train_mask, val_mask, feats_filter,
        out_dir=run_dir / "filter", verbose=args.verbose,
    )

    # ── 6. Stage 2 : Direction ────────────────────────────────────────────────
    side_results: Dict = {}

    if args.side in ("long", "both"):
        side_results["long"] = train_side(
            df, train_mask, val_mask, feats_long, label_long, "long",
            out_dir=run_dir / "edge_long",
            verbose=args.verbose,
            train_tcn=args.train_tcn,
        )

    if args.side in ("short", "both"):
        side_results["short"] = train_side(
            df, train_mask, val_mask, feats_short, label_short, "short",
            out_dir=run_dir / "edge_short",
            verbose=args.verbose,
            train_tcn=False,
        )

    # ── 7. Backtest sur test set complet ──────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  BACKTEST — test ≥{args.test_from_year}")
    print(f"{'='*65}")

    backtest_results = {}
    for side, thr in [("long", args.thr_long), ("short", args.thr_short)]:
        if side not in side_results:
            continue
        print(f"\n  ► {side.upper()} backtest (thr={thr:.2f})")
        try:
            r = run_simple_backtest(
                df=df,
                test_mask=test_mask,
                side_results=side_results,
                filt_result=filt_result,
                side=side,
                threshold=thr,
                filter_threshold=args.filter_thr,
                cost_pct=args.cost_pct,
                verbose=args.verbose,
            )
            backtest_results[side] = r
        except Exception as e:
            LOG.warning("Backtest %s : %s", side, e)

    # ── 8. Walk-forward ───────────────────────────────────────────────────────
    wf_signals = None
    if args.walk_forward:
        print(f"\n{'='*65}")
        print(f"  WALK-FORWARD ({args.wf_step.upper()} steps)")
        print(f"{'='*65}")
        try:
            wf_signals = run_walk_forward(
                df=df,
                base_train_mask=train_mask,
                base_val_mask=val_mask,
                test_mask=test_mask,
                features_long=feats_long,
                features_short=feats_short,
                features_filter=feats_filter,
                out_dir=run_dir / "walk_forward",
                step=args.wf_step,
                side=args.side,
                use_tb=args.use_tb,
                verbose=args.verbose,
            )
            if wf_signals is not None and len(wf_signals):
                wf_out = run_dir / "walk_forward" / "signals.csv"
                wf_signals.to_csv(wf_out)
                print(f"\n  Walk-forward signals : {len(wf_signals):,} bars → {wf_out}")

                # Métriques agrégées WF
                _print_wf_summary(wf_signals)
        except Exception as e:
            LOG.exception("Walk-forward échoué : %s", e)

    # ── 9. Sauvegarde des artefacts globaux ───────────────────────────────────
    elapsed = time.monotonic() - t0
    summary = {
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "interval":     "1h",
        "horizon":      args.horizon,
        "label_long":   label_long,
        "label_short":  label_short,
        "n_features":   {"long": len(feats_long), "short": len(feats_short), "filter": len(feats_filter)},
        "splits":       {
            "train": int(train_mask.sum()),
            "val":   int(val_mask.sum()),
            "test":  int(test_mask.sum()),
        },
        "best_models":  {s: r["best_name"] for s, r in side_results.items()},
        "val_metrics":  {s: r["best_metric"] for s, r in side_results.items()},
        "elapsed_s":    round(elapsed, 1),
    }
    with open(run_dir / "pipeline_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n{'='*65}")
    print(f"  DONE  — {elapsed:.0f}s  → artefacts dans {run_dir}/")
    print(f"{'='*65}")
    for side, res in side_results.items():
        m = res["best_metric"]
        print(f"  {side.upper():5s} {res['best_name']:<18} AUC={m['auc']:.4f}  F1={m['macro_f1']:.4f}")


def _print_wf_summary(signals: pd.DataFrame) -> None:
    """Résumé des signaux et returns agrégés du walk-forward."""
    print("\n  ── Walk-Forward Agrégé ──────────────────────────────────")
    for side in ["long", "short"]:
        col_sig = f"signal_{side}"
        if col_sig not in signals.columns:
            continue
        active = signals[signals[col_sig] == 1]
        if len(active) == 0:
            print(f"  {side.upper()} : 0 signaux")
            continue
        rets = active["future_ret_4h"].dropna()
        cost = 0.0010
        net_rets = rets * (1 if side == "long" else -1) - cost
        win_rate = float((net_rets > 0).mean())
        avg_ret  = float(net_rets.mean())
        sharpe   = float(net_rets.mean() / net_rets.std() * np.sqrt(252 * 24)) if net_rets.std() > 0 else 0
        print(f"  {side.upper():5s} : {len(active):4d} trades  WR={win_rate:.1%}  "
              f"avg_net={avg_ret*100:.3f}%  Sharpe≈{sharpe:.2f}")


def _dump(obj, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(obj, f)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pipeline ML institutionnel — BTC/USDT 1h",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--side",          choices=("long", "short", "both"), default="both")
    p.add_argument("--horizon",       type=int,   default=5,      help="Barres forward (5 = 5h)")
    p.add_argument("--walk-forward",  action="store_true",         help="Activer le walk-forward")
    p.add_argument("--wf-step",       choices=("month", "quarter"), default="quarter")
    p.add_argument("--use-tb",        action="store_true",         help="Utiliser le label triple-barrier")
    p.add_argument("--run-dir",       default="runs/institutional", help="Répertoire de sortie")
    p.add_argument("--train-tcn",     action="store_true",         help="Activer TCN si AUC > 0.58")
    p.add_argument("--cost-pct",      type=float, default=0.0010)
    p.add_argument("--thr-long",      type=float, default=0.52,   help="Seuil probabilité long")
    p.add_argument("--thr-short",     type=float, default=0.55,   help="Seuil probabilité short")
    p.add_argument("--filter-thr",    type=float, default=0.45,   help="Seuil filtre")
    p.add_argument("--train-end-year",type=int,   default=2022)
    p.add_argument("--val-year",      type=int,   default=2023)
    p.add_argument("--test-from-year",type=int,   default=2024)
    p.add_argument("--verbose",       action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args)
