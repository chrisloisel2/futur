from __future__ import annotations

import os
import io
import json
import math
import gzip
import time
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple, Iterable, Optional

import numpy as np
import tensorflow as tf

# =========================
# PERF / RUNTIME SETTINGS
# =========================
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
try:
    tf.config.optimizer.set_jit(True)  # XLA
except Exception:
    pass

# Mixed precision (si GPU compatible)
try:
    from tensorflow.keras import mixed_precision
    mixed_precision.set_global_policy("mixed_float16")
except Exception:
    pass


# =========================
# CONFIG
# =========================
@dataclass(frozen=True)
class TRMConfig:
    # Data
    lookback: int = 256              # fenêtre d’entrée
    horizon: int = 12                # steps futurs à prédire
    stride: int = 1
    batch_size: int = 256
    shuffle_buffer: int = 50_000
    prefetch: int = 2
    # Model (tiny)
    d_model: int = 128
    n_heads: int = 4
    d_ff: int = 256
    dropout: float = 0.10
    # Recursive memory
    mem_dim: int = 128
    mem_update_iters: int = 2        # récursif: raffine l’état
    # Training
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0
    epochs: int = 20
    steps_per_epoch: int = 2000      # adapter à ton volume
    val_steps: int = 200
    seed: int = 1337
    # Targets weighting
    w_ret: float = 1.0
    w_dir: float = 0.6
    w_rv: float = 0.4


CONFIG = TRMConfig()


# =========================
# FEATURE SPEC
# =========================
# On prend un set stable: prix/volume + tes features.
# Tu peux en enlever/ajouter sans casser le pipeline.
FEATURE_KEYS: List[str] = [
    # OHLCV
    "Open", "High", "Low", "Close", "Volume",
    "Quote_Volume", "Trades", "Taker_Buy_Base", "Taker_Buy_Quote",
    # returns / risk
    "ret", "log_ret",
    "rv_5", "rv_15", "rv_30", "rv_60", "rv_120", "rv_240", "rv_720", "rv_1440",
    "rv_ann_5", "rv_ann_15", "rv_ann_30", "rv_ann_60", "rv_ann_120", "rv_ann_240", "rv_ann_720", "rv_ann_1440",
    # trend
    "ema_20", "ema_50", "ema_100", "ema_200",
    "dist_ema_20", "dist_ema_50", "dist_ema_100", "dist_ema_200",
    # vol/oscillator
    "atr_14", "atr_pct_14", "rsi_14",
    # tail risk
    "var_99_60", "cvar_99_60", "var_99_240", "cvar_99_240", "var_99_1440", "cvar_99_1440",
]

# Targets: prédire ret/log_ret sur horizon + direction (3 classes) + rv_60 future (proxy vol)
TARGET_RET_KEY = "log_ret"   # plus stable numériquement que "ret"
TARGET_RV_KEY = "rv_60"


# =========================
# S3 LOADER (JSONL / JSONL.GZ)
# =========================
def iter_s3_jsonl(
    bucket: str,
    prefix: str,
    region: Optional[str] = None,
    aws_profile: Optional[str] = None,
    max_keys: Optional[int] = None,
) -> Iterable[Dict]:
    """
    Stream des objets JSON depuis S3 (JSONL ou JSONL.GZ).
    - bucket: nom du bucket
    - prefix: chemin (dossier) ou pattern logique (ex: "btc/1m/")
    - max_keys: limiter pour tests
    """
    import boto3

    if aws_profile:
        import boto3.session
        session = boto3.session.Session(profile_name=aws_profile, region_name=region)
        s3 = session.client("s3")
    else:
        s3 = boto3.client("s3", region_name=region)

    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents", [])
        for obj in contents:
            key = obj["Key"]
            if key.endswith("/"):
                continue

            resp = s3.get_object(Bucket=bucket, Key=key)
            body = resp["Body"].read()

            is_gz = key.endswith(".gz")
            if is_gz:
                with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as gz:
                    for line in gz:
                        line = line.strip()
                        if not line:
                            continue
                        yield json.loads(line.decode("utf-8"))
            else:
                for line in body.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line.decode("utf-8"))

            n += 1
            if max_keys and n >= max_keys:
                return


