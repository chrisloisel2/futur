# event_classifier.py
# ============================================================
# EVENT REGIME / TRADEABILITY CLASSIFIER
#
# But :
# - Modèle NON directionnel pour le routing.
# - Multi-tâche : régime (CHOP/UP/DOWN) + tradeability (binary) + fwd_ret_pred.
#
# Sorties (API stable) :
#   {
#     "regime_logits": [B, R]   (R = n_regimes)
#     "regime_probs":  [B, R]
#     "tradeability":  [B, 1]   (sigmoid, proba que le contexte est exploitable)
#     "fwd_ret_pred":  [B, 1]   (tanh, retour normalisé prédit ∈ (-1, 1))
#     "entropy":       [B, 1]   (incertitude du routing)
#   }
#
# Exploitation :
#   signal = P(UP)>0.60 OR P(DOWN)>0.60   AND   tradeability>0.55
#
# Notes :
# - Pas de softmax "interne" présenté comme vérité : on expose logits + probs.
# - Entropy fournie pour pénaliser les contextes ambigus.
# - Tradeability head séparé, calibrable indépendamment.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
import tensorflow as tf


# ============================================================
# CONFIG
# ============================================================
@dataclass(frozen=True)
class EventClassifierConfig:
    d_model: int = 64
    n_layers: int = 3

    # Régimes : 0=CHOP, 1=UP, 2=DOWN
    n_regimes: int = 3

    dropout: float = 0.2
    head_dropout: float = 0.1

    # Num stability
    eps: float = 1e-8


# ============================================================
# MODEL
# ============================================================
class EventClassifier(tf.keras.Model):
    """
    Détecteur de régimes + tradeability + fwd_ret_pred (multi-tâche)

    Entrée:
      x: [B, L, F]

    Sortie:
      dict {
        regime_logits: [B, R]          — logits bruts (pour CE loss)
        regime_probs:  [B, R]          — softmax
        tradeability:  [B, 1]          — sigmoid ∈ (0, 1)
        fwd_ret_pred:  [B, 1]          — tanh ∈ (-1, 1)
        entropy:       [B, 1]          — incertitude routing
      }
    """

    def __init__(self, cfg: EventClassifierConfig):
        super().__init__(name="event_classifier")
        self.cfg = cfg

        # ---------- Input projection ----------
        self.in_proj = tf.keras.layers.Dense(cfg.d_model, use_bias=False, name="in_proj")
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6, name="in_ln")
        self.in_act = tf.keras.layers.Activation("gelu", name="in_act")

        # ---------- Temporal blocks (TCN dilaté causal) ----------
        self.blocks = []
        for i in range(cfg.n_layers):
            self.blocks.append(
                tf.keras.Sequential(
                    [
                        tf.keras.layers.Conv1D(
                            cfg.d_model,
                            kernel_size=3,
                            padding="causal",
                            dilation_rate=2**i,
                            use_bias=False,
                            name=f"tcn_conv_{i}",
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6, name=f"tcn_ln_{i}"),
                        tf.keras.layers.Activation("gelu", name=f"tcn_act_{i}"),
                        tf.keras.layers.Dropout(cfg.dropout, name=f"tcn_do_{i}"),
                    ],
                    name=f"tcn_block_{i}",
                )
            )

        # ---------- Pooling + lightweight temporal stats ----------
        self.pool_mean = tf.keras.layers.GlobalAveragePooling1D(name="pool_mean")
        self.pool_max = tf.keras.layers.GlobalMaxPooling1D(name="pool_max")

        # "stats" simples, différentiables, utiles au régime
        self.stats_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6, name="stats_ln")

        # ---------- Shared representation ----------
        self.shared = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model, activation="gelu", name="shared_dense"),
                tf.keras.layers.Dropout(cfg.dropout, name="shared_do"),
            ],
            name="shared_repr",
        )

        # ---------- Heads ----------
        # Régime logits (routing)
        self.regime_logits_head = tf.keras.layers.Dense(cfg.n_regimes, name="regime_logits")

        # Tradeability : exploitable vs bruit (sigmoid)
        self.tradeability_head = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model // 2, activation="gelu", name="trade_dense"),
                tf.keras.layers.Dropout(cfg.head_dropout, name="trade_do"),
                tf.keras.layers.Dense(1, activation="sigmoid", name="tradeability"),
            ],
            name="tradeability_head",
        )

        # Forward return prediction (tanh → ∈ (-1, 1), Sharpe-normalized)
        self.fwd_ret_head = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model // 2, activation="gelu", name="fwd_dense"),
                tf.keras.layers.Dropout(cfg.head_dropout, name="fwd_do"),
                tf.keras.layers.Dense(1, activation="tanh", name="fwd_ret_pred"),
            ],
            name="fwd_ret_head",
        )

    def _temporal_stats(self, h: tf.Tensor) -> tf.Tensor:
        """
        h: [B, L, D]
        Retour:
          stats: [B, 2D]
        """
        # variance temporelle (proxy de "activité / instabilité")
        mean_t = tf.reduce_mean(h, axis=1)  # [B, D]
        var_t = tf.reduce_mean(tf.square(h - mean_t[:, None, :]), axis=1)  # [B, D]
        stats = tf.concat([mean_t, var_t], axis=-1)  # [B, 2D]
        return self.stats_ln(stats)

    def call(self, x: tf.Tensor, training: bool = False) -> dict:
        # ---------- Input ----------
        h = self.in_proj(x)
        h = self.in_ln(h)
        h = self.in_act(h)

        # ---------- Temporal ----------
        for b in self.blocks:
            h = b(h, training=training)

        # ---------- Pooling ----------
        z_mean = self.pool_mean(h)  # [B, D]
        z_max = self.pool_max(h)    # [B, D]
        z_stats = self._temporal_stats(h)  # [B, 2D]

        # concat riche pour régime
        z = tf.concat([z_mean, z_max, z_stats], axis=-1)  # [B, 4D]

        z = self.shared(z, training=training)

        # ---------- Heads ----------
        regime_logits = self.regime_logits_head(z)                    # [B, R]
        regime_probs  = tf.nn.softmax(regime_logits, axis=-1)         # [B, R]

        tradeability  = self.tradeability_head(z, training=training)  # [B, 1]
        fwd_ret_pred  = self.fwd_ret_head(z, training=training)       # [B, 1]

        # Entropy (incertitude du routing)
        p = tf.clip_by_value(regime_probs, self.cfg.eps, 1.0)
        entropy = -tf.reduce_sum(p * tf.math.log(p), axis=-1, keepdims=True)  # [B, 1]

        return {
            "regime_logits": regime_logits,
            "regime_probs":  regime_probs,
            "tradeability":  tradeability,
            "fwd_ret_pred":  fwd_ret_pred,
            "entropy":       entropy,
        }


