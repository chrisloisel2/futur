# meta_decider/train_policy.py
from __future__ import annotations

import os
import numpy as np
import tensorflow as tf

from meta_decider.policy_network import (
    CFG,
    PolicyNet,
    PPOTrainer,
    pack_policy_inputs,
)


def build_policy_input_dim(example: dict) -> int:
    return int(example["tradeability_score"].shape[-1]
               + example["pattern_confidences"].shape[-1]
               + example["direction_probs"].shape[-1]
               + example["pairwise_score"].shape[-1]
               + example["event_probs"].shape[-1]
               + example["recent_model_performance"].shape[-1])


def train_policy_on_rollouts(
    rollouts: dict,
    save_dir: str = "meta_decider_out",
    ppo_epochs: int = 4,
    minibatch_size: int = 2048,
):
    """
    rollouts must contain:
      - tradeability_score:        [T, 1]
      - pattern_confidences:       [T, P]
      - direction_probs:           [T, 2 or 3]
      - pairwise_score:            [T, 3 or 1]
      - event_probs:               [T, 4]
      - recent_model_performance:  [T, K]
      - ret_next:                  [T] realized next-step return (log_ret or ret)
      - done:                      [T] episode boundary 0/1 (can be all zeros)
      - prev_action:               [T] previous action id (0/1/2) for turnover penalty
      - equity:                    [T] equity curve after step
      - action:                    [T] action actually taken during rollout (behavior policy)
      - old_logp:                  [T] logprob of that action under behavior policy
      - value_bootstrap:           [T+1] value estimates for GAE bootstrap (can be zeros at start)
    """

    os.makedirs(save_dir, exist_ok=True)

    # Build input vectors
    obs = pack_policy_inputs(
        rollouts["tradeability_score"],
        rollouts["pattern_confidences"],
        rollouts["direction_probs"],
        rollouts["pairwise_score"],
        rollouts["event_probs"],
        rollouts["recent_model_performance"],
    )  # [T, D]

    T, D = obs.shape
    model = PolicyNet(input_dim=D, cfg=CFG)

    # build
    _ = model(tf.zeros((1, D), tf.float32), training=False)

    trainer = PPOTrainer(model, cfg=CFG)

    # Compute rewards (risk-aware)
    rewards = trainer.reward_fn.compute(
        action=rollouts["action"],
        ret_next=rollouts["ret_next"],
        prev_action=rollouts["prev_action"],
        equity=rollouts["equity"],
    )

    # Values for GAE
    values = rollouts["value_bootstrap"].astype(np.float32)  # [T+1]
    dones = rollouts["done"].astype(np.float32)              # [T]
    adv, ret = trainer._gae(rewards, values, dones)

    # Convert to tensors
    obs_t = tf.convert_to_tensor(obs, tf.float32)
    act_t = tf.convert_to_tensor(rollouts["action"].astype(np.int32), tf.int32)
    old_logp_t = tf.convert_to_tensor(rollouts["old_logp"].astype(np.float32), tf.float32)
    adv_t = tf.convert_to_tensor(adv, tf.float32)
    ret_t = tf.convert_to_tensor(ret, tf.float32)

    # PPO epochs
    idx = np.arange(T)
    for ep in range(ppo_epochs):
        np.random.shuffle(idx)
        for s in range(0, T, minibatch_size):
            j = idx[s:s + minibatch_size]
            loss, pi_loss, v_loss, ent = trainer.train_minibatch(
                tf.gather(obs_t, j),
                tf.gather(act_t, j),
                tf.gather(old_logp_t, j),
                tf.gather(adv_t, j),
                tf.gather(ret_t, j),
            )
        print(f"[PPO] epoch={ep+1}/{ppo_epochs} loss={float(loss):.4f} pi={float(pi_loss):.4f} v={float(v_loss):.4f} ent={float(ent):.4f}")

    # Save
    model.save(os.path.join(save_dir, "policy.keras"))
    print(f"saved: {save_dir}/policy.keras")
    return model
