#!/usr/bin/env python3
"""
train_local.py — entraînement local robuste sur CSV enrichi
=========================================================

Objectifs de cette version :
- arrêter de relabeliser Level 0 avec une logique externe incohérente
- utiliser directement les labels du CSV enrichi pour Level 0 et Level 1
- remplacer le Level 0 linéaire fragile par un vrai modèle tabulaire non linéaire
- construire de vraies features de fenêtre pour Level 0
- ajouter des logs de diagnostic complets et sauvegarder les artefacts même en cas d'échec du gate
- conserver un Level 1 séquentiel compatible avec le pipeline existant

Hypothèses d'entrée :
- le CSV a été produit par build_binance_features.py
- il contient au minimum :
  - les colonnes de FEATURE_KEYS
  - label_regime_3
  - label_tradeable
  - future_ret_h / future_rv_h / future_dd_h

Usage :
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv --skip-event
    python ~/futur/train_local.py --data ~/futur/data/ --out ~/futur/runs/local
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv --years 2022,2023,2024
"""
from __future__ import annotations

import json
import sys
import time
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import warnings
import numpy as np
import pandas as pd

from sklearn.exceptions import ConvergenceWarning
warnings.filterwarnings("ignore", category=ConvergenceWarning)

FUTUR = Path(__file__).parent
sys.path.insert(0, str(FUTUR / "ai" / "models"))

from training.common.scaler import RobustScaler, ReservoirSampler
from level_1.Event_Classifier import EventClassifier, EventClassifierConfig

import tensorflow as tf

# ── GPU setup ────────────────────────────────────────────────────────────────
gpus = tf.config.list_physical_devices("GPU")
if gpus:
    try:
        tf.config.set_logical_device_configuration(
            gpus[0], [tf.config.LogicalDeviceConfiguration(memory_limit=6144)]
        )
        from tensorflow.keras import mixed_precision
        mixed_precision.set_global_policy("mixed_float16")
        print(f"✅ GPU : {gpus[0].name}  |  FP16 mixed precision")
    except RuntimeError as e:
        print(f"⚠  GPU config : {e}")
else:
    print("⚠  Pas de GPU détecté — CPU utilisé")


# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CFG:
    lookback: int = 128
    horizon: int = 12
    stride: int = 1

    train_frac: float = 0.80
    val_frac: float = 0.10

    batch_size: int = 128
    epochs: int = 40

    lr: float = 3e-4
    min_lr: float = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    scaler_sample_max: int = 250_000

    reduce_lr_patience: int = 3
    reduce_lr_factor: float = 0.5
    early_stop_patience: int = 6
    min_delta: float = 1e-4

    n_regimes: int = 3  # 0=bear 1=neutral 2=bull
    seed: int = 1337

    # Level 0 gate
    min_bull_recall: float = 0.25
    min_macro_f1: float = 0.42

    # Level 0 model params
    l0_learning_rate: float = 0.05
    l0_max_iter: int = 500
    l0_max_depth: int = 6
    l0_min_samples_leaf: int = 40
    l0_l2: float = 1e-3


FEATURE_KEYS = [
    "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
    "ret", "log_ret", "hl_log_range", "co_log_ret",
    "rv_12", "rv_24", "rv_72", "rv_168",
    "rv_ratio_12_48", "rv_ratio_24_72",
    "ema_20", "dist_ema_20", "ema_50", "dist_ema_50",
    "ema_200", "dist_ema_200",
    "ema_spread_20_50", "ema_spread_50_200",
    "rsi_14", "atr_14", "atr_pct_14", "cci_20",
    "boll_pos_20", "boll_width_20",
    "taker_buy_ratio_base", "delta_taker_pressure",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]
RET_KEY = "log_ret"
RV_KEY = "rv_24"
CLOSE_KEY = "Close"
CLASS_NAMES = ["bear", "neutral", "bull"]
CLASS_ID_TO_NAME = {0: "bear", 1: "neutral", 2: "bull"}


