# context_detectors.py
# Détecteurs de contexte orthogonaux (scalable, propres, pro)
#
# Objectif:
# - Produire des sorties "contexte" indépendantes (orthogonales) à partir de X:[B,L,F]
# - Chaque détecteur est un petit modèle spécialisé avec son propre head et sa propre loss.
# - Les détecteurs sont entraînables séparément, exportables séparément, et consommables par ton Gating/MoE.
#
# Détecteurs fournis (niveau production-ready):
#   1) TradeabilityDetector     -> P(tradeable) (binaire)
#   2) DirectionDetector        -> P(down/flat/up) (3 classes) sur cumret(H)
#   3) PatternDetector          -> multi-label patterns (impulse, reversal, breakout, squeeze)
#   4) EventDetector            -> P(event) (binaire) (spike volume / vol-of-vol / tail risk)
#   5) PairwiseContextDetector  -> embedding pairwise + P(interaction_state) (k classes)
#
# Notes:
# - Labels générés automatiquement (sans annotation manuelle) via heuristiques causales sur future horizon.
# - Tout est compatible avec tes records JSON (FEATURE_KEYS).
# - "Orthogonaux" = têtes séparées + pertes séparées + (optionnel) gradient stop sur backbone partagé.
#
# Dépendances:
# - numpy
# - tensorflow
#
# Usage minimal:
#   cfg = ContextConfig(...)
#   builder = ContextDatasetBuilder(cfg)
#   Xw, y = builder.make_windows_and_labels(X_all_norm, y_ret, y_rv)  # Xw:[N,L,F], y: dict labels
#   det = TradeabilityDetector(cfg).compile_and_fit(Xw, y["tradeable"])
#
# Intégration avec ton Level0 gating:
# - Tu utilises ces détecteurs pour produire des features de contexte.
# - Ensuite ton "decider" (gating global/routeur) combine les sorties.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import tensorflow as tf


# ============================================================
# 0) FEATURE KEYS (doit matcher tes JSON)
# ============================================================
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

RET_KEY = "log_ret"
RV_KEY = "rv_60"


# ============================================================
# 1) CONFIG
# ============================================================
@dataclass(frozen=True)
class ContextConfig:
    lookback: int = 256
    horizon: int = 12
    stride: int = 1

    # Training
    batch_size: int = 256
    shuffle_buffer: int = 50_000
    prefetch: int = 2

    epochs: int = 10
    steps_per_epoch: int = 2000
    val_steps: int = 200

    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    # Model sizes
    d_model: int = 128
    dropout: float = 0.15
    n_tcn_layers: int = 3

    # Labels thresholds (auto-calibrés par quantiles si tu veux)
    dir_neutral_k: float = 0.25     # neutral zone: thr = std(cumret)*k
    min_neutral_thr: float = 0.0

    # Tradeability label rule (simple, stable)
    trade_absR_q: float = 0.70
    trade_rv_q: float = 0.70
    trade_dd_q: float = 0.70
    trade_use_dd: bool = True

    # Pattern heads
    pattern_names: Tuple[str, ...] = ("impulse", "reversal", "breakout", "squeeze")
    pattern_positive_frac_cap: float = 0.35  # évite sur-labellisation

    # Pairwise detector
    pairwise_classes: int = 4  # ex: {low_interact, trend_interact, vol_interact, tail_interact}


# ============================================================
# 2) UTILS: labels from future horizon
# ============================================================
def _cumret_and_dd(fut_ret: np.ndarray) -> Tuple[float, float]:
    path = np.cumsum(fut_ret.astype(np.float64))
    R = float(path[-1]) if path.size else 0.0
    if path.size == 0:
        return R, 0.0
    peak = np.maximum.accumulate(path)
    dd = peak - path
    return R, float(np.max(dd))


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def _direction_3class(cumret: np.ndarray, thr: float) -> np.ndarray:
    # 0=DOWN, 1=FLAT, 2=UP
    out = np.ones_like(cumret, dtype=np.int32)
    out[cumret > thr] = 2
    out[cumret < -thr] = 0
    return out


