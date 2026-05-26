#!/usr/bin/env python3
"""
train_1m.py — Pipeline ML natif 1 minute
=========================================

Table centrale : barres 1m Binance Vision brutes.
Le 1m N'EST PAS rééchantillonné en 1h — il reste la table principale.
Le contexte 5m/15m/1h est injecté comme features secondaires.

Horizon principal : 60 minutes (permet la comparaison directe avec train_pipeline.py).
Horizons additionnels : 15m et 30m (ablation et multi-target).

Architecture modèles
--------------------
    Stage 1 — Filtre tradeable  : XGBoost sur FEATURES_ALL_1M
    Stage 2 — Direction LONG    : XGBoost sur FEATURES_LONG_1M
    Stage 3 — Direction SHORT   : XGBoost sur FEATURES_SHORT_1M

Backtest
--------
    Walk-forward minute-aware : entrée sur close[t], sortie sur close[t+H].
    Cooldown de 60 barres entre trades (pas d'entrées en cascade).
    Max 10 trades par jour (1440 barres).
    Coût 10bps long / 15bps short.

Règles de rejet
---------------
    - Si F1 long val < 0.52  → modèle rejeté
    - Si Sharpe WF test < 1.0 → pipeline non déployable
    - Si PnL net < 0 après coûts → signal non exploitable

Usage
-----
    # Entraîner en mode long uniquement (recommandé pour la validation initiale)
    python train_1m.py \\
        --data data/datasets/binance_vision_downloads/data/spot/monthly/klines/BTCUSDT/1m \\
        --mode long

    # Comparer horizons 15m / 30m / 60m
    python train_1m.py --data ... --mode long --horizon 30

    # Run complet avec backtest walk-forward
    python train_1m.py --data ... --mode combined --wf
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import (
    f1_score, roc_auc_score, precision_recall_fscore_support,
    average_precision_score,
)
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=UserWarning)

FUTUR = Path(__file__).parent
sys.path.insert(0, str(FUTUR))
sys.path.insert(0, str(FUTUR / "ai" / "models"))

from core.features_1m import (
    compute_all_features,
    FEATURES_LONG_1M, FEATURES_SHORT_1M, FEATURES_ALL_1M,
)
from core.labels_1m import (
    compute_forward_returns, compute_mae_forward,
    build_labels_1m, chronological_split_1m,
    compute_local_regime_1m,
)
from core.labels_economic import (
    build_economic_labels,
    TP_PCT, SL_PCT, FEE_RT, SLIPPAGE_RT, HORIZON,
)


# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ═════════════════════════════════════════════════════════════════════════════

_BINANCE_1M_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "number_of_trades",
    "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
]

COST_LONG:  float = 0.0010   # 10 bps
COST_SHORT: float = 0.0015   # 15 bps


# ═════════════════════════════════════════════════════════════════════════════
# NOTE DATASET — data/bundle_btc/features_merged.parquet
# ─────────────────────────────────────────────────────────────────────────────
# Source     : Binance Vision klines BTCUSDT 1m (mensuel + quotidien)
# Couverture : 2017-08-17 → 2026-04-16  |  4 548 799 barres 1m  |  123 colonnes
# Format     : parquet zstd float32, ~640 MB sur disque
#
# Ce script est natif 1m : le bundle est chargé tel quel (OHLCV+taker).
# Les 123 colonnes du bundle incluent des features 1m pré-calculées
# (rv_5/15/60/240, vol_z_*, body_abs_pct, etc.) que compute_all_features
# ignore et RECALCULE indépendamment — c'est intentionnel pour garder
# les features du modèle cohérentes avec l'inférence en live.
#
# Mapping bundle → architecture pour train_1m :
#   Features 1m (FEATURES_LONG/SHORT_1M, ~59 cols) :
#     ret_1m/5m/15m/30m/60m, rv_5m→60m, ATR, EMA distances, RSI,
#     breakout signals, taker flow, vol z-scores, VWAP, reversal density
#   Multi-TF context (ctx5/ctx15/ctx1h) : agrégats sur barres 5m/15m/1h
#
# Colonnes bundle NON utilisées ici (réservées pour enrichissement futur) :
#   funding_rate, open_interest, long/short ratios, fear_greed_value
#   → à brancher sur le contexte multi-TF (ctx1h ou ctx4h) pour donner
#     un signal macro aux modèles 1m
#
# Split temporel recommandé :
#   train ≤ 2022  (~2.7M barres 1m)
#   val   = 2023  (~525k barres 1m)
#   test  ≥ 2024  (~1.3M barres 1m)
# ═════════════════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Config1m:
    # Horizon
    primary_horizon: int = 60        # minutes — horizon du signal principal

    # Mode de labeling
    # "direction"  : cible forward return > seuil (ancien comportement)
    # "economic"   : cible PnL net réel après TP/SL/frais (nouveau)
    label_mode: str = "economic"

    # Labeling direction (ancien, conservé pour ablation)
    tradeable_quantile: float = 0.80
    gray_zone_factor: float   = 0.15
    mae_factor: float         = 0.60
    mae_window: int           = 10
    noise_filter_q: float     = 0.97

    # Labeling économique TP/SL
    tp_pct:       float = TP_PCT        # 1.00% take profit
    sl_pct:       float = SL_PCT        # 0.50% stop loss
    fee_rt:       float = FEE_RT        # 0.08% frais aller-retour
    slippage_rt:  float = SLIPPAGE_RT   # 0.04% slippage aller-retour

    # Modèles XGBoost
    xgb_n_estimators: int     = 500
    xgb_max_depth: int        = 4
    xgb_lr: float             = 0.05
    xgb_subsample: float      = 0.75
    xgb_colsample: float      = 0.70
    xgb_reg_alpha: float      = 0.10
    xgb_reg_lambda: float     = 1.00
    xgb_min_child_weight: int = 50   # plus élevé qu'en 1h car données beaucoup plus bruitées

    # Splits
    train_end_year: int  = 2022
    val_year: int        = 2023
    test_from_year: int  = 2024
    purge_bars: int      = 240   # 4h

    # Backtest
    cooldown_bars: int    = 60    # 1h minimum entre trades
    max_trades_day: int   = 10
    initial_equity: float = 10_000.0

    # Seuil de décision : top-k percentile des scores
    # top 0.5% pour LONG (PF>1.15 observé), top 0.1% pour SHORT (signal plus faible)
    # Si 0.0 → utilise le seuil calibré F0.5 de la validation
    topk_pct_long:  float = 0.5   # top 0.5% des barres test = ~5 trades/jour
    topk_pct_short: float = 0.1   # top 0.1% des barres test = ~1 trade/jour

    # Modes
    enable_long: bool  = True
    enable_short: bool = True


# ═════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DES DONNÉES BRUTES 1m
# ═════════════════════════════════════════════════════════════════════════════

def load_raw_1m(path: Path) -> pd.DataFrame:
    """
    Charge les CSV bruts Binance Vision 1m ou le bundle parquet features_merged.
    Retourne un DataFrame avec index DatetimeIndex UTC, colonnes lowercase.
    """
    from data_pipeline.mongo_training import is_mongo_training_uri, load_mongo_training_uri

    if is_mongo_training_uri(str(path)):
        print("   Source MongoDB enrichie détectée")
        df = load_mongo_training_uri(str(path))
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.sort_values("datetime").set_index("datetime")
        rename = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "close"])
        print(f"   {len(df):,} barres Mongo ({df.index[0].date()} → {df.index[-1].date()})")
        return df

    path = Path(path)

    # ── Bundle parquet (features_merged.parquet) ──────────────────────────────
    # Contient 4.5M barres 1m BTCUSDT 2017-08→2026-04, 123 colonnes.
    # On charge OHLCV+taker + les colonnes macro disponibles (forward-fillées).
    # compute_all_features recalcule les features price-action ; les macros
    # sont passées en pass-through pour être disponibles dans FEATURES_LONG/SHORT_1M.
    if path.suffix.lower() == ".parquet":
        from ai.level_0.live_features import MACRO_BUNDLE_COLS
        import pyarrow.parquet as _pq
        print(f"   Bundle parquet détecté ({path.name})")
        _OHLCV_COLS = [
            "datetime", "open", "high", "low", "close", "volume",
            "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume",
        ]
        _avail = set(_pq.read_schema(path).names)
        _macro_cols = [c for c in MACRO_BUNDLE_COLS if c in _avail]
        df = pd.read_parquet(path, columns=_OHLCV_COLS + _macro_cols)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="ISO8601")
        df = df.set_index("datetime").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=["open", "close"])
        for col in ["open", "high", "low", "close", "volume",
                    "quote_asset_volume", "number_of_trades",
                    "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # Forward-fill les macros (mises à jour moins fréquentes que 1m)
        if _macro_cols:
            df[_macro_cols] = df[_macro_cols].ffill().fillna(0.0)
        print(f"   {len(df):,} barres 1m  ({df.index[0].date()} → {df.index[-1].date()})")
        return df

    # ── CSV bruts Binance Vision ──────────────────────────────────────────────
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        if not files:
            raise RuntimeError(f"Aucun CSV dans {path}")
        print(f"   {len(files)} fichier(s) 1m dans {path}")
    else:
        files = [path]

    frames = []
    for f in files:
        try:
            chunk = pd.read_csv(f, header=None, names=_BINANCE_1M_COLS, low_memory=False)
            ts    = int(chunk["open_time"].iloc[0])
            unit  = "us" if len(str(abs(ts))) >= 16 else "ms"
            chunk["open_time"] = pd.to_datetime(
                chunk["open_time"].astype("int64"), unit=unit, utc=True
            )
            frames.append(chunk)
        except Exception as e:
            print(f"   ⚠  {f.name} : {e}")

    if not frames:
        raise RuntimeError("Aucune donnée chargée")

    raw = pd.concat(frames, ignore_index=True)

    for col in ["open", "high", "low", "close", "volume",
                "quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw = raw.set_index("open_time").sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    raw = raw.dropna(subset=["open", "close"])

    print(f"   {len(raw):,} barres 1m  "
          f"({raw.index[0].date()} → {raw.index[-1].date()})")
    return raw


# ═════════════════════════════════════════════════════════════════════════════
# ENTRAÎNEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _build_xgb(cfg: Config1m, scale_pos_weight: float = 1.0, seed: int = 42):
    """Construit un XGBoost classifier selon la config.

    scale_pos_weight = n_neg / n_pos compense le déséquilibre de classes.
    Sans ça, le modèle prédit presque tout à 0 sur un problème rare-event.
    """
    try:
        from xgboost import XGBClassifier
        return XGBClassifier(
            n_estimators=cfg.xgb_n_estimators,
            max_depth=cfg.xgb_max_depth,
            learning_rate=cfg.xgb_lr,
            subsample=cfg.xgb_subsample,
            colsample_bytree=cfg.xgb_colsample,
            reg_alpha=cfg.xgb_reg_alpha,
            reg_lambda=cfg.xgb_reg_lambda,
            min_child_weight=cfg.xgb_min_child_weight,
            scale_pos_weight=scale_pos_weight,
            use_label_encoder=False,
            eval_metric="aucpr",
            random_state=seed,
            n_jobs=-1,
            verbosity=0,
        )
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        print("   XGBoost absent — HistGradientBoosting utilisé")
        return HistGradientBoostingClassifier(
            max_iter=cfg.xgb_n_estimators,
            max_depth=cfg.xgb_max_depth,
            learning_rate=cfg.xgb_lr,
            l2_regularization=cfg.xgb_reg_lambda,
            class_weight="balanced",
            random_state=seed,
        )


def _get_clean_xy(
    df: pd.DataFrame,
    mask: np.ndarray,
    label_col: str,
    features: List[str],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Extrait X, y en excluant les gray zones (-1).
    Retourne (X, y, valid_mask_inside_mask).
    """
    idx_all   = np.where(mask)[0]
    y_all     = df.loc[mask, label_col].values.astype(np.int32)
    valid     = y_all >= 0
    idx_clean = idx_all[valid]
    clean_mask = np.zeros(len(df), dtype=bool)
    clean_mask[idx_clean] = True

    X = df.loc[clean_mask, features].values.astype(np.float32)
    y = df.loc[clean_mask, label_col].values.astype(np.int32)
    return X, y, clean_mask


