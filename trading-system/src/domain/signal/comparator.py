from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd


@dataclass
class ComparatorOutput:
    novelty_score: float
    disagreement_score: float


def compute_novelty(state: pd.DataFrame, reference_stats: Dict[str, Dict[str, float]]) -> float:
    if state.empty or not reference_stats:
        return 0.0
    cols = [c for c in state.columns if c in reference_stats]
    if not cols:
        return 0.0
    diffs = []
    for c in cols:
        mu = reference_stats[c].get("mean", 0.0)
        sigma = reference_stats[c].get("std", 1.0) or 1.0
        diffs.append(((state[c].iloc[-1] - mu) / sigma) ** 2)
    return float(np.sqrt(np.mean(diffs)))


def compute_disagreement(regime_probs: Dict[str, float], edge_outputs: Dict[str, float], specialists_outputs: Optional[Dict[str, float]] = None) -> float:
    probs = np.array(list(regime_probs.values())) if regime_probs else np.array([0.0])
    entropy = -np.nansum(probs * np.log(probs + 1e-9))
    disp = np.nanstd(list(edge_outputs.values())) if edge_outputs else 0.0
    spec_var = np.nanstd(list((specialists_outputs or {}).values())) if specialists_outputs else 0.0
    return float(entropy + disp + spec_var)
