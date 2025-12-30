from __future__ import annotations
import os, json, time, argparse
from dataclasses import dataclass
from typing import List, Dict, Any

import numpy as np
import tensorflow as tf

from common.io_s3 import count_total_windows
from common.scaler import RobustScaler, ReservoirSampler
from common.io_s3 import read_year_df
from common.windows import iter_windows_common
from common.logger import JsonlLogger
from common.metrics import safe_mean, safe_std

# ---- your model
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from level_1.Event_Classifier import EventClassifier, EventClassifierConfig


# ============================================================================
# GPU OPTIMIZATION - RTX 3070
# ============================================================================
gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        # Limit memory to 6GB (safe margin on 8GB card)
        tf.config.set_logical_device_configuration(
            gpus[0],
            [tf.config.LogicalDeviceConfiguration(memory_limit=6144)]
        )

        # Enable mixed precision (FP16) for Ampere GPUs
        from tensorflow.keras import mixed_precision
        mixed_precision.set_global_policy('mixed_float16')

        print(f"✅ GPU ENABLED: {gpus[0].name}")
        print(f"   Memory limit: 6GB / 8GB")
        print(f"   Mixed precision: FP16 (TensorCore)")

    except RuntimeError as e:
        print(f"⚠️  GPU config failed: {e}")
else:
    print("⚠️  No GPU detected, using CPU")
# ============================================================================


@dataclass(frozen=True)
class CFG:
    lookback: int = 256
    horizon: int = 12
    stride: int = 1

    train_frac: float = 0.80
    val_frac: float = 0.10

    # GPU OPTIMIZATION: 4x batch size (32 → 128)
    batch_size: int = 128  # Was 32 (CPU)
    epochs: int = 40

    lr: float = 3e-4
    min_lr: float = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    scaler_sample_max: int = 250_000

    # labels (quantiles train-only)
    q_absR: float = 0.70
    q_RV_hi: float = 0.70
    q_DD_lo: float = 0.70

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


def fit_scaler_train_only(base, symbol, quote, interval, years: List[int], cfg: CFG, train_end_idx: int) -> RobustScaler:
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    gi = 0
    bridge = cfg.lookback + cfg.horizon
    tail = None

    for y in years:
        df = read_year_df(base, symbol, quote, interval, y, ["datetime"] + FEATURE_KEYS)
        if tail is not None:
            df = df if tail is None else (df if tail is None else None)
        if tail is not None:
            import pandas as pd
            df = pd.concat([tail, df], ignore_index=True)

        Xraw = df[FEATURE_KEYS].values.astype(np.float32, copy=False)
        T = len(df)
        max_i = max(0, T - cfg.lookback - cfg.horizon)

        for i in range(max_i):
            if gi >= train_end_idx:
                break
            sampler.add(Xraw[i:i+cfg.lookback])
            gi += 1

        if gi >= train_end_idx:
            break

        tail = df.iloc[-bridge:].copy() if T >= bridge else df.copy()

    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("No scaler samples.")
    sc = RobustScaler()
    sc.fit(Xfit)
    return sc


def fit_tradeability_thresholds_train_only(base, symbol, quote, interval, years, cfg: CFG, scaler: RobustScaler, train_start, train_end):
    # Collect absR, RV, DD on train windows only (bounded memory: sample)
    absR_list, RV_list, DD_list = [], [], []
    it = iter_windows_common(
        base, symbol, quote, interval, years,
        FEATURE_KEYS, RET_KEY, RV_KEY, CLOSE_KEY,
        cfg.lookback, cfg.horizon, scaler,
        start=train_start, end=train_end,
        stride=cfg.stride, n_regimes=4,
        score_cap=50.0, mag_cap=8.0
    )
    for s in it:
        absR_list.append(abs(float(s["R"])))
        RV_list.append(float(s["RV"]))
        DD_list.append(float(s["DD"]))
        if len(absR_list) >= 400_000:
            break

    absR = np.asarray(absR_list, dtype=np.float64)
    RV   = np.asarray(RV_list, dtype=np.float64)
    DD   = np.asarray(DD_list, dtype=np.float64)
    if absR.size < 1000:
        raise RuntimeError("Not enough windows for thresholds.")

    thr_absR  = float(np.quantile(absR, cfg.q_absR))
    thr_RV_hi = float(np.quantile(RV,  cfg.q_RV_hi))
    thr_DD_lo = float(np.quantile(DD,  cfg.q_DD_lo))

    return {"thr_absR": thr_absR, "thr_RV_hi": thr_RV_hi, "thr_DD_lo": thr_DD_lo}


