from __future__ import annotations
import math
import warnings
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List
import numpy as np
import tensorflow as tf
from scipy import stats
from config import CONFIG, REGIME_NAMES


def compute_regime_labels_causal(
    features: np.ndarray,          # [T, F] chronological
    feature_keys: List[str],
    lookback: int = 256,
    warmup: int = 256,
) -> np.ndarray:
    T, F = features.shape
    labels = np.full(T, -1, dtype=np.int32)

    fmap = {k: i for i, k in enumerate(feature_keys)}
    idx_ret = fmap.get("log_ret", fmap.get("ret", 0))
    idx_rv = fmap.get("rv_ann_60", fmap.get("rv_60", 1))
    idx_rsi = fmap.get("rsi_14", 2)
    idx_dist_ema_20 = fmap.get("dist_ema_20", 3)

    ret = features[:, idx_ret]
    rv = features[:, idx_rv]
    rsi = features[:, idx_rsi]
    dist_ema_20 = features[:, idx_dist_ema_20]

    # Precompute rolling quantiles/thresholds causally with expanding window (more stable than per-window q25/q75)
    # Uses only past data for thresholds.
    rv_q25 = np.full(T, np.nan, dtype=np.float32)
    rv_q75 = np.full(T, np.nan, dtype=np.float32)

    for t in range(warmup, T):
        past_rv = rv[max(0, t - lookback): t]  # strictly past up to t-1
        if past_rv.size < 32:
            continue
        rv_q25[t] = np.percentile(past_rv, 25)
        rv_q75[t] = np.percentile(past_rv, 75)

    for t in range(warmup, T):
        start = t - lookback
        if start < 0:
            continue

        window_ret = ret[start: t]              # strictly past
        window_rsi = rsi[start: t]
        window_dist = dist_ema_20[start: t]

        if window_ret.size < lookback:
            continue

        # Trend proxy: slope of dist_ema_20 over past window
        slope = np.polyfit(np.arange(window_dist.size), window_dist, deg=1)[0]
        abs_slope = float(np.abs(slope))

        # Direction stability: fewer sign flips in past returns
        sign_ret = np.sign(window_ret)
        flips = np.sum(np.diff(sign_ret) != 0)
        direction_stability = 1.0 - (flips / max(1, window_ret.size))

        rsi_current = float(window_rsi[-1])
        rsi_extreme = (rsi_current < 30.0) or (rsi_current > 70.0)

        anticorr = float(np.mean(np.sign(window_dist) != np.sign(window_ret)))

        rv_current = float(rv[t])
        q25 = rv_q25[t]
        q75 = rv_q75[t]
        if not np.isfinite(q25) or not np.isfinite(q75):
            continue

        # Range proxy: low slope + dist near 0 relative to its own past dispersion
        abs_dist_q25 = np.percentile(np.abs(window_dist), 25)

        scores = np.zeros(5, dtype=np.float32)
        # 0: TREND
        scores[0] = (abs_slope > np.percentile(np.abs(window_dist), 75)) * direction_stability
        # 1: MEAN_REVERT
        scores[1] = float(rsi_extreme) * anticorr
        # 2: HIGH_VOL
        scores[2] = float(rv_current > q75)
        # 3: LOW_VOL
        scores[3] = float(rv_current < q25)
        # 4: RANGE
        scores[4] = (abs_slope < np.percentile(np.abs(window_dist), 25)) * (abs(window_dist[-1]) < abs_dist_q25)

        labels[t] = int(np.argmax(scores))

    # Fill unlabeled with nearest previous label to avoid holes, but keep -1 for early warmup if needed
    last = -1
    for t in range(T):
        if labels[t] >= 0:
            last = labels[t]
        else:
            labels[t] = last

    return labels


