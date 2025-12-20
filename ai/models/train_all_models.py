# train_all_models.py
# Single training entrypoint for the whole stack (N0 → N4) on sample.parquet.
#
# What it does:
# - Loads 1y BTC parquet (your schema)
# - Builds causal windows (lookback → horizon)
# - Fits robust scaler on TRAIN ONLY
# - Trains + evaluates:
#   N0: Global Gating (tradeability + direction probs + regime/proxy context)
#   N1: Orthogonal Context Detectors (pattern confidences)
#   N2: Conditional Specialists (one head per pattern, trained only on active samples)
#   N3: Event-based classifier + Pairwise comparator (parallel signals)
#   N4: Meta-decider policy (BUY/SELL/WAIT) trained via cost-aware supervised proxy
#
# Output:
# - ./runs/<timestamp>/
#   - metrics.json
#   - scaler.json
#   - saved_models/ (keras)
#
# Run:
#   python train_all_models.py --data sample.parquet
#
# Notes:
# - This script is self-contained: it does NOT require your previous modules to exist.
# - If you already have your own implementations, you can swap the model classes with imports.

from __future__ import annotations

import os
import json
import math
import time
import argparse
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional

import numpy as np
import tensorflow as tf

# Optional parquet reader (pandas/pyarrow)
try:
    import pandas as pd
except Exception as e:
    raise RuntimeError("Install pandas + pyarrow: pip install pandas pyarrow") from e


# =========================
# RUNTIME
# =========================
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
try:
    tf.config.optimizer.set_jit(True)  # XLA
except Exception:
    pass

try:
    from tensorflow.keras import mixed_precision
    mixed_precision.set_global_policy("mixed_float16")
except Exception:
    pass


# =========================
# CONFIG
# =========================
@dataclass(frozen=True)
class CFG:
    # data
    lookback: int = 256
    horizon: int = 12
    stride: int = 1

    # split (time-based)
    train_frac: float = 0.80
    val_frac: float = 0.10  # test = remaining

    # training
    batch_size: int = 256
    epochs_n0: int = 6
    epochs_n1: int = 6
    epochs_n2: int = 6
    epochs_n3: int = 6
    epochs_n4: int = 8

    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    # labels / thresholds
    dir_threshold_std_frac: float = 0.25   # neutral band for N4 label generation
    min_dir_threshold: float = 5e-4

    # costs for N4 proxy training
    cost_wrong_dir: float = 1.0
    cost_wait: float = 0.15
    reward_scale: float = 1.0

    seed: int = 1337


CFG0 = CFG()


# =========================
# FEATURE KEYS (your schema)
# =========================
FEATURE_KEYS: List[str] = [
    "Open", "High", "Low", "Close", "Volume",
    "Quote_Volume", "Trades", "Taker_Buy_Base", "Taker_Buy_Quote",
    "ret", "log_ret",
    "rv_5", "rv_15", "rv_30", "rv_60", "rv_120", "rv_240", "rv_720", "rv_1440",
    "rv_ann_5", "rv_ann_15", "rv_ann_30", "rv_ann_60", "rv_ann_120", "rv_ann_240", "rv_ann_720", "rv_ann_1440",
    "ema_20", "ema_50", "ema_100", "ema_200",
    "dist_ema_20", "dist_ema_50", "dist_ema_100", "dist_ema_200",
    "atr_14", "atr_pct_14", "rsi_14",
    "var_99_60", "cvar_99_60", "var_99_240", "cvar_99_240", "var_99_1440", "cvar_99_1440",
]

TARGET_RET_KEY = "log_ret"
TARGET_RV_KEY = "rv_60"


# =========================
# SEED
# =========================
def set_seed(seed: int) -> None:
    import random
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)


# =========================
# SCALER (robust, train-only)
# =========================
class RobustTrainScaler:
    """
    Robust z-score: (x - median) / (1.4826 * MAD)
    Fit on TRAIN ONLY.
    """
    def __init__(self):
        self.median: Optional[np.ndarray] = None
        self.mad: Optional[np.ndarray] = None

    def fit(self, X2d: np.ndarray) -> None:
        med = np.median(X2d, axis=0).astype(np.float32)
        mad = np.median(np.abs(X2d - med), axis=0).astype(np.float32)
        mad = np.maximum(mad, 1e-6)
        self.median, self.mad = med, mad

    def transform(self, X2d: np.ndarray) -> np.ndarray:
        assert self.median is not None and self.mad is not None
        return (X2d - self.median) / (1.4826 * self.mad)

    def to_json(self) -> Dict:
        return {"median": self.median.tolist(), "mad": self.mad.tolist()}