def _print_proba_distribution(y_prob: np.ndarray, y_true: np.ndarray, label: str) -> None:
    """Imprime les percentiles de la distribution des probabilités."""
    pcts = [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
    vals = np.percentile(y_prob, pcts)
    pos_mask = y_true == 1
    neg_mask = y_true == 0
    mean_pos = float(y_prob[pos_mask].mean()) if pos_mask.any() else float("nan")
    mean_neg = float(y_prob[neg_mask].mean()) if neg_mask.any() else float("nan")

    print(f"      Distribution {label} (n={len(y_prob):,}):")
    row = "  ".join(f"p{p}={v:.4f}" for p, v in zip(pcts, vals))
    print(f"        {row}")
    print(f"        mean(pos)={mean_pos:.4f}  mean(neg)={mean_neg:.4f}")


def _scan_thresholds(
    y_prob: np.ndarray,
    y_true: np.ndarray,
    label: str,
) -> Tuple[float, Dict]:
    """
    Scanne les seuils de décision sur la validation.

    Pour chaque seuil calcule : precision, recall, F1, F0.5, n_signals,
    precision×sqrt(n).  Retourne le seuil optimal et le tableau complet.

    Critère de sélection : maximise F0.5 (favorise la précision sur le recall)
    parmi les seuils ayant au moins 30 signaux.
    """
    thresholds = [0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05,
                  0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]

    rows = []
    best_thr   = 0.50   # fallback
    best_score = -1.0

    print(f"      Scan seuils {label} :")
    print(f"        {'thr':>6}  {'n_sig':>6}  {'prec':>6}  {'rec':>6}  {'F1':>6}  {'F0.5':>6}  {'P×√n':>7}")

    for thr in thresholds:
        y_pred = (y_prob >= thr).astype(int)
        n_sig  = int(y_pred.sum())
        if n_sig == 0:
            continue

        prec = float(np.sum((y_pred == 1) & (y_true == 1)) / max(n_sig, 1))
        rec  = float(np.sum((y_pred == 1) & (y_true == 1)) / max(int(y_true.sum()), 1))
        denom_f1  = prec + rec
        f1   = 2 * prec * rec / denom_f1 if denom_f1 > 0 else 0.0
        beta  = 0.5
        denom_fb = (1 + beta**2) * prec + rec
        fb   = (1 + beta**2) * prec * rec / denom_fb if denom_fb > 0 else 0.0
        psqrtn = prec * np.sqrt(n_sig)

        print(f"        {thr:>6.3f}  {n_sig:>6,}  {prec:>6.3f}  {rec:>6.3f}  {f1:>6.3f}  {fb:>6.3f}  {psqrtn:>7.2f}")
        rows.append({"thr": thr, "n_sig": n_sig, "precision": round(prec, 4),
                     "recall": round(rec, 4), "f1": round(f1, 4),
                     "f0_5": round(fb, 4), "prec_sqrt_n": round(psqrtn, 3)})

        if n_sig >= 30 and fb > best_score:
            best_score = fb
            best_thr   = thr

    print(f"      → Seuil optimal ({label}) : {best_thr:.3f}  (F0.5={best_score:.3f})")
    return best_thr, {"threshold_scan": rows, "optimal_threshold": best_thr}


def _eval_model(clf, scaler, X_val, y_val, label: str) -> Dict:
    """Évalue un modèle sur le jeu de validation (AUC + PR-AUC)."""
    X_sc  = scaler.transform(X_val)
    y_prob = clf.predict_proba(X_sc)[:, 1] if hasattr(clf, "predict_proba") else clf.predict(X_sc).astype(float)

    valid = y_val >= 0
    y_v   = y_val[valid]
    y_pr  = y_prob[valid]

    auc    = roc_auc_score(y_v, y_pr) if len(np.unique(y_v)) > 1 else 0.5
    pr_auc = average_precision_score(y_v, y_pr) if len(np.unique(y_v)) > 1 else float("nan")

    # F1 au seuil optimal (scan complet)
    _print_proba_distribution(y_pr, y_v, label)
    opt_thr, scan_info = _scan_thresholds(y_pr, y_v, label)

    y_pred_opt = (y_pr >= opt_thr).astype(int)
    prec_opt, rec_opt, f1_opt, _ = precision_recall_fscore_support(
        y_v, y_pred_opt, zero_division=0, average="binary"
    )

    metrics = {
        "label":             label,
        "auc":               round(float(auc),    4),
        "pr_auc":            round(float(pr_auc), 4),
        "optimal_threshold": round(float(opt_thr), 4),
        "f1_at_opt_thr":     round(float(f1_opt), 4),
        "precision_at_opt":  round(float(prec_opt), 4),
        "recall_at_opt":     round(float(rec_opt), 4),
        "n_signals_opt":     int(y_pred_opt.sum()),
        "n_val_pos":         int(y_v.sum()),
        "n_val_total":       int(len(y_v)),
        **scan_info,
    }
    print(f"      {label:20s}  AUC={auc:.3f}  PR-AUC={pr_auc:.3f}  "
          f"thr={opt_thr:.3f}  F1={f1_opt:.3f}  prec={prec_opt:.3f}  "
          f"n_sig={metrics['n_signals_opt']}/{metrics['n_val_total']}")
    return metrics


def train_side(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    side: str,             # "long" ou "short"
    h: int,                # horizon en minutes
    features: List[str],
    cfg: Config1m,
    out_dir: Path,
    label_col: Optional[str] = None,   # None → déduit depuis label_mode
) -> Optional[Dict]:
    """
    Entraîne le modèle d'une branche (long ou short) pour un horizon donné.
    Retourne les métriques ou None si rejeté.

    label_col : colonne cible dans df.
        - mode "direction"  → y_{side}_{h}m  (ex: y_long_60m)
        - mode "economic"   → y_{side}_cls    (ex: y_long_cls)
    """
    import pickle
    if label_col is None:
        if cfg.label_mode == "economic":
            label_col = f"y_{side}_cls"
        else:
            label_col = f"y_{side}_{h}m"

    if label_col not in df.columns:
        print(f"   ⚠  Colonne {label_col} manquante — skip")
        return None

    # Filtre les features macro absentes du dataset (ex : bundle non disponible en 1m pur)
    available = [f for f in features if f in df.columns]
    dropped   = [f for f in features if f not in df.columns]
    if dropped:
        print(f"   ⚠  {len(dropped)} features absentes ignorées : {dropped}")
    features = available

    n_pos = int((df.loc[train_mask, label_col] == 1).sum())
    if n_pos < 500:
        print(f"   ⚠  Trop peu d'exemples {side.upper()} {h}m en train ({n_pos}) — skip")
        return None

    X_train, y_train, _ = _get_clean_xy(df, train_mask, label_col, features)
    X_val,   y_val,   _ = _get_clean_xy(df, val_mask,   label_col, features)

    if len(np.unique(y_train)) < 2:
        print(f"   ⚠  Label mono-classe {side} {h}m — skip")
        return None

    # Sample weights : compense le déséquilibre n_neg >> n_pos
    n_neg = int((y_train == 0).sum())
    n_pos = int((y_train == 1).sum())
    spw   = n_neg / max(n_pos, 1)
    print(f"   Train pos={n_pos:,}  neg={n_neg:,}  scale_pos_weight={spw:.1f}")

    scaler = StandardScaler()
    X_tr_sc = scaler.fit_transform(X_train)
    X_v_sc  = scaler.transform(X_val)

    # ── Baseline : LogisticRegression class_weight=balanced ───────────────────
    print(f"   Baseline LogisticRegression balanced :")
    lr_clf = LogisticRegression(class_weight="balanced", max_iter=1000, C=1.0, random_state=42)
    lr_clf.fit(X_tr_sc, y_train)
    lr_metrics = _eval_model(lr_clf, scaler, X_val, y_val, f"LR/{side.upper()}/{h}m")

    # ── Modèle principal : XGBoost avec scale_pos_weight ──────────────────────
    print(f"   XGBoost scale_pos_weight={spw:.1f} :")
    clf = _build_xgb(cfg, scale_pos_weight=spw)
    clf.fit(X_tr_sc, y_train)
    metrics = _eval_model(clf, scaler, X_val, y_val, f"{side.upper()}/{h}m")

    # Garder le meilleur sur AUC
    if lr_metrics["auc"] > metrics["auc"]:
        print(f"   → LR bat XGBoost sur AUC ({lr_metrics['auc']:.3f} > {metrics['auc']:.3f}) — LR sélectionné")
        clf     = lr_clf
        metrics = lr_metrics
        metrics["model_selected"] = "LogisticRegression"
    else:
        metrics["model_selected"] = "XGBoost"

    # Règle de rejet : AUC < 0.65 seulement — F1 brut est inutile sur rare-event
    if metrics["auc"] < 0.65:
        print(f"      ✗ Rejeté : AUC={metrics['auc']:.3f} < 0.65 (signal insuffisant)")
        return None
    if metrics["n_signals_opt"] < 30:
        print(f"      ✗ Rejeté : seulement {metrics['n_signals_opt']} signaux sur val après calibration")
        return None

    metrics["lr_auc"]    = lr_metrics["auc"]
    metrics["lr_pr_auc"] = lr_metrics["pr_auc"]

    # Sauvegarde — inclut le seuil calibré sur validation
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"model_{side}_{h}m.pkl", "wb") as fh:
        pickle.dump({
            "clf": clf, "scaler": scaler, "features": features,
            "label_col": label_col, "h": h, "side": side,
            "calibrated_threshold": metrics["optimal_threshold"],
        }, fh)

    # Feature importance XGBoost
    if hasattr(clf, "feature_importances_"):
        imp = sorted(zip(features, clf.feature_importances_),
                     key=lambda x: x[1], reverse=True)[:15]
        metrics["top_features"] = [(f, round(float(v), 4)) for f, v in imp]

    return metrics


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST MINUTE-AWARE
# ═════════════════════════════════════════════════════════════════════════════

