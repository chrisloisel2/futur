"""
src/institutional/engines/_labels.py
─────────────────────────────────────────────────────────────────────────────
Fonctions de label partagées par les moteurs ML (signature commune).

    label_fn(df, horizon_hours, cost) -> pd.Series {0,1} (NaN où non calculable)

Le label est "rendement forward net favorable" : le modèle apprend QUAND le
setup paye. Les conditions de setup (drawdown, oversold, funding…) sont des
FEATURES, pas des filtres durs sur le label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _forward_net_return(df: pd.DataFrame, horizon_hours: float, cost: float) -> pd.Series:
    h = max(1, int(round(horizon_hours)))
    fwd = df["close"].shift(-h) / df["close"] - 1.0
    return fwd - cost


def label_forward_up(df: pd.DataFrame, horizon_hours: float, cost: float,
                     threshold: float = 0.005) -> pd.Series:
    """1 si le rendement forward net dépasse threshold (long payant)."""
    net = _forward_net_return(df, horizon_hours, cost)
    lab = (net > threshold).astype(float)
    return lab.where(net.notna())


def label_rebound(df: pd.DataFrame, horizon_hours: float, cost: float,
                  threshold: float = 0.0) -> pd.Series:
    """1 si rebond net positif sur l'horizon court (capitulation → rebond)."""
    net = _forward_net_return(df, horizon_hours, cost)
    lab = (net > threshold).astype(float)
    return lab.where(net.notna())