# =========================
# ROBUST STREAMING SCALER
# =========================
class RunningRobustScaler:
    """
    Approx robuste: médiane + MAD via sous-échantillonnage réservoir (reservoir sampling).
    Suffisant pour normaliser sans charger 10 ans en RAM.
    """

    def __init__(self, feature_dim: int, reservoir_size: int = 200_000, seed: int = 1337):
        self.feature_dim = feature_dim
        self.reservoir_size = reservoir_size
        self.rng = random.Random(seed)
        self._n_seen = 0
        self._reservoir = np.zeros((reservoir_size, feature_dim), dtype=np.float32)
        self._filled = 0

        self.median = np.zeros((feature_dim,), dtype=np.float32)
        self.mad = np.ones((feature_dim,), dtype=np.float32)

    def update(self, x: np.ndarray) -> None:
        # x: [feature_dim]
        self._n_seen += 1
        if self._filled < self.reservoir_size:
            self._reservoir[self._filled] = x
            self._filled += 1
            return

        j = self.rng.randint(0, self._n_seen - 1)
        if j < self.reservoir_size:
            self._reservoir[j] = x

    def finalize(self) -> None:
        data = self._reservoir[: self._filled]
        if data.shape[0] < 10:
            self.median = np.zeros((self.feature_dim,), dtype=np.float32)
            self.mad = np.ones((self.feature_dim,), dtype=np.float32)
            return

        med = np.median(data, axis=0).astype(np.float32)
        mad = np.median(np.abs(data - med), axis=0).astype(np.float32)
        mad = np.maximum(mad, 1e-6)
        self.median = med
        self.mad = mad

    def transform(self, x: np.ndarray) -> np.ndarray:
        # robust z-score
        return (x - self.median) / (1.4826 * self.mad)


# =========================
# DATASET BUILDER
# =========================
def record_to_feature_vector(rec: Dict) -> np.ndarray:
    v = []
    for k in FEATURE_KEYS:
        val = rec.get(k, 0.0)
        # sécurise types
        if val is None:
            val = 0.0
        v.append(float(val))
    return np.asarray(v, dtype=np.float32)