def backtest_1m(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    model_path: Path,
    side: str,
    h: int,
    cfg: Config1m,
) -> Dict:
    """
    Backtest minute-aware sur la période test.

    Règles d'exécution
    ------------------
    - Entrée  : close[t] (fin de la barre signal)
    - Sortie  : close[t+h] (fin de la barre à horizon h)
    - Cooldown : `cfg.cooldown_bars` barres entre deux entrées
    - Max trades/jour : `cfg.max_trades_day`
    - Coût : COST_LONG ou COST_SHORT selon la branche
    - Pas de position overlappée (on attend la sortie avant de reprendre)
    """
    import pickle

    if not model_path.exists():
        return {"error": f"{model_path} introuvable"}

    with open(model_path, "rb") as fh:
        saved = pickle.load(fh)
    clf      = saved["clf"]
    scaler   = saved["scaler"]
    features = saved["features"]
    fallback_thr = saved.get("calibrated_threshold", 0.5)

    cost = COST_LONG if side == "long" else COST_SHORT

    test_idx  = np.where(test_mask)[0]
    closes    = df["close"].values.astype(np.float64)

    X_test    = df.iloc[test_idx][features].values.astype(np.float32)
    X_sc      = scaler.transform(X_test)
    y_prob    = clf.predict_proba(X_sc)[:, 1]

    # Seuil percentile (top-k) ou seuil calibré F0.5 si topk_pct=0
    topk_pct = cfg.topk_pct_long if side == "long" else cfg.topk_pct_short
    if topk_pct > 0:
        threshold = float(np.percentile(y_prob, 100.0 - topk_pct))
        print(f"   Seuil {side.upper()} : {threshold:.4f}  "
              f"(top {topk_pct}% des {len(y_prob):,} barres test = "
              f"~{int(len(y_prob)*topk_pct/100):,} signaux bruts)")
    else:
        threshold = fallback_thr
        print(f"   Seuil {side.upper()} : {threshold:.4f} (calibré F0.5 val)")

    equity     = cfg.initial_equity
    equity_curve: List[float] = [equity]
    pnl_list:  List[float]    = []
    trades:    List[Dict]     = []

    last_trade_bar = -cfg.cooldown_bars - 1
    trades_today   = 0
    current_day    = None
    in_position    = False
    exit_bar       = -1

    for k, bar_idx in enumerate(test_idx):
        if in_position and bar_idx >= exit_bar:
            in_position = False

        if in_position:
            equity_curve.append(equity)
            continue

        # Reset compteur journalier
        bar_dt = df.index[bar_idx]
        if hasattr(bar_dt, "date"):
            day = bar_dt.date()
        else:
            day = pd.Timestamp(bar_dt).date()

        if day != current_day:
            current_day  = day
            trades_today = 0

        # Conditions d'entrée
        cooldown_ok  = (bar_idx - last_trade_bar) >= cfg.cooldown_bars
        max_ok       = trades_today < cfg.max_trades_day
        signal_ok    = y_prob[k] >= threshold
        exit_bar_ok  = (bar_idx + h) < len(closes)

        if not (cooldown_ok and max_ok and signal_ok and exit_bar_ok):
            equity_curve.append(equity)
            continue

        # Exécution
        entry_price = closes[bar_idx]
        exit_price  = closes[bar_idx + h]

        if side == "long":
            gross_ret = np.log(exit_price / entry_price)
        else:
            gross_ret = -np.log(exit_price / entry_price)

        net_ret = gross_ret - cost
        pnl     = equity * net_ret
        equity += pnl

        pnl_list.append(pnl)
        equity_curve.append(equity)
        trades.append({
            "bar": int(bar_idx),
            "dt": str(df.index[bar_idx]),
            "prob": round(float(y_prob[k]), 4),
            "gross_ret": round(float(gross_ret), 6),
            "net_ret": round(float(net_ret), 6),
            "pnl": round(float(pnl), 2),
            "equity": round(float(equity), 2),
        })

        last_trade_bar = bar_idx
        exit_bar       = bar_idx + h
        in_position    = True
        trades_today  += 1

    # ── Métriques ─────────────────────────────────────────────────────────────
    n_trades = len(trades)
    if n_trades == 0:
        return {"n_trades": 0, "pnl_net": 0.0, "sharpe": 0.0, "max_dd": 0.0}

    wins     = sum(1 for t in trades if t["net_ret"] > 0)
    pnl_arr  = np.array(pnl_list)
    eq_arr   = np.array(equity_curve)
    peak     = np.maximum.accumulate(eq_arr)
    dd       = (peak - eq_arr) / np.maximum(peak, 1e-9)
    max_dd   = float(np.max(dd))
    pf_wins  = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    pf_loss  = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf       = pf_wins / pf_loss if pf_loss > 0 else float("inf")

    periods_year = 365 * 24 * 60  # barres 1m par an
    sharpe = float(pnl_arr.mean() / (pnl_arr.std() + 1e-9)) * np.sqrt(periods_year)

    result = {
        "side": side,
        "horizon_m": h,
        "threshold_used": threshold,
        "n_trades": n_trades,
        "win_rate": round(wins / n_trades, 4),
        "pnl_net": round(float(pnl_arr.sum()), 2),
        "pnl_pct": round(float(pnl_arr.sum()) / cfg.initial_equity, 4),
        "profit_factor": round(pf, 3),
        "sharpe_annualized": round(sharpe, 3),
        "max_drawdown": round(max_dd, 4),
        "equity_final": round(float(equity), 2),
        "avg_pnl_per_trade": round(float(pnl_arr.mean()), 4),
        "trades_per_day": round(n_trades / max(1, len(set(t["dt"][:10] for t in trades))), 2),
    }

    print(f"\n   BACKTEST {side.upper()} {h}m :")
    print(f"      Seuil     : {threshold:.4f}")
    print(f"      Trades    : {n_trades}  Win={result['win_rate']:.1%}  PF={pf:.2f}")
    print(f"      PnL net   : {result['pnl_pct']:+.2%}  Sharpe={sharpe:.2f}  MaxDD={max_dd:.1%}")

    # ── Top-k ranking backtest ─────────────────────────────────────────────────
    topk_results = _topk_backtest(y_prob, test_idx, closes, df, h, side, cost, cfg)
    result["topk"] = topk_results

    return result


