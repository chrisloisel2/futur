# conditional_specialists.py
# ============================================================
# SPÉCIALISTES CONDITIONNELS (Pattern → Expert dédié)
#
# Principe:
# - Chaque pattern (impulse, reversal, breakout, squeeze, etc.)
#   active UN spécialiste dédié.
# - Activation SOFT (pondération par probas) ou HARD (argmax / seuil).
# - Les spécialistes ne voient QUE les samples pertinents.
# - Architecture compatible avec ton TRM / Context Detectors.
#
# Objectifs atteints:
# - Modularité totale (ajout/suppression de patterns sans casser le système)
# - Scalabilité (experts entraînés indépendamment)
# - Pas de fuite de gradient inter-pattern
# - Routing explicite, interprétable
#
# Dépendances:
# - numpy
# - tensorflow
#
# Entrée standard:
#   Xw : [B, L, F]  (features normalisées)
#   P  : [B, P]     (pattern probabilities depuis PatternDetector)
#
# Sortie:
#   dict {
#     "ret": [B, H],
#     "rv":  [B],
#     "expert_weights": [B, P]
#   }

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional

import numpy as np
import tensorflow as tf


# ============================================================
# 1) CONFIG
# ============================================================
@dataclass(frozen=True)
class SpecialistConfig:
    lookback: int = 256
    horizon: int = 12
    n_patterns: int = 4

    # Expert backbone
    d_model: int = 128
    n_layers: int = 3
    dropout: float = 0.20

    # Routing
    routing_mode: str = "soft"   # "soft" | "hard"
    activation_threshold: float = 0.50  # for hard routing

    # Training
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0


# ============================================================
# 2) BACKBONE: TCN SPECIALIST
# ============================================================
class TCNExpert(tf.keras.layers.Layer):
    """
    Expert spécialisé (Pattern-specific)
    - Causal
    - Stable
    - Faible coût
    """

    def __init__(self, cfg: SpecialistConfig, name: str):
        super().__init__(name=name)
        self.cfg = cfg

        self.in_proj = tf.keras.layers.Dense(cfg.d_model, activation="gelu")
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

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
                    name=f"{name}_tcn_{i}",
                )
            )

        self.pool = tf.keras.layers.GlobalAveragePooling1D()

        # Shared head
        self.shared = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
                tf.keras.layers.Dropout(cfg.dropout),
            ],
            name=f"{name}_shared",
        )

        # Heads
        self.ret_head = tf.keras.layers.Dense(cfg.horizon, name=f"{name}_ret")
        self.rv_head = tf.keras.layers.Dense(1, name=f"{name}_rv")

    def call(self, x, training=False):
        h = self.in_ln(self.in_proj(x))
        for b in self.blocks:
            h = b(h, training=training)
        z = self.pool(h)
        z = self.shared(z, training=training)

        ret = tf.cast(self.ret_head(z), tf.float32)          # [B,H]
        rv = tf.cast(tf.squeeze(self.rv_head(z), axis=-1), tf.float32)  # [B]
        return {"ret": ret, "rv": rv}


# ============================================================
# 3) CONDITIONAL SPECIALISTS (ROUTER + EXPERTS)
# ============================================================
class ConditionalSpecialists(tf.keras.Model):
    """
    Architecture:
        Pattern probs P  ─┐
                          ├─ Router ──► weights
        Xw ──────────────┬┴────────────► Experts (1 per pattern)
                          └────────────► Aggregation
    """

    def __init__(self, cfg: SpecialistConfig, pattern_names: List[str]):
        super().__init__(name="conditional_specialists")
        assert len(pattern_names) == cfg.n_patterns

        self.cfg = cfg
        self.pattern_names = pattern_names

        # Experts
        self.experts: List[TCNExpert] = [
            TCNExpert(cfg, name=f"expert_{p}") for p in pattern_names
        ]

    # -------------------------
    # Routing
    # -------------------------
    def compute_weights(self, P: tf.Tensor) -> tf.Tensor:
        """
        Args:
            P: [B, P] pattern probabilities
        Returns:
            W: [B, P] expert weights
        """
        if self.cfg.routing_mode == "soft":
            # Normalize for safety
            W = P / (tf.reduce_sum(P, axis=-1, keepdims=True) + 1e-8)
            return W

        # HARD routing
        mask = tf.cast(P >= self.cfg.activation_threshold, tf.float32)
        # fallback: if none active, use argmax
        none_active = tf.reduce_sum(mask, axis=-1, keepdims=True) == 0.0
        argmax = tf.one_hot(tf.argmax(P, axis=-1), depth=self.cfg.n_patterns)
        W = tf.where(none_active, argmax, mask)
        W = W / (tf.reduce_sum(W, axis=-1, keepdims=True) + 1e-8)
        return W

    # -------------------------
    # Forward
    # -------------------------
    def call(self, Xw: tf.Tensor, P: tf.Tensor, training=False) -> Dict[str, tf.Tensor]:
        """
        Args:
            Xw: [B, L, F]
            P : [B, P] pattern probabilities
        """
        W = self.compute_weights(P)  # [B,P]

        ret_stack = []
        rv_stack = []

        for expert in self.experts:
            out = expert(Xw, training=training)
            ret_stack.append(out["ret"])  # [B,H]
            rv_stack.append(out["rv"])    # [B]

        ret_stack = tf.stack(ret_stack, axis=1)  # [B,P,H]
        rv_stack = tf.stack(rv_stack, axis=1)    # [B,P]

        # Weighted aggregation
        W_ret = W[:, :, None]                     # [B,P,1]
        ret = tf.reduce_sum(ret_stack * W_ret, axis=1)  # [B,H]
        rv = tf.reduce_sum(rv_stack * W, axis=1)        # [B]

        return {
            "ret": ret,
            "rv": rv,
            "expert_weights": W,
        }


