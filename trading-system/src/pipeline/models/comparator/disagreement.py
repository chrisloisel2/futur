from __future__ import annotations

import numpy as np


def disagreement_score(regime_probs: dict, quantiles: dict, experts: dict | None = None) -> float:
    probs = np.array(list(regime_probs.values())) if regime_probs else np.array([0.0])
    ent = -np.nansum(probs * np.log(probs + 1e-9))
    qs = np.array(list(quantiles.values())) if quantiles else np.array([0.0])
    disp = float(np.nanstd(qs))
    exp_var = float(np.nanstd(list(experts.values()))) if experts else 0.0
    return float(ent + disp + exp_var)