def _topk_backtest(
    y_prob: np.ndarray,
    test_idx: np.ndarray,
    closes: np.ndarray,
    df: pd.DataFrame,
    h: int,
    side: str,
    cost: float,
    cfg: "Config1m",
) -> List[Dict]:
    """
    Backtest top-k : prend les k% de barres avec le score le plus élevé,
    calcule le rendement net moyen et le win-rate.

    Question : est-ce que les meilleurs scores gagnent de l'argent ?
    (indépendamment du seuil de décision)
    """
    valid_k = [(k, bar_idx) for k, bar_idx in enumerate(test_idx)
               if (bar_idx + h) < len(closes)]
    if not valid_k:
        return []

    idxs   = [k         for k, _ in valid_k]
    bars   = [bar_idx   for _, bar_idx in valid_k]
    probs  = y_prob[idxs]

    # Rendements bruts sur cette population
    gross_rets = np.array([
        np.log(closes[b + h] / closes[b]) * (1 if side == "long" else -1)
        for b in bars
    ])
    net_rets = gross_rets - cost

    n_total = len(probs)
    results = []
    print(f"\n      Top-k {side.upper()} {h}m (sur {n_total:,} barres test valides) :")
    print(f"        {'pct':>5}  {'n':>6}  {'p_min':>7}  {'wr':>6}  {'avg_ret':>8}  {'PF':>6}")

    for pct in [0.1, 0.25, 0.5, 1.0, 2.0, 5.0]:
        n_take = max(1, int(n_total * pct / 100))
        rank_idx = np.argsort(probs)[-n_take:]
        sel_rets = net_rets[rank_idx]
        sel_probs = probs[rank_idx]
        wr = float((sel_rets > 0).mean())
        avg_ret = float(sel_rets.mean())
        wins_r  = sel_rets[sel_rets > 0].sum()
        loss_r  = abs(sel_rets[sel_rets < 0].sum())
        pf      = wins_r / loss_r if loss_r > 0 else float("inf")
        p_min   = float(sel_probs.min())
        print(f"        {pct:>4.1f}%  {n_take:>6,}  {p_min:>7.4f}  {wr:>6.1%}  {avg_ret:>8.5f}  {pf:>6.2f}")
        results.append({
            "top_pct": pct, "n": n_take, "prob_min": round(p_min, 5),
            "win_rate": round(wr, 4), "avg_net_ret": round(avg_ret, 6), "pf": round(pf, 3),
        })

    return results