def build_numpy_from_stream(
    stream: Iterable[Dict],
    scaler: Optional[RunningRobustScaler] = None,
    limit_rows: Optional[int] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Retourne:
    X_all: [T, F]
    y_ret: [T]  (log_ret)
    y_rv:  [T]  (rv_60)
    """
    xs = []
    y_ret = []
    y_rv = []

    n = 0
    for rec in stream:
        x = record_to_feature_vector(rec)
        xs.append(x)
        y_ret.append(float(rec.get(TARGET_RET_KEY, 0.0)))
        y_rv.append(float(rec.get(TARGET_RV_KEY, 0.0)))

        if scaler is not None:
            scaler.update(x)

        n += 1
        if limit_rows and n >= limit_rows:
            break

    X_all = np.stack(xs, axis=0) if xs else np.zeros((0, len(FEATURE_KEYS)), np.float32)
    y_ret = np.asarray(y_ret, dtype=np.float32)
    y_rv = np.asarray(y_rv, dtype=np.float32)
    return X_all, y_ret, y_rv


def make_windows(
    X: np.ndarray,
    y_ret: np.ndarray,
    y_rv: np.ndarray,
    lookback: int,
    horizon: int,
    stride: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Génère:
    Xw: [N, lookback, F]
    y_ret_h: [N, horizon]      (log_ret futurs)
    y_dir: [N]                 (0 down, 1 flat, 2 up) basé sur somme log_ret
    y_rv_h: [N, horizon]       (rv_60 futurs)
    """
    T = X.shape[0]
    F = X.shape[1]
    N = 1 + (T - lookback - horizon) // stride
    if N <= 0:
        raise ValueError("Pas assez de données pour windowing.")

    Xw = np.zeros((N, lookback, F), dtype=np.float32)
    y_ret_h = np.zeros((N, horizon), dtype=np.float32)
    y_rv_h = np.zeros((N, horizon), dtype=np.float32)
    y_dir = np.zeros((N,), dtype=np.int32)

    idx = 0
    for s in range(0, T - lookback - horizon + 1, stride):
        Xw[idx] = X[s : s + lookback]
        fut_ret = y_ret[s + lookback : s + lookback + horizon]
        fut_rv = y_rv[s + lookback : s + lookback + horizon]
        y_ret_h[idx] = fut_ret
        y_rv_h[idx] = fut_rv

        # direction = signe du cumul futur (log_ret)
        cum = float(np.sum(fut_ret))
        if cum > 1e-4:
            y_dir[idx] = 2
        elif cum < -1e-4:
            y_dir[idx] = 0
        else:
            y_dir[idx] = 1

        idx += 1

    return Xw, y_ret_h, y_dir, y_rv_h


def tf_dataset_from_windows(
    Xw: np.ndarray,
    y_ret_h: np.ndarray,
    y_dir: np.ndarray,
    y_rv_h: np.ndarray,
    batch_size: int,
    shuffle_buffer: int,
    training: bool,
    prefetch: int,
) -> tf.data.Dataset:
    ds = tf.data.Dataset.from_tensor_slices((Xw, {"ret": y_ret_h, "dir": y_dir, "rv": y_rv_h}))
    if training:
        ds = ds.shuffle(min(shuffle_buffer, Xw.shape[0]), reshuffle_each_iteration=True)
    ds = ds.batch(batch_size, drop_remainder=training)
    ds = ds.prefetch(prefetch)
    return ds


# =========================
# MODEL: TINY RECURSIVE MULTI-MODULAR
# =========================
class GEGLU(tf.keras.layers.Layer):
    def __init__(self, d_ff: int):
        super().__init__()
        self.proj = tf.keras.layers.Dense(d_ff * 2)

    def call(self, x):
        a, b = tf.split(self.proj(x), 2, axis=-1)
        return a * tf.nn.gelu(b)


class TransformerBlock(tf.keras.layers.Layer):
    def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float):
        super().__init__()
        self.ln1 = tf.keras.layers.LayerNormalization(epsilon=1e-5)
        self.ln2 = tf.keras.layers.LayerNormalization(epsilon=1e-5)

        self.attn = tf.keras.layers.MultiHeadAttention(
            num_heads=n_heads,
            key_dim=d_model // n_heads,
            dropout=dropout,
        )
        self.drop1 = tf.keras.layers.Dropout(dropout)

        self.ff = tf.keras.Sequential([
            GEGLU(d_ff),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(d_model),
        ])
        self.drop2 = tf.keras.layers.Dropout(dropout)

    def call(self, x, training=False):
        h = self.ln1(x)
        a = self.attn(h, h, training=training)
        x = x + self.drop1(a, training=training)
        h = self.ln2(x)
        f = self.ff(h, training=training)
        x = x + self.drop2(f, training=training)
        return x