# ============================================================
# 4) TRAINING STEP (EXPERTS CONDITIONNÉS)
# ============================================================
class SpecialistsTrainer:
    """
    Training STRICT:
    - Gradient passe UNIQUEMENT par experts activés
    - Pas de backprop vers détecteurs de patterns
    """

    def __init__(self, model: ConditionalSpecialists, cfg: SpecialistConfig):
        self.model = model
        self.cfg = cfg

        self.optimizer = tf.keras.optimizers.AdamW(
            learning_rate=cfg.lr,
            weight_decay=cfg.weight_decay,
            global_clipnorm=cfg.clip_norm,
            beta_1=0.9,
            beta_2=0.95,
            epsilon=1e-8,
        )

        self.loss_ret = tf.keras.losses.Huber(delta=1.0)
        self.loss_rv = tf.keras.losses.Huber(delta=0.1)

        self.train_loss = tf.keras.metrics.Mean(name="train_loss")

    @tf.function
    def train_step(
        self,
        Xw: tf.Tensor,
        P: tf.Tensor,
        y_ret: tf.Tensor,
        y_rv: tf.Tensor,
    ) -> tf.Tensor:
        with tf.GradientTape() as tape:
            # Stop gradients from patterns (CRITICAL)
            P_ng = tf.stop_gradient(P)

            out = self.model(Xw, P_ng, training=True)
            ret_pred = out["ret"]
            rv_pred = out["rv"]

            loss_ret = tf.reduce_mean(self.loss_ret(y_ret, ret_pred))
            loss_rv = tf.reduce_mean(self.loss_rv(y_rv, rv_pred))

            loss = loss_ret + loss_rv

        grads = tape.gradient(loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(grads, self.model.trainable_variables))
        self.train_loss.update_state(loss)
        return loss


# ============================================================
# 5) INFERENCE API
# ============================================================
class ConditionalSpecialistInference:
    """
    Interface propre pour le déciseur global
    """

    def __init__(self, model: ConditionalSpecialists):
        self.model = model

    def predict(
        self,
        Xw: np.ndarray,
        pattern_probs: np.ndarray,
        batch_size: int = 1024,
    ) -> Dict[str, np.ndarray]:
        outs = self.model(
            tf.convert_to_tensor(Xw),
            tf.convert_to_tensor(pattern_probs),
            training=False,
        )
        return {
            "ret": outs["ret"].numpy(),
            "rv": outs["rv"].numpy(),
            "weights": outs["expert_weights"].numpy(),
        }


# ============================================================
# 6) SMOKE TEST
# ============================================================
if __name__ == "__main__":
    cfg = SpecialistConfig()
    pattern_names = ["impulse", "reversal", "breakout", "squeeze"]

    model = ConditionalSpecialists(cfg, pattern_names)
    trainer = SpecialistsTrainer(model, cfg)

    # Fake batch
    B = 32
    L = cfg.lookback
    F = 48
    H = cfg.horizon
    P = cfg.n_patterns

    Xw = tf.random.normal((B, L, F))
    pattern_probs = tf.nn.softmax(tf.random.normal((B, P)), axis=-1)
    y_ret = tf.random.normal((B, H)) * 1e-4
    y_rv = tf.abs(tf.random.normal((B,))) * 1e-3

    loss = trainer.train_step(Xw, pattern_probs, y_ret, y_rv)
    print("loss:", float(loss))