# ═════════════════════════════════════════════════════════════════════════════
# COMPARAISON AVEC BASELINE 1H
# ═════════════════════════════════════════════════════════════════════════════

def compare_with_baseline(results_1m: Dict, baseline_path: Optional[Path]) -> None:
    """
    Affiche une table de comparaison 1m vs baseline 1h si disponible.
    """
    if baseline_path is None or not baseline_path.exists():
        print("   (Pas de baseline 1h disponible pour comparaison)")
        return

    try:
        with open(baseline_path) as fh:
            baseline = json.load(fh)
        print("\n" + "=" * 60)
        print("COMPARAISON 1m vs 1h baseline")
        print("=" * 60)
        for key in ["f1", "auc", "sharpe_annualized", "win_rate", "max_drawdown"]:
            v1m = results_1m.get(key, "—")
            v1h = baseline.get(key, "—")
            gain = ""
            if isinstance(v1m, float) and isinstance(v1h, float):
                delta = v1m - v1h
                gain  = f"  Δ={delta:+.3f}"
            print(f"   {key:25s}: 1m={v1m}  1h={v1h}{gain}")
    except Exception as e:
        print(f"   ⚠  Comparaison échouée : {e}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    ap = argparse.ArgumentParser(
        description="Pipeline ML natif 1m — horizon 15/30/60min, table centrale = 1m.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data", default="data/bundle_btc/features_merged.parquet",
                    help="Bundle parquet (défaut) ou répertoire de CSV bruts Binance Vision 1m")
    ap.add_argument("--out", default=str(FUTUR / "runs" / "pipeline_1m"),
                    help="Dossier de sortie racine")
    ap.add_argument("--horizon", type=int, default=60, choices=[15, 30, 60],
                    help="Horizon principal du signal (minutes)")
    ap.add_argument("--mode", choices=["long", "short", "combined"], default="long",
                    help="Branches à entraîner")
    ap.add_argument("--wf", action="store_true",
                    help="Lancer le backtest walk-forward sur test")
    ap.add_argument("--baseline", default=None,
                    help="Chemin vers pipeline_summary.json du baseline 1h (pour comparaison)")
    ap.add_argument("--tradeable-q", type=float, default=0.80)
    ap.add_argument("--test-from", type=int, default=2024)
    ap.add_argument("--purge", type=int, default=240,
                    help="Barres de purge aux frontières train/val/test")
    ap.add_argument("--topk-long", type=float, default=0.5,
                    help="Top-k%% pour LONG : ne trader que les X%% meilleures proba "
                         "(0=utilise seuil F0.5). Ex: 0.5 = top 0.5%%")
    ap.add_argument("--topk-short", type=float, default=0.1,
                    help="Top-k%% pour SHORT (défaut 0.1 car signal plus faible)")

    # ── Mode de labeling ──────────────────────────────────────────────────────
    ap.add_argument("--label-mode", choices=["direction", "economic"], default="economic",
                    help="'economic' = labels TP/SL nets (nouveau, recommandé). "
                         "'direction' = forward return > seuil (ancien comportement).")
    ap.add_argument("--tp-pct", type=float, default=TP_PCT,
                    help=f"Take profit en fraction (défaut {TP_PCT:.3f} = {TP_PCT:.1%})")
    ap.add_argument("--sl-pct", type=float, default=SL_PCT,
                    help=f"Stop loss en fraction (défaut {SL_PCT:.3f} = {SL_PCT:.1%})")
    ap.add_argument("--fee-rt", type=float, default=FEE_RT,
                    help=f"Frais aller-retour en fraction (défaut {FEE_RT:.4f} = {FEE_RT:.2%})")
    ap.add_argument("--slippage-rt", type=float, default=SLIPPAGE_RT,
                    help=f"Slippage aller-retour en fraction (défaut {SLIPPAGE_RT:.4f})")

    return ap.parse_args()


