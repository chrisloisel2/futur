# meta_decider/example_integration.py
from __future__ import annotations

import numpy as np

from meta_decider.policy_network import PolicyNet, MetaDecider, pack_policy_inputs


def example_single_bar():
    """
    Example using your data schema.
    Level outputs shown here are placeholders; in your pipeline they come from:
      - gatingGlobal/
      - context/
      - conditional/
      - EventClassifier.py
      - PairwiseComparator.py
      - level3_decision.py (optional extra rule layer)

    This file demonstrates the exact input contract of Level 4.
    """

    # ----- Level 0
    tradeability_score = np.array([[0.82]], dtype=np.float32)  # [B,1]

    # ----- Level 1 (orthogonal contexts)
    # ex: [trend_strength, mean_revert_strength, high_vol, low_vol, range]
    pattern_confidences = np.array([[0.10, 0.05, 0.70, 0.05, 0.10]], dtype=np.float32)  # [B,P]

    # ----- Level 2 (specialist direction)
    # binary
    direction_probs = np.array([[0.25, 0.75]], dtype=np.float32)  # [B,2] (DOWN, UP)

    # ----- Level 3a (pairwise)
    # [CONSISTENT, WEAKENING, CONTRADICTION]
    pairwise_score = np.array([[0.62, 0.28, 0.10]], dtype=np.float32)  # [B,3]

    # ----- Level 3b (event)
    # [NORMAL, EVENT_UP, EVENT_DOWN, VOL_SHOCK]
    event_probs = np.array([[0.30, 0.10, 0.05, 0.55]], dtype=np.float32)  # [B,4]

    # ----- recent_model_performance (rolling stats)
    # ex: [ema_acc_dir, ema_mae_ret_h1, ema_pnl, winrate, max_dd]
    recent_model_performance = np.array([[0.58, 0.00042, 0.0012, 0.54, 0.006]], dtype=np.float32)  # [B,K]

    x = pack_policy_inputs(
        tradeability_score,
        pattern_confidences,
        direction_probs,
        pairwise_score,
        event_probs,
        recent_model_performance,
    )

    # Load trained policy; here we just build a dummy model.
    policy = PolicyNet(input_dim=x.shape[-1])
    _ = policy(np.zeros((1, x.shape[-1]), np.float32))

    brain = MetaDecider(policy)
    out = brain.decide(x)

    print(out)


if __name__ == "__main__":
    example_single_bar()