def make_ds(base, symbol, quote, interval, years, cfg: CFG, scaler, thresholds, start, end, n_regimes: int):
    def gen():
        it = iter_windows_common(
            base, symbol, quote, interval, years,
            FEATURE_KEYS, RET_KEY, RV_KEY, CLOSE_KEY,
            cfg.lookback, cfg.horizon, scaler,
            start=start, end=end, stride=cfg.stride,
            n_regimes=n_regimes
        )
        for s in it:
            absR = abs(float(s["R"]))
            RV   = float(s["RV"])
            DD   = float(s["DD"])
            y_conf = 1 if (absR >= thresholds["thr_absR"] and RV >= thresholds["thr_RV_hi"] and DD <= thresholds["thr_DD_lo"]) else 0
            yield (s["Xw"], np.int32(s["regime"]), np.float32(y_conf))

    sig = (
        tf.TensorSpec((cfg.lookback, len(FEATURE_KEYS)), tf.float32),
        tf.TensorSpec((), tf.int32),
        tf.TensorSpec((), tf.float32),
    )
    ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
    return ds.batch(cfg.batch_size)


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
    out_dir = os.path.join(args.out, "event_classifier", run_id)
    os.makedirs(out_dir, exist_ok=True)
    logger = JsonlLogger(os.path.join(out_dir, "log.jsonl"))

    total = count_total_windows(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg.lookback, cfg.horizon)
    n_train = int(total * cfg.train_frac)
    n_val   = int(total * cfg.val_frac)

    train_start, train_end = 0, n_train
    val_start, val_end     = n_train, n_train + n_val

    scaler = fit_scaler_train_only(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, train_end)
    with open(os.path.join(out_dir, "scaler.json"), "w") as f:
        json.dump(scaler.to_json(), f)

    thresholds = fit_tradeability_thresholds_train_only(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, train_start, train_end)
    with open(os.path.join(out_dir, "tradeability_thresholds.json"), "w") as f:
        json.dump(thresholds, f)

    ds_train = make_ds(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, thresholds, train_start, train_end, cfg.n_regimes).shuffle(1024, seed=cfg.seed, reshuffle_each_iteration=True).prefetch(tf.data.AUTOTUNE)
    ds_val   = make_ds(args.s3_dataset, args.symbol, args.quote, args.interval, years, cfg, scaler, thresholds, val_start, val_end, cfg.n_regimes).prefetch(tf.data.AUTOTUNE)

    model = EventClassifier(EventClassifierConfig(d_model=64, n_layers=3, n_regimes=cfg.n_regimes, dropout=0.2, confidence_dropout=0.1))
    opt = tf.keras.optimizers.AdamW(cfg.lr, weight_decay=cfg.weight_decay, global_clipnorm=cfg.clip_norm)

    ce = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)
    bce = tf.keras.losses.BinaryCrossentropy(from_logits=False)

    best = -1e18
    bad = 0

    def val_eval():
        reg_loss, conf_loss = [], []
        conf_mean, ent_mean = [], []
        hist = np.zeros((cfg.n_regimes,), dtype=np.int64)

        for x, y_reg, y_conf in ds_val:
            out = model(x, training=False)
            reg_logits = out["regime_logits"]
            conf = out["confidence"]
            ent  = out["entropy"]

            reg_loss.append(float(ce(y_reg, reg_logits).numpy()))
            conf_loss.append(float(bce(tf.expand_dims(y_conf, -1), conf).numpy()))
            conf_mean.append(float(tf.reduce_mean(conf).numpy()))
            ent_mean.append(float(tf.reduce_mean(ent).numpy()))

            yhat = tf.argmax(out["regime_probs"], axis=-1).numpy()
            for k in yhat:
                hist[int(k)] += 1

        return {
            "val_reg_loss": float(np.mean(reg_loss)) if reg_loss else 0.0,
            "val_conf_loss": float(np.mean(conf_loss)) if conf_loss else 0.0,
            "val_conf_mean": float(np.mean(conf_mean)) if conf_mean else 0.0,
            "val_entropy_mean": float(np.mean(ent_mean)) if ent_mean else 0.0,
            "val_regime_hist": hist.tolist(),
        }

    reduce_lr = tf.keras.callbacks.ReduceLROnPlateau(
        monitor="val_score",
        factor=cfg.reduce_lr_factor,
        patience=cfg.reduce_lr_patience,
        min_lr=cfg.min_lr,
        verbose=1,
        mode="max"
    )

    for ep in range(cfg.epochs):
        tr_reg_loss, tr_conf_loss, tr_conf_mean, tr_ent_mean = [], [], [], []

        for x, y_reg, y_conf in ds_train:
            with tf.GradientTape() as tape:
                out = model(x, training=True)
                reg_logits = out["regime_logits"]
                conf = out["confidence"]
                ent  = out["entropy"]

                loss_reg = ce(y_reg, reg_logits)
                loss_conf = bce(tf.expand_dims(y_conf, -1), conf)
                loss = loss_reg + loss_conf + 0.01 * tf.reduce_mean(ent)

            grads = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grads, model.trainable_variables))

            tr_reg_loss.append(float(loss_reg.numpy()))
            tr_conf_loss.append(float(loss_conf.numpy()))
            tr_conf_mean.append(float(tf.reduce_mean(conf).numpy()))
            tr_ent_mean.append(float(tf.reduce_mean(ent).numpy()))

        v = val_eval()
        val_score = v["val_conf_mean"] - v["val_entropy_mean"] - v["val_reg_loss"]

        print(
            f"[ep {ep+1}] "
            f"reg={np.mean(tr_reg_loss):.4f} conf={np.mean(tr_conf_loss):.4f} "
            f"v_reg={v['val_reg_loss']:.4f} v_conf={v['val_conf_loss']:.4f} "
            f"v_conf_mean={v['val_conf_mean']:.3f} v_ent={v['val_entropy_mean']:.3f}"
        )

        logger.log({
            "ep": ep+1,
            "train_reg_loss": float(np.mean(tr_reg_loss)),
            "train_conf_loss": float(np.mean(tr_conf_loss)),
            "train_conf_mean": float(np.mean(tr_conf_mean)),
            "train_entropy_mean": float(np.mean(tr_ent_mean)),
            **v,
            "val_score": float(val_score),
            "lr": float(opt.learning_rate.numpy() if hasattr(opt.learning_rate, "numpy") else cfg.lr),
        })

        reduce_lr.on_epoch_end(ep, logs={"val_score": val_score})

        if val_score > best + cfg.min_delta:
            best = val_score
            bad = 0
            model.save(os.path.join(out_dir, "best_event_classifier.keras"))
        else:
            bad += 1
            if bad >= cfg.early_stop_patience:
                break

    model.save(os.path.join(out_dir, "final_event_classifier.keras"))
    logger.close()
    print(out_dir)

if __name__ == "__main__":
    main()
