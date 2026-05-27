# EdgeScorer.py
# ============================================================
# LEVEL 2 — EDGE SCORER (DIRECTIONAL, CONTINUOUS)
#
# Rôle :
# - Prédire un edge directionnel normalisé (R / RV)
# - UNIQUE source de direction du système
#
# Sortie :
#   {
#     "edge": [B]   (score continu, signé)
#     "rv":   [B]   (optionnel, supervision auxiliaire)
#   }
# ============================================================

from __future__ import annotations
from dataclasses import dataclass
import tensorflow as tf


# ============================================================
# CONFIG
# ============================================================
@dataclass(frozen=True)
class EdgeScorerConfig:
    d_model: int = 96
    n_layers: int = 3
    dropout: float = 0.15

    # Target scaling
    edge_cap: float = 5.0

    # Loss
    huber_delta: float = 1.0
    rv_loss_weight: float = 0.2

    # Stability
    eps: float = 1e-8


# ============================================================
# MODEL
# ============================================================
class EdgeScorer(tf.keras.Model):
    """
    Entrée:
      x: [B, L, F]

    Sortie:
      dict {
        edge: [B]  — score directionnel
        rv:   [B]  — volatilité future prédite (optionnelle)
      }
    """

    def __init__(self, cfg: EdgeScorerConfig):
        super().__init__(name="edge_scorer")
        self.cfg = cfg

        # ---------- Input ----------
        self.in_proj = tf.keras.layers.Dense(cfg.d_model, activation="gelu")
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        # ---------- Temporal encoder (TCN causal) ----------
        self.blocks = []
        for i in range(cfg.n_layers):
            self.blocks.append(
                tf.keras.Sequential(
                    [
                        tf.keras.layers.Conv1D(
                            cfg.d_model,
                            kernel_size=3,
                            padding="causal",
                            dilation_rate=2 ** i,
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6),
                        tf.keras.layers.Activation("gelu"),
                        tf.keras.layers.Dropout(cfg.dropout),
                    ],
                    name=f"tcn_block_{i}",
                )
            )

        self.pool = tf.keras.layers.GlobalAveragePooling1D()

        # ---------- Shared ----------
        self.shared = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model // 2, activation="gelu"),
                tf.keras.layers.Dropout(cfg.dropout),
            ]
        )

        # ---------- Heads ----------
        self.edge_head = tf.keras.layers.Dense(1, name="edge_head")
        self.rv_head = tf.keras.layers.Dense(1, name="rv_head")

    def call(self, x, training=False) -> dict:
        h = self.in_ln(self.in_proj(x))
        for b in self.blocks:
            h = b(h, training=training)

        z = self.pool(h)
        z = self.shared(z, training=training)

        edge = tf.squeeze(self.edge_head(z), axis=-1)
        rv = tf.squeeze(self.rv_head(z), axis=-1)

        return {
            "edge": tf.cast(edge, tf.float32),
            "rv": tf.cast(rv, tf.float32),
        }


# ============================================================
# LOSS (SUPERVISED, SIMPLE)
# ============================================================
class EdgeLoss(tf.keras.losses.Loss):
    """
    Supervision directe :
      edge_target = clip(R_future / RV_future, -cap, +cap)
    """

    def __init__(self, cfg: EdgeScorerConfig):
        super().__init__()
        self.cfg = cfg
        self.huber = tf.keras.losses.Huber(
            delta=cfg.huber_delta,
            reduction="none",
        )

    def call(self, y_true, y_pred):
        """
        y_true:
          {
            "edge": [B]
            "rv":   [B]
            "weight": [B] (optionnel)
          }
        """
        edge_t = tf.cast(y_true["edge"], tf.float32)
        rv_t = tf.cast(y_true["rv"], tf.float32)
        w = tf.cast(y_true.get("weight", 1.0), tf.float32)

        edge_p = y_pred["edge"]
        rv_p = tf.nn.softplus(y_pred["rv"])  # RV ≥ 0

        loss_edge = self.huber(edge_t, edge_p)
        loss_edge = tf.reduce_mean(loss_edge * w)

        loss_rv = self.huber(rv_t, rv_p)
        loss_rv = tf.reduce_mean(loss_rv * w)

        return loss_edge + self.cfg.rv_loss_weight * loss_rv