# ═════════════════════════════════════════════════════════════════════════════
# UTILS
# ═════════════════════════════════════════════════════════════════════════════
def json_dump(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def count_windows(df: pd.DataFrame, cfg: CFG) -> int:
    return max(0, len(df) - cfg.lookback - cfg.horizon)


def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    if fut_ret.size == 0:
        return 0.0, 0.0
    path = np.cumsum(fut_ret.astype(np.float64))
    r_total = float(path[-1])
    peak = np.maximum.accumulate(path)
    dd = peak - path
    max_dd = float(np.max(dd)) if dd.size else 0.0
    return r_total, max_dd


def rms_vol(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    z = x.astype(np.float64)
    return float(np.sqrt(np.mean(z * z)))


def safe_float(x: float) -> float:
    if np.isnan(x) or np.isinf(x):
        return 0.0
    return float(x)


def summarize_counts(values: np.ndarray) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, name in CLASS_ID_TO_NAME.items():
        out[name] = int((values == i).sum())
    return out


def linear_slope(y: np.ndarray) -> float:
    if y.size < 2:
        return 0.0
    x = np.arange(y.size, dtype=np.float64)
    x = x - x.mean()
    yy = y.astype(np.float64) - float(np.mean(y))
    denom = float(np.sum(x * x))
    if denom <= 0:
        return 0.0
    return float(np.sum(x * yy) / denom)


def rolling_zscore_last(y: np.ndarray) -> float:
    if y.size < 2:
        return 0.0
    m = float(np.mean(y))
    s = float(np.std(y))
    if s <= 1e-12:
        return 0.0
    return float((float(y[-1]) - m) / s)


# ═════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════════════
def load_data(path_arg: str, years: Optional[List[int]] = None) -> pd.DataFrame:
    p = Path(path_arg)
    if p.is_dir():
        files = sorted(p.glob("*features*.csv"))
        if not files:
            files = sorted(p.glob("*.csv"))
        if not files:
            raise RuntimeError(f"Aucun CSV dans {p}")
        print(f"📂 {len(files)} fichier(s) trouvé(s) dans {p}")
        frames = []
        for f in files:
            print(f"   └ {f.name}")
            frames.append(pd.read_csv(f, low_memory=False))
        raw = pd.concat(frames, ignore_index=True)
    else:
        print(f"📄 Chargement : {p.name}")
        raw = pd.read_csv(p, low_memory=False)

    required = FEATURE_KEYS + [
        "datetime", "label_regime_3", "label_tradeable",
        "future_ret_h", "future_rv_h", "future_dd_h",
    ]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise RuntimeError(
            "CSV enrichi invalide — colonnes manquantes : " + ", ".join(missing)
        )

    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True)
    df = raw.sort_values("datetime").reset_index(drop=True)

    numeric_cols = list(set(FEATURE_KEYS + ["label_regime_3", "label_tradeable", "future_ret_h", "future_rv_h", "future_dd_h"]))
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required).reset_index(drop=True)
    df["label_regime_3"] = df["label_regime_3"].astype(np.int32)
    df["label_tradeable"] = df["label_tradeable"].astype(np.float32)

    if years:
        df["_year"] = df["datetime"].dt.year
        df = df[df["_year"].isin(years)].reset_index(drop=True).drop(columns=["_year"])
        if df.empty:
            raise RuntimeError(f"Aucune donnée pour les années {years}")

    print(f"   {len(df):,} barres  |  {df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()}")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 0 — WINDOW FEATURES
