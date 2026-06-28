"""
src/institutional/labels/carry_labels.py
─────────────────────────────────────────────────────────────────────────────
Labels pour CARRY_ENGINE.

Question centrale :
    "Le funding est-il capturable SANS se faire détruire par le mouvement de prix ?"

Label net_carry :
    +1 si funding_collected - |price_move_adverse| - fees > 0
    -1 si net carry négatif (on perd de l'argent)
     0 si dans la zone neutre

Le carry label est défini sur une fenêtre spécifique (horizon 8h ou 24h)
car le funding se règle toutes les 8h sur Binance.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# Fréquence de règlement du funding sur Binance : 3 fois/jour
FUNDING_PERIODS_PER_DAY = 3
FUNDING_PERIOD_HOURS     = 8


def net_carry_label(
    close: pd.Series,
    funding_rate: pd.Series,      # taux par période (8h)
    vol_annual: pd.Series,
    horizon_bars: int = 24,       # en barres 1h
    *,
    cost_bps: float = 10.0,
    adverse_move_k: float = 0.5,  # fraction de vol_horizon tolérée
    bars_per_year: int = 8760,
) -> pd.Series:
    """
    Label de capture de carry net — Convention Binance CORRECTE.

    funding > 0 → longs paient shorts → harvest = être SHORT
    funding < 0 → shorts paient longs → harvest = être LONG

    carry_received = |funding_rate| × (horizon_bars / 8)
        → toujours positif (on harvest peu importe le signe)

    adverse_move :
        si funding > 0 (SHORT) : max(0, +fwd_ret)  — perd si prix monte
        si funding < 0 (LONG)  : max(0, -fwd_ret)  — perd si prix baisse

    net_carry = carry_received - adverse_move - cost_fraction

    UP   (+1) : net_carry > cost_fraction × 0.5
    DOWN (-1) : net_carry < -cost_fraction
    FLAT  (0) : intermédiaire
    """
    cost_frac = cost_bps / 10_000.0

    # Convention Binance CORRECTE (fix v2 2026-05-31) :
    #   funding_rate > 0 → les LONGS paient les SHORTS
    #     → harvest = être SHORT (recevoir le funding)
    #     → adverse = hausse des prix (perte pour le short)
    #   funding_rate < 0 → les SHORTS paient les LONGS
    #     → harvest = être LONG (recevoir le funding)
    #     → adverse = baisse des prix (perte pour le long)
    n_periods      = horizon_bars / FUNDING_PERIOD_HOURS
    carry_received = funding_rate.abs() * n_periods   # toujours positif

    fwd_ret = np.log(close.shift(-horizon_bars) / close)

    adverse = np.where(
        funding_rate > 0,
        fwd_ret.clip(lower=0),             # SHORT : perd si prix monte
        (-fwd_ret).clip(lower=0),          # LONG  : perd si prix baisse
    )
    adverse_series = pd.Series(adverse, index=close.index)

    net = carry_received - adverse_series - cost_frac

    label = pd.Series(0, index=close.index, dtype=np.int8)
    label[net >  cost_frac * 0.5] = 1
    label[net < -cost_frac]       = -1
    label[fwd_ret.isna()]         = pd.NA
    return label.astype("Int8")


def build_carry_labels(
    close: pd.Series,
    funding_rate: pd.Series,
    vol_annual: pd.Series,
    horizons_h: Optional[list] = None,
    cost_bps: float = 10.0,
) -> pd.DataFrame:
    """
    Construit tous les labels carry.
    Horizons par défaut : [8, 24, 72]
    """
    if horizons_h is None:
        horizons_h = [8, 24, 72]

    out = pd.DataFrame(index=close.index)
    for h in horizons_h:
        out[f"carry_net_{h}h"] = net_carry_label(
            close, funding_rate, vol_annual, horizon_bars=h, cost_bps=cost_bps
        )
    return out
