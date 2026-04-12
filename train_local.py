#!/usr/bin/env python3
"""
train_local.py — Entraînement local sur CSV Binance
====================================================

Entraîne les deux blocks IA séquentiellement :
  Level 0 : Regime Classifier (sklearn SGD + calibration)
  Level 1 : Event Classifier  (TF/Keras TCN)

Usage :
    python ~/train_local.py --data ~/futur/data/BTCUSD_1h_Binance.csv
    python ~/train_local.py --data ~/futur/data/BTCUSD_1h_Binance.csv --years 2021,2022,2023
    python ~/train_local.py --data ~/futur/data/ --out ~/futur/runs/local
    python ~/train_local.py --data ~/futur/data/ --skip-regime   # Level 1 uniquement
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from pathlib import Path
from dataclasses import dataclass

import warnings
import numpy as np
import pandas as pd

# Supprime le ConvergenceWarning sklearn (SGD max_iter)
from sklearn.exceptions import ConvergenceWarning
warnings.filterwarnings("ignore", category=ConvergenceWarning)

# ── Ajoute le dossier ai/models au path pour les imports du projet ─────────
FUTUR = Path(__file__).parent / "futur"
sys.path.insert(0, str(FUTUR / "ai" / "models"))

from training.common.scaler import RobustScaler, ReservoirSampler
from training.common.labels import future_path_stats, rms_vol, compute_regime
from training.common.production_regime import (
    add_impulse_discriminant_features,
    print_regime_metrics_report,
)
from level_1.Event_Classifier import EventClassifier, EventClassifierConfig

import tensorflow as tf

# ── GPU setup ─────────────────────────────────────────────────────────────────
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
    lookback: int = 128    # fenêtre d'entrée (en barres)
    horizon: int  = 24     # horizon de prédiction (en barres)
    stride: int   = 1

    train_frac: float = 0.80
    val_frac:   float = 0.10

    batch_size: int   = 128
    epochs: int       = 40

    lr: float           = 3e-4
    min_lr: float       = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float    = 1.0

    scaler_sample_max: int = 250_000

    q_absR:  float = 0.70
    q_RV_hi: float = 0.70
    q_DD_lo: float = 0.70

    reduce_lr_patience: int   = 3
    reduce_lr_factor: float   = 0.5
    early_stop_patience: int  = 6
    min_delta: float          = 1e-4

    n_regimes: int = 4
    seed: int      = 1337

    min_impulse_recall: float = 0.35


FEATURE_KEYS = [
    "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
    "ret", "log_ret",
    "rv_15", "rv_60", "rv_240",
    "ema_20", "ema_50", "ema_200",
    "atr_14", "rsi_14",
]
RET_KEY   = "log_ret"
RV_KEY    = "rv_60"
CLOSE_KEY = "Close"


# ═════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ═════════════════════════════════════════════════════════════════════════════

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=n, min_periods=n, adjust=False).mean()


def _rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=n, min_periods=n, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=n, min_periods=n, adjust=False).mean()
    rs    = gain / (loss + 1e-8)
    return 100.0 - 100.0 / (1.0 + rs)


def compute_features(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Calcule toutes les features à partir d'un DataFrame OHLCV Binance."""
    df = df_raw.copy()

    # Renommage colonnes Binance CSV
    df = df.rename(columns={
        "Open time":          "datetime",
        "Quote asset volume": "Quote_Volume",
    })

    # Assure datetime UTC
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    # Types numériques
    for col in ["Open", "High", "Low", "Close", "Volume", "Quote_Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Returns
    df["ret"]     = df["Close"].pct_change().fillna(0.0)
    df["log_ret"] = np.log(df["Close"] / df["Close"].shift(1)).fillna(0.0)

    # Volatilités réalisées
    df["rv_15"]  = df["log_ret"].rolling(15,  min_periods=5).std().fillna(0.0)
    df["rv_60"]  = df["log_ret"].rolling(60,  min_periods=15).std().fillna(0.0)
    df["rv_240"] = df["log_ret"].rolling(240, min_periods=30).std().fillna(0.0)

    # EMAs
    df["ema_20"]  = df["Close"].ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = df["Close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"] = df["Close"].ewm(span=200, adjust=False).mean()

    # ATR + RSI
    df["atr_14"] = _atr(df, 14)
    df["rsi_14"] = _rsi(df["Close"], 14)

    # Supprime les NaN initiaux
    df = df.dropna(subset=FEATURE_KEYS).reset_index(drop=True)

    return df


def load_data(path_arg: str, years: list[int] | None = None) -> pd.DataFrame:
    """Charge un CSV ou tous les CSV d'un dossier, avec filtre années optionnel."""
    p = Path(path_arg)
    if p.is_dir():
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

    df = compute_features(raw)

    if years:
        df["_year"] = pd.to_datetime(df["datetime"]).dt.year
        df = df[df["_year"].isin(years)].reset_index(drop=True).drop(columns=["_year"])
        if df.empty:
            raise RuntimeError(f"Aucune donnée pour les années {years}")

    print(f"   {len(df):,} barres  |  {df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()}")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# FENÊTRES GLISSANTES (local, sans S3)
# ═════════════════════════════════════════════════════════════════════════════

def count_windows(df: pd.DataFrame, cfg: CFG) -> int:
    return max(0, len(df) - cfg.lookback - cfg.horizon)


def iter_windows(
    df: pd.DataFrame,
    cfg: CFG,
    scaler: RobustScaler,
    thresholds: dict,
    start: int,
    end: int,
):
    """Générateur de fenêtres → (Xw, regime, y_conf)."""
    Xraw  = df[FEATURE_KEYS].values.astype(np.float32)
    Xn    = scaler.transform(Xraw)
    ret   = df[RET_KEY].values.astype(np.float32)
    rv    = df[RV_KEY].values.astype(np.float32)
    close = df[CLOSE_KEY].values.astype(np.float32)

    T     = len(df)
    max_i = max(0, T - cfg.lookback - cfg.horizon)

    for i in range(start, min(end, max_i), cfg.stride):
        Xw      = Xn[i : i + cfg.lookback]
        fut_ret = ret[i + cfg.lookback : i + cfg.lookback + cfg.horizon]
        fut_rv  = rv[i + cfg.lookback : i + cfg.lookback + cfg.horizon]

        R, DD = future_path_stats(fut_ret)
        RV    = max(rms_vol(fut_rv), 1e-8)

        regime = int(compute_regime(
            close[i : i + cfg.lookback], fut_ret, fut_rv, cfg.n_regimes
        ))
        absR   = abs(float(R))
        y_conf = int(
            absR  >= thresholds["thr_absR"]
            and RV >= thresholds["thr_RV_hi"]
            and DD <= thresholds["thr_DD_lo"]
        )
        yield (Xw.astype(np.float32), np.int32(regime), np.float32(y_conf))


# ═════════════════════════════════════════════════════════════════════════════
# SCALER + SEUILS (train-only, pas de data leakage)
# ═════════════════════════════════════════════════════════════════════════════

def fit_scaler(df: pd.DataFrame, cfg: CFG, train_end: int) -> RobustScaler:
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    Xraw  = df[FEATURE_KEYS].values.astype(np.float32)
    T     = len(df)
    max_i = max(0, T - cfg.lookback - cfg.horizon)
    for i in range(min(train_end, max_i)):
        sampler.add(Xraw[i : i + cfg.lookback])
    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("Pas assez de données pour le scaler.")
    sc = RobustScaler()
    sc.fit(Xfit)
    return sc


def fit_thresholds(
    df: pd.DataFrame, cfg: CFG, scaler: RobustScaler,
    train_start: int, train_end: int
) -> dict:
    """Calibre les seuils de tradeabilité sur les données de train uniquement."""
    ret   = df[RET_KEY].values.astype(np.float32)
    rv    = df[RV_KEY].values.astype(np.float32)
    T     = len(df)
    max_i = max(0, T - cfg.lookback - cfg.horizon)

    absR_list, RV_list, DD_list = [], [], []
    for i in range(train_start, min(train_end, max_i)):
        fut_ret = ret[i + cfg.lookback : i + cfg.lookback + cfg.horizon]
        fut_rv  = rv[i + cfg.lookback : i + cfg.lookback + cfg.horizon]
        R, DD   = future_path_stats(fut_ret)
        RV      = max(rms_vol(fut_rv), 1e-8)
        absR_list.append(abs(float(R)))
        RV_list.append(float(RV))
        DD_list.append(float(DD))
        if len(absR_list) >= 400_000:
            break

    if len(absR_list) < 1000:
        raise RuntimeError("Pas assez de fenêtres pour calibrer les seuils.")

    return {
        "thr_absR":  float(np.quantile(absR_list, cfg.q_absR)),
        "thr_RV_hi": float(np.quantile(RV_list,  cfg.q_RV_hi)),
        "thr_DD_lo": float(np.quantile(DD_list,  cfg.q_DD_lo)),
    }


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 0 — REGIME CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

def train_regime_classifier(df: pd.DataFrame, cfg: CFG, out_dir: Path):
    print("\n" + "=" * 70)
    print("LEVEL 0 — REGIME CLASSIFIER  (SGD + calibration isotonique)")
    print("=" * 70)

    df_feat = df.copy()
    add_impulse_discriminant_features(df_feat)

    REGIME_FEATURES = [
        "abs_ret_1m", "abs_ret_5m", "range_1m", "vol_z_60m", "rv_ratio_5_60",
        "rv_15", "rv_60", "rv_240", "rsi_14", "atr_14",
    ]
    for col in REGIME_FEATURES:
        if col not in df_feat.columns:
            df_feat[col] = 0.0

    ret   = df_feat[RET_KEY].values.astype(np.float32)
    rv    = df_feat[RV_KEY].values.astype(np.float32)
    close = df_feat[CLOSE_KEY].values.astype(np.float32)
    Xfeat = df_feat[REGIME_FEATURES].values.astype(np.float32)

    T        = len(df_feat)
    max_i    = max(0, T - cfg.lookback - cfg.horizon)
    n_train  = int(max_i * cfg.train_frac)

    print(f"   Fenêtres totales : {max_i:,}  |  train : {n_train:,}")
    print("   Calcul des labels régime ...", end=" ", flush=True)

    CLASS_NAMES = ["squeeze", "breakout", "reversal", "impulse"]

    labels = []
    for i in range(n_train):
        fut_ret = ret[i + cfg.lookback : i + cfg.lookback + cfg.horizon]
        fut_rv  = rv[i + cfg.lookback : i + cfg.lookback + cfg.horizon]
        regime  = compute_regime(close[i : i + cfg.lookback], fut_ret, fut_rv, cfg.n_regimes)
        labels.append(CLASS_NAMES[regime % len(CLASS_NAMES)])

    print("OK")

    # Feature au milieu de la fenêtre de lookback
    X_train = np.array([Xfeat[i + cfg.lookback // 2] for i in range(n_train)], dtype=np.float32)
    y_train = np.array(labels)

    print("   Entraînement SGD + calibration isotonique ...")
    t0 = time.time()

    # On réimplémente directement avec max_iter=200 (évite le monkey-patch)
    from sklearn.linear_model import SGDClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support,
        confusion_matrix as sklearn_cm,
    )
    from training.common.production_regime import (
        RegimeClassifierMetrics, compute_ece_multiclass
    )

    base_clf = SGDClassifier(
        loss="log_loss", penalty="l2", alpha=1e-5,
        max_iter=200, tol=1e-3,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    clf = CalibratedClassifierCV(base_clf, method="isotonic", cv=3, n_jobs=-1)
    clf.fit(X_train, y_train)

    y_pred  = clf.predict(X_train)
    y_proba = clf.predict_proba(X_train)

    accuracy    = float(accuracy_score(y_train, y_pred))
    macro_f1    = float(f1_score(y_train, y_pred, average="macro"))
    weighted_f1 = float(f1_score(y_train, y_pred, average="weighted"))

    precision, recall, f1, _ = precision_recall_fscore_support(
        y_train, y_pred, labels=CLASS_NAMES, zero_division=0
    )
    per_class_recall    = {c: float(r) for c, r in zip(CLASS_NAMES, recall)}
    per_class_precision = {c: float(p) for c, p in zip(CLASS_NAMES, precision)}
    per_class_f1        = {c: float(f) for c, f in zip(CLASS_NAMES, f1)}

    n_cls    = len(CLASS_NAMES)
    y_onehot = np.zeros((len(y_train), n_cls))
    for i, cls in enumerate(CLASS_NAMES):
        y_onehot[y_train == cls, i] = 1
    brier       = float(np.mean((y_proba - y_onehot) ** 2))
    ece         = compute_ece_multiclass(y_train, y_proba, CLASS_NAMES)
    avg_entropy = float(-(y_proba * np.log(y_proba + 1e-9)).sum(axis=1).mean())

    cm      = sklearn_cm(y_train, y_pred, labels=CLASS_NAMES)
    cm_norm = cm.astype("float") / cm.sum(axis=1)[:, np.newaxis]

    unique, counts = np.unique(y_pred, return_counts=True)
    pred_dist = {
        cls: float(counts[np.where(unique == cls)[0][0]] / len(y_pred))
        if cls in unique else 0.0
        for cls in CLASS_NAMES
    }

    impulse_recall  = per_class_recall.get("impulse", 0.0)
    gate_passed     = impulse_recall >= cfg.min_impulse_recall

    metrics = RegimeClassifierMetrics(
        accuracy=accuracy, macro_f1=macro_f1, weighted_f1=weighted_f1,
        per_class_recall=per_class_recall,
        per_class_precision=per_class_precision,
        per_class_f1=per_class_f1,
        brier_score=brier, ece=ece, avg_entropy=avg_entropy,
        confusion_matrix=cm.tolist(),
        confusion_matrix_normalized=cm_norm.tolist(),
        pred_distribution=pred_dist,
        impulse_recall_gate_passed=gate_passed,
        min_impulse_recall=cfg.min_impulse_recall,
    )

    print(f"   Entraîné en {time.time()-t0:.1f}s")

    if not gate_passed:
        raise ValueError(
            f"IMPULSE RECALL GATE FAILED : {impulse_recall:.3f} < {cfg.min_impulse_recall} "
            "— modèle rejeté (class collapse détecté)."
        )

    print_regime_metrics_report(metrics, CLASS_NAMES)

    # Sauvegarde
    import pickle
    regime_dir = out_dir / "regime_classifier"
    regime_dir.mkdir(parents=True, exist_ok=True)
    with open(regime_dir / "model.pkl", "wb") as f:
        pickle.dump(clf, f)
    with open(regime_dir / "metrics.json", "w") as f:
        json.dump(metrics.to_dict(), f, indent=2)

    print(f"   Sauvegardé : {regime_dir}")
    return clf, metrics


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 1 — EVENT CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

def _make_tf_dataset(
    df: pd.DataFrame, cfg: CFG, scaler: RobustScaler,
    thresholds: dict, start: int, end: int, shuffle: bool = False
) -> tf.data.Dataset:
    F   = len(FEATURE_KEYS)
    sig = (
        tf.TensorSpec((cfg.lookback, F), tf.float32),
        tf.TensorSpec((),               tf.int32),
        tf.TensorSpec((),               tf.float32),
    )

    def gen():
        yield from iter_windows(df, cfg, scaler, thresholds, start, end)

    ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
    if shuffle:
        ds = ds.shuffle(2048, seed=cfg.seed, reshuffle_each_iteration=True)
    return ds.batch(cfg.batch_size).prefetch(tf.data.AUTOTUNE)


def _val_eval(model, ds_val, ce, bce):
    """Évalue val_loss + accuracy régime + accuracy confidence."""
    reg_loss, conf_loss = [], []
    conf_mean, ent_mean = [], []
    all_yhat, all_ytrue = [], []
    n_conf_correct = 0
    n_total        = 0

    for x, y_reg, y_conf in ds_val:
        out    = model(x, training=False)
        logits = out["regime_logits"]
        conf   = out["confidence"]
        ent    = out["entropy"]

        reg_loss.append(float(ce(y_reg, logits).numpy()))
        conf_loss.append(float(bce(tf.expand_dims(y_conf, -1), conf).numpy()))
        conf_mean.append(float(tf.reduce_mean(conf).numpy()))
        ent_mean.append(float(tf.reduce_mean(ent).numpy()))

        yhat  = tf.argmax(out["regime_probs"], axis=-1).numpy()
        ytrue = y_reg.numpy()
        all_yhat.extend(yhat.tolist())
        all_ytrue.extend(ytrue.tolist())

        conf_pred   = (conf.numpy().squeeze(-1) >= 0.5).astype(int)
        conf_target = y_conf.numpy().astype(int)
        n_conf_correct += int((conf_pred == conf_target).sum())
        n_total        += int(len(conf_target))

    all_yhat  = np.array(all_yhat)
    all_ytrue = np.array(all_ytrue)
    regime_acc = float((all_yhat == all_ytrue).mean()) if len(all_ytrue) else 0.0
    conf_acc   = n_conf_correct / max(n_total, 1)

    return {
        "val_reg_loss":  float(np.mean(reg_loss))  if reg_loss  else 0.0,
        "val_conf_loss": float(np.mean(conf_loss)) if conf_loss else 0.0,
        "val_conf_mean": float(np.mean(conf_mean)) if conf_mean else 0.0,
        "val_ent_mean":  float(np.mean(ent_mean))  if ent_mean  else 0.0,
        "regime_acc":    regime_acc,
        "conf_acc":      conf_acc,
    }


def train_event_classifier(df: pd.DataFrame, cfg: CFG, out_dir: Path):
    print("\n" + "=" * 70)
    print("LEVEL 1 — EVENT CLASSIFIER  (TCN TensorFlow/Keras)")
    print("=" * 70)

    np.random.seed(cfg.seed)
    tf.random.set_seed(cfg.seed)

    total   = count_windows(df, cfg)
    n_train = int(total * cfg.train_frac)
    n_val   = int(total * cfg.val_frac)

    train_start, train_end = 0,       n_train
    val_start,   val_end   = n_train, n_train + n_val

    print(f"   Total fenêtres : {total:,}  |  train {n_train:,}  val {n_val:,}")

    # Scaler
    print("   Ajustement du scaler ...", end=" ", flush=True)
    scaler = fit_scaler(df, cfg, train_end)
    print("OK")

    # Seuils
    print("   Calcul des seuils de tradeabilité ...", end=" ", flush=True)
    thresholds = fit_thresholds(df, cfg, scaler, train_start, train_end)
    print(
        f"  absR≥{thresholds['thr_absR']:.5f}"
        f"  RV≥{thresholds['thr_RV_hi']:.5f}"
        f"  DD≤{thresholds['thr_DD_lo']:.5f}"
    )

    # Datasets TF
    ds_train = _make_tf_dataset(df, cfg, scaler, thresholds, train_start, train_end, shuffle=True)
    ds_val   = _make_tf_dataset(df, cfg, scaler, thresholds, val_start,   val_end,   shuffle=False)

    # Modèle
    model = EventClassifier(EventClassifierConfig(
        d_model=64, n_layers=3, n_regimes=cfg.n_regimes,
        dropout=0.2, confidence_dropout=0.1
    ))
    opt = tf.keras.optimizers.AdamW(
        cfg.lr, weight_decay=cfg.weight_decay, global_clipnorm=cfg.clip_norm
    )
    ce  = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)
    bce = tf.keras.losses.BinaryCrossentropy(from_logits=False)

    # Dossier de sortie
    event_dir = out_dir / "event_classifier"
    event_dir.mkdir(parents=True, exist_ok=True)
    log_path = event_dir / "log.jsonl"
    log_f = open(log_path, "a", buffering=1)

    best_score = -1e18
    bad        = 0

    # En-tête du tableau de métriques en temps réel
    print()
    print(
        f"{'Ep':>3}  "
        f"{'tr_reg':>8} {'tr_conf':>8}  "
        f"{'v_reg':>8} {'v_conf':>8}  "
        f"{'reg_acc':>8} {'conf_acc':>8}  "
        f"{'score':>8}  {'lr':>9}  t(s)"
    )
    print("─" * 88)

    # Boucle d'entraînement
    for ep in range(cfg.epochs):
        ep_t0 = time.time()
        tr_reg_loss, tr_conf_loss = [], []

        for x, y_reg, y_conf in ds_train:
            with tf.GradientTape() as tape:
                out       = model(x, training=True)
                loss_reg  = ce(y_reg, out["regime_logits"])
                loss_conf = bce(tf.expand_dims(y_conf, -1), out["confidence"])
                loss_ent  = 0.01 * tf.reduce_mean(out["entropy"])
                loss      = loss_reg + loss_conf + loss_ent

            grads = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grads, model.trainable_variables))
            tr_reg_loss.append(float(loss_reg.numpy()))
            tr_conf_loss.append(float(loss_conf.numpy()))

        v   = _val_eval(model, ds_val, ce, bce)
        lr  = float(opt.learning_rate.numpy() if hasattr(opt.learning_rate, "numpy") else cfg.lr)

        # Score composite
        val_score = (
            v["regime_acc"] * 0.40
            + v["conf_acc"]  * 0.40
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
            "ep":              ep + 1,
            "train_reg_loss":  float(np.mean(tr_reg_loss)),
            "train_conf_loss": float(np.mean(tr_conf_loss)),
            **v,
            "val_score": float(val_score),
            "lr": lr,
        }
        log_f.write(json.dumps(row) + "\n")

        # Reduce LR si pas d'amélioration depuis reduce_lr_patience epochs
        if ep > 0 and (ep % cfg.reduce_lr_patience == 0) and val_score <= best_score:
            new_lr = max(lr * cfg.reduce_lr_factor, cfg.min_lr)
            opt.learning_rate.assign(new_lr)
            print(f"     → lr réduit à {new_lr:.2e}")

        # Best model + early stopping
        if val_score > best_score + cfg.min_delta:
            best_score = val_score
            bad = 0
            model.save_weights(str(event_dir / "best.weights.h5"))
        else:
            bad += 1
            if bad >= cfg.early_stop_patience:
                print(f"\n   Early stop à l'epoch {ep+1}  (patience={cfg.early_stop_patience})")
                break

    log_f.close()
    model.save_weights(str(event_dir / "final.weights.h5"))

    # Sauvegarde scaler + seuils
    with open(event_dir / "scaler.json", "w") as f:
        json.dump(scaler.to_json(), f, indent=2)
    with open(event_dir / "thresholds.json", "w") as f:
        json.dump(thresholds, f, indent=2)

    print(f"\n   Best val_score : {best_score:.4f}")
    print(f"   Sauvegardé    : {event_dir}")
    return model


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    ap = argparse.ArgumentParser(
        description="Entraîne Level 0 + Level 1 sur des CSV Binance locaux."
    )
    ap.add_argument(
        "--data", required=True,
        help="Chemin vers un CSV ou un dossier contenant des CSV Binance"
    )
    ap.add_argument(
        "--out", default=str(FUTUR / "runs" / "local"),
        help=f"Dossier de sortie (défaut : {FUTUR}/runs/local)"
    )
    ap.add_argument(
        "--years", default=None,
        help="Années à utiliser, ex : 2021,2022,2023  (défaut : tout)"
    )
    ap.add_argument(
        "--skip-regime", action="store_true",
        help="Saute l'entraînement du Regime Classifier (Level 0)"
    )
    ap.add_argument(
        "--skip-event", action="store_true",
        help="Saute l'entraînement de l'Event Classifier (Level 1)"
    )
    return ap.parse_args()


def main():
    t_start = time.time()
    args    = parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else None
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out    = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("ML TRAINING PIPELINE — LOCAL CSV")
    print("=" * 70)
    print(f"  Data   : {args.data}")
    print(f"  Sortie : {out}")
    if years:
        print(f"  Années : {years}")

    df  = load_data(args.data, years)
    cfg = CFG()

    # Level 0
    if not args.skip_regime:
        try:
            train_regime_classifier(df, cfg, out)
        except ValueError as e:
            print(f"\n❌  {e}")
            print("   Pipeline continue malgré l'échec du gate impulse.")

    # Level 1
    if not args.skip_event:
        train_event_classifier(df, cfg, out)

    elapsed = time.time() - t_start
    print("\n" + "=" * 70)
    print(f"✅  Pipeline terminé en {elapsed/60:.1f} min")
    print(f"   Résultats : {out}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
