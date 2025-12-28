from __future__ import annotations

import numpy as np
import pandas as pd


def ood_novelty_l2(state_df: pd.DataFrame, ref_means: dict, ref_vars: dict) -> float:
    if state_df.empty:
        return 0.0
    last = state_df.iloc[-1]
    scores = []
    for col, mu in ref_means.items():
        var = ref_vars.get(col, 1.0) or 1.0
        scores.append(((float(last.get(col, 0)) - mu) ** 2) / var)
    return float(np.sqrt(np.mean(scores))) if scores else 0.0