class TinyRecursiveMemory(tf.keras.layers.Layer):
    """
    Module récursif:
    - Agrège une séquence en un état mem
    - Raffine mem plusieurs itérations (mem_update_iters)
    - Injecte mem dans la séquence (conditioning)
    """
    def __init__(self, d_model: int, mem_dim: int, iters: int, dropout: float):
        super().__init__()
        self.mem_dim = mem_dim
        self.iters = iters

        self.to_mem = tf.keras.layers.Dense(mem_dim)
        self.mem_ln = tf.keras.layers.LayerNormalization(epsilon=1e-5)

        self.update_gate = tf.keras.Sequential([
            tf.keras.layers.Dense(mem_dim * 2),
            tf.keras.layers.Dropout(dropout),
        ])
        self.update_ff = tf.keras.Sequential([
            tf.keras.layers.Dense(mem_dim * 4, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(mem_dim),
        ])

        self.inject = tf.keras.layers.Dense(d_model)
        self.inject_ln = tf.keras.layers.LayerNormalization(epsilon=1e-5)

    def call(self, seq, training=False):
        # seq: [B, L, d_model]
        pooled = tf.reduce_mean(seq, axis=1)             # [B, d_model]
        mem = self.to_mem(pooled)                        # [B, mem_dim]
        mem = self.mem_ln(mem)

        # Recursive refinement
        for _ in range(self.iters):
            # gating: [B, 2*mem_dim] -> split
            g = self.update_gate(mem, training=training)
            u, r = tf.split(g, 2, axis=-1)
            u = tf.sigmoid(u)
            r = tf.tanh(r)

            cand = self.update_ff(mem * r, training=training)
            mem = mem + u * cand
            mem = self.mem_ln(mem)

        # Inject mem back into sequence (conditioning)
        mem_proj = self.inject(mem)[:, None, :]          # [B, 1, d_model]
        seq = self.inject_ln(seq + mem_proj)
        return seq, mem


class TinyRecursiveMarketModel(tf.keras.Model):
    """
    Entrée: [B, L, F]
    Sorties:
      - ret: [B, H]   (log_ret futurs)
      - dir: [B, 3]   (down/flat/up)
      - rv:  [B, H]   (rv_60 futurs)
    """
    def __init__(self, cfg: TRMConfig, feature_dim: int):
        super().__init__()
        self.cfg = cfg

        self.in_proj = tf.keras.layers.Dense(cfg.d_model)
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-5)
        self.in_drop = tf.keras.layers.Dropout(cfg.dropout)

        # 2 blocs transformer tiny
        self.block1 = TransformerBlock(cfg.d_model, cfg.n_heads, cfg.d_ff, cfg.dropout)
        self.block2 = TransformerBlock(cfg.d_model, cfg.n_heads, cfg.d_ff, cfg.dropout)

        # module récursif
        self.mem = TinyRecursiveMemory(cfg.d_model, cfg.mem_dim, cfg.mem_update_iters, cfg.dropout)

        # pooling multi-échelles
        self.pool_ln = tf.keras.layers.LayerNormalization(epsilon=1e-5)

        # heads
        self.head_shared = tf.keras.Sequential([
            tf.keras.layers.Dense(cfg.d_ff, activation="gelu"),
            tf.keras.layers.Dropout(cfg.dropout),
            tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
        ])

        # ret head (régression)
        self.ret_head = tf.keras.Sequential([
            tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
            tf.keras.layers.Dense(cfg.horizon),
        ])

        # rv head (régression positive)
        self.rv_head = tf.keras.Sequential([
            tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
            tf.keras.layers.Dense(cfg.horizon),
            tf.keras.layers.Activation("softplus"),
        ])

        # dir head (classification)
        self.dir_head = tf.keras.Sequential([
            tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
            tf.keras.layers.Dropout(cfg.dropout),
            tf.keras.layers.Dense(3),
            tf.keras.layers.Activation("softmax", dtype="float32"),  # force float32
        ])

    def call(self, x, training=False):
        # x: [B, L, F]
        h = self.in_proj(x)
        h = self.in_ln(h)
        h = self.in_drop(h, training=training)

        h = self.block1(h, training=training)
        h, mem = self.mem(h, training=training)
        h = self.block2(h, training=training)

        # pooling: mean + last token
        mean = tf.reduce_mean(h, axis=1)
        last = h[:, -1, :]
        pooled = self.pool_ln(tf.concat([mean, last, mem], axis=-1))

        shared = self.head_shared(pooled, training=training)

        y_ret = self.ret_head(shared, training=training)     # [B, H]
        y_rv = self.rv_head(shared, training=training)       # [B, H]
        y_dir = self.dir_head(shared, training=training)     # [B, 3]

        # ret en float32 pour stabilité loss
        y_ret = tf.cast(y_ret, tf.float32)
        y_rv = tf.cast(y_rv, tf.float32)

        return {"ret": y_ret, "dir": y_dir, "rv": y_rv}


