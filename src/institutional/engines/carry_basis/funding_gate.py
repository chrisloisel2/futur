"""
src/institutional/engines/carry_basis/funding_gate.py
─────────────────────────────────────────────────────────────────────────────
Gate de régime de funding (Phase 37.5).

Le stress funding-flip casse le carry always-on. Le carry n'est donc actif que
si le régime de funding est POSITIF et STABLE. Tout est calculé sur une fenêtre
PASSÉE (aucun lookahead).

Régimes :
    FUNDING_POSITIVE_STABLE   → carry autorisé
    FUNDING_POSITIVE_UNSTABLE → non
    FUNDING_NEUTRAL           → non
    FUNDING_NEGATIVE          → non (sortie forcée)
    FUNDING_FLIP_RISK         → non (sortie forcée)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CARRY_OK = "FUNDING_POSITIVE_STABLE"


@dataclass
class FundingGateConfig:
    window_periods: int = 21          # ~7 jours de funding (3/jour)
    min_mean: float = 1e-5            # funding moyen 7j > 0 (net de borrow ~0.9e-5)
    min_positive_ratio: float = 0.65  # ≥65% des périodes positives
    max_flip_count: int = 6           # ≤6 changements de signe sur la fenêtre
    max_abs_zscore: float = 3.0       # pas de funding extrême


def classify_funding_regime(funding_window: pd.Series, cfg: FundingGateConfig = FundingGateConfig()) -> str:
    f = funding_window.dropna()
    if len(f) < max(5, cfg.window_periods // 2):
        return "FUNDING_NEUTRAL"
    mean = float(f.mean())
    pos_ratio = float((f > 0).mean())
    flips = int((np.sign(f).diff().fillna(0) != 0).sum())
    std = float(f.std()) or 1e-9
    z = abs(float(f.iloc[-1] - mean) / std)

    if mean < 0:
        return "FUNDING_NEGATIVE"
    if z > cfg.max_abs_zscore:
        return "FUNDING_FLIP_RISK"
    if mean >= cfg.min_mean and pos_ratio >= cfg.min_positive_ratio and flips <= cfg.max_flip_count:
        return CARRY_OK
    if mean > 0:
        return "FUNDING_POSITIVE_UNSTABLE"
    return "FUNDING_NEUTRAL"


def carry_allowed(funding_window: pd.Series, cfg: FundingGateConfig = FundingGateConfig()) -> bool:
    return classify_funding_regime(funding_window, cfg) == CARRY_OK