# =========================
# WINDOWING
# =========================
def make_windows(
    X: np.ndarray,               # [T, F]
    y_ret: np.ndarray,           # [T]
    y_rv: np.ndarray,            # [T]
    lookback: int,
    horizon: int,
    stride: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns:
      Xw:      [N, L, F]
      yret_h:  [N, H]
      yrv_h:   [N, H]
      yrv_agg: [N]     (RMS over horizon)
      t_end:   [N]     end time index of window (for baselines / alignment)
    """
    T, F = X.shape
    N = 1 + (T - lookback - horizon) // stride
    if N <= 0:
        raise ValueError("Not enough rows for windowing.")

    Xw = np.zeros((N, lookback, F), dtype=np.float32)
    yret_h = np.zeros((N, horizon), dtype=np.float32)
    yrv_h = np.zeros((N, horizon), dtype=np.float32)
    yrv_agg = np.zeros((N,), dtype=np.float32)
    t_end = np.zeros((N,), dtype=np.int64)

    i = 0
    for s in range(0, T - lookback - horizon + 1, stride):
        Xw[i] = X[s : s + lookback]
        fut_ret = y_ret[s + lookback : s + lookback + horizon]
        fut_rv = y_rv[s + lookback : s + lookback + horizon]

        yret_h[i] = fut_ret
        yrv_h[i] = fut_rv
        yrv_agg[i] = float(np.sqrt(np.mean(fut_rv ** 2)))
        t_end[i] = s + lookback - 1
        i += 1

    return Xw, yret_h, yrv_h, yrv_agg, t_end


# =========================
# LABELS (proxy, trainable)
# =========================
def labels_direction_binary(yret_h: np.ndarray) -> np.ndarray:
    # 1 if sum >= 0 else 0
    s = np.sum(yret_h, axis=1)
    return (s >= 0.0).astype(np.int32)

def labels_tradeability(yret_h: np.ndarray, yrv_agg: np.ndarray) -> np.ndarray:
    """
    Tradeability proxy in [0,1]:
    - high absolute return magnitude
    - not too extreme volatility
    This is a proxy to train N0 before real RL.
    """
    mag = np.abs(np.sum(yret_h, axis=1))  # [N]
    # robust normalize
    mag_n = (mag - np.percentile(mag, 10)) / (np.percentile(mag, 90) - np.percentile(mag, 10) + 1e-8)
    mag_n = np.clip(mag_n, 0.0, 1.0)

    vol = yrv_agg
    vol_n = (vol - np.percentile(vol, 10)) / (np.percentile(vol, 90) - np.percentile(vol, 10) + 1e-8)
    vol_n = np.clip(vol_n, 0.0, 1.0)

    # prefer return magnitude and mid volatility (penalize extreme)
    vol_pen = 1.0 - np.abs(vol_n - 0.5) * 2.0
    vol_pen = np.clip(vol_pen, 0.0, 1.0)

    score = 0.65 * mag_n + 0.35 * vol_pen
    return score.astype(np.float32)

def labels_patterns_orthogonal(Xw: np.ndarray, feature_keys: List[str]) -> np.ndarray:
    """
    Orthogonal context detectors (proxy labels):
      P0: vol_spike      -> last rv_60 above its window q90
      P1: trend          -> abs(dist_ema_20) above window q80 AND stable direction
      P2: mean_revert    -> rsi extreme AND dist_ema_20 opposite sign to recent ret
      P3: compression    -> rv_60 below window q20
      P4: breakout_risk  -> atr_pct_14 above window q80
    Output:
      y_pat: [N, P] multi-label (0/1)
    """
    fmap = {k: i for i, k in enumerate(feature_keys)}
    idx_rv60 = fmap.get("rv_60", None)
    idx_dist = fmap.get("dist_ema_20", None)
    idx_rsi = fmap.get("rsi_14", None)
    idx_ret = fmap.get("log_ret", fmap.get("ret", None))
    idx_atr = fmap.get("atr_pct_14", None)

    if None in (idx_rv60, idx_dist, idx_rsi, idx_ret, idx_atr):
        raise RuntimeError("Missing required features for pattern labels.")

    N = Xw.shape[0]
    P = 5
    y = np.zeros((N, P), dtype=np.float32)

    for i in range(N):
        w = Xw[i]  # [L,F]
        rv = w[:, idx_rv60]
        dist = w[:, idx_dist]
        rsi = w[:, idx_rsi]
        ret = w[:, idx_ret]
        atr = w[:, idx_atr]

        rv_last = float(rv[-1])
        rv_q90 = float(np.percentile(rv, 90))
        rv_q20 = float(np.percentile(rv, 20))

        dist_last = float(dist[-1])
        dist_q80 = float(np.percentile(np.abs(dist), 80))

        rsi_last = float(rsi[-1])
        rsi_extreme = (rsi_last < 30.0) or (rsi_last > 70.0)

        # direction stability in window (few sign flips)
        sign = np.sign(ret)
        flips = float(np.sum(np.diff(sign) != 0))
        stability = 1.0 - flips / max(1.0, len(sign))

        atr_last = float(atr[-1])
        atr_q80 = float(np.percentile(atr, 80))

        # P0 vol_spike
        y[i, 0] = 1.0 if rv_last > rv_q90 else 0.0
        # P1 trend
        y[i, 1] = 1.0 if (abs(dist_last) > dist_q80 and stability > 0.55) else 0.0
        # P2 mean_revert
        anticorr = float(np.mean(np.sign(dist) != np.sign(ret)))
        y[i, 2] = 1.0 if (rsi_extreme and anticorr > 0.55) else 0.0
        # P3 compression
        y[i, 3] = 1.0 if rv_last < rv_q20 else 0.0
        # P4 breakout_risk
        y[i, 4] = 1.0 if atr_last > atr_q80 else 0.0

    return y


def labels_event_proxy(Xw: np.ndarray, feature_keys: List[str]) -> np.ndarray:
    """
    Event-based proxy labels (3 classes):
      0: NONE
      1: LIQUIDITY_SHOCK (volume spike)
      2: VOLATILITY_SHOCK (rv jump)
    """
    fmap = {k: i for i, k in enumerate(feature_keys)}
    idx_vol = fmap.get("Volume", None)
    idx_rv60 = fmap.get("rv_60", None)
    if None in (idx_vol, idx_rv60):
        raise RuntimeError("Missing Volume/rv_60 for event proxy labels.")

    N = Xw.shape[0]
    y = np.zeros((N,), dtype=np.int32)

    for i in range(N):
        w = Xw[i]
        vol = w[:, idx_vol]
        rv = w[:, idx_rv60]
        vol_last = float(vol[-1])
        rv_last = float(rv[-1])
        vol_q95 = float(np.percentile(vol, 95))
        rv_q95 = float(np.percentile(rv, 95))

        if vol_last > vol_q95 and rv_last <= rv_q95:
            y[i] = 1
        elif rv_last > rv_q95:
            y[i] = 2
        else:
            y[i] = 0

    return y


def labels_pairwise_proxy(yret_h: np.ndarray) -> np.ndarray:
    """
    Pairwise comparator proxy: scalar in [-1,1]
    - compare short horizon vs long horizon expected drift.
    """
    h = yret_h.shape[1]
    k = max(1, h // 3)
    short = np.sum(yret_h[:, :k], axis=1)
    long = np.sum(yret_h[:, k:], axis=1)
    score = np.tanh((short - long) / (np.std(short - long) + 1e-8))
    return score.astype(np.float32)


def labels_policy_proxy(
    yret_h: np.ndarray,
    cfg: CFG,
    train_std_h1: float,
) -> np.ndarray:
    """
    Proxy labels for N4 policy:
      BUY if expected return > +thr
      SELL if expected return < -thr
      WAIT otherwise
    thr based on train std (neutral band).
    """
    r = np.sum(yret_h, axis=1)
    thr = max(train_std_h1 * cfg.dir_threshold_std_frac, cfg.min_dir_threshold)

    y = np.zeros((len(r),), dtype=np.int32)  # 0 BUY, 1 SELL, 2 WAIT
    y[r > thr] = 0
    y[r < -thr] = 1
    y[(r >= -thr) & (r <= thr)] = 2
    return y


# =========================
# MODEL BUILDING BLOCKS
# =========================
def make_optimizer(cfg: CFG) -> tf.keras.optimizers.Optimizer:
    return tf.keras.optimizers.AdamW(
        learning_rate=cfg.lr,
        weight_decay=cfg.weight_decay,
        beta_1=0.9,
        beta_2=0.95,
        epsilon=1e-8,
        global_clipnorm=cfg.clip_norm,
    )

class TinyTCN(tf.keras.layers.Layer):
    def __init__(self, d_model: int = 64, n_layers: int = 3, dropout: float = 0.15, name="tiny_tcn"):
        super().__init__(name=name)
        self.inp = tf.keras.layers.Dense(d_model, activation="gelu")
        self.ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.blocks = []
        for i in range(n_layers):
            self.blocks.append(
                tf.keras.Sequential([
                    tf.keras.layers.Conv1D(d_model, 3, padding="causal", dilation_rate=2**i),
                    tf.keras.layers.LayerNormalization(epsilon=1e-6),
                    tf.keras.layers.Activation("gelu"),
                    tf.keras.layers.Dropout(dropout),
                ])
            )
        self.pool = tf.keras.layers.GlobalAveragePooling1D()

    def call(self, x, training=False):
        h = self.inp(x)
        h = self.ln(h)
        for b in self.blocks:
            h = b(h, training=training)
        return self.pool(h)

class MLP(tf.keras.layers.Layer):
    def __init__(self, d: int, dropout: float = 0.15, name="mlp"):
        super().__init__(name=name)
        self.net = tf.keras.Sequential([
            tf.keras.layers.Dense(d * 2, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(d, activation="gelu"),
        ])

    def call(self, x, training=False):
        return self.net(x, training=training)


# =========================
# N0 — GLOBAL GATING
# =========================
class GlobalGating(tf.keras.Model):
    """
    Inputs: window features [B,L,F]
    Outputs:
      - tradeability_score: [B,1] sigmoid
      - direction_probs:    [B,2] softmax
      - regime_probs:       [B,5] softmax (proxy)
    """
    def __init__(self, n_regimes: int = 5, d_model: int = 64):
        super().__init__()
        self.enc = TinyTCN(d_model=d_model, n_layers=3, dropout=0.15)
        self.shared = MLP(d_model, dropout=0.15)

        self.trade_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(1),
            tf.keras.layers.Activation("sigmoid", dtype="float32"),
        ])

        self.dir_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(2),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

        self.regime_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(n_regimes),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

    def call(self, x, training=False):
        z = self.enc(x, training=training)
        z = self.shared(z, training=training)
        return {
            "tradeability": self.trade_head(z, training=training),
            "direction": self.dir_head(z, training=training),
            "regime": self.regime_head(z, training=training),
        }


# =========================
# N1 — ORTHOGONAL CONTEXT DETECTORS
# =========================
class ContextDetectors(tf.keras.Model):
    """
    Multi-label pattern detector.
    Output: pattern_confidences [B,P] sigmoid
    """
    def __init__(self, n_patterns: int = 5, d_model: int = 64):
        super().__init__()
        self.enc = TinyTCN(d_model=d_model, n_layers=3, dropout=0.15)
        self.shared = MLP(d_model, dropout=0.15)
        self.head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(n_patterns),
            tf.keras.layers.Activation("sigmoid", dtype="float32"),
        ])

    def call(self, x, training=False):
        z = self.enc(x, training=training)
        z = self.shared(z, training=training)
        return {"patterns": self.head(z, training=training)}


# =========================
# N2 — CONDITIONAL SPECIALISTS
# =========================
class Specialists(tf.keras.Model):
    """
    One specialist per pattern.
    Each specialist predicts direction_probs (2) and expected_return (1).
    Trained only on samples where that pattern label is active.
    """
    def __init__(self, n_patterns: int = 5, d_model: int = 64):
        super().__init__()
        self.n_patterns = n_patterns
        self.encoders = [TinyTCN(d_model=d_model, n_layers=3, dropout=0.20, name=f"spec_enc_{i}") for i in range(n_patterns)]
        self.shareds = [MLP(d_model, dropout=0.20, name=f"spec_mlp_{i}") for i in range(n_patterns)]
        self.dir_heads = []
        self.ret_heads = []
        for i in range(n_patterns):
            self.dir_heads.append(tf.keras.Sequential([
                tf.keras.layers.Dense(d_model, activation="gelu"),
                tf.keras.layers.Dense(2),
                tf.keras.layers.Activation("softmax", dtype="float32"),
            ], name=f"spec_dir_{i}"))
            self.ret_heads.append(tf.keras.Sequential([
                tf.keras.layers.Dense(d_model, activation="gelu"),
                tf.keras.layers.Dense(1),
            ], name=f"spec_ret_{i}"))

    def call(self, x, training=False):
        outs = []
        for i in range(self.n_patterns):
            z = self.encoders[i](x, training=training)
            z = self.shareds[i](z, training=training)
            outs.append({
                "dir": self.dir_heads[i](z, training=training),
                "ret": tf.cast(self.ret_heads[i](z, training=training), tf.float32),
            })
        return outs


# =========================
# N3 — EVENT + PAIRWISE
# =========================
class EventClassifier(tf.keras.Model):
    def __init__(self, n_events: int = 3, d_model: int = 64):
        super().__init__()
        self.enc = TinyTCN(d_model=d_model, n_layers=3, dropout=0.15)
        self.shared = MLP(d_model, dropout=0.15)
        self.head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(n_events),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

    def call(self, x, training=False):
        z = self.enc(x, training=training)
        z = self.shared(z, training=training)
        return {"event_probs": self.head(z, training=training)}

class PairwiseComparator(tf.keras.Model):
    def __init__(self, d_model: int = 64):
        super().__init__()
        self.enc = TinyTCN(d_model=d_model, n_layers=3, dropout=0.15)
        self.shared = MLP(d_model, dropout=0.15)
        self.head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(1),
            tf.keras.layers.Activation("tanh", dtype="float32"),
        ])

    def call(self, x, training=False):
        z = self.enc(x, training=training)
        z = self.shared(z, training=training)
        return {"pairwise": self.head(z, training=training)}


# =========================
# N4 — META-DECIDER POLICY
# =========================
class MetaDecider(tf.keras.Model):
    """
    Inputs:
      - tradeability_score      [B,1]
      - pattern_confidences     [B,P]
      - direction_probs         [B,2]
      - pairwise_score          [B,1]
      - event_probs             [B,E]
      - recent_model_perf       [B,K]  (here simple: zeros or rolling stats placeholder)
    Output:
      - action_probs [B,3] (BUY, SELL, WAIT)
      - confidence   [B,1] (max prob)
    """
    def __init__(self, n_patterns: int = 5, n_events: int = 3, perf_dim: int = 8, d: int = 128):
        super().__init__()
        self.perf_dim = perf_dim
        self.net = tf.keras.Sequential([
            tf.keras.layers.Dense(d, activation="gelu"),
            tf.keras.layers.Dropout(0.15),
            tf.keras.layers.Dense(d, activation="gelu"),
            tf.keras.layers.Dropout(0.15),
            tf.keras.layers.Dense(3),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

        self.n_patterns = n_patterns
        self.n_events = n_events

    def call(self, inputs: Dict[str, tf.Tensor], training=False):
        trade = inputs["tradeability"]           # [B,1]
        pats = inputs["patterns"]                # [B,P]
        dire = inputs["direction"]               # [B,2]
        pair = inputs["pairwise"]                # [B,1]
        evnt = inputs["event_probs"]             # [B,E]
        perf = inputs["perf"]                    # [B,K]

        x = tf.concat([trade, pats, dire, pair, evnt, perf], axis=-1)
        probs = self.net(x, training=training)
        conf = tf.reduce_max(probs, axis=-1, keepdims=True)
        return {"probs": probs, "confidence": conf}


# =========================
# TRAINING HELPERS
# =========================
def ds_windows(Xw, batch, shuffle: bool, seed: int):
    ds = tf.data.Dataset.from_tensor_slices(Xw)
    if shuffle:
        ds = ds.shuffle(min(200_000, len(Xw)), seed=seed, reshuffle_each_iteration=True)
    ds = ds.batch(batch).prefetch(2)
    return ds

def to_tf(x: np.ndarray) -> tf.Tensor:
    return tf.convert_to_tensor(x, dtype=tf.float32)

def accuracy_sparse(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    yp = np.argmax(y_prob, axis=-1)
    return float(np.mean(yp == y_true))

def bce_multi(y_true, y_pred):
    return tf.reduce_mean(tf.keras.losses.binary_crossentropy(y_true, y_pred))

def mae(y_true, y_pred):
    return float(np.mean(np.abs(y_true - y_pred)))

def corr(a, b):
    sa = float(np.std(a)); sb = float(np.std(b))
    if sa < 1e-12 or sb < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


# =========================
# MAIN
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=str, default="sample.parquet")
    ap.add_argument("--out", type=str, default="runs")
    args = ap.parse_args()

    cfg = CFG0
    set_seed(cfg.seed)

    if not os.path.exists(args.data):
        raise FileNotFoundError(f"Missing {args.data}")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.out, run_id)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "saved_models"), exist_ok=True)

    # ----- LOAD -----
    df = pd.read_parquet(args.data)
    if "datetime" in df.columns:
        df = df.sort_values("datetime")
    df = df.reset_index(drop=True)

    # enforce columns
    missing = [k for k in FEATURE_KEYS + [TARGET_RET_KEY, TARGET_RV_KEY] if k not in df.columns]
    if missing:
        raise RuntimeError(f"Missing columns: {missing}")

    X_all = df[FEATURE_KEYS].astype("float32").to_numpy()
    y_ret = df[TARGET_RET_KEY].astype("float32").to_numpy()
    y_rv = df[TARGET_RV_KEY].astype("float32").to_numpy()

    # ----- WINDOWING (raw, unscaled labels) -----
    Xw_raw, yret_h_raw, yrv_h_raw, yrv_agg_raw, t_end = make_windows(
        X_all, y_ret, y_rv,
        lookback=cfg.lookback,
        horizon=cfg.horizon,
        stride=cfg.stride,
    )

    N = Xw_raw.shape[0]
    n_train = int(N * cfg.train_frac)
    n_val = int(N * cfg.val_frac)
    n_test = N - n_train - n_val

    idx_train = slice(0, n_train)
    idx_val = slice(n_train, n_train + n_val)
    idx_test = slice(n_train + n_val, N)

    # ----- SCALING (fit on TRAIN ONLY, flatten window) -----
    scaler = RobustTrainScaler()
    X_train_2d = Xw_raw[idx_train].reshape(-1, Xw_raw.shape[-1])
    scaler.fit(X_train_2d)

    def scale_Xw(Xw):
        X2 = Xw.reshape(-1, Xw.shape[-1])
        X2s = scaler.transform(X2)
        return X2s.reshape(Xw.shape).astype(np.float32)

    Xw = scale_Xw(Xw_raw)

    # ----- LABELS (generated from RAW targets) -----
    y_dir = labels_direction_binary(yret_h_raw)
    y_trade = labels_tradeability(yret_h_raw, yrv_agg_raw)  # [N]
    y_patterns = labels_patterns_orthogonal(Xw_raw, FEATURE_KEYS)  # [N,P]
    y_event = labels_event_proxy(Xw_raw, FEATURE_KEYS)            # [N]
    y_pairwise = labels_pairwise_proxy(yret_h_raw)                # [N] in [-1,1]

    train_std_h1 = float(np.std(np.sum(yret_h_raw[idx_train], axis=1)))
    y_policy = labels_policy_proxy(yret_h_raw, cfg, train_std_h1)  # 0/1/2

    # ----- DATASETS -----
    Xtr, Xva, Xte = Xw[idx_train], Xw[idx_val], Xw[idx_test]

    # ===== N0 TRAIN =====
    n0 = GlobalGating(n_regimes=5, d_model=64)
    opt0 = make_optimizer(cfg)

    bce = tf.keras.losses.BinaryCrossentropy()
    sce = tf.keras.losses.SparseCategoricalCrossentropy()

    ytr_trade = y_trade[idx_train].astype(np.float32)[:, None]
    yva_trade = y_trade[idx_val].astype(np.float32)[:, None]
    ytr_dir = y_dir[idx_train].astype(np.int32)
    yva_dir = y_dir[idx_val].astype(np.int32)

    # regime proxy: reuse patterns to create a cheap 5-class pseudo-regime
    # map:
    # 0 TREND -> pattern1
    # 1 MEAN_REVERT -> pattern2
    # 2 HIGH_VOL -> pattern0
    # 3 LOW_VOL -> pattern3
    # 4 RANGE -> else
    def pseudo_regime(y_pat):
        r = np.full((y_pat.shape[0],), 4, dtype=np.int32)
        r[y_pat[:, 1] > 0.5] = 0
        r[y_pat[:, 2] > 0.5] = 1
        r[y_pat[:, 0] > 0.5] = 2
        r[y_pat[:, 3] > 0.5] = 3
        return r

    ytr_reg = pseudo_regime(y_patterns[idx_train]).astype(np.int32)
    yva_reg = pseudo_regime(y_patterns[idx_val]).astype(np.int32)

    @tf.function
    def n0_step(x, yt_trade, yt_dir, yt_reg, training: bool):
        with tf.GradientTape() as tape:
            out = n0(x, training=training)
            loss_trade = bce(yt_trade, out["tradeability"])
            loss_dir = sce(yt_dir, out["direction"])
            loss_reg = sce(yt_reg, out["regime"])
            loss = 0.8 * loss_trade + 0.7 * loss_dir + 0.3 * loss_reg
        if training:
            grads = tape.gradient(loss, n0.trainable_variables)
            opt0.apply_gradients(zip(grads, n0.trainable_variables))
        return loss, out

    # train loop
    for ep in range(cfg.epochs_n0):
        ds = tf.data.Dataset.from_tensor_slices((Xtr, ytr_trade, ytr_dir, ytr_reg)).shuffle(200_000, seed=cfg.seed).batch(cfg.batch_size).prefetch(2)
        for xb, tb, db, rb in ds:
            n0_step(xb, tb, db, rb, True)

    # val
    out_va = n0(to_tf(Xva), training=False)
    n0_val_dir_acc = accuracy_sparse(yva_dir, out_va["direction"].numpy())
    n0_val_trade_mae = mae(yva_trade[:, 0], out_va["tradeability"].numpy()[:, 0])
    n0.save(os.path.join(out_dir, "saved_models", "level0_gating.keras"))

    # ===== N1 TRAIN =====
    n1 = ContextDetectors(n_patterns=y_patterns.shape[1], d_model=64)
    opt1 = make_optimizer(cfg)

    ytr_pat = y_patterns[idx_train].astype(np.float32)
    yva_pat = y_patterns[idx_val].astype(np.float32)

    @tf.function
    def n1_step(x, ypat, training: bool):
        with tf.GradientTape() as tape:
            out = n1(x, training=training)["patterns"]
            loss = bce_multi(ypat, out)
        if training:
            grads = tape.gradient(loss, n1.trainable_variables)
            opt1.apply_gradients(zip(grads, n1.trainable_variables))
        return loss, out

    for ep in range(cfg.epochs_n1):
        ds = tf.data.Dataset.from_tensor_slices((Xtr, ytr_pat)).shuffle(200_000, seed=cfg.seed).batch(cfg.batch_size).prefetch(2)
        for xb, yb in ds:
            n1_step(xb, yb, True)

    pat_va = n1(to_tf(Xva), training=False)["patterns"].numpy()
    n1_val_bce = float(np.mean(tf.keras.losses.binary_crossentropy(yva_pat, pat_va).numpy()))
    n1.save(os.path.join(out_dir, "saved_models", "level1_context.keras"))

    # ===== N2 TRAIN =====
    n2 = Specialists(n_patterns=y_patterns.shape[1], d_model=64)
    opt2 = make_optimizer(cfg)

    # Specialist targets:
    # - dir: y_dir (binary)
    # - ret: cumulative future return
    ytr_ret_sum = np.sum(yret_h_raw[idx_train], axis=1).astype(np.float32)[:, None]
    yva_ret_sum = np.sum(yret_h_raw[idx_val], axis=1).astype(np.float32)[:, None]

    @tf.function
    def n2_step(x, ydir_b, yret_sum_b, ypat_b, training: bool):
        with tf.GradientTape() as tape:
            outs = n2(x, training=training)  # list of dicts
            total = 0.0
            for i, o in enumerate(outs):
                # train only where pattern active
                mask = tf.cast(ypat_b[:, i] > 0.5, tf.float32)  # [B]
                m = tf.reduce_sum(mask) + 1e-6

                l_dir = tf.keras.losses.sparse_categorical_crossentropy(ydir_b, o["dir"])
                l_dir = tf.reduce_sum(l_dir * mask) / m

                l_ret = tf.keras.losses.huber(yret_sum_b, o["ret"], delta=1.0)
                l_ret = tf.reduce_sum(tf.squeeze(l_ret, axis=-1) * mask) / m

                total += 0.6 * l_dir + 0.4 * l_ret
        if training:
            grads = tape.gradient(total, n2.trainable_variables)
            opt2.apply_gradients(zip(grads, n2.trainable_variables))
        return total

    for ep in range(cfg.epochs_n2):
        ds = tf.data.Dataset.from_tensor_slices((Xtr, ytr_dir, ytr_ret_sum, ytr_pat)).shuffle(200_000, seed=cfg.seed).batch(cfg.batch_size).prefetch(2)
        for xb, db, rb, pb in ds:
            n2_step(xb, db, rb, pb, True)

    # quick val metric: average specialist dir accuracy on active samples
    outs_va = n2(to_tf(Xva), training=False)
    spec_dir_acc = []
    for i, o in enumerate(outs_va):
        pred = o["dir"].numpy()
        m = (yva_pat[:, i] > 0.5)
        if np.sum(m) < 100:
            continue
        spec_dir_acc.append(accuracy_sparse(yva_dir[m], pred[m]))
    n2_val_spec_dir_acc = float(np.mean(spec_dir_acc)) if spec_dir_acc else 0.0
    n2.save(os.path.join(out_dir, "saved_models", "level2_specialists.keras"))

    # ===== N3 TRAIN =====
    n3_event = EventClassifier(n_events=3, d_model=64)
    n3_pair = PairwiseComparator(d_model=64)
    opt3 = make_optimizer(cfg)

    ytr_event = y_event[idx_train].astype(np.int32)
    yva_event = y_event[idx_val].astype(np.int32)
    ytr_pair = y_pairwise[idx_train].astype(np.float32)[:, None]
    yva_pair = y_pairwise[idx_val].astype(np.float32)[:, None]

    @tf.function
    def n3_step(x, yev, ypair, training: bool):
        with tf.GradientTape() as tape:
            ev = n3_event(x, training=training)["event_probs"]
            pr = n3_pair(x, training=training)["pairwise"]
            loss_ev = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(yev, ev))
            loss_pr = tf.reduce_mean(tf.keras.losses.huber(ypair, pr, delta=0.25))
            loss = 0.7 * loss_ev + 0.3 * loss_pr
        if training:
            vars_ = n3_event.trainable_variables + n3_pair.trainable_variables
            grads = tape.gradient(loss, vars_)
            opt3.apply_gradients(zip(grads, vars_))
        return loss

    for ep in range(cfg.epochs_n3):
        ds = tf.data.Dataset.from_tensor_slices((Xtr, ytr_event, ytr_pair)).shuffle(200_000, seed=cfg.seed).batch(cfg.batch_size).prefetch(2)
        for xb, yeb, ypb in ds:
            n3_step(xb, yeb, ypb, True)

    ev_va = n3_event(to_tf(Xva), training=False)["event_probs"].numpy()
    pr_va = n3_pair(to_tf(Xva), training=False)["pairwise"].numpy()[:, 0]
    n3_val_event_acc = accuracy_sparse(yva_event, ev_va)
    n3_val_pair_mae = mae(yva_pair[:, 0], pr_va)
    n3_event.save(os.path.join(out_dir, "saved_models", "level3_event.keras"))
    n3_pair.save(os.path.join(out_dir, "saved_models", "level3_pairwise.keras"))

    # ===== N4 TRAIN =====
    n4 = MetaDecider(n_patterns=y_patterns.shape[1], n_events=3, perf_dim=8, d=128)
    opt4 = make_optimizer(cfg)

    ytr_pol = y_policy[idx_train].astype(np.int32)
    yva_pol = y_policy[idx_val].astype(np.int32)

    # perf placeholder (zeros) — replace with rolling online perf later
    def perf_block(n):
        return np.zeros((n, 8), dtype=np.float32)

    @tf.function
    def n4_step(batch_x, y_pol, training: bool):
        with tf.GradientTape() as tape:
            # gather inputs from trained models (N0,N1,N3) frozen during N4 training
            o0 = n0(batch_x, training=False)
            o1 = n1(batch_x, training=False)
            oev = n3_event(batch_x, training=False)
            opr = n3_pair(batch_x, training=False)

            inputs = {
                "tradeability": tf.cast(o0["tradeability"], tf.float32),
                "patterns": tf.cast(o1["patterns"], tf.float32),
                "direction": tf.cast(o0["direction"], tf.float32),
                "pairwise": tf.cast(opr["pairwise"], tf.float32),
                "event_probs": tf.cast(oev["event_probs"], tf.float32),
                "perf": tf.zeros((tf.shape(batch_x)[0], 8), dtype=tf.float32),
            }
            out = n4(inputs, training=training)["probs"]  # [B,3]

            # cost-aware CE (proxy)
            ce = tf.keras.losses.sparse_categorical_crossentropy(y_pol, out)

            # penalize WAIT less/more via sample weights
            # (BUY/SELL errors expensive, WAIT cheap)
            w = tf.ones_like(tf.cast(y_pol, tf.float32))
            w = tf.where(y_pol == 2, w * cfg.cost_wait, w)
            loss = tf.reduce_mean(ce * w)

        if training:
            grads = tape.gradient(loss, n4.trainable_variables)
            opt4.apply_gradients(zip(grads, n4.trainable_variables))
        return loss

    for ep in range(cfg.epochs_n4):
        ds = tf.data.Dataset.from_tensor_slices((Xtr, ytr_pol)).shuffle(200_000, seed=cfg.seed).batch(cfg.batch_size).prefetch(2)
        for xb, yb in ds:
            n4_step(xb, yb, True)

    # val eval N4
    o0v = n0(to_tf(Xva), training=False)
    o1v = n1(to_tf(Xva), training=False)
    oevv = n3_event(to_tf(Xva), training=False)
    oprv = n3_pair(to_tf(Xva), training=False)

    inputs_v = {
        "tradeability": tf.cast(o0v["tradeability"], tf.float32),
        "patterns": tf.cast(o1v["patterns"], tf.float32),
        "direction": tf.cast(o0v["direction"], tf.float32),
        "pairwise": tf.cast(oprv["pairwise"], tf.float32),
        "event_probs": tf.cast(oevv["event_probs"], tf.float32),
        "perf": tf.convert_to_tensor(perf_block(len(Xva)), dtype=tf.float32),
    }
    pol_va = n4(inputs_v, training=False)["probs"].numpy()
    n4_val_acc = accuracy_sparse(yva_pol, pol_va)
    n4.save(os.path.join(out_dir, "saved_models", "level4_policy.keras"))

    # ===== TEST (end-to-end forward, metrics only) =====
    Xts = to_tf(Xte)
    o0t = n0(Xts, training=False)
    o1t = n1(Xts, training=False)
    oevt = n3_event(Xts, training=False)
    oprt = n3_pair(Xts, training=False)
    inputs_t = {
        "tradeability": tf.cast(o0t["tradeability"], tf.float32),
        "patterns": tf.cast(o1t["patterns"], tf.float32),
        "direction": tf.cast(o0t["direction"], tf.float32),
        "pairwise": tf.cast(oprt["pairwise"], tf.float32),
        "event_probs": tf.cast(oevt["event_probs"], tf.float32),
        "perf": tf.zeros((tf.shape(Xts)[0], 8), dtype=tf.float32),
    }
    pol_te = n4(inputs_t, training=False)["probs"].numpy()
    yte_pol = y_policy[idx_test].astype(np.int32)
    n4_test_acc = accuracy_sparse(yte_pol, pol_te)

    # ===== SAVE METRICS =====
    metrics = {
        "data": {
            "rows": int(len(df)),
            "windows": int(N),
            "train": int(n_train),
            "val": int(n_val),
            "test": int(n_test),
            "lookback": cfg.lookback,
            "horizon": cfg.horizon,
            "stride": cfg.stride,
        },
        "level0_global_gating": {
            "val_direction_acc": n0_val_dir_acc,
            "val_tradeability_mae": n0_val_trade_mae,
        },
        "level1_context": {
            "val_bce": n1_val_bce,
        },
        "level2_specialists": {
            "val_avg_dir_acc_on_active_patterns": n2_val_spec_dir_acc,
        },
        "level3_parallel": {
            "val_event_acc": n3_val_event_acc,
            "val_pairwise_mae": n3_val_pair_mae,
        },
        "level4_policy": {
            "val_acc_proxy": n4_val_acc,
            "test_acc_proxy": n4_test_acc,
        },
    }

    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    with open(os.path.join(out_dir, "scaler.json"), "w") as f:
        json.dump(scaler.to_json(), f)

    # print summary
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
