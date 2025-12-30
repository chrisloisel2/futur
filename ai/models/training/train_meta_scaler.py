from __future__ import annotations
import os, json, time, argparse
from dataclasses import dataclass
from typing import List

import numpy as np
import tensorflow as tf

from common.io_s3 import count_total_windows
from common.scaler import RobustScaler
from common.windows import iter_windows_common
from common.logger import JsonlLogger
from common.metrics import roi_proxy, pearson_corr, sign_acc

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from level_6.meta_scaler import MetaScaler, MetaScalerConfig, pack_meta_inputs


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

    edge_min: float = 0.6  # define "meaningful"
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


def make_ds_meta(
    base, symbol, quote, interval, years: List[int], cfg: CFG,
    scaler, start, end,
    edge_model_path: str,
    event_model_path: str,
    pairwise_model_path: str,
    embedder_path: str,
):
    edge_model = tf.keras.models.load_model(edge_model_path)
    event_model = tf.keras.models.load_model(event_model_path)
    pair_model = tf.keras.models.load_model(pairwise_model_path)
    embedder = tf.keras.models.load_model(embedder_path)

    def gen():
        it = iter_windows_common(
            base, symbol, quote, interval, years,
            FEATURE_KEYS, RET_KEY, RV_KEY, CLOSE_KEY,
            cfg.lookback, cfg.horizon, scaler,
            start=start, end=end, stride=cfg.stride
        )
        prev_Xw = None

        for s in it:
            Xw = s["Xw"][None, ...]  # [1,L,F]
            edge_true = float(s["edge"])

            # edge_raw
            edge_raw = float(edge_model(tf.convert_to_tensor(Xw), training=False)["edge"].numpy().squeeze())

            # event heads
            evt = event_model(tf.convert_to_tensor(Xw), training=False)
            conf = float(evt["confidence"].numpy().squeeze())
            ent  = float(evt["entropy"].numpy().squeeze())

            # tradeability proxy (use confidence directly)
            tradeability = 1.0 if conf >= 0.5 else 0.0

            # pairwise consistency (need ref)
            if prev_Xw is None:
                pair_cons = 1.0
            else:
                z_now = embedder(tf.convert_to_tensor(Xw), training=False)
                z_ref = embedder(tf.convert_to_tensor(prev_Xw), training=False)
                pw = pair_model(z_now, z_ref, training=False).numpy().squeeze()
                pair_cons = float(pw[0])  # p_consistent
            prev_Xw = Xw

            recent_roi = 0.0  # placeholder; in prod you feed rolling pnl

            inputs_vec = pack_meta_inputs(
                tradeability=np.array([[tradeability]], np.float32),
                regime_confidence=np.array([[conf]], np.float32),
                regime_entropy=np.array([[ent]], np.float32),
                pairwise_consistency=np.array([[pair_cons]], np.float32),
                recent_roi=np.array([[recent_roi]], np.float32),
            ).squeeze(0)  # [5]

            # label profitable (binary)
            y_profit = 1.0 if (abs(edge_true) >= cfg.edge_min and edge_raw * edge_true > 0) else 0.0

            yield inputs_vec.astype(np.float32), np.float32(edge_raw), np.float32(edge_true), np.float32(y_profit)

    sig = (
        tf.TensorSpec((5,), tf.float32),
        tf.TensorSpec((), tf.float32),
        tf.TensorSpec((), tf.float32),
        tf.TensorSpec((), tf.float32),
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
    out_dir = os.path.join(args.out, "meta_scaler", run_id)
    os.makedirs(out_dir, exist_ok=True)
    logger = JsonlLogger(os.path.join(out_dir, "log.jsonl"))

    # Auto-detect all dependencies from standard output structure
    event_runs_dir = os.path.join(args.out, "event_classifier")
    specialists_runs_dir = os.path.join(args.out, "specialists")
    pairwise_runs_dir = os.path.join(args.out, "pairwise")

    if not os.path.exists(event_runs_dir):
        raise RuntimeError(f"Event classifier directory not found: {event_runs_dir}. Train event_classifier first.")
    if not os.path.exists(specialists_runs_dir):
        raise RuntimeError(f"Specialists directory not found: {specialists_runs_dir}. Train specialists first.")
    if not os.path.exists(pairwise_runs_dir):
        raise RuntimeError(f"Pairwise directory not found: {pairwise_runs_dir}. Train pairwise_comparator first.")

    latest_event_run = sorted([d for d in os.listdir(event_runs_dir) if os.path.isdir(os.path.join(event_runs_dir, d))])[-1]
    latest_specialists_run = sorted([d for d in os.listdir(specialists_runs_dir) if os.path.isdir(os.path.join(specialists_runs_dir, d))])[-1]
    latest_pairwise_run = sorted([d for d in os.listdir(pairwise_runs_dir) if os.path.isdir(os.path.join(pairwise_runs_dir, d))])[-1]

    event_run_dir = os.path.join(event_runs_dir, latest_event_run)
    specialists_run_dir = os.path.join(specialists_runs_dir, latest_specialists_run)
    pairwise_run_dir = os.path.join(pairwise_runs_dir, latest_pairwise_run)

    scaler_json = os.path.join(event_run_dir, "scaler.json")
    event_model_path = os.path.join(event_run_dir, "best_event_classifier.keras")
    edge_model_path = os.path.join(specialists_run_dir, "best_specialists.keras")
    pairwise_model_path = os.path.join(pairwise_run_dir, "best_pairwise.keras")
    embedder_path = os.path.join(pairwise_run_dir, "best_embedder.keras")

    if not os.path.exists(scaler_json):
        raise RuntimeError(f"Scaler not found: {scaler_json}")
    if not os.path.exists(event_model_path):
        raise RuntimeError(f"Event model not found: {event_model_path}")
    if not os.path.exists(edge_model_path):
        raise RuntimeError(f"Edge model not found: {edge_model_path}")
    if not os.path.exists(pairwise_model_path):
        raise RuntimeError(f"Pairwise model not found: {pairwise_model_path}")
    if not os.path.exists(embedder_path):
        raise RuntimeError(f"Embedder not found: {embedder_path}")

    with open(scaler_json, "r") as f:
        scaler = RobustScaler.from_json(json.load(f))

    total = count_total_windows(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg.lookback, cfg.horizon)
    n_train = int(total * cfg.train_frac)
    n_val   = int(total * cfg.val_frac)
    train_start, train_end = 0, n_train
    val_start, val_end     = n_train, n_train + n_val

    ds_train = make_ds_meta(
        args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg,
        scaler, train_start, train_end,
        edge_model_path, event_model_path, pairwise_model_path, embedder_path
    ).shuffle(1024, seed=cfg.seed, reshuffle_each_iteration=True).prefetch(tf.data.AUTOTUNE)
    ds_val = make_ds_meta(
        args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg,
        scaler, val_start, val_end,
        edge_model_path, event_model_path, pairwise_model_path, embedder_path
    ).prefetch(tf.data.AUTOTUNE)

    model = MetaScaler(input_dim=5, cfg=MetaScalerConfig(d_model=64, n_layers=3, dropout=0.1))
    opt = tf.keras.optimizers.AdamW(cfg.lr, weight_decay=cfg.weight_decay, global_clipnorm=cfg.clip_norm)
    bce = tf.keras.losses.BinaryCrossentropy(from_logits=False)

    best = -1e18
    bad = 0

    def val_eval():
        scales, e_raw, e_true, y = [], [], [], []
        losses = []
        for x, edge_raw, edge_true, y_profit in ds_val:
            s = model(x, training=False).numpy().squeeze()
            scales.append(s)
            e_raw.append(edge_raw.numpy())
            e_true.append(edge_true.numpy())
            y.append(y_profit.numpy())
            losses.append(float(bce(y_profit, model(x, training=False)).numpy()))

        S = np.concatenate([np.atleast_1d(z) for z in scales])
        ER = np.concatenate(e_raw)
        ET = np.concatenate(e_true)
        Y  = np.concatenate(y)

        edge_final = ER * S
        val_roi = roi_proxy(edge_final, ET)
        acc = float(np.mean((S >= 0.5) == (Y >= 0.5))) if Y.size else 0.0

        return {
            "val_loss": float(np.mean(losses)) if losses else 0.0,
            "val_roi": float(val_roi),
            "val_acc": float(acc),
            "scale_mean": float(S.mean()) if S.size else 0.0,
            "scale_std": float(S.std()) if S.size else 0.0,
        }

    reduce_lr = tf.keras.callbacks.ReduceLROnPlateau(
        monitor="val_roi",
        factor=cfg.reduce_lr_factor,
        patience=cfg.reduce_lr_patience,
        min_lr=cfg.min_lr,
        verbose=1,
        mode="max"
    )

    for ep in range(cfg.epochs):
        tr_losses = []
        for x, edge_raw, edge_true, y_profit in ds_train:
            with tf.GradientTape() as tape:
                s = model(x, training=True)  # [B,1]
                loss = bce(y_profit, s)
            grads = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grads, model.trainable_variables))
            tr_losses.append(float(loss.numpy()))

        v = val_eval()
        print(f"[ep {ep+1}] loss={np.mean(tr_losses):.4f} v_loss={v['val_loss']:.4f} v_roi={v['val_roi']:.5f} scaleμ={v['scale_mean']:.3f}")
        logger.log({"ep": ep+1, "train_loss": float(np.mean(tr_losses)), **v})

        reduce_lr.on_epoch_end(ep, logs={"val_roi": v["val_roi"]})

        if v["val_roi"] > best + cfg.min_delta:
            best = v["val_roi"]
            bad = 0
            model.save(os.path.join(out_dir, "best_meta_scaler.keras"))
        else:
            bad += 1
            if bad >= cfg.early_stop_patience:
                break

    model.save(os.path.join(out_dir, "final_meta_scaler.keras"))
    logger.close()
    print(out_dir)

if __name__ == "__main__":
    main()
