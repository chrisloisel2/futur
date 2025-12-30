# conditional_specialists.py
# ============================================================
# CONDITIONAL SPECIALISTS — VERSION CORRIGÉE
#
# Rôle :
# - Prédire des trajectoires conditionnelles (ret, rv)
# - UNIQUEMENT conditionné par un régime (P)
# - AUCUNE logique directionnelle globale
#
# Corrections appliquées :
# 1) Routage correct sans fuite de gradient : masquage strict par expert.
# 2) STOP_GRAD sur P et sur W (routing ne backprop pas dans EventClassifier).
# 3) Loss neutre (pas d'importance basée sur |y_ret|) : pas de biais "edge".
# 4) API stable : ret [B,H], rv [B], expert_weights [B,R]
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import tensorflow as tf


# ============================================================
# CONFIG
# ============================================================
@dataclass(frozen=True)
class SpecialistConfig:
    lookback: int = 256
    horizon: int = 12
    n_regimes: int = 4

    d_model: int = 128
    n_layers: int = 3
    dropout: float = 0.2

    # Routing
    routing_mode: str = "hard"      # "soft" | "hard"
    activation_threshold: float = 0.5
    min_active_weight: float = 1e-3

    # Training
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    # Num stability
    eps: float = 1e-8


# ============================================================
# EXPERT (INDÉPENDANT)
# ============================================================
class TCNExpert(tf.keras.layers.Layer):
    def __init__(self, cfg: SpecialistConfig, name: str):
        super().__init__(name=name)
        self.cfg = cfg

        self.in_proj = tf.keras.layers.Dense(cfg.d_model, use_bias=False, name=f"{name}_in_proj")
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6, name=f"{name}_in_ln")

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
                            use_bias=False,
                            name=f"{name}_tcn_conv_{i}",
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6, name=f"{name}_tcn_ln_{i}"),
                        tf.keras.layers.Activation("gelu", name=f"{name}_tcn_act_{i}"),
                        tf.keras.layers.Dropout(cfg.dropout, name=f"{name}_tcn_do_{i}"),
                    ],
                    name=f"{name}_tcn_block_{i}",
                )
            )

        self.pool = tf.keras.layers.GlobalAveragePooling1D(name=f"{name}_pool")

        self.head = tf.keras.Sequential(
            [
                tf.keras.layers.Dense(cfg.d_model, activation="gelu", name=f"{name}_head_dense"),
                tf.keras.layers.Dropout(cfg.dropout, name=f"{name}_head_do"),
            ],
            name=f"{name}_head",
        )

        self.ret_head = tf.keras.layers.Dense(cfg.horizon, name=f"{name}_ret_head")
        self.rv_head = tf.keras.layers.Dense(1, name=f"{name}_rv_head")

    def call(self, x: tf.Tensor, training: bool = False):
        h = self.in_ln(self.in_proj(x))
        for b in self.blocks:
            h = b(h, training=training)

        z = self.pool(h)
        z = self.head(z, training=training)

        ret = self.ret_head(z)                    # [B, H]
        rv = tf.squeeze(self.rv_head(z), axis=-1) # [B]
        return tf.cast(ret, tf.float32), tf.cast(rv, tf.float32)