def _safe_percentile(x: np.ndarray, q: float, default: float) -> float:
    if x.size < 64:
        return default
    return float(np.percentile(x, q * 100.0))


# ============================================================
# 3) DATASET BUILDER
# ============================================================
class ContextDatasetBuilder:
    """
    Construit:
      Xw: [N, L, F] (déjà normalisé en amont)
      labels:
        tradeable: [N] 0/1
        direction: [N] 0/1/2
        patterns: [N, P] multi-label 0/1
        event: [N] 0/1
        pairwise: [N] 0..K-1
    """

    def __init__(self, cfg: ContextConfig):
        self.cfg = cfg

    def make_windows_and_labels(
        self,
        X: np.ndarray,          # [T,F] normalized features
        y_ret: np.ndarray,      # [T] raw or normalized; must match model target space you want for labels
        y_rv: np.ndarray,       # [T]
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        L, H, s = self.cfg.lookback, self.cfg.horizon, self.cfg.stride
        T, F = X.shape
        N = 1 + (T - L - H) // s
        if N <= 0:
            raise ValueError("Pas assez de données pour windowing.")

        Xw = np.zeros((N, L, F), dtype=np.float32)

        # Precompute per-sample future stats
        cumR = np.zeros((N,), dtype=np.float32)
        DD = np.zeros((N,), dtype=np.float32)
        RV = np.zeros((N,), dtype=np.float32)

        # Patterns raw scores
        P = len(self.cfg.pattern_names)
        patt = np.zeros((N, P), dtype=np.int32)

        # Event label
        event = np.zeros((N,), dtype=np.int32)

        # Pairwise class
        pairwise = np.zeros((N,), dtype=np.int32)

        # Build samples
        idx = 0
        end_t = np.zeros((N,), dtype=np.int64)  # end index in original time
        for start in range(0, T - L - H + 1, s):
            end = start + L - 1
            fut_ret = y_ret[end + 1:end + 1 + H].astype(np.float32)
            fut_rv = y_rv[end + 1:end + 1 + H].astype(np.float32)

            Xw[idx] = X[start:start + L]
            end_t[idx] = end

            r, dd = _cumret_and_dd(fut_ret)
            rv = _rms(fut_rv)

            cumR[idx] = r
            DD[idx] = dd
            RV[idx] = rv

            idx += 1

        # -----------------------------
        # Direction 3-class label
        # -----------------------------
        stdR = float(np.std(cumR))
        thr_dir = max(stdR * self.cfg.dir_neutral_k, self.cfg.min_neutral_thr)
        direction = _direction_3class(cumR, thr_dir)

        # -----------------------------
        # Tradeability label (quantile rules)
        # -----------------------------
        thr_absR = _safe_percentile(np.abs(cumR), self.cfg.trade_absR_q, default=float(np.median(np.abs(cumR))))
        thr_RV = _safe_percentile(RV, self.cfg.trade_rv_q, default=float(np.median(RV)))
        if self.cfg.trade_use_dd:
            thr_DD = _safe_percentile(DD, self.cfg.trade_dd_q, default=float(np.median(DD)))
            tradeable = ((np.abs(cumR) >= thr_absR) & (RV <= thr_RV) & (DD <= thr_DD)).astype(np.int32)
        else:
            tradeable = ((np.abs(cumR) >= thr_absR) & (RV <= thr_RV)).astype(np.int32)

        # -----------------------------
        # Pattern labels (multi-label, orthogonaux)
        # Calibrage par quantiles (cap pour éviter explosion de positifs).
        # -----------------------------
        # Impulse: |cumR| high AND RV not extreme high
        q_imp = min(0.90, 1.0 - (1.0 - self.cfg.pattern_positive_frac_cap))
        thr_imp = _safe_percentile(np.abs(cumR), q_imp, default=float(np.percentile(np.abs(cumR), 90)))
        thr_rv_hi = _safe_percentile(RV, 0.85, default=float(np.percentile(RV, 85)))
        impulse = ((np.abs(cumR) >= thr_imp) & (RV <= thr_rv_hi)).astype(np.int32)

        # Reversal: drawdown high within horizon AND |cumR| small
        thr_dd = _safe_percentile(DD, 0.85, default=float(np.percentile(DD, 85)))
        thr_smallR = _safe_percentile(np.abs(cumR), 0.40, default=float(np.percentile(np.abs(cumR), 40)))
        reversal = ((DD >= thr_dd) & (np.abs(cumR) <= thr_smallR)).astype(np.int32)

        # Breakout: RV increases AND |cumR| moderate/high
        # proxy: RV high + |cumR| above median
        thr_rv = _safe_percentile(RV, 0.75, default=float(np.percentile(RV, 75)))
        thr_medR = float(np.median(np.abs(cumR)))
        breakout = ((RV >= thr_rv) & (np.abs(cumR) >= thr_medR)).astype(np.int32)

        # Squeeze: RV very low + |cumR| low
        thr_rv_lo = _safe_percentile(RV, 0.20, default=float(np.percentile(RV, 20)))
        squeeze = ((RV <= thr_rv_lo) & (np.abs(cumR) <= thr_smallR)).astype(np.int32)

        patt[:, 0] = impulse
        patt[:, 1] = reversal
        patt[:, 2] = breakout
        patt[:, 3] = squeeze

        # -----------------------------
        # Event label (rare): RV very high OR tail-risk proxy high
        # Utilise X features sur la FIN de fenêtre (causal).
        # -----------------------------
        # Use last-step tail-risk features if present:
        fmap = {k: i for i, k in enumerate(FEATURE_KEYS)}
        idx_vol = fmap.get("Volume", None)
        idx_trades = fmap.get("Trades", None)
        idx_cvar = fmap.get("cvar_99_60", None)
        idx_var = fmap.get("var_99_60", None)

        last = Xw[:, -1, :]  # [N,F]
        vol = last[:, idx_vol] if idx_vol is not None else np.zeros((N,), np.float32)
        trades = last[:, idx_trades] if idx_trades is not None else np.zeros((N,), np.float32)
        cvar = last[:, idx_cvar] if idx_cvar is not None else np.zeros((N,), np.float32)
        var = last[:, idx_var] if idx_var is not None else np.zeros((N,), np.float32)

        # Heuristic: volume spike OR RV extreme OR tail-risk extreme
        thr_vol = _safe_percentile(vol, 0.95, default=float(np.percentile(vol, 95))) if vol.size else 0.0
        thr_tr = _safe_percentile(trades, 0.95, default=float(np.percentile(trades, 95))) if trades.size else 0.0
        thr_rv_event = _safe_percentile(RV, 0.95, default=float(np.percentile(RV, 95)))
        # For var/cvar: more negative = worse tail; use low percentile
        thr_cvar = _safe_percentile(cvar, 0.05, default=float(np.percentile(cvar, 5)))
        thr_var = _safe_percentile(var, 0.05, default=float(np.percentile(var, 5)))

        event = (
            (RV >= thr_rv_event) |
            (vol >= thr_vol) |
            (trades >= thr_tr) |
            (cvar <= thr_cvar) |
            (var <= thr_var)
        ).astype(np.int32)

        # -----------------------------
        # Pairwise class (K=4): interaction regime proxy
        # Basé sur (trend distance EMA) × (RV) × (tail)
        # -----------------------------
        idx_dist20 = fmap.get("dist_ema_20", None)
        dist20 = last[:, idx_dist20] if idx_dist20 is not None else np.zeros((N,), np.float32)
        absd = np.abs(dist20)

        thr_absd = _safe_percentile(absd, 0.70, default=float(np.percentile(absd, 70)))
        thr_rv_mid = _safe_percentile(RV, 0.60, default=float(np.percentile(RV, 60)))
        thr_tail = _safe_percentile(-cvar, 0.70, default=float(np.percentile(-cvar, 70)))  # larger = worse tail

        # class mapping:
        # 0 low_interact   : low absd and low RV
        # 1 trend_interact : high absd and low RV
        # 2 vol_interact   : RV high (>=thr_rv_mid) and tail not extreme
        # 3 tail_interact  : tail extreme
        pairwise = np.zeros((N,), dtype=np.int32)
        tail_ext = (-cvar >= thr_tail)
        pairwise[tail_ext] = 3

        vol_hi = (RV >= thr_rv_mid) & (~tail_ext)
        pairwise[vol_hi] = 2

        trend = (absd >= thr_absd) & (~tail_ext) & (~vol_hi)
        pairwise[trend] = 1

        # else 0

        labels: Dict[str, np.ndarray] = {
            "tradeable": tradeable.astype(np.int32),
            "direction": direction.astype(np.int32),
            "patterns": patt.astype(np.float32),   # multi-label -> float
            "event": event.astype(np.int32),
            "pairwise": pairwise.astype(np.int32),
            "end_t": end_t.astype(np.int64),
            # for debugging/analysis
            "cumR": cumR.astype(np.float32),
            "DD": DD.astype(np.float32),
            "RV": RV.astype(np.float32),
        }
        return Xw, labels

    def tf_dataset(
        self,
        Xw: np.ndarray,
        y: np.ndarray,
        training: bool,
    ) -> tf.data.Dataset:
        ds = tf.data.Dataset.from_tensor_slices((Xw, y))
        if training:
            ds = ds.shuffle(min(self.cfg.shuffle_buffer, Xw.shape[0]), reshuffle_each_iteration=True)
        ds = ds.batch(self.cfg.batch_size, drop_remainder=training)
        ds = ds.prefetch(self.cfg.prefetch)
        return ds


# ============================================================
# 4) BACKBONE: TCN encoder (fast, stable)
# ============================================================
class TCNEncoder(tf.keras.layers.Layer):
    def __init__(self, d_model: int, n_layers: int, dropout: float, name: str = "tcn_encoder"):
        super().__init__(name=name)
        self.in_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        blocks = []
        for i in range(n_layers):
            dilation = 2 ** i
            blocks.append(
                tf.keras.Sequential(
                    [
                        tf.keras.layers.Conv1D(d_model, 3, padding="causal", dilation_rate=dilation),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6),
                        tf.keras.layers.Activation("gelu"),
                        tf.keras.layers.Dropout(dropout),
                    ],
                    name=f"tcn_{i}",
                )
            )
        self.blocks = blocks
        self.pool = tf.keras.layers.GlobalAveragePooling1D()

        self.post = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(d_model, activation="gelu"),
                tf.keras.layers.Dropout(dropout),
            ],
            name="post",
        )

    def call(self, x, training=False):
        h = self.in_ln(self.in_proj(x))
        for b in self.blocks:
            h = b(h, training=training)
        z = self.pool(h)
        z = self.post(z, training=training)
        return tf.cast(z, tf.float32)  # keep stable for heads