# ============================================================
# LOSSES (fonctions pures — pas de sous-classes Keras Loss)
# ============================================================

def regime_loss_fn(
    y_true,
    logits,
    class_weights_tf=None,
    label_smoothing: float = 0.05,
    n_classes: int = 3,
):
    """
    Cross-entropy sur régime avec label smoothing et class weights optionnels.
    y_true : [B]   int32
    logits : [B, R] float32
    """
    y_oh = tf.one_hot(tf.cast(y_true, tf.int32), n_classes)
    eps  = label_smoothing
    y_smooth = y_oh * (1.0 - eps) + eps / float(n_classes)
    ce = tf.reduce_sum(-y_smooth * tf.nn.log_softmax(logits, axis=-1), axis=-1)  # [B]
    if class_weights_tf is not None:
        sample_w = tf.gather(tf.cast(class_weights_tf, tf.float32),
                             tf.cast(y_true, tf.int32))
        ce = ce * sample_w
    return tf.reduce_mean(ce)


def trade_loss_fn(y_true, pred):
    """
    BCE sur tradeability.
    y_true : [B, 1] float32   (0 ou 1)
    pred   : [B, 1] float32   sigmoid output
    """
    y = tf.cast(y_true, tf.float32)
    p = tf.cast(pred,   tf.float32)
    bce = -(y * tf.math.log(p + 1e-7) + (1.0 - y) * tf.math.log(1.0 - p + 1e-7))
    return tf.reduce_mean(bce)


def reg_loss_fn(y_true, pred):
    """
    MSE sur fwd_ret_pred.
    y_true, pred : [B, 1] float32
    """
    return tf.reduce_mean(tf.square(tf.cast(y_true, tf.float32) - tf.cast(pred, tf.float32)))


# ============================================================
# SMOKE TEST
# ============================================================
if __name__ == "__main__":
    cfg   = EventClassifierConfig(d_model=64, n_layers=3, n_regimes=3, dropout=0.2)
    model = EventClassifier(cfg)

    B, L, F = 16, 256, 48
    x   = tf.random.normal((B, L, F))
    out = model(x, training=False)

    print("regime_logits:", out["regime_logits"].shape)
    print("regime_probs :", out["regime_probs"].shape)
    print("tradeability :", out["tradeability"].shape)
    print("fwd_ret_pred :", out["fwd_ret_pred"].shape)
    print("entropy      :", out["entropy"].shape)

    # Quick loss smoke-test
    import numpy as np
    rng  = np.random.default_rng(0)
    yr   = tf.constant(rng.integers(0, 3, (B,)),          dtype=tf.int32)
    yt   = tf.constant(rng.integers(0, 2, (B, 1)),         dtype=tf.float32)
    yf   = tf.constant(np.tanh(rng.standard_normal((B, 1)) / 2.0), dtype=tf.float32)
    rl   = regime_loss_fn(yr, out["regime_logits"])
    tl   = trade_loss_fn(yt, out["tradeability"])
    gl   = reg_loss_fn(yf, out["fwd_ret_pred"])
    total = rl + 0.30 * tl + 0.15 * gl
    print(f"regime_loss={rl:.4f}  trade_loss={tl:.4f}  reg_loss={gl:.4f}  total={total:.4f}")
    print(f"Expected regime_loss ≈ {float(tf.math.log(3.0)):.4f} (log(3))")
