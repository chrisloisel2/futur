from __future__ import annotations
import os, time, argparse
from dataclasses import dataclass
from typing import List

import numpy as np
import tensorflow as tf

from common.io_s3 import count_total_windows
from common.scaler import RobustScaler
from common.windows import iter_windows_common
from common.logger import JsonlLogger
from common.metrics import safe_mean

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from level_4.PairwiseComparator import PairwiseComparator


@dataclass(frozen=True)
class CFG:
    lookback: int = 256
    horizon: int = 12
    stride: int = 1

    train_frac: float = 0.80
    val_frac: float = 0.10

    batch_size: int = 64
    epochs: int = 30

    lr: float = 3e-4
    min_lr: float = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    reduce_lr_patience: int = 3
    reduce_lr_factor: float = 0.5
    early_stop_patience: int = 6
    min_delta: float = 1e-4

    # label thresholds
    strong_edge: float = 0.8

    seed: int = 1337


FEATURE_KEYS = [
    "Open","High","Low","Close","Volume","Quote_Volume",
    "ret","log_ret","rv_15","rv_60","rv_240",
    "ema_20","ema_50","ema_200",
    "atr_14","rsi_14",
]
RET_KEY = "log_ret"
RV_KEY  = "rv_60"
CLOSE_KEY = "Close"


def set_seed(seed: int):
    np.random.seed(seed)
    tf.random.set_seed(seed)


class WindowEmbedder(tf.keras.Model):
    def __init__(self, d_model=64):
        super().__init__()
        self.net = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.LayerNormalization(),
            tf.keras.layers.Conv1D(d_model, 3, padding="causal", activation="gelu"),
            tf.keras.layers.GlobalAveragePooling1D(),
            tf.keras.layers.Dense(d_model, activation="gelu"),
        ])

    def call(self, Xw, training=False):
        return self.net(Xw, training=training)  # [B,D]


def make_ds_pairs(base, symbol, quote, interval, years: List[int], cfg: CFG, scaler, start, end):
    def gen():
        it = iter_windows_common(
            base, symbol, quote, interval, years,
            FEATURE_KEYS, RET_KEY, RV_KEY, CLOSE_KEY,
            cfg.lookback, cfg.horizon, scaler,
            start=start, end=end, stride=cfg.stride
        )
        prev = None
        for s in it:
            if prev is None:
                prev = s
                continue
            edge_now = float(s["edge"])
            edge_ref = float(prev["edge"])

            # label: 0 consistent, 1 weak, 2 contradict
            if abs(edge_now) >= cfg.strong_edge and abs(edge_ref) >= cfg.strong_edge:
                if np.sign(edge_now) == np.sign(edge_ref):
                    y = 0
                else:
                    y = 2
            else:
                y = 1

            yield prev["Xw"], s["Xw"], np.int32(y)
            prev = s

    sig = (
        tf.TensorSpec((cfg.lookback, len(FEATURE_KEYS)), tf.float32),
        tf.TensorSpec((cfg.lookback, len(FEATURE_KEYS)), tf.float32),
        tf.TensorSpec((), tf.int32),
    )
    return tf.data.Dataset.from_generator(gen, output_signature=sig).batch(cfg.batch_size)


def parse_args():
    ap = argparse.ArgumentParser()

    # === DATASET (STANDARD) ===
    ap.add_argument("--s3_dataset", required=True, help="S3 base path (e.g. s3://qbia/bourse/processed/market/)")
    ap.add_argument("--symbol", required=True, help="BTCUSDT")
    ap.add_argument("--quote", required=True, help="USDT")
    ap.add_argument("--interval", required=True, help="1m")
    ap.add_argument("--years", required=True, help="2019,2020,...")

    # === OUTPUT ===
    ap.add_argument("--out", default="runs")

    return ap.parse_args()