# ═════════════════════════════════════════════════════════════════════════════
def build_level0_window_features(df: pd.DataFrame, cfg: CFG) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """
    Transforme chaque fenêtre [i, i+lookback) en vecteur riche.
    Le label lu est celui de la dernière barre visible de la fenêtre,
    qui encode déjà le futur horizon suivant dans le CSV enrichi.
    """
    feature_matrix = df[FEATURE_KEYS].values.astype(np.float32)
    labels = df["label_regime_3"].values.astype(np.int32)
    tradeable = df["label_tradeable"].values.astype(np.float32)

    col_idx = {c: i for i, c in enumerate(FEATURE_KEYS)}

    selected_series = [
        "log_ret", "rv_12", "rv_24", "rv_72", "rv_168",
        "atr_pct_14", "rsi_14", "boll_pos_20", "boll_width_20",
        "dist_ema_20", "dist_ema_50", "dist_ema_200",
        "ema_spread_20_50", "ema_spread_50_200",
        "taker_buy_ratio_base", "delta_taker_pressure",
        "Volume", "Quote_Volume",
    ]

    windows = [12, 24, 48, 96, cfg.lookback]
    rows: List[List[float]] = []
    y: List[int] = []
    y_conf: List[float] = []
    names: List[str] = []
    names_ready = False

    max_i = count_windows(df, cfg)
    for i in range(max_i):
        end = i + cfg.lookback
        w = feature_matrix[i:end]
        row: List[float] = []
        row_names: List[str] = []

        # Snapshot final
        for c in [
            "rv_12", "rv_24", "rv_72", "rv_168",
            "atr_pct_14", "rsi_14", "boll_pos_20", "boll_width_20",
            "dist_ema_20", "dist_ema_50", "dist_ema_200",
            "ema_spread_20_50", "ema_spread_50_200",
            "taker_buy_ratio_base", "delta_taker_pressure",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
        ]:
            row.append(safe_float(w[-1, col_idx[c]]))
            row_names.append(f"last__{c}")

        # Aggregats multi-fenêtres
        for c in selected_series:
            s = w[:, col_idx[c]].astype(np.float64)
            for win in windows:
                ss = s[-win:] if s.size >= win else s
                row.extend([
                    safe_float(ss[-1]),
                    safe_float(np.mean(ss)),
                    safe_float(np.std(ss)),
                    safe_float(np.min(ss)),
                    safe_float(np.max(ss)),
                    safe_float(np.quantile(ss, 0.25)),
                    safe_float(np.quantile(ss, 0.75)),
                    safe_float(linear_slope(ss)),
                    safe_float(rolling_zscore_last(ss)),
                    safe_float(ss[-1] - ss[0]) if ss.size > 1 else 0.0,
                ])
                row_names.extend([
                    f"{c}__w{win}__last",
                    f"{c}__w{win}__mean",
                    f"{c}__w{win}__std",
                    f"{c}__w{win}__min",
                    f"{c}__w{win}__max",
                    f"{c}__w{win}__q25",
                    f"{c}__w{win}__q75",
                    f"{c}__w{win}__slope",
                    f"{c}__w{win}__zlast",
                    f"{c}__w{win}__delta",
                ])

        # Structure prix
        close = w[:, col_idx["Close"]].astype(np.float64)
        high = w[:, col_idx["High"]].astype(np.float64)
        low = w[:, col_idx["Low"]].astype(np.float64)
        ret = w[:, col_idx["log_ret"]].astype(np.float64)

        for win in [12, 24, 48, cfg.lookback]:
            c = close[-win:] if close.size >= win else close
            h = high[-win:] if high.size >= win else high
            l = low[-win:] if low.size >= win else low
            r = ret[-win:] if ret.size >= win else ret
            price_range = float(np.max(h) - np.min(l)) if c.size else 0.0
            denom = max(abs(float(c[-1])) if c.size else 0.0, 1e-12)
            row.extend([
                safe_float((float(c[-1]) - float(c[0])) / max(abs(float(c[0])), 1e-12)) if c.size > 1 else 0.0,
                safe_float(price_range / denom),
                safe_float(np.sum(np.abs(np.diff(c))) / max(price_range, 1e-12)) if c.size > 1 else 0.0,
                safe_float(np.mean(r)),
                safe_float(np.std(r)),
                safe_float(np.sum(r)),
            ])
            row_names.extend([
                f"price__w{win}__ret",
                f"price__w{win}__range_pct",
                f"price__w{win}__path_to_range",
                f"price__w{win}__ret_mean",
                f"price__w{win}__ret_std",
                f"price__w{win}__ret_sum",
            ])

        if not names_ready:
            names = row_names
            names_ready = True

        rows.append(row)
        y.append(int(labels[end - 1]))
        y_conf.append(float(tradeable[end - 1]))

    X = np.asarray(rows, dtype=np.float32)
    y_arr = np.asarray(y, dtype=np.int32)
    y_conf_arr = np.asarray(y_conf, dtype=np.float32)
    return X, y_arr, y_conf_arr, names