# ============================================================
# 5) BASE DETECTOR
# ============================================================
class BaseDetector(tf.keras.Model):
    detector_name: str = "base"

    def __init__(self, cfg: ContextConfig, out_dim: int, out_activation: Optional[str]):
        super().__init__(name=f"{self.detector_name}_detector")
        self.cfg = cfg
        self.encoder = TCNEncoder(cfg.d_model, cfg.n_tcn_layers, cfg.dropout)

        head_layers = [
            tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
            tf.keras.layers.Dropout(cfg.dropout),
            tf.keras.layers.Dense(out_dim),
        ]
        if out_activation is not None:
            head_layers.append(tf.keras.layers.Activation(out_activation, dtype="float32"))
        else:
            # logits
            head_layers.append(tf.keras.layers.Activation("linear", dtype="float32"))
        self.head = tf.keras.Sequential(head_layers, name="head")

    def call(self, x, training=False):
        z = self.encoder(x, training=training)
        y = self.head(z, training=training)
        return y

    def make_optimizer(self) -> tf.keras.optimizers.Optimizer:
        total_steps = self.cfg.epochs * self.cfg.steps_per_epoch
        warmup = max(1, int(0.05 * total_steps))

        class CosineWarmup(tf.keras.optimizers.schedules.LearningRateSchedule):
            def __init__(self, base_lr: float, warmup_steps: int, total_steps: int):
                super().__init__()
                self.base_lr = float(base_lr)
                self.warmup_steps = int(warmup_steps)
                self.total_steps = int(total_steps)

            def __call__(self, step):
                step = tf.cast(step, tf.float32)
                warm = tf.minimum(1.0, step / tf.cast(self.warmup_steps, tf.float32))
                progress = tf.minimum(
                    1.0,
                    (step - self.warmup_steps) / tf.cast(max(1, self.total_steps - self.warmup_steps), tf.float32),
                )
                cosine = 0.5 * (1.0 + tf.cos(np.pi * progress))
                return self.base_lr * warm * cosine

        lr = CosineWarmup(self.cfg.lr, warmup, total_steps)
        return tf.keras.optimizers.AdamW(
            learning_rate=lr,
            weight_decay=self.cfg.weight_decay,
            global_clipnorm=self.cfg.clip_norm,
            beta_1=0.9,
            beta_2=0.95,
            epsilon=1e-8,
        )

    def compile_and_fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        loss,
        metrics,
        class_weight: Optional[Dict[int, float]] = None,
        out_dir: Optional[str] = None,
    ) -> tf.keras.callbacks.History:
        opt = self.make_optimizer()
        self.compile(optimizer=opt, loss=loss, metrics=metrics)

        callbacks = [tf.keras.callbacks.TerminateOnNaN()]
        if out_dir is not None:
            callbacks.extend(
                [
                    tf.keras.callbacks.ModelCheckpoint(
                        filepath=f"{out_dir}/{self.name}.best.keras",
                        monitor="val_loss",
                        save_best_only=True,
                    ),
                    tf.keras.callbacks.EarlyStopping(
                        monitor="val_loss",
                        patience=3,
                        restore_best_weights=True,
                    ),
                ]
            )

        ds_tr = tf.data.Dataset.from_tensor_slices((X_train, y_train))
        ds_tr = ds_tr.shuffle(min(self.cfg.shuffle_buffer, X_train.shape[0])).batch(self.cfg.batch_size, drop_remainder=True).prefetch(self.cfg.prefetch)

        ds_va = tf.data.Dataset.from_tensor_slices((X_val, y_val))
        ds_va = ds_va.batch(self.cfg.batch_size).prefetch(self.cfg.prefetch)

        return self.fit(
            ds_tr,
            validation_data=ds_va,
            epochs=self.cfg.epochs,
            steps_per_epoch=self.cfg.steps_per_epoch,
            validation_steps=self.cfg.val_steps,
            callbacks=callbacks,
            verbose=1,
            class_weight=class_weight,
        )


