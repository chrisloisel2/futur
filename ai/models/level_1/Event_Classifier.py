# event_classifier.py
# ============================================================
# EVENT REGIME / TRADEABILITY CLASSIFIER (ROUTING ONLY)
#
# But :
# - Modèle NON directionnel.
# - Produit un signal "exploitable vs bruit" pour gater l'EdgeScorer / Specialists.
#
# Sorties (API stable) :
#   {
#     "regime_logits": [B, R]   (R = n_regimes, optionnel selon ton training)
#     "regime_probs":  [B, R]
#     "confidence":    [B, 1]   (proba que le contexte est exploitable)
#     "entropy":       [B, 1]   (incertitude du routing)
#   }
#
# Notes :
# - Pas de softmax "interne" présenté comme vérité : on expose logits + probs.
# - Entropy fournie pour pénaliser les contextes ambigus.
# - Confidence head séparé, calibrable indépendamment.
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

    # Régimes (routing). 4 par défaut : impulse, reversal, breakout, squeeze (ou autre)
    n_regimes: int = 4

    dropout: float = 0.2
    confidence_dropout: float = 0.1

    # Num stability
    eps: float = 1e-8


# ============================================================
# MODEL
# ============================================================
class EventClassifier(tf.keras.Model):
    """
    Détecteur de régimes / tradeability (NON directionnel)

    Entrée:
      x: [B, L, F]

    Sortie:
      dict {
        regime_logits: [B, R]
        regime_probs:  [B, R]
        confidence:    [B, 1]
        entropy:       [B, 1]
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

        # Confidence exploitable vs bruit
        self.confidence_head = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model // 2, activation="gelu", name="conf_dense"),
                tf.keras.layers.Dropout(cfg.confidence_dropout, name="conf_do"),
                tf.keras.layers.Dense(1, activation="sigmoid", name="confidence"),
            ],
            name="confidence_head",
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
        regime_logits = self.regime_logits_head(z)          # [B, R]
        regime_probs = tf.nn.softmax(regime_logits, axis=-1)  # [B, R]

        confidence = self.confidence_head(z)                # [B, 1]

        # Entropy (incertitude)
        p = tf.clip_by_value(regime_probs, self.cfg.eps, 1.0)
        entropy = -tf.reduce_sum(p * tf.math.log(p), axis=-1, keepdims=True)  # [B, 1]

        return {
            "regime_logits": regime_logits,
            "regime_probs": regime_probs,
            "confidence": confidence,
            "entropy": entropy,
        }


# ============================================================
# LOSSES (OPTIONNEL)
# ============================================================
class RegimeLoss(tf.keras.losses.Loss):
    """
    CE sur régime + option: pénaliser forte entropie (routing incertain)
    """
    def __init__(self, entropy_weight: float = 0.0):
        super().__init__()
        self.entropy_weight = float(entropy_weight)
        self.ce = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True, reduction="none")

    def call(self, y_true, regime_logits, entropy=None):
        base = self.ce(y_true, regime_logits)  # [B]
        loss = tf.reduce_mean(base)
        if self.entropy_weight > 0.0 and entropy is not None:
            loss = loss + self.entropy_weight * tf.reduce_mean(tf.cast(entropy, tf.float32))
        return loss


class ConfidenceLoss(tf.keras.losses.Loss):
    """
    BCE sur confidence (labels : 1 = exploitable, 0 = bruit)
    """
    def __init__(self):
        super().__init__()
        self.bce = tf.keras.losses.BinaryCrossentropy(from_logits=False, reduction="none")

    def call(self, y_true, confidence):
        y = tf.cast(y_true, tf.float32)
        c = tf.cast(confidence, tf.float32)
        return tf.reduce_mean(self.bce(y, c))


# ============================================================
# SMOKE TEST
# ============================================================
if __name__ == "__main__":
    cfg = EventClassifierConfig(d_model=64, n_layers=3, n_regimes=4, dropout=0.2)
    model = EventClassifier(cfg)

    B, L, F = 16, 256, 48
    x = tf.random.normal((B, L, F))
    out = model(x, training=False)

    print("regime_logits:", out["regime_logits"].shape)
    print("regime_probs :", out["regime_probs"].shape)
    print("confidence   :", out["confidence"].shape)
    print("entropy      :", out["entropy"].shape)
