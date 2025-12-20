# meta_decider/policy_network.py
from __future__ import annotations

import os
import math
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import numpy as np
import tensorflow as tf


# ============================================================
# CONFIG
# ============================================================
@dataclass(frozen=True)
class PolicyConfig:
    # Action space
    n_actions: int = 3  # BUY, SELL, WAIT

    # Network
    d_model: int = 128
    n_layers: int = 3
    dropout: float = 0.15

    # RL / Risk
    gamma: float = 0.98
    lam: float = 0.95                 # GAE
    clip_ratio: float = 0.20          # PPO
    vf_coef: float = 0.5
    ent_coef: float = 0.01

    # Reward shaping weights
    w_pnl: float = 1.0                # realized return
    w_error_cost: float = 0.35        # penalize wrong direction / bad trades
    w_drawdown: float = 0.50          # penalize drawdown
    w_turnover: float = 0.02          # penalize action changes / overtrading

    # Training
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0


CFG = PolicyConfig()

ACTION_NAMES = ["BUY", "SELL", "WAIT"]


# ============================================================
# INPUT PACKER (ADAPTS TO YOUR PIPELINE OUTPUTS)
# ============================================================
def pack_policy_inputs(
    tradeability_score: np.ndarray,        # [B, 1]
    pattern_confidences: np.ndarray,       # [B, P]
    direction_probs: np.ndarray,           # [B, 2] or [B, 3]
    pairwise_score: np.ndarray,            # [B, 3] probs or [B,1]
    event_probs: np.ndarray,               # [B, 4]
    recent_model_performance: np.ndarray,  # [B, K]
) -> np.ndarray:
    """
    Concatenate policy inputs into a single vector.

    Notes:
    - pairwise_score can be either:
        * probabilities [CONSISTENT, WEAKENING, CONTRADICTION] -> [B,3]
        * scalar confidence -> [B,1]
    - direction_probs can be:
        * binary -> [B,2]
        * ternary -> [B,3]
    """
    parts = [
        tradeability_score.astype(np.float32),
        pattern_confidences.astype(np.float32),
        direction_probs.astype(np.float32),
        pairwise_score.astype(np.float32),
        event_probs.astype(np.float32),
        recent_model_performance.astype(np.float32),
    ]
    return np.concatenate(parts, axis=-1).astype(np.float32)


# ============================================================
# POLICY NETWORK (ACTOR-CRITIC)
# ============================================================
class PolicyNet(tf.keras.Model):
    """
    Output:
      - logits: [B, 3]  (BUY, SELL, WAIT)
      - value:  [B]     state value

    confidence ∈ [0,1] is derived from max softmax probability.
    """

    def __init__(self, input_dim: int, cfg: PolicyConfig = CFG, name="policy_net"):
        super().__init__(name=name)
        self.cfg = cfg

        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        blocks = []
        d = cfg.d_model
        for _ in range(cfg.n_layers):
            blocks.extend([
                tf.keras.layers.Dense(d, activation="gelu"),
                tf.keras.layers.Dropout(cfg.dropout),
                tf.keras.layers.LayerNormalization(epsilon=1e-6),
            ])
        self.backbone = tf.keras.Sequential(blocks, name="backbone")

        self.actor = tf.keras.Sequential([
            tf.keras.layers.Dense(d, activation="gelu"),
            tf.keras.layers.Dense(cfg.n_actions),
        ], name="actor")

        self.critic = tf.keras.Sequential([
            tf.keras.layers.Dense(d, activation="gelu"),
            tf.keras.layers.Dense(1),
        ], name="critic")

    def call(self, x, training=False):
        x = self.in_ln(x)
        h = self.backbone(x, training=training)
        logits = self.actor(h, training=training)
        value = self.critic(h, training=training)
        value = tf.squeeze(value, axis=-1)
        return logits, value

    @staticmethod
    def action_and_confidence_from_logits(logits: tf.Tensor) -> Tuple[tf.Tensor, tf.Tensor]:
        probs = tf.nn.softmax(logits, axis=-1)
        action = tf.argmax(probs, axis=-1, output_type=tf.int32)
        confidence = tf.reduce_max(probs, axis=-1)
        return action, confidence