# ============================================================
# 6) DETECTORS
# ============================================================
class TradeabilityDetector(BaseDetector):
    detector_name = "tradeability"

    def __init__(self, cfg: ContextConfig):
        super().__init__(cfg, out_dim=1, out_activation="sigmoid")

    def predict_proba(self, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p = self.predict(X, batch_size=batch_size, verbose=0).reshape(-1)
        return p.astype(np.float32)


class DirectionDetector(BaseDetector):
    detector_name = "direction"

    def __init__(self, cfg: ContextConfig):
        super().__init__(cfg, out_dim=3, out_activation="softmax")

    def predict_proba(self, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p = self.predict(X, batch_size=batch_size, verbose=0)
        return p.astype(np.float32)


class PatternDetector(BaseDetector):
    detector_name = "patterns"

    def __init__(self, cfg: ContextConfig):
        super().__init__(cfg, out_dim=len(cfg.pattern_names), out_activation="sigmoid")

    def predict_proba(self, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p = self.predict(X, batch_size=batch_size, verbose=0)
        return p.astype(np.float32)


class EventDetector(BaseDetector):
    detector_name = "event"

    def __init__(self, cfg: ContextConfig):
        super().__init__(cfg, out_dim=1, out_activation="sigmoid")

    def predict_proba(self, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p = self.predict(X, batch_size=batch_size, verbose=0).reshape(-1)
        return p.astype(np.float32)


class PairwiseContextDetector(BaseDetector):
    detector_name = "pairwise"

    def __init__(self, cfg: ContextConfig):
        super().__init__(cfg, out_dim=cfg.pairwise_classes, out_activation="softmax")

    def predict_proba(self, X: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p = self.predict(X, batch_size=batch_size, verbose=0)
        return p.astype(np.float32)


# ============================================================
# 7) LOSSES (correctes pour chaque head)
# ============================================================
def bce():
    return tf.keras.losses.BinaryCrossentropy(from_logits=False)

def cce_sparse():
    return tf.keras.losses.SparseCategoricalCrossentropy(from_logits=False)

def bce_multilabel():
    return tf.keras.losses.BinaryCrossentropy(from_logits=False)


# ============================================================
# 8) TRAINING ORCHESTRATION (séparé, scalable)
# ============================================================
def temporal_split(X: np.ndarray, y: np.ndarray, frac_train: float = 0.9) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = X.shape[0]
    s = int(n * frac_train)
    return X[:s], y[:s], X[s:], y[s:]


def make_class_weight_binary(y: np.ndarray) -> Dict[int, float]:
    # class weights for imbalanced binary labels
    y = y.astype(np.int32).reshape(-1)
    p = float(np.mean(y))
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    w0 = 0.5 / (1.0 - p)
    w1 = 0.5 / p
    return {0: float(w0), 1: float(w1)}


def train_all_detectors(
    cfg: ContextConfig,
    Xw: np.ndarray,
    labels: Dict[str, np.ndarray],
    out_dir: str,
) -> Dict[str, tf.keras.Model]:
    models: Dict[str, tf.keras.Model] = {}

    # 1) Tradeability
    Xt, yt, Xv, yv = temporal_split(Xw, labels["tradeable"])
    m = TradeabilityDetector(cfg)
    m.compile_and_fit(
        Xt, yt, Xv, yv,
        loss=bce(),
        metrics=[tf.keras.metrics.BinaryAccuracy(name="acc"), tf.keras.metrics.AUC(name="auc")],
        class_weight=make_class_weight_binary(yt),
        out_dir=out_dir,
    )
    models["tradeability"] = m

    # 2) Direction (3-class)
    Xt, yt, Xv, yv = temporal_split(Xw, labels["direction"])
    m = DirectionDetector(cfg)
    m.compile_and_fit(
        Xt, yt, Xv, yv,
        loss=cce_sparse(),
        metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
        out_dir=out_dir,
    )
    models["direction"] = m

    # 3) Patterns (multi-label)
    Xt, yt, Xv, yv = temporal_split(Xw, labels["patterns"])
    m = PatternDetector(cfg)
    m.compile_and_fit(
        Xt, yt, Xv, yv,
        loss=bce_multilabel(),
        metrics=[tf.keras.metrics.BinaryAccuracy(name="acc"), tf.keras.metrics.AUC(name="auc")],
        out_dir=out_dir,
    )
    models["patterns"] = m

    # 4) Event (binary, rare)
    Xt, yt, Xv, yv = temporal_split(Xw, labels["event"])
    m = EventDetector(cfg)
    m.compile_and_fit(
        Xt, yt, Xv, yv,
        loss=bce(),
        metrics=[tf.keras.metrics.BinaryAccuracy(name="acc"), tf.keras.metrics.AUC(name="auc")],
        class_weight=make_class_weight_binary(yt),
        out_dir=out_dir,
    )
    models["event"] = m

    # 5) Pairwise class (K classes)
    Xt, yt, Xv, yv = temporal_split(Xw, labels["pairwise"])
    m = PairwiseContextDetector(cfg)
    m.compile_and_fit(
        Xt, yt, Xv, yv,
        loss=cce_sparse(),
        metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
        out_dir=out_dir,
    )
    models["pairwise"] = m

    return models


# ============================================================
# 9) INFERENCE AGGREGATOR (concat outputs for your decider)
# ============================================================
class ContextAggregator:
    """
    Produit un vecteur contexte concaténé:
      [p_tradeable, p_down, p_flat, p_up, patt_probs..., p_event, pairwise_probs...]
    """

    def __init__(
        self,
        tradeability: TradeabilityDetector,
        direction: DirectionDetector,
        patterns: PatternDetector,
        event: EventDetector,
        pairwise: PairwiseContextDetector,
        pattern_names: Tuple[str, ...],
    ):
        self.tradeability = tradeability
        self.direction = direction
        self.patterns = patterns
        self.event = event
        self.pairwise = pairwise
        self.pattern_names = pattern_names

    def transform(self, Xw: np.ndarray, batch_size: int = 1024) -> np.ndarray:
        p_tr = self.tradeability.predict_proba(Xw, batch_size=batch_size)[:, None]  # [N,1]
        p_dir = self.direction.predict_proba(Xw, batch_size=batch_size)             # [N,3]
        p_pat = self.patterns.predict_proba(Xw, batch_size=batch_size)              # [N,P]
        p_evt = self.event.predict_proba(Xw, batch_size=batch_size)[:, None]        # [N,1]
        p_pair = self.pairwise.predict_proba(Xw, batch_size=batch_size)             # [N,K]
        return np.concatenate([p_tr, p_dir, p_pat, p_evt, p_pair], axis=-1).astype(np.float32)


# ============================================================
# 10) SMOKE TEST (no training, just build)
# ============================================================
if __name__ == "__main__":
    cfg = ContextConfig()
    # Fake data
    N = 1024
    Xw = np.random.randn(N, cfg.lookback, len(FEATURE_KEYS)).astype(np.float32)
    y_ret = np.random.randn(N + cfg.lookback + cfg.horizon).astype(np.float32) * 1e-4
    y_rv = np.abs(np.random.randn(N + cfg.lookback + cfg.horizon).astype(np.float32)) * 1e-3

    builder = ContextDatasetBuilder(cfg)
    # To match shapes, build on a synthetic "timeline"
    T = cfg.lookback + cfg.horizon + N * cfg.stride
    X = np.random.randn(T, len(FEATURE_KEYS)).astype(np.float32)
    y_ret_t = np.random.randn(T).astype(np.float32) * 1e-4
    y_rv_t = np.abs(np.random.randn(T).astype(np.float32)) * 1e-3

    Xw2, labels = builder.make_windows_and_labels(X, y_ret_t, y_rv_t)
    print("Xw:", Xw2.shape)
    print("tradeable:", labels["tradeable"].shape, "pos%", float(np.mean(labels["tradeable"])) * 100.0)
    print("direction:", labels["direction"].shape, "counts", np.bincount(labels["direction"], minlength=3))
    print("patterns:", labels["patterns"].shape, "pos%", np.mean(labels["patterns"], axis=0))
    print("event:", labels["event"].shape, "pos%", float(np.mean(labels["event"])) * 100.0)
    print("pairwise:", labels["pairwise"].shape, "counts", np.bincount(labels["pairwise"], minlength=cfg.pairwise_classes))