# ═════════════════════════════════════════════════════════════════════════════
# SCALER
# ═════════════════════════════════════════════════════════════════════════════
def fit_scaler_from_matrix(X_train: np.ndarray, cfg: CFG) -> RobustScaler:
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    for i in range(len(X_train)):
        sampler.add(X_train[i:i+1])
    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("Pas assez de données pour le scaler.")
    sc = RobustScaler()
    sc.fit(Xfit)
    return sc


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 0 — REGIME CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════
def train_regime_classifier(df: pd.DataFrame, cfg: CFG, out_dir: Path):
    print("\n" + "=" * 70)
    print("LEVEL 0 — REGIME CLASSIFIER  (HistGradientBoosting)")
    print("=" * 70)

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support,
        confusion_matrix,
    )

    X_all, y_all, y_conf_all, feature_names = build_level0_window_features(df, cfg)
    total = len(X_all)
    n_train = int(total * cfg.train_frac)
    n_val = int(total * cfg.val_frac)

    X_train = X_all[:n_train]
    y_train = y_all[:n_train]
    X_val = X_all[n_train:n_train + n_val]
    y_val = y_all[n_train:n_train + n_val]

    print(f"   Fenêtres totales : {total:,}  |  train : {n_train:,}  val : {n_val:,}")
    print(f"   Distribution train  : {summarize_counts(y_train)}")
    print(f"   Distribution val    : {summarize_counts(y_val)}")
    print(f"   Nb features fenêtre : {X_train.shape[1]:,}")

    scaler = fit_scaler_from_matrix(X_train, cfg)
    X_train_sc = scaler.transform(X_train).astype(np.float32)
    X_val_sc = scaler.transform(X_val).astype(np.float32)

    print("   Entraînement HistGradientBoosting ...")
    t0 = time.time()
    clf = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=cfg.l0_learning_rate,
        max_iter=cfg.l0_max_iter,
        max_depth=cfg.l0_max_depth,
        min_samples_leaf=cfg.l0_min_samples_leaf,
        l2_regularization=cfg.l0_l2,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=cfg.seed,
    )
    clf.fit(X_train_sc, y_train)
    elapsed = time.time() - t0
    print(f"   Entraîné en {elapsed:.1f}s")

    y_pred = clf.predict(X_val_sc)
    y_proba = clf.predict_proba(X_val_sc)

    acc = float(accuracy_score(y_val, y_pred))
    macro_f1 = float(f1_score(y_val, y_pred, average="macro"))
    weighted_f1 = float(f1_score(y_val, y_pred, average="weighted"))

    precision, recall, f1, support = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1, 2], zero_division=0
    )
    per_class_recall = {CLASS_ID_TO_NAME[i]: float(r) for i, r in enumerate(recall)}
    per_class_precision = {CLASS_ID_TO_NAME[i]: float(p) for i, p in enumerate(precision)}
    per_class_f1 = {CLASS_ID_TO_NAME[i]: float(v) for i, v in enumerate(f1)}
    per_class_support = {CLASS_ID_TO_NAME[i]: int(v) for i, v in enumerate(support)}

    cm = confusion_matrix(y_val, y_pred, labels=[0, 1, 2])
    cm_norm = cm.astype(float) / np.maximum(cm.sum(axis=1, keepdims=True), 1.0)

    pred_dist = summarize_counts(y_pred)
    bull_recall = per_class_recall.get("bull", 0.0)
    gate_passed = (bull_recall >= cfg.min_bull_recall) and (macro_f1 >= cfg.min_macro_f1)

    # Feature importance permutation-free proxy from boosting importances if unavailable -> skip
    diagnostics = {
        "accuracy": acc,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "per_class_recall": per_class_recall,
        "per_class_precision": per_class_precision,
        "per_class_f1": per_class_f1,
        "per_class_support": per_class_support,
        "pred_distribution_val": pred_dist,
        "true_distribution_val": summarize_counts(y_val),
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_normalized": cm_norm.tolist(),
        "bull_recall": bull_recall,
        "min_bull_recall": cfg.min_bull_recall,
        "min_macro_f1": cfg.min_macro_f1,
        "gate_passed": gate_passed,
        "n_features": int(X_train.shape[1]),
        "feature_names": feature_names,
        "model": {
            "type": "HistGradientBoostingClassifier",
            "learning_rate": cfg.l0_learning_rate,
            "max_iter": cfg.l0_max_iter,
            "max_depth": cfg.l0_max_depth,
            "min_samples_leaf": cfg.l0_min_samples_leaf,
            "l2_regularization": cfg.l0_l2,
        },
    }

    print(f"   Accuracy val        : {acc:.4f}")
    print(f"   Macro F1 val        : {macro_f1:.4f}")
    print(f"   Recall val          : {per_class_recall}")
    print(f"   Distribution prédite: {pred_dist}")
    print("   Confusion val (normalisée) :")
    print(np.array2string(cm_norm, precision=3, suppress_small=True))

    regime_dir = out_dir / "regime_classifier"
    regime_dir.mkdir(parents=True, exist_ok=True)

    import pickle
    with open(regime_dir / "model.pkl", "wb") as f:
        pickle.dump(clf, f)
    with open(regime_dir / "window_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    json_dump(regime_dir / "metrics.json", diagnostics)

    if not gate_passed:
        raise ValueError(
            f"BULL RECALL GATE FAILED (val) : {bull_recall:.3f} < {cfg.min_bull_recall} "
            f"ou MACRO_F1 {macro_f1:.3f} < {cfg.min_macro_f1} — modèle rejeté."
        )

    print(f"   Sauvegardé : {regime_dir}")
    return clf, diagnostics


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 1 — EVENT CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════
def iter_windows(
    df: pd.DataFrame,
    cfg: CFG,
    scaler: RobustScaler,
    start: int,
    end: int,
):
    Xraw = df[FEATURE_KEYS].values.astype(np.float32)
    Xn = scaler.transform(Xraw)
    regime_arr = df["label_regime_3"].values.astype(np.int32)
    tradeable_arr = df["label_tradeable"].values.astype(np.float32)

    max_i = count_windows(df, cfg)
    for i in range(start, min(end, max_i), cfg.stride):
        Xw = Xn[i:i + cfg.lookback]
        regime = int(regime_arr[i + cfg.lookback - 1])
        y_conf = float(tradeable_arr[i + cfg.lookback - 1])
        yield (Xw.astype(np.float32), np.int32(regime), np.float32(y_conf))


def _make_tf_dataset(
    df: pd.DataFrame, cfg: CFG, scaler: RobustScaler,
    start: int, end: int, shuffle: bool = False
) -> tf.data.Dataset:
    F = len(FEATURE_KEYS)
    sig = (
        tf.TensorSpec((cfg.lookback, F), tf.float32),
        tf.TensorSpec((), tf.int32),
        tf.TensorSpec((), tf.float32),
    )

    def gen():
        yield from iter_windows(df, cfg, scaler, start, end)

    ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
    if shuffle:
        ds = ds.shuffle(2048, seed=cfg.seed, reshuffle_each_iteration=True)
    return ds.batch(cfg.batch_size).prefetch(tf.data.AUTOTUNE)


def _val_eval(model, ds_val, ce, bce):
    reg_loss, conf_loss = [], []
    conf_mean, ent_mean = [], []
    all_yhat, all_ytrue = [], []
    n_conf_correct = 0
    n_total = 0

    for x, y_reg, y_conf in ds_val:
        out = model(x, training=False)
        logits = out["regime_logits"]
        conf = out["confidence"]
        ent = out["entropy"]

        reg_loss.append(float(ce(y_reg, logits).numpy()))
        conf_loss.append(float(bce(tf.expand_dims(y_conf, -1), conf).numpy()))
        conf_mean.append(float(tf.reduce_mean(conf).numpy()))
        ent_mean.append(float(tf.reduce_mean(ent).numpy()))

        yhat = tf.argmax(out["regime_probs"], axis=-1).numpy()
        ytrue = y_reg.numpy()
        all_yhat.extend(yhat.tolist())
        all_ytrue.extend(ytrue.tolist())

        conf_pred = (conf.numpy().squeeze(-1) >= 0.5).astype(int)
        conf_target = y_conf.numpy().astype(int)
        n_conf_correct += int((
            conf_pred == conf_target
        ).sum())
        n_total += int(len(conf_target))

    all_yhat = np.array(all_yhat, dtype=np.int32)
    all_ytrue = np.array(all_ytrue, dtype=np.int32)

    regime_acc = float((all_yhat == all_ytrue).mean()) if len(all_ytrue) else 0.0
    conf_acc = float(n_conf_correct / max(n_total, 1))

    return {
        "val_reg_loss": float(np.mean(reg_loss)) if reg_loss else 0.0,
        "val_conf_loss": float(np.mean(conf_loss)) if conf_loss else 0.0,
        "val_conf_mean": float(np.mean(conf_mean)) if conf_mean else 0.0,
        "val_ent_mean": float(np.mean(ent_mean)) if ent_mean else 0.0,
        "regime_acc": regime_acc,
        "conf_acc": conf_acc,
    }


def train_event_classifier(df: pd.DataFrame, cfg: CFG, out_dir: Path):
    print("\n" + "=" * 70)
    print("LEVEL 1 — EVENT CLASSIFIER  (TCN TensorFlow/Keras)")
    print("=" * 70)

    np.random.seed(cfg.seed)
    tf.random.set_seed(cfg.seed)

    if "label_regime_3" not in df.columns or "label_tradeable" not in df.columns:
        raise RuntimeError(
            "label_regime_3 / label_tradeable absents du CSV — "
            "relance build_binance_features.py pour générer le CSV enrichi."
        )

    total = count_windows(df, cfg)
    n_train = int(total * cfg.train_frac)
    n_val = int(total * cfg.val_frac)

    train_start, train_end = 0, n_train
    val_start, val_end = n_train, n_train + n_val

    print(f"   Total fenêtres : {total:,}  |  train {n_train:,}  val {n_val:,}")

    # Scaler ajusté sur train uniquement
    print("   Ajustement du scaler ...", end=" ", flush=True)
    X_train_scaler = df[FEATURE_KEYS].values.astype(np.float32)[: n_train + cfg.lookback]
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    for i in range(max(0, len(X_train_scaler) - cfg.lookback)):
        sampler.add(X_train_scaler[i:i + cfg.lookback])
    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("Pas assez de données pour ajuster le scaler du Level 1.")
    scaler = RobustScaler()
    scaler.fit(Xfit)
    print("OK")

    ds_train = _make_tf_dataset(df, cfg, scaler, train_start, train_end, shuffle=True)
    ds_val = _make_tf_dataset(df, cfg, scaler, val_start, val_end, shuffle=False)

    model = EventClassifier(
        EventClassifierConfig(
            d_model=64,
            n_layers=3,
            n_regimes=cfg.n_regimes,
            dropout=0.2,
            confidence_dropout=0.1,
        )
    )

    opt = tf.keras.optimizers.AdamW(
        learning_rate=cfg.lr,
        weight_decay=cfg.weight_decay,
        global_clipnorm=cfg.clip_norm,
    )
    ce = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)
    bce = tf.keras.losses.BinaryCrossentropy(from_logits=False)

    event_dir = out_dir / "event_classifier"
    event_dir.mkdir(parents=True, exist_ok=True)
    log_path = event_dir / "log.jsonl"

    best_score = -1e18
    best_epoch = -1
    bad = 0

    print()
    print(
        f"{'Ep':>3}  "
        f"{'tr_reg':>8} {'tr_conf':>8}  "
        f"{'v_reg':>8} {'v_conf':>8}  "
        f"{'reg_acc':>8} {'conf_acc':>8}  "
        f"{'score':>8}  {'lr':>9}  t(s)"
    )
    print("─" * 88)

    with open(log_path, "a", buffering=1, encoding="utf-8") as log_f:
        for ep in range(cfg.epochs):
            ep_t0 = time.time()
            tr_reg_loss, tr_conf_loss = [], []

            for step, (x, y_reg, y_conf) in enumerate(ds_train, start=1):
                with tf.GradientTape() as tape:
                    out = model(x, training=True)
                    loss_reg = ce(y_reg, out["regime_logits"])
                    loss_conf = bce(tf.expand_dims(y_conf, -1), out["confidence"])
                    loss_ent = 0.01 * tf.reduce_mean(out["entropy"])
                    loss = loss_reg + loss_conf + loss_ent

                grads = tape.gradient(loss, model.trainable_variables)
                opt.apply_gradients(zip(grads, model.trainable_variables))

                tr_reg_loss.append(float(loss_reg.numpy()))
                tr_conf_loss.append(float(loss_conf.numpy()))

            v = _val_eval(model, ds_val, ce, bce)
            lr = float(
                opt.learning_rate.numpy()
                if hasattr(opt.learning_rate, "numpy")
                else cfg.lr
            )

            val_score = (
                v["regime_acc"] * 0.40
                + v["conf_acc"] * 0.40
                - v["val_reg_loss"] * 0.10
                - v["val_ent_mean"] * 0.10
            )

            ep_time = time.time() - ep_t0
            print(
                f"{ep+1:>3}  "
                f"{np.mean(tr_reg_loss):>8.4f} {np.mean(tr_conf_loss):>8.4f}  "
                f"{v['val_reg_loss']:>8.4f} {v['val_conf_loss']:>8.4f}  "
                f"{v['regime_acc']:>7.2%} {v['conf_acc']:>8.2%}  "
                f"{val_score:>8.4f}  {lr:.2e}  {ep_time:.0f}"
            )

            row = {
                "epoch": ep + 1,
                "train_reg_loss": float(np.mean(tr_reg_loss)) if tr_reg_loss else 0.0,
                "train_conf_loss": float(np.mean(tr_conf_loss)) if tr_conf_loss else 0.0,
                **v,
                "val_score": float(val_score),
                "lr": lr,
                "epoch_time_sec": float(ep_time),
            }
            log_f.write(json.dumps(row, ensure_ascii=False) + "\n")

            if ep > 0 and (ep % cfg.reduce_lr_patience == 0) and val_score <= best_score:
                new_lr = max(lr * cfg.reduce_lr_factor, cfg.min_lr)
                opt.learning_rate.assign(new_lr)
                print(f"     → lr réduit à {new_lr:.2e}")

            if val_score > best_score + cfg.min_delta:
                best_score = val_score
                best_epoch = ep + 1
                bad = 0
                model.save_weights(str(event_dir / "best.weights.h5"))
            else:
                bad += 1
                if bad >= cfg.early_stop_patience:
                    print(f"\n   Early stop à l'epoch {ep+1}  (patience={cfg.early_stop_patience})")
                    break

    model.save_weights(str(event_dir / "final.weights.h5"))

    try:
        with open(event_dir / "scaler.pkl", "wb") as f:
            import pickle
            pickle.dump(scaler, f)
    except Exception as e:
        print(f"⚠  Sauvegarde scaler Level 1 impossible : {e}")

    summary = {
        "best_val_score": float(best_score),
        "best_epoch": int(best_epoch),
        "cfg": {
            "lookback": cfg.lookback,
            "horizon": cfg.horizon,
            "batch_size": cfg.batch_size,
            "epochs": cfg.epochs,
            "lr": cfg.lr,
            "min_lr": cfg.min_lr,
            "weight_decay": cfg.weight_decay,
            "clip_norm": cfg.clip_norm,
            "n_regimes": cfg.n_regimes,
        },
    }
    json_dump(event_dir / "summary.json", summary)

    print(f"\n   Best val_score : {best_score:.4f}  (epoch {best_epoch})")
    print(f"   Sauvegardé    : {event_dir}")
    return model, summary


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def parse_args():
    ap = argparse.ArgumentParser(
        description="Entraîne Level 0 + Level 1 sur des CSV Binance enrichis."
    )
    ap.add_argument(
        "--data",
        required=True,
        help="Chemin vers un CSV enrichi ou un dossier contenant des CSV enrichis",
    )
    ap.add_argument(
        "--out",
        default=str(FUTUR / "runs" / "local"),
        help=f"Dossier de sortie (défaut : {FUTUR}/runs/local)",
    )
    ap.add_argument(
        "--years",
        default=None,
        help="Années à utiliser, ex : 2021,2022,2023",
    )
    ap.add_argument(
        "--skip-regime",
        action="store_true",
        help="Saute l'entraînement du Regime Classifier (Level 0)",
    )
    ap.add_argument(
        "--skip-event",
        action="store_true",
        help="Saute l'entraînement de l'Event Classifier (Level 1)",
    )
    return ap.parse_args()