def main():
    args = parse_args()

    cfg = CFG()
    set_seed(cfg.seed)

    years = [int(x) for x in args.years.split(",")]
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.out, "pairwise", run_id)
    os.makedirs(out_dir, exist_ok=True)
    logger = JsonlLogger(os.path.join(out_dir, "log.jsonl"))

    # Auto-detect scaler from event_classifier output
    event_runs_dir = os.path.join(args.out, "event_classifier")
    if not os.path.exists(event_runs_dir):
        raise RuntimeError(f"Event classifier directory not found: {event_runs_dir}. Train event_classifier first.")

    latest_event_run = sorted([d for d in os.listdir(event_runs_dir) if os.path.isdir(os.path.join(event_runs_dir, d))])[-1]
    event_run_dir = os.path.join(event_runs_dir, latest_event_run)
    scaler_json = os.path.join(event_run_dir, "scaler.json")

    if not os.path.exists(scaler_json):
        raise RuntimeError(f"Scaler not found: {scaler_json}")

    with open(scaler_json, "r") as f:
        scaler = RobustScaler.from_json(__import__("json").load(f))

    total = count_total_windows(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg.lookback, cfg.horizon)
    n_train = int(total * cfg.train_frac)
    n_val   = int(total * cfg.val_frac)
    train_start, train_end = 0, n_train
    val_start, val_end     = n_train, n_train + n_val

    ds_train = make_ds_pairs(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, train_start, train_end).shuffle(1024, seed=cfg.seed, reshuffle_each_iteration=True).prefetch(tf.data.AUTOTUNE)
    ds_val   = make_ds_pairs(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, val_start, val_end).prefetch(tf.data.AUTOTUNE)

    embed = WindowEmbedder(d_model=64)
    model = PairwiseComparator(d_model=64, dropout=0.2)

    opt = tf.keras.optimizers.AdamW(cfg.lr, weight_decay=cfg.weight_decay, global_clipnorm=cfg.clip_norm)
    ce = tf.keras.losses.SparseCategoricalCrossentropy()

    best = 1e18
    bad = 0

    def val_eval():
        losses, accs = [], []
        for x_ref, x_now, y in ds_val:
            z_ref = embed(x_ref, training=False)
            z_now = embed(x_now, training=False)
            p = model(z_now, z_ref, training=False)
            loss = ce(y, p).numpy()
            yhat = tf.argmax(p, axis=-1)
            acc = tf.reduce_mean(tf.cast(yhat == y, tf.float32)).numpy()
            losses.append(float(loss))
            accs.append(float(acc))
        return {"val_loss": float(np.mean(losses)) if losses else 0.0,
                "val_acc": float(np.mean(accs)) if accs else 0.0}

    reduce_lr = tf.keras.callbacks.ReduceLROnPlateau(
        monitor="val_acc",
        factor=cfg.reduce_lr_factor,
        patience=cfg.reduce_lr_patience,
        min_lr=cfg.min_lr,
        verbose=1,
        mode="max"
    )

    for ep in range(cfg.epochs):
        tr_losses, tr_accs = [], []

        for x_ref, x_now, y in ds_train:
            with tf.GradientTape() as tape:
                z_ref = embed(x_ref, training=True)
                z_now = embed(x_now, training=True)
                p = model(z_now, z_ref, training=True)
                loss = ce(y, p)

            vars_ = embed.trainable_variables + model.trainable_variables
            grads = tape.gradient(loss, vars_)
            opt.apply_gradients(zip(grads, vars_))

            yhat = tf.argmax(p, axis=-1)
            acc = tf.reduce_mean(tf.cast(yhat == y, tf.float32))
            tr_losses.append(float(loss.numpy()))
            tr_accs.append(float(acc.numpy()))

        v = val_eval()
        print(f"[ep {ep+1}] loss={np.mean(tr_losses):.4f} acc={np.mean(tr_accs):.3f} v_loss={v['val_loss']:.4f} v_acc={v['val_acc']:.3f}")

        logger.log({"ep": ep+1, "train_loss": float(np.mean(tr_losses)), "train_acc": float(np.mean(tr_accs)), **v})
        reduce_lr.on_epoch_end(ep, logs={"val_acc": v["val_acc"]})

        if v["val_loss"] < best - cfg.min_delta:
            best = v["val_loss"]
            bad = 0
            embed.save(os.path.join(out_dir, "best_embedder.keras"))
            model.save(os.path.join(out_dir, "best_pairwise.keras"))
        else:
            bad += 1
            if bad >= cfg.early_stop_patience:
                break

    embed.save(os.path.join(out_dir, "final_embedder.keras"))
    model.save(os.path.join(out_dir, "final_pairwise.keras"))
    logger.close()
    print(out_dir)

if __name__ == "__main__":
    main()