# ============================================================
# 2) MODEL (UNCHANGED CORE), BUT OUTPUT SCALE MATCHED
# ============================================================
class RegimeClassifier(tf.keras.layers.Layer):
    def __init__(self, name="regime_classifier", **kwargs):
        super().__init__(name=name, **kwargs)
        d_model = CONFIG.regime_d_model
        n_layers = CONFIG.regime_n_layers
        dropout = CONFIG.regime_dropout
        n_regimes = CONFIG.n_regimes

        self.input_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.input_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        self.layers_list = []
        for i in range(n_layers):
            k = 3 if i == 0 else 5
            self.layers_list.append(
                tf.keras.Sequential([
                    tf.keras.layers.Conv1D(d_model, k, padding="causal"),
                    tf.keras.layers.LayerNormalization(epsilon=1e-6),
                    tf.keras.layers.Activation("gelu"),
                    tf.keras.layers.Dropout(dropout),
                ], name=f"cnn_{i}")
            )

        self.global_pool = tf.keras.layers.GlobalAveragePooling1D()
        self.classifier = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(n_regimes),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ], name="classifier")

    def call(self, x, training=False):
        h = self.input_proj(x)
        h = self.input_ln(h)
        for layer in self.layers_list:
            h = layer(h, training=training)
        pooled = self.global_pool(h)
        return self.classifier(pooled, training=training)