def main():
    t_start = time.time()
    args = parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else None
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("ML TRAINING PIPELINE — LOCAL CSV")
    print("=" * 70)
    print(f"  Data   : {args.data}")
    print(f"  Sortie : {out}")
    if years:
        print(f"  Années : {years}")

    df = load_data(args.data, years)
    cfg = CFG()

    pipeline_summary: Dict[str, object] = {
        "run_id": run_id,
        "data": args.data,
        "years": years,
        "n_rows": int(len(df)),
        "date_start": str(df["datetime"].iloc[0]),
        "date_end": str(df["datetime"].iloc[-1]),
        "cfg": {
            "lookback": cfg.lookback,
            "horizon": cfg.horizon,
            "stride": cfg.stride,
            "train_frac": cfg.train_frac,
            "val_frac": cfg.val_frac,
            "batch_size": cfg.batch_size,
            "epochs": cfg.epochs,
            "lr": cfg.lr,
            "min_lr": cfg.min_lr,
            "weight_decay": cfg.weight_decay,
            "clip_norm": cfg.clip_norm,
            "n_regimes": cfg.n_regimes,
            "min_bull_recall": cfg.min_bull_recall,
            "min_macro_f1": cfg.min_macro_f1,
        },
        "level0": None,
        "level1": None,
    }

    if not args.skip_regime:
        try:
            _, l0_diag = train_regime_classifier(df, cfg, out)
            pipeline_summary["level0"] = {
                "status": "ok",
                "metrics": l0_diag,
            }
        except ValueError as e:
            print(f"\n❌  {e}")
            print("   Pipeline continue malgré l'échec du gate Level 0.")
            metrics_path = out / "regime_classifier" / "metrics.json"
            recovered_metrics = None
            if metrics_path.exists():
                with open(metrics_path, "r", encoding="utf-8") as f:
                    recovered_metrics = json.load(f)
            pipeline_summary["level0"] = {
                "status": "gate_failed",
                "error": str(e),
                "metrics": recovered_metrics,
            }
        except Exception as e:
            print(f"\n❌  Erreur Level 0 : {e}")
            pipeline_summary["level0"] = {
                "status": "error",
                "error": str(e),
            }

    if not args.skip_event:
        try:
            _, l1_summary = train_event_classifier(df, cfg, out)
            pipeline_summary["level1"] = {
                "status": "ok",
                "summary": l1_summary,
            }
        except Exception as e:
            print(f"\n❌  Erreur Level 1 : {e}")
            pipeline_summary["level1"] = {
                "status": "error",
                "error": str(e),
            }

    elapsed = time.time() - t_start
    pipeline_summary["elapsed_sec"] = float(elapsed)
    json_dump(out / "pipeline_summary.json", pipeline_summary)

    print("\n" + "=" * 70)
    print(f"✅  Pipeline terminé en {elapsed/60:.1f} min")
    print(f"   Résultats : {out}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