def main():
    t0   = time.time()
    args = parse_args()

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out    = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    cfg = Config1m(
        primary_horizon    = args.horizon,
        tradeable_quantile = args.tradeable_q,
        test_from_year     = args.test_from,
        purge_bars         = args.purge,
        enable_long        = args.mode in ("long",  "combined"),
        enable_short       = args.mode in ("short", "combined"),
        topk_pct_long      = args.topk_long,
        topk_pct_short     = args.topk_short,
        label_mode         = args.label_mode,
        tp_pct             = args.tp_pct,
        sl_pct             = args.sl_pct,
        fee_rt             = args.fee_rt,
        slippage_rt        = args.slippage_rt,
    )

    print("=" * 70)
    print("PIPELINE ML 1m — TABLE CENTRALE = 1 MINUTE")
    print("=" * 70)
    print(f"  Mode       : {args.mode.upper()}")
    print(f"  Labeling   : {args.label_mode.upper()}", end="")
    if args.label_mode == "economic":
        print(f"  TP={args.tp_pct:.2%}  SL={args.sl_pct:.2%}  "
              f"fees={args.fee_rt:.3%}  slip={args.slippage_rt:.3%}")
    else:
        print()
    print(f"  Horizon    : {args.horizon}m")
    print(f"  Data       : {args.data}")
    print(f"  Sortie     : {out}")

    # ── 1. Chargement brut ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("CHARGEMENT DES DONNÉES BRUTES 1m")
    print("=" * 70)
    raw = load_raw_1m(args.data)

    # ── 2. Feature engineering ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("FEATURE ENGINEERING 1m + CONTEXTE MULTI-TF")
    print("=" * 70)
    df = compute_all_features(raw)

    # ── 3. Split chronologique ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SPLIT CHRONOLOGIQUE AVEC PURGE")
    print("=" * 70)
    train_mask, val_mask, test_mask = chronological_split_1m(
        df,
        train_end_year = cfg.train_end_year,
        val_year       = cfg.val_year,
        test_from_year = cfg.test_from_year,
        purge_bars     = cfg.purge_bars,
    )

    # ── 4. Labels ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"CONSTRUCTION DES LABELS 1m-NATIFS  [mode={cfg.label_mode.upper()}]")
    print("=" * 70)

    if cfg.label_mode == "economic":
        # ── Labels économiques TP/SL ──────────────────────────────────────────
        # Les rendements forward restent utiles pour le régime et pour l'ablation.
        # On les calcule quand même, mais ils ne sont PAS la cible du modèle.
        print("   Calcul des rendements forward (contexte régime) …")
        df = compute_forward_returns(df)
        df, label_stats = build_economic_labels(
            df,
            train_mask,
            tp_pct      = cfg.tp_pct,
            sl_pct      = cfg.sl_pct,
            fee_rt      = cfg.fee_rt,
            slippage_rt = cfg.slippage_rt,
            horizon     = cfg.primary_horizon,
            noise_filter_q = cfg.noise_filter_q,
        )
        # Réémission des stats au format attendu par le résumé JSON
        label_stats = {"mode": "economic", "economic": label_stats}

    else:
        # ── Labels de direction classiques (ancien comportement) ──────────────
        print("   Calcul des rendements forward …")
        df = compute_forward_returns(df)
        df = compute_mae_forward(df, window=cfg.mae_window)
        df, label_stats = build_labels_1m(
            df,
            train_mask,
            tradeable_quantile = cfg.tradeable_quantile,
            gray_zone_factor   = cfg.gray_zone_factor,
            mae_factor         = cfg.mae_factor,
            mae_window         = cfg.mae_window,
            noise_filter_q     = cfg.noise_filter_q,
            primary_horizon    = cfg.primary_horizon,
        )
        label_stats = {"mode": "direction", "direction": label_stats}

    # ── 5. Régimes locaux ─────────────────────────────────────────────────────
    print("\n   Calcul des régimes locaux 1m …")
    df = compute_local_regime_1m(df)

    # ── 6. Entraînement ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("ENTRAÎNEMENT DES MODÈLES")
    print("=" * 70)

    all_metrics: Dict = {}
    h = cfg.primary_horizon

    # Détermine le nom de la colonne cible selon le mode de labeling
    label_col_long  = "y_long_cls"  if cfg.label_mode == "economic" else f"y_long_{h}m"
    label_col_short = "y_short_cls" if cfg.label_mode == "economic" else f"y_short_{h}m"

    if cfg.enable_long:
        print(f"\n  — LONG / H={h}m  [cible={label_col_long}] —")
        m = train_side(
            df, train_mask, val_mask,
            side="long", h=h,
            features=FEATURES_LONG_1M,
            cfg=cfg,
            out_dir=out / "models",
            label_col=label_col_long,
        )
        if m:
            all_metrics[f"long_{h}m"] = m
            print(f"   ✓ Modèle LONG {h}m accepté : AUC={m['auc']:.3f}  PR-AUC={m['pr_auc']:.3f}  thr={m['optimal_threshold']:.3f}")
        else:
            print(f"   ✗ Modèle LONG {h}m rejeté")

    if cfg.enable_short:
        print(f"\n  — SHORT / H={h}m  [cible={label_col_short}] —")
        m = train_side(
            df, train_mask, val_mask,
            side="short", h=h,
            features=FEATURES_SHORT_1M,
            cfg=cfg,
            out_dir=out / "models",
            label_col=label_col_short,
        )
        if m:
            all_metrics[f"short_{h}m"] = m
            print(f"   ✓ Modèle SHORT {h}m accepté : AUC={m['auc']:.3f}  PR-AUC={m['pr_auc']:.3f}  thr={m['optimal_threshold']:.3f}")

    # ── 7. Backtest walk-forward ──────────────────────────────────────────────
    if args.wf and test_mask.sum() > 0:
        print("\n" + "=" * 70)
        print("BACKTEST WALK-FORWARD TEST")
        print("=" * 70)

        for side in (["long"] if args.mode == "long" else
                     ["short"] if args.mode == "short" else
                     ["long", "short"]):
            mp = out / "models" / f"model_{side}_{h}m.pkl"
            if mp.exists():
                bt = backtest_1m(df, test_mask, mp, side, h, cfg)
                all_metrics[f"backtest_{side}_{h}m"] = bt

                # Règle de rejet walk-forward
                if bt.get("sharpe_annualized", 0) < 1.0:
                    print(f"   ✗ Sharpe WF {side.upper()} = {bt.get('sharpe_annualized', 0):.2f} < 1.0 → NON DÉPLOYABLE")
                if bt.get("pnl_net", 0) < 0:
                    print(f"   ✗ PnL net {side.upper()} négatif → signal non exploitable")

    # ── 8. Comparaison baseline ───────────────────────────────────────────────
    if all_metrics:
        compare_with_baseline(
            all_metrics.get(f"long_{h}m", {}),
            Path(args.baseline) if args.baseline else None,
        )

    # ── 9. Sauvegarde ─────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    summary = {
        "run_id":           run_id,
        "mode":             args.mode,
        "primary_horizon":  h,
        "data":             args.data,
        "n_bars_1m":        int(len(df)),
        "train_bars":       int(train_mask.sum()),
        "val_bars":         int(val_mask.sum()),
        "test_bars":        int(test_mask.sum()),
        "label_stats":      label_stats,
        "metrics":          all_metrics,
        "elapsed_sec":      round(elapsed, 1),
        "config":           asdict(cfg),
    }

    with open(out / "pipeline_1m_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)

    print(f"\n✓ Run terminé en {elapsed:.0f}s — sortie : {out}")
    print(f"  Résultats sauvegardés : {out / 'pipeline_1m_summary.json'}")


if __name__ == "__main__":
    main()
