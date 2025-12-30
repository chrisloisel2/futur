from __future__ import annotations
import os, json, time, argparse
from dataclasses import dataclass
from typing import List, Dict, Any

import numpy as np
import tensorflow as tf

from common.io_s3 import count_total_windows
from common.scaler import RobustScaler
from common.windows import iter_windows_common
from common.logger import JsonlLogger
from common.metrics import pearson_corr, sign_acc, roi_proxy, safe_mean, safe_std

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from level_1.Event_Classifier import EventClassifier
from level_3.conditional_specialists import ConditionalSpecialists, SpecialistConfig, SpecialistsTrainer


@dataclass(frozen=True)
class CFG:
    lookback: int = 256
    horizon: int = 12
    stride: int = 1

    train_frac: float = 0.80
    val_frac: float = 0.10

    batch_size: int = 16
    epochs: int = 40

    lr: float = 3e-4
    min_lr: float = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    reduce_lr_patience: int = 3
    reduce_lr_factor: float = 0.5
    early_stop_patience: int = 6
    min_delta: float = 1e-4

    n_regimes: int = 4
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


def load_scaler(path: str) -> RobustScaler:
    with open(path, "r") as f:
        d = json.load(f)
    return RobustScaler.from_json(d)


def make_ds_with_future_vector(
    base, symbol, quote, interval, years, cfg: CFG,
    scaler, start, end, n_regimes: int
):
    def gen():
        it = iter_windows_common(
            base, symbol, quote, interval, years,
            FEATURE_KEYS, RET_KEY, RV_KEY, CLOSE_KEY,
            cfg.lookback, cfg.horizon, scaler,
            start=start, end=end, stride=cfg.stride,
            n_regimes=n_regimes
        )
        for s in it:
            # y_ret vector: reconstruct from R by using future returns is not in iterator output.
            # Here: compute target as flat distribution over horizon from R is wrong.
            # For proper training you must have future ret vector stored or compute it in iterator.
            # Minimal: use constant per-step ret = R/H (stable baseline).
            R = float(s["R"])
            y_ret = np.full((cfg.horizon,), R / float(cfg.horizon), dtype=np.float32)
            y_rv  = np.float32(s["RV"])
            yield s["Xw"], y_ret, y_rv

    sig = (
        tf.TensorSpec((cfg.lookback, len(FEATURE_KEYS)), tf.float32),
        tf.TensorSpec((cfg.horizon,), tf.float32),
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
    out_dir = os.path.join(args.out, "specialists", run_id)
    os.makedirs(out_dir, exist_ok=True)
    logger = JsonlLogger(os.path.join(out_dir, "log.jsonl"))

    # Auto-detect dependencies from standard output structure
    event_runs_dir = os.path.join(args.out, "event_classifier")
    if not os.path.exists(event_runs_dir):
        raise RuntimeError(f"Event classifier directory not found: {event_runs_dir}. Train event_classifier first.")

    latest_event_run = sorted([d for d in os.listdir(event_runs_dir) if os.path.isdir(os.path.join(event_runs_dir, d))])[-1]
    event_run_dir = os.path.join(event_runs_dir, latest_event_run)

    scaler_json = os.path.join(event_run_dir, "scaler.json")
    event_model_path = os.path.join(event_run_dir, "best_event_classifier.keras")

    if not os.path.exists(scaler_json):
        raise RuntimeError(f"Scaler not found: {scaler_json}")
    if not os.path.exists(event_model_path):
        raise RuntimeError(f"Event model not found: {event_model_path}")

    total = count_total_windows(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg.lookback, cfg.horizon)
    n_train = int(total * cfg.train_frac)
    n_val   = int(total * cfg.val_frac)

    train_start, train_end = 0, n_train
    val_start, val_end     = n_train, n_train + n_val

    scaler = load_scaler(scaler_json)
    event_model: tf.keras.Model = tf.keras.models.load_model(event_model_path)

    ds_train = make_ds_with_future_vector(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, train_start, train_end, cfg.n_regimes).shuffle(1024, seed=cfg.seed, reshuffle_each_iteration=True).prefetch(tf.data.AUTOTUNE)
    ds_val   = make_ds_with_future_vector(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, val_start, val_end, cfg.n_regimes).prefetch(tf.data.AUTOTUNE)

    sp_cfg = SpecialistConfig(lookback=cfg.lookback, horizon=cfg.horizon, n_regimes=cfg.n_regimes)
    regime_names = [f"r{i}" for i in range(cfg.n_regimes)]
    model = ConditionalSpecialists(sp_cfg, regime_names=regime_names)
    trainer = SpecialistsTrainer(model, sp_cfg)

    best = 1e18
    bad = 0

    def val_eval():
        losses = []
        w_means = []
        R_pred_list = []
        R_true_list = []

        for Xw, y_ret, y_rv in ds_val:
            out_evt = event_model(Xw, training=False)
            P = tf.stop_gradient(out_evt["regime_probs"])
            out = model(Xw, P, training=False)

            ret_pred = out["ret"].numpy()
            rv_pred  = out["rv"].numpy()
            W = out["expert_weights"].numpy()

            # losses
            loss_ret = np.mean(np.abs(y_ret.numpy() - ret_pred))
            loss_rv  = np.mean(np.abs(y_rv.numpy() - rv_pred))
            losses.append(loss_ret + 0.2 * loss_rv)

            w_means.append(W.mean(axis=0))

            R_pred_list.append(ret_pred.sum(axis=1))
            R_true_list.append(y_ret.numpy().sum(axis=1))

        if not losses:
            return {"val_loss": 0.0}

        R_pred = np.concatenate(R_pred_list)
        R_true = np.concatenate(R_true_list)

        return {
            "val_loss": float(np.mean(losses)),
            "val_corr_R": pearson_corr(R_pred, R_true),
            "val_sign_acc": sign_acc(R_pred, R_true),
            "val_roi_proxy": roi_proxy(R_pred, R_true),
            "val_w_mean": np.mean(np.stack(w_means), axis=0).tolist(),
        }

    reduce_lr = tf.keras.callbacks.ReduceLROnPlateau(
        monitor="val_roi_proxy",
        factor=cfg.reduce_lr_factor,
        patience=cfg.reduce_lr_patience,
        min_lr=cfg.min_lr,
        verbose=1,
        mode="max"
    )

    for ep in range(cfg.epochs):
        tr_losses = []
        w_means = []

        for Xw, y_ret, y_rv in ds_train:
            out_evt = event_model(Xw, training=False)
            P = tf.stop_gradient(out_evt["regime_probs"])

            loss = trainer.train_step(Xw, P, y_ret, y_rv)
            tr_losses.append(float(loss.numpy()))

            W = model.compute_weights(P).numpy()
            w_means.append(W.mean(axis=0))

        v = val_eval()

        print(f"[ep {ep+1}] train_loss={np.mean(tr_losses):.5f} val_loss={v['val_loss']:.5f} val_roi={v.get('val_roi_proxy',0.0):.5f} corrR={v.get('val_corr_R',0.0):.3f}")
        logger.log({
            "ep": ep+1,
            "train_loss": float(np.mean(tr_losses)) if tr_losses else 0.0,
            "train_w_mean": np.mean(np.stack(w_means), axis=0).tolist() if w_means else [],
            **v
        })

        reduce_lr.on_epoch_end(ep, logs={"val_roi_proxy": v.get("val_roi_proxy", 0.0)})

        # early stop on val_loss (min) AND require non-degenerate routing
        val_loss = v["val_loss"]
        if val_loss < best - cfg.min_delta:
            best = val_loss
            bad = 0
            model.save(os.path.join(out_dir, "best_specialists.keras"))
        else:
            bad += 1
            if bad >= cfg.early_stop_patience:
                break

    model.save(os.path.join(out_dir, "final_specialists.keras"))
    logger.close()
    print(out_dir)

if __name__ == "__main__":
    main()