# ============================================================
# RISK-AWARE REWARD (REALIZED + ERROR COST + DRAWDOWN)
# ============================================================
class RiskReward:
    """
    Reward based on:
      - realized reward (pnl proxy)
      - cost of error (direction mismatch)
      - drawdown penalty
      - turnover penalty
    """

    def __init__(self, cfg: PolicyConfig = CFG):
        self.cfg = cfg

    @staticmethod
    def _drawdown(equity_curve: np.ndarray) -> np.ndarray:
        peak = np.maximum.accumulate(equity_curve)
        dd = (equity_curve - peak)  # negative
        return dd

    def compute(
        self,
        action: np.ndarray,         # [B] 0/1/2 BUY/SELL/WAIT
        ret_next: np.ndarray,       # [B] realized next-step log_ret or ret
        prev_action: np.ndarray,    # [B]
        equity: np.ndarray,         # [B] equity curve after applying action
    ) -> np.ndarray:
        """
        ret_next: realized return for the next step. If log_ret, it's fine; keep consistent.
        equity: equity after step update (used for drawdown penalty).
        """
        cfg = self.cfg

        # pnl proxy:
        # BUY  -> +ret_next
        # SELL -> -ret_next
        # WAIT -> 0
        pnl = np.zeros_like(ret_next, dtype=np.float32)
        pnl[action == 0] = ret_next[action == 0]
        pnl[action == 1] = -ret_next[action == 1]
        pnl[action == 2] = 0.0

        # error cost: punish taking the wrong side when move is meaningful
        # Use sign mismatch cost. If WAIT, no error cost.
        move_sign = np.sign(ret_next).astype(np.float32)  # -1/0/1
        act_sign = np.zeros_like(move_sign)
        act_sign[action == 0] = 1.0
        act_sign[action == 1] = -1.0
        act_sign[action == 2] = 0.0

        wrong = (act_sign != 0) & (move_sign != 0) & (np.sign(act_sign) != np.sign(move_sign))
        error_cost = np.zeros_like(ret_next, dtype=np.float32)
        error_cost[wrong] = 1.0

        # drawdown penalty
        dd = self._drawdown(equity)  # negative or 0
        dd_pen = -dd.astype(np.float32)  # positive penalty when drawdown exists

        # turnover penalty
        turnover = (action != prev_action).astype(np.float32)

        reward = (
            cfg.w_pnl * pnl
            - cfg.w_error_cost * error_cost
            - cfg.w_drawdown * dd_pen
            - cfg.w_turnover * turnover
        ).astype(np.float32)

        return reward


# ============================================================
# PPO TRAINER (ON-POLICY)
# ============================================================
class PPOTrainer:
    def __init__(self, model: PolicyNet, cfg: PolicyConfig = CFG):
        self.model = model
        self.cfg = cfg
        self.reward_fn = RiskReward(cfg)

        self.opt = tf.keras.optimizers.AdamW(
            learning_rate=cfg.lr,
            weight_decay=cfg.weight_decay,
            beta_1=0.9,
            beta_2=0.95,
            epsilon=1e-8,
            global_clipnorm=cfg.clip_norm,
        )

    @staticmethod
    def _logprob_from_logits(logits: tf.Tensor, actions: tf.Tensor) -> tf.Tensor:
        logp_all = tf.nn.log_softmax(logits, axis=-1)
        idx = tf.stack([tf.range(tf.shape(actions)[0]), actions], axis=1)
        return tf.gather_nd(logp_all, idx)

    def _gae(self, rewards, values, dones):
        """
        rewards: [T]
        values:  [T+1]  bootstrap
        dones:   [T]    0/1
        """
        cfg = self.cfg
        T = rewards.shape[0]
        adv = np.zeros(T, dtype=np.float32)
        gae = 0.0
        for t in reversed(range(T)):
            delta = rewards[t] + cfg.gamma * values[t + 1] * (1.0 - dones[t]) - values[t]
            gae = delta + cfg.gamma * cfg.lam * (1.0 - dones[t]) * gae
            adv[t] = gae
        ret = adv + values[:-1]
        return adv, ret

    @tf.function
    def train_minibatch(
        self,
        obs: tf.Tensor,            # [B, D]
        act: tf.Tensor,            # [B]
        old_logp: tf.Tensor,       # [B]
        adv: tf.Tensor,            # [B]
        ret: tf.Tensor,            # [B]
    ):
        cfg = self.cfg

        with tf.GradientTape() as tape:
            logits, v = self.model(obs, training=True)
            logp = self._logprob_from_logits(logits, act)

            ratio = tf.exp(logp - old_logp)

            adv_n = (adv - tf.reduce_mean(adv)) / (tf.math.reduce_std(adv) + 1e-8)

            clip_adv = tf.clip_by_value(ratio, 1.0 - cfg.clip_ratio, 1.0 + cfg.clip_ratio) * adv_n
            pi_loss = -tf.reduce_mean(tf.minimum(ratio * adv_n, clip_adv))

            v_loss = tf.reduce_mean((ret - v) ** 2)

            ent = -tf.reduce_mean(tf.reduce_sum(tf.nn.softmax(logits) * tf.nn.log_softmax(logits), axis=-1))

            loss = pi_loss + cfg.vf_coef * v_loss - cfg.ent_coef * ent

        grads = tape.gradient(loss, self.model.trainable_variables)
        self.opt.apply_gradients(zip(grads, self.model.trainable_variables))
        return loss, pi_loss, v_loss, ent


# ============================================================
# META-DECIDER (INFERENCE API)
# ============================================================
class MetaDecider:
    """
    This is the 'brain':
      - consumes outputs from Levels 0..3
      - outputs action + confidence
      - does NOT vote; it executes a policy
    """

    def __init__(self, policy: PolicyNet):
        self.policy = policy

    def decide(self, inputs_vec: np.ndarray) -> Dict[str, np.ndarray]:
        """
        inputs_vec: [B, D]
        returns:
          action_name: [B] str
          action_id: [B] int
          confidence: [B] float in [0,1]
          probs: [B,3]
        """
        x = tf.convert_to_tensor(inputs_vec, dtype=tf.float32)
        logits, _ = self.policy(x, training=False)
        probs = tf.nn.softmax(logits, axis=-1)
        act = tf.argmax(probs, axis=-1, output_type=tf.int32)
        conf = tf.reduce_max(probs, axis=-1)

        act_np = act.numpy()
        out = {
            "action_id": act_np,
            "action_name": np.array([ACTION_NAMES[i] for i in act_np], dtype=object),
            "confidence": conf.numpy(),
            "probs": probs.numpy(),
        }
        return out