# =========================
# OPTIMIZER: ADAMW + COSINE
# =========================
class CosineWarmup(tf.keras.optimizers.schedules.LearningRateSchedule):
    def __init__(self, base_lr: float, warmup_steps: int, total_steps: int):
        super().__init__()
        self.base_lr = base_lr
        self.warmup_steps = max(1, warmup_steps)
        self.total_steps = max(self.warmup_steps + 1, total_steps)

    def __call__(self, step):
        step = tf.cast(step, tf.float32)
        warm = tf.minimum(1.0, step / self.warmup_steps)
        progress = tf.minimum(1.0, (step - self.warmup_steps) / (self.total_steps - self.warmup_steps))
        cosine = 0.5 * (1.0 + tf.cos(math.pi * progress))
        return self.base_lr * warm * cosine


def make_optimizer(cfg: TRMConfig) -> tf.keras.optimizers.Optimizer:
    total_steps = cfg.epochs * cfg.steps_per_epoch
    lr = CosineWarmup(cfg.lr, warmup_steps=int(0.05 * total_steps), total_steps=total_steps)
    opt = tf.keras.optimizers.AdamW(
        learning_rate=lr,
        weight_decay=cfg.weight_decay,
        beta_1=0.9,
        beta_2=0.95,
        epsilon=1e-8,
        global_clipnorm=cfg.clip_norm,
    )
    return opt


# =========================
# LOSSES / METRICS
# =========================
def huber_loss(y_true, y_pred, delta=1.0):
    return tf.keras.losses.Huber(delta=delta)(y_true, y_pred)

def rv_loss(y_true, y_pred):
    # log-space mse pour stabiliser la volatilité
    y_true = tf.maximum(y_true, 1e-8)
    y_pred = tf.maximum(y_pred, 1e-8)
    return tf.reduce_mean(tf.square(tf.math.log(y_pred) - tf.math.log(y_true)))

def directional_accuracy(y_true_dir, y_pred_probs):
    y_pred = tf.argmax(y_pred_probs, axis=-1, output_type=tf.int32)
    return tf.reduce_mean(tf.cast(tf.equal(tf.cast(y_true_dir, tf.int32), y_pred), tf.float32))


# =========================
# TRAIN / EVAL
# =========================
def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)