class RegimeExpert(tf.keras.layers.Layer):
    def __init__(self, regime_id: int, name: Optional[str] = None, **kwargs):
        super().__init__(name=name or f"expert_{regime_id}", **kwargs)
        self.regime_id = regime_id
        d_model = CONFIG.expert_d_model
        n_layers = CONFIG.expert_n_layers
        dropout = CONFIG.expert_dropout
        horizon = CONFIG.horizon

        self.input_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.input_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        self.backbone_layers = []
        for i in range(n_layers):
            dilation = 2 ** i
            self.backbone_layers.append(
                tf.keras.Sequential([
                    tf.keras.layers.Conv1D(d_model, 3, padding="causal", dilation_rate=dilation),
                    tf.keras.layers.LayerNormalization(epsilon=1e-6),
                    tf.keras.layers.Activation("gelu"),
                    tf.keras.layers.Dropout(dropout),
                ], name=f"tcn_{i}")
            )

        self.global_pool = tf.keras.layers.GlobalAveragePooling1D()
        self.shared = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
        ], name="shared")

        # Ret head predicts in NORMALIZED target space directly (same as y_ret)
        self.ret_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model // 2, activation="gelu"),
            tf.keras.layers.Dense(horizon),
        ], name="ret_head")

        # RV head predicts NORMALIZED rv (same as y_rv) with positivity if your normalized target is >=0.
        # If your y_rv is z-scored / robust-scaled (can be negative), remove softplus.
        if getattr(CONFIG, "rv_target_nonnegative", False):
            self.rv_head = tf.keras.Sequential([
                tf.keras.layers.Dense(d_model // 2, activation="gelu"),
                tf.keras.layers.Dense(1),
                tf.keras.layers.Activation("softplus"),
            ], name="rv_head")
        else:
            self.rv_head = tf.keras.Sequential([
                tf.keras.layers.Dense(d_model // 2, activation="gelu"),
                tf.keras.layers.Dense(1),
            ], name="rv_head")

    def call(self, x, training=False):
        h = self.input_proj(x)
        h = self.input_ln(h)
        for layer in self.backbone_layers:
            h = layer(h, training=training)
        pooled = self.global_pool(h)
        shared = self.shared(pooled, training=training)

        ret = self.ret_head(shared, training=training)
        rv = self.rv_head(shared, training=training)

        ret = tf.cast(ret, tf.float32)
        rv = tf.squeeze(rv, axis=-1)
        rv = tf.cast(rv, tf.float32)

        return {"ret": ret, "rv": rv}


class RegimeAwareModel(tf.keras.Model):
    def __init__(self, feature_dim: int, name="regime_aware_model", **kwargs):
        super().__init__(name=name, **kwargs)
        self.feature_dim = feature_dim
        self.regime_classifier = RegimeClassifier()
        self.experts = [RegimeExpert(regime_id=i) for i in range(CONFIG.n_regimes)]

    def call(self, x, training=False, return_regime_probs=False):
        B = tf.shape(x)[0]
        p_regime = self.regime_classifier(x, training=training)
        expert_preds = [expert(x, training=training) for expert in self.experts]

        if CONFIG.gating_mode == "hard":
            regime_indices = tf.argmax(p_regime, axis=-1)
            ret_list = tf.stack([pred["ret"] for pred in expert_preds], axis=1)  # [B, R, H]
            rv_list = tf.stack([pred["rv"] for pred in expert_preds], axis=1)   # [B, R]
            gather = tf.stack([tf.range(B), tf.cast(regime_indices, tf.int32)], axis=1)
            ret_pred = tf.gather_nd(ret_list, gather)
            rv_pred = tf.gather_nd(rv_list, gather)
        else:
            p_ret = p_regime[:, :, None]  # [B, R, 1]
            p_rv = p_regime               # [B, R]
            ret_stack = tf.stack([pred["ret"] for pred in expert_preds], axis=1)  # [B, R, H]
            rv_stack = tf.stack([pred["rv"] for pred in expert_preds], axis=1)    # [B, R]
            ret_pred = tf.reduce_sum(ret_stack * p_ret, axis=1)
            rv_pred = tf.reduce_sum(rv_stack * p_rv, axis=1)

        out = {"ret": ret_pred, "rv": rv_pred}
        if return_regime_probs:
            out["regime_probs"] = p_regime
        return out

    def entropy_regularization(self, p_regime):
        p = tf.clip_by_value(p_regime, 1e-8, 1.0)
        ent = -tf.reduce_sum(p * tf.math.log(p), axis=-1)
        return -tf.reduce_mean(ent)


# ============================================================
# 3) TRAINER (FIXED: LOSS COHERENT + OPTIMIZATIONS)
# ============================================================
class CosineWarmup(tf.keras.optimizers.schedules.LearningRateSchedule):
    def __init__(self, base_lr: float, warmup_steps: int, total_steps: int):
        super().__init__()
        self.base_lr = float(base_lr)
        self.warmup_steps = max(1, int(warmup_steps))
        self.total_steps = max(self.warmup_steps + 1, int(total_steps))

    def __call__(self, step):
        step = tf.cast(step, tf.float32)
        warm = tf.minimum(1.0, step / self.warmup_steps)
        progress = tf.minimum(1.0, (step - self.warmup_steps) / (self.total_steps - self.warmup_steps))
        cosine = 0.5 * (1.0 + tf.cos(math.pi * progress))
        return self.base_lr * warm * cosine


class Trainer:
    """
    Key fixes:
    - ret_loss and rv_loss operate on SAME target scale as y_ret/y_rv
    - per-horizon weighting to prioritize short horizon (improves practical accuracy)
    - optional label smoothing for regime classifier
    """

    def __init__(self, model: RegimeAwareModel):
        self.model = model

        total_steps = CONFIG.epochs * CONFIG.steps_per_epoch
        lr_schedule = CosineWarmup(
            base_lr=CONFIG.lr,
            warmup_steps=int(0.05 * total_steps),
            total_steps=total_steps,
        )

        self.optimizer = tf.keras.optimizers.AdamW(
            learning_rate=lr_schedule,
            weight_decay=CONFIG.weight_decay,
            beta_1=0.9,
            beta_2=0.95,
            epsilon=1e-8,
            global_clipnorm=CONFIG.clip_norm,
        )

        # Exponential horizon weights (short-term emphasized)
        H = CONFIG.horizon
        decay = getattr(CONFIG, "horizon_weight_decay", 0.25)
        w = np.exp(-np.arange(H, dtype=np.float32) * decay)
        w = w / (np.sum(w) + 1e-8)
        self.horizon_weights = tf.constant(w[None, :], dtype=tf.float32)  # [1, H]

        self.train_loss_tracker = tf.keras.metrics.Mean(name="train_loss")
        self.val_loss_tracker = tf.keras.metrics.Mean(name="val_loss")
        self.regime_acc_tracker = tf.keras.metrics.SparseCategoricalAccuracy(name="regime_acc")

    @tf.function
    def train_step(self, x, y_regime, y_ret, y_rv):
        with tf.GradientTape() as tape:
            outputs = self.model(x, training=True, return_regime_probs=True)
            p_regime = outputs["regime_probs"]
            y_ret_pred = outputs["ret"]
            y_rv_pred = outputs["rv"]

            # Regime CE (optional label smoothing via mix with uniform)
            if getattr(CONFIG, "regime_label_smoothing", 0.0) > 0:
                eps = tf.cast(CONFIG.regime_label_smoothing, tf.float32)
                n = tf.cast(CONFIG.n_regimes, tf.float32)
                y_one = tf.one_hot(y_regime, CONFIG.n_regimes, dtype=tf.float32)
                y_smooth = (1.0 - eps) * y_one + eps * (1.0 / n)
                regime_loss = tf.reduce_mean(
                    tf.keras.losses.categorical_crossentropy(y_smooth, p_regime)
                )
            else:
                regime_loss = tf.reduce_mean(
                    tf.keras.losses.sparse_categorical_crossentropy(y_regime, p_regime)
                )

            # Ret loss: weighted per horizon (Huber in normalized space)
            ret_err = tf.keras.losses.huber(y_ret, y_ret_pred, delta=getattr(CONFIG, "ret_huber_delta", 1.0))
            # ret_err shape: [B, H]
            ret_loss = tf.reduce_mean(ret_err * self.horizon_weights)

            # RV loss: Huber in normalized space (delta configurable)
            rv_loss = tf.reduce_mean(tf.keras.losses.huber(y_rv, y_rv_pred, delta=getattr(CONFIG, "rv_huber_delta", 1.0)))

            entropy_loss = self.model.entropy_regularization(p_regime)

            total_loss = (
                CONFIG.w_regime * regime_loss
                + CONFIG.w_ret * ret_loss
                + CONFIG.w_rv * rv_loss
                + CONFIG.entropy_weight * entropy_loss
            )

        grads = tape.gradient(total_loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(grads, self.model.trainable_variables))

        self.train_loss_tracker.update_state(total_loss)
        self.regime_acc_tracker.update_state(y_regime, p_regime)
        return total_loss

    @tf.function
    def val_step(self, x, y_regime, y_ret, y_rv):
        outputs = self.model(x, training=False, return_regime_probs=True)
        p_regime = outputs["regime_probs"]
        y_ret_pred = outputs["ret"]
        y_rv_pred = outputs["rv"]

        regime_loss = tf.reduce_mean(
            tf.keras.losses.sparse_categorical_crossentropy(y_regime, p_regime)
        )

        ret_err = tf.keras.losses.huber(y_ret, y_ret_pred, delta=getattr(CONFIG, "ret_huber_delta", 1.0))
        ret_loss = tf.reduce_mean(ret_err * self.horizon_weights)

        rv_loss = tf.reduce_mean(tf.keras.losses.huber(y_rv, y_rv_pred, delta=getattr(CONFIG, "rv_huber_delta", 1.0)))

        total_loss = (CONFIG.w_regime * regime_loss + CONFIG.w_ret * ret_loss + CONFIG.w_rv * rv_loss)
        self.val_loss_tracker.update_state(total_loss)
        return total_loss


# ============================================================
# 4) DENORMALIZATION (CORRECT + FEATUREWISE)
# ============================================================
class Denormalizer:
    """
    Supports:
    - sklearn RobustScaler: center_ and scale_ (preferred)
    - legacy: median and mad (your previous code)
    - StandardScaler: mean_ and scale_
    """
    def __init__(self, scaler, feature_keys: List[str]):
        self.scaler = scaler
        self.feature_keys = feature_keys

    def _idx(self, names: List[str]) -> Optional[int]:
        for n in names:
            if n in self.feature_keys:
                return self.feature_keys.index(n)
        return None

    def inverse_feature(self, x: np.ndarray, feature_names: List[str]) -> np.ndarray:
        if self.scaler is None:
            return x

        idx = self._idx(feature_names)
        if idx is None:
            return x

        # RobustScaler (sklearn): center_ and scale_
        if hasattr(self.scaler, "center_") and hasattr(self.scaler, "scale_"):
            c = float(self.scaler.center_[idx])
            s = float(self.scaler.scale_[idx])
            return x * s + c

        # Legacy robust: median and mad (your earlier assumption, kept but fixed)
        if hasattr(self.scaler, "median") and hasattr(self.scaler, "mad"):
            med = float(self.scaler.median[idx])
            mad = float(self.scaler.mad[idx])
            # If your normalization was (x - med) / (1.4826*mad)
            # inverse is x * (1.4826*mad) + med
            return x * (1.4826 * mad) + med

        # StandardScaler
        if hasattr(self.scaler, "mean_") and hasattr(self.scaler, "scale_"):
            m = float(self.scaler.mean_[idx])
            s = float(self.scaler.scale_[idx])
            return x * s + m

        return x

    def denorm_ret(self, y_ret_norm: np.ndarray) -> np.ndarray:
        return self.inverse_feature(y_ret_norm, ["log_ret", "ret"])

    def denorm_rv(self, y_rv_norm: np.ndarray) -> np.ndarray:
        return self.inverse_feature(y_rv_norm, ["rv_ann_60", "rv_60", "rv"])


# ============================================================
# 5) EVALUATION (RIGOROUS, PER-HORIZON, TRUE BASELINES)
# ============================================================
def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 3:
        return float("nan")
    sa = float(np.std(a))
    sb = float(np.std(b))
    if sa < 1e-12 or sb < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _r2_with_train_var(y_true: np.ndarray, y_pred: np.ndarray, train_var: float) -> float:
    # R2 = 1 - MSE / Var_train ; stable baseline in finance
    mse = float(np.mean((y_true - y_pred) ** 2))
    denom = float(train_var) if train_var > 1e-18 else float(np.var(y_true) + 1e-18)
    return float(1.0 - mse / denom)


def _direction_3class(x: np.ndarray, thr: float) -> np.ndarray:
    # returns {-1,0,1}
    out = np.zeros_like(x, dtype=np.int8)
    out[x > thr] = 1
    out[x < -thr] = -1
    return out


class Evaluator:
    """
    Fixes:
    - Direction computed PER HORIZON (no sum), with neutral zone
    - Baselines use sample_t_index to compute true persistence
    - MAE/corr/R2 all per-horizon + weighted aggregate
    - RV metrics included
    - Sanity checks included
    """

    def __init__(self, scaler, feature_keys: List[str]):
        self.denorm = Denormalizer(scaler, feature_keys)

    def evaluate(
        self,
        model: RegimeAwareModel,
        X: np.ndarray,                     # [N, L, F]
        y_regime: np.ndarray,              # [N]
        y_ret: np.ndarray,                 # [N, H] normalized
        y_rv: np.ndarray,                  # [N] normalized
        sample_t_index: np.ndarray,        # [N] int, end-time index for each sample
        y_ret_train: Optional[np.ndarray] = None,  # [Ntr, H] normalized
        y_rv_train: Optional[np.ndarray] = None,   # [Ntr] normalized
    ) -> Dict:
        # Predict
        out = model(X, training=False, return_regime_probs=True)
        y_ret_pred = out["ret"].numpy()
        y_rv_pred = out["rv"].numpy()
        p_regime = out["regime_probs"].numpy()
        regime_pred = np.argmax(p_regime, axis=-1)

        # Denorm for interpretable reporting (returns in native scale, NOT forced to %; convert outside if needed)
        y_ret_true_d = self.denorm.denorm_ret(y_ret)
        y_ret_pred_d = self.denorm.denorm_ret(y_ret_pred)
        y_rv_true_d = self.denorm.denorm_rv(y_rv)
        y_rv_pred_d = self.denorm.denorm_rv(y_rv_pred)

        N, H = y_ret.shape

        # Horizon weights for aggregates
        decay = getattr(CONFIG, "horizon_weight_decay", 0.25)
        w = np.exp(-np.arange(H, dtype=np.float32) * decay)
        w = w / (np.sum(w) + 1e-8)

        # MAE per horizon
        mae_h = np.mean(np.abs(y_ret_true_d - y_ret_pred_d), axis=0)  # [H]
        mae_weighted = float(np.sum(mae_h * w))

        # Corr per horizon
        corr_h = np.array([_safe_corr(y_ret_true_d[:, h], y_ret_pred_d[:, h]) for h in range(H)], dtype=np.float32)
        corr_mean = float(np.nanmean(corr_h))
        corr_weighted = float(np.nansum(np.where(np.isfinite(corr_h), corr_h, 0.0) * w))

        # R² per horizon with TRAIN variance baseline (must be computed from TRAIN targets)
        if y_ret_train is not None:
            y_ret_train_d = self.denorm.denorm_ret(y_ret_train)
            train_var_h = np.var(y_ret_train_d, axis=0)  # [H]
        else:
            train_var_h = np.var(y_ret_true_d, axis=0) + 1e-18

        r2_h = np.array([_r2_with_train_var(y_ret_true_d[:, h], y_ret_pred_d[:, h], float(train_var_h[h])) for h in range(H)], dtype=np.float32)
        r2_mean = float(np.mean(r2_h))
        r2_weighted = float(np.sum(r2_h * w))

        # Direction PER horizon (3-class), volatility-aware threshold per horizon
        # threshold = max(std_true_h * k, min_thr)
        k = float(getattr(CONFIG, "eval_direction_threshold", 0.25))
        min_thr = float(getattr(CONFIG, "eval_min_threshold", 0.0))

        dir_acc_h = np.zeros(H, dtype=np.float32)
        dir_n_h = np.zeros(H, dtype=np.int32)
        dir_p_h = np.ones(H, dtype=np.float32)

        for h in range(H):
            std_h = float(np.std(y_ret_true_d[:, h]))
            thr = max(std_h * k, min_thr)
            d_true = _direction_3class(y_ret_true_d[:, h], thr)
            d_pred = _direction_3class(y_ret_pred_d[:, h], thr)

            # Evaluate only when both non-neutral
            m = (d_true != 0) & (d_pred != 0)
            n = int(np.sum(m))
            if n <= 0:
                continue

            c = int(np.sum(d_true[m] == d_pred[m]))
            dir_acc_h[h] = float(c / n)
            dir_n_h[h] = n

            # Two-sided binomial vs 0.5 (only makes sense after filtering)
            dir_p_h[h] = float(stats.binomtest(c, n, p=0.5, alternative="two-sided").pvalue)

        dir_acc_weighted = float(np.sum(dir_acc_h * w))
        dir_acc_mean = float(np.mean(dir_acc_h))

        # RV metrics (denorm)
        rv_mae = float(np.mean(np.abs(y_rv_true_d - y_rv_pred_d)))
        rv_corr = _safe_corr(y_rv_true_d, y_rv_pred_d)

        # Regime classification accuracy
        regime_acc = float(np.mean(regime_pred == y_regime))

        # TRUE baselines (require sample_t_index)
        baselines = {}
        if y_ret_train is not None:
            # Mean baseline from TRAIN only (per horizon)
            mean_train = np.mean(self.denorm.denorm_ret(y_ret_train), axis=0)  # [H]
            mean_pred = np.tile(mean_train[None, :], (N, 1))
            baselines["mae_mean_per_h"] = np.mean(np.abs(y_ret_true_d - mean_pred), axis=0).tolist()
            baselines["mae_mean_weighted"] = float(np.sum(np.mean(np.abs(y_ret_true_d - mean_pred), axis=0) * w))

        # Persistence baseline using true temporal adjacency:
        # For each sample ending at time t, baseline predicts next return as previous observed return at time t-1 for that horizon.
        # This only works if your dataset aligns y_ret[:,0] with "next step after t".
        # Implementation: map time->sample index; use t-1 sample if exists.
        idx_by_t = {int(t): i for i, t in enumerate(sample_t_index.tolist())}
        pers_pred = np.zeros_like(y_ret_true_d)
        valid = np.zeros(N, dtype=bool)
        for i, t in enumerate(sample_t_index.tolist()):
            j = idx_by_t.get(int(t) - 1, None)
            if j is None:
                continue
            # persistence predicts the same target as previous sample's target (for each horizon)
            pers_pred[i, :] = y_ret_true_d[j, :]
            valid[i] = True

        if np.any(valid):
            pers_mae_h = np.mean(np.abs(y_ret_true_d[valid] - pers_pred[valid]), axis=0)
            baselines["mae_persistence_per_h"] = pers_mae_h.tolist()
            baselines["mae_persistence_weighted"] = float(np.sum(pers_mae_h * w))

        # Per-regime metrics (use true y_regime labels; also report predicted regime confusion proxy)
        per_regime = {}
        for rid, rname in enumerate(REGIME_NAMES):
            m = (y_regime == rid)
            n = int(np.sum(m))
            if n == 0:
                continue
            rt = y_ret_true_d[m]
            rp = y_ret_pred_d[m]
            mae_r_h = np.mean(np.abs(rt - rp), axis=0)
            per_regime[rname] = {
                "n": n,
                "mae_per_h": mae_r_h.tolist(),
                "mae_weighted": float(np.sum(mae_r_h * w)),
                "corr_per_h": [float(_safe_corr(rt[:, h], rp[:, h])) for h in range(H)],
                "regime_pred_acc_within": float(np.mean(regime_pred[m] == y_regime[m])),
            }

        # Sanity checks (fast)
        sanity = self.sanity_checks(
            y_ret_true_d=y_ret_true_d,
            y_ret_pred_d=y_ret_pred_d,
            sample_t_index=sample_t_index,
            y_ret_train_d=self.denorm.denorm_ret(y_ret_train) if y_ret_train is not None else None,
        )

        return {
            "overall": {
                "regime_classification_acc": regime_acc,
                "ret": {
                    "mae_per_h": mae_h.tolist(),
                    "mae_weighted": mae_weighted,
                    "corr_per_h": corr_h.tolist(),
                    "corr_mean": corr_mean,
                    "corr_weighted": corr_weighted,
                    "r2_per_h": r2_h.tolist(),
                    "r2_mean": r2_mean,
                    "r2_weighted": r2_weighted,
                    "direction": {
                        "acc_per_h": dir_acc_h.tolist(),
                        "acc_mean": dir_acc_mean,
                        "acc_weighted": dir_acc_weighted,
                        "n_per_h": dir_n_h.tolist(),
                        "p_per_h": dir_p_h.tolist(),
                    },
                },
                "rv": {
                    "mae": rv_mae,
                    "corr": float(rv_corr) if np.isfinite(rv_corr) else None,
                },
                "baselines": baselines,
            },
            "per_regime": per_regime,
            "sanity": sanity,
        }

    def sanity_checks(
        self,
        y_ret_true_d: np.ndarray,
        y_ret_pred_d: np.ndarray,
        sample_t_index: np.ndarray,
        y_ret_train_d: Optional[np.ndarray] = None,
    ) -> Dict:
        N, H = y_ret_true_d.shape
        out = {}

        # Target distribution shift vs train
        if y_ret_train_d is not None:
            train_std = np.std(y_ret_train_d[:, 0])
            test_std = np.std(y_ret_true_d[:, 0])
            out["target_std_ratio_h1_test_over_train"] = float(test_std / (train_std + 1e-12))

        # Temporal adjacency density
        ts = sample_t_index.astype(np.int64)
        out["unique_time_coverage_pct"] = float(100.0 * len(np.unique(ts)) / max(1, len(ts)))
        out["time_gap_mean"] = float(np.mean(np.diff(np.sort(ts)))) if len(ts) > 2 else None

        # Shuffle test (prediction should get worse when pairing y with wrong pred)
        # Cheap proxy: compute MAE between true and permuted pred
        rng = np.random.default_rng(42)
        perm = rng.permutation(N)
        mae_real = float(np.mean(np.abs(y_ret_true_d[:, 0] - y_ret_pred_d[:, 0])))
        mae_perm = float(np.mean(np.abs(y_ret_true_d[:, 0] - y_ret_pred_d[perm, 0])))
        out["shuffle_leakage_suspected"] = bool(mae_perm < mae_real)
        out["mae_h1_real"] = mae_real
        out["mae_h1_permuted"] = mae_perm

        return out


# ============================================================
# 6) SPLIT-SAFE DATASET RULES (ENFORCE NO CROSS-SPLIT WINDOWS)
# ============================================================
def filter_samples_no_cross_split(
    sample_t_index: np.ndarray,   # end time of each sample window
    lookback: int,                # window length used to build X
    split_start_t: int,
    split_end_t: int,
) -> np.ndarray:
    """
    Keep only samples where the entire window [t-lookback+1, t] lies inside [split_start_t, split_end_t].
    This is mandatory to avoid regime-label leakage and feature leakage.
    """
    t = sample_t_index.astype(np.int64)
    window_start = t - (lookback - 1)
    m = (window_start >= split_start_t) & (t <= split_end_t)
    return m