# ============================================================
# ROUTER + SPECIALISTS
# ============================================================
class ConditionalSpecialists(tf.keras.Model):
    """
    Entrées :
      Xw : [B, L, F]
      P  : [B, R]  (regime_probs) -> STOP_GRAD
    Sorties :
      ret: [B, H]
      rv:  [B]
      expert_weights: [B, R]
    """

    def __init__(self, cfg: SpecialistConfig, regime_names: List[str]):
        super().__init__(name="conditional_specialists")
        if len(regime_names) != cfg.n_regimes:
            raise ValueError("len(regime_names) must match cfg.n_regimes")

        self.cfg = cfg
        self.regime_names = regime_names
        self.experts = [TCNExpert(cfg, name=f"expert_{r}") for r in regime_names]

    # -------------------------
    # ROUTING
    # -------------------------
    def compute_weights(self, P: tf.Tensor) -> tf.Tensor:
        """
        P: [B,R] (probas)
        Retour:
          W: [B,R] normalisé, >=0
        """
        eps = self.cfg.eps

        if self.cfg.routing_mode == "soft":
            W = tf.nn.relu(P)
            W = tf.where(W >= self.cfg.min_active_weight, W, 0.0)
            return W / (tf.reduce_sum(W, axis=-1, keepdims=True) + eps)

        # HARD routing
        active = tf.cast(P >= self.cfg.activation_threshold, tf.float32)  # [B,R]
        none = tf.reduce_sum(active, axis=-1, keepdims=True) == 0.0       # [B,1]
        fallback = tf.one_hot(tf.argmax(P, axis=-1), depth=self.cfg.n_regimes, dtype=tf.float32)  # [B,R]
        W = tf.where(none, fallback, active)
        return W / (tf.reduce_sum(W, axis=-1, keepdims=True) + eps)

    # -------------------------
    # FORWARD
    # -------------------------
    def call(self, Xw: tf.Tensor, P: tf.Tensor, training: bool = False) -> Dict[str, tf.Tensor]:
        """
        Masquage strict par expert pour éviter la fuite de gradient.
        """
        P = tf.stop_gradient(P)
        W = self.compute_weights(P)               # [B,R]
        W = tf.stop_gradient(W)                  # routing figé

        B = tf.shape(Xw)[0]
        H = self.cfg.horizon

        ret_out = tf.zeros((B, H), dtype=tf.float32)
        rv_out = tf.zeros((B,), dtype=tf.float32)

        # Chaque expert ne contribue que pour ses samples actifs
        for i, expert in enumerate(self.experts):
            w_i = W[:, i]                         # [B]
            mask = tf.cast(w_i > 0.0, tf.float32)  # [B]
            mask = tf.stop_gradient(mask)

            # Option: skip compute si personne d'actif
            if tf.reduce_any(mask > 0.0):
                ret_i, rv_i = expert(Xw, training=training)  # [B,H], [B]
                ret_out += ret_i * (w_i * mask)[:, None]
                rv_out += rv_i * (w_i * mask)

        return {
            "ret": ret_out,
            "rv": rv_out,
            "expert_weights": W,
        }


# ============================================================
# TRAINER
# ============================================================
class SpecialistsTrainer:
    def __init__(self, model: ConditionalSpecialists, cfg: SpecialistConfig):
        self.model = model
        self.cfg = cfg

        self.optimizer = tf.keras.optimizers.AdamW(
            learning_rate=cfg.lr,
            weight_decay=cfg.weight_decay,
            global_clipnorm=cfg.clip_norm,
        )

        self.loss_ret = tf.keras.losses.Huber(delta=1.0, reduction="none")  # per-element
        self.loss_rv = tf.keras.losses.Huber(delta=0.1, reduction="none")   # per-sample

    @tf.function
    def train_step(self, Xw: tf.Tensor, P: tf.Tensor, y_ret: tf.Tensor, y_rv: tf.Tensor) -> tf.Tensor:
        """
        Loss neutre :
        - pas de pondération par |y_ret|
        - pas de logique edge dans ce module
        """
        P = tf.stop_gradient(P)

        with tf.GradientTape() as tape:
            out = self.model(Xw, P, training=True)
            ret_pred = out["ret"]   # [B,H]
            rv_pred = out["rv"]     # [B]

            # ret loss: mean over horizon, then mean over batch
            loss_ret = tf.reduce_mean(self.loss_ret(y_ret, ret_pred), axis=-1)  # [B]
            loss_rv = self.loss_rv(y_rv, rv_pred)                               # [B]

            loss = tf.reduce_mean(loss_ret + loss_rv)

        grads = tape.gradient(loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(grads, self.model.trainable_variables))
        return loss


# ============================================================
# INFERENCE
# ============================================================
class ConditionalSpecialistInference:
    def __init__(self, model: ConditionalSpecialists):
        self.model = model

    def predict(self, Xw: np.ndarray, regime_probs: np.ndarray) -> Dict[str, np.ndarray]:
        out = self.model(
            tf.convert_to_tensor(Xw, dtype=tf.float32),
            tf.convert_to_tensor(regime_probs, dtype=tf.float32),
            training=False,
        )
        return {k: v.numpy() for k, v in out.items()}