def train_trm(
    Xw_train: np.ndarray,
    yret_train: np.ndarray,
    ydir_train: np.ndarray,
    yrv_train: np.ndarray,
    Xw_val: np.ndarray,
    yret_val: np.ndarray,
    ydir_val: np.ndarray,
    yrv_val: np.ndarray,
    cfg: TRMConfig,
    out_dir: str = "trm_out",
) -> TinyRecursiveMarketModel:
    os.makedirs(out_dir, exist_ok=True)

    ds_train = tf_dataset_from_windows(
        Xw_train, yret_train, ydir_train, yrv_train,
        batch_size=cfg.batch_size,
        shuffle_buffer=cfg.shuffle_buffer,
        training=True,
        prefetch=cfg.prefetch,
    )
    ds_val = tf_dataset_from_windows(
        Xw_val, yret_val, ydir_val, yrv_val,
        batch_size=cfg.batch_size,
        shuffle_buffer=1,
        training=False,
        prefetch=cfg.prefetch,
    )

    model = TinyRecursiveMarketModel(cfg, feature_dim=Xw_train.shape[-1])
    opt = make_optimizer(cfg)

    # losses pondérées
    losses = {
        "ret": lambda yt, yp: huber_loss(yt, yp, delta=1.0),
        "dir": tf.keras.losses.SparseCategoricalCrossentropy(),
        "rv": rv_loss,
    }
    loss_weights = {"ret": cfg.w_ret, "dir": cfg.w_dir, "rv": cfg.w_rv}

    # metrics
    metrics = {
        "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
        "dir": [tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
        "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    }

    model.compile(optimizer=opt, loss=losses, loss_weights=loss_weights, metrics=metrics)

    cbs = [
        tf.keras.callbacks.ModelCheckpoint(
            filepath=os.path.join(out_dir, "best.keras"),
            monitor="val_loss",
            save_best_only=True,
            save_weights_only=False,
        ),
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=4,
            restore_best_weights=True,
        ),
        tf.keras.callbacks.TerminateOnNaN(),
        tf.keras.callbacks.CSVLogger(os.path.join(out_dir, "train_log.csv")),
    ]

    model.fit(
        ds_train,
        validation_data=ds_val,
        epochs=cfg.epochs,
        steps_per_epoch=cfg.steps_per_epoch,
        validation_steps=cfg.val_steps,
        callbacks=cbs,
        verbose=1,
    )

    model.save(os.path.join(out_dir, "final.keras"))
    return model


# =========================
# END-TO-END MAIN
# =========================
def main():
    """
    Exemple d’usage:
    - Charge depuis S3
    - Fit scaler robuste
    - Transform + windowing
    - Split train/val temporel
    - Train
    """

    set_seed(CONFIG.seed)

    # ---- PARAMS S3 (à mettre en env pour éviter hardcode)
    # export S3_BUCKET="..."
    # export S3_PREFIX="btc/1m/"
    # export AWS_PROFILE="default" (optionnel)
    bucket = os.environ.get("S3_BUCKET", "").strip()
    prefix = os.environ.get("S3_PREFIX", "").strip()
    aws_profile = os.environ.get("AWS_PROFILE", "").strip() or None
    region = os.environ.get("AWS_REGION", "").strip() or None

    if not bucket or not prefix:
        raise RuntimeError("Définis S3_BUCKET et S3_PREFIX (env vars).")

    # 1) pass 1: fit scaler robuste
    scaler = RunningRobustScaler(feature_dim=len(FEATURE_KEYS), reservoir_size=200_000, seed=CONFIG.seed)
    stream1 = iter_s3_jsonl(bucket=bucket, prefix=prefix, region=region, aws_profile=aws_profile)
    # on lit tout pour scaler (tu peux limiter si tu veux)
    X_all, y_ret, y_rv = build_numpy_from_stream(stream1, scaler=scaler, limit_rows=None)
    scaler.finalize()

    # 2) transform
    X_all = scaler.transform(X_all)

    # 3) windowing
    Xw, yret_h, ydir, yrv_h = make_windows(
        X_all, y_ret, y_rv,
        lookback=CONFIG.lookback,
        horizon=CONFIG.horizon,
        stride=CONFIG.stride,
    )

    # 4) split temporel (pas de shuffle global)
    n = Xw.shape[0]
    split = int(n * 0.9)
    Xw_train, Xw_val = Xw[:split], Xw[split:]
    yret_train, yret_val = yret_h[:split], yret_h[split:]
    ydir_train, ydir_val = ydir[:split], ydir[split:]
    yrv_train, yrv_val = yrv_h[:split], yrv_h[split:]

    # 5) train
    model = train_trm(
        Xw_train, yret_train, ydir_train, yrv_train,
        Xw_val, yret_val, ydir_val, yrv_val,
        cfg=CONFIG,
        out_dir="trm_out",
    )

    # 6) quick inference
    sample = Xw_val[:8]
    out = model(sample, training=False)
    print("ret_pred[0]:", out["ret"][0].numpy())
    print("dir_pred[0]:", out["dir"][0].numpy())
    print("rv_pred[0]:", out["rv"][0].numpy())


if __name__ == "__main__":
    main()
