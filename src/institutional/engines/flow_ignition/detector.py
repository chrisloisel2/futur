"""
src/institutional/engines/flow_ignition/detector.py
─────────────────────────────────────────────────────────────────────────────
Détection CAUSALE d'IGNITIONS de flux : OI en expansion anormale (z ≥ +3 vs
7j passés) + taker ratio acheteur (z ≥ +1) + thrust prix positif (≥ +0.3% /
30 min) = de l'argent FRAIS entre côté long → continuation attendue (heures).

Distinct de LIQ_CASCADE (OI z ≤ −3 = compression) et de SHORT_SQUEEZE
(prix ↑ + OI ↓ = couverture, PF 0.94 mesuré) : ici OI ↑ AVEC le prix.
Émet les colonnes d'intensité génériques du pipeline (oi_drop_* signés).
Causalité : rolling passé only (shift(1)), warm-up 3j.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BARS_30M = 6
ROLL_7D = 2016


@dataclass
class IgnitionConfig:
    oi_exp_z_min: float = 3.0        # z d'EXPANSION d'OI 30-min
    taker_z_min: float = 1.0         # flux taker acheteur
    px_thrust_min: float = 0.003     # thrust prix 30-min ≥ +0.3%
    min_gap_bars: int = 12           # 1h min entre ignitions par symbole
    roll_bars: int = ROLL_7D
    min_warmup_bars: int = 864


def detect_ignitions(d: pd.DataFrame,
                     cfg: IgnitionConfig = IgnitionConfig()) -> pd.DataFrame:
    d = d.copy()
    oi = d["sum_open_interest"].astype(float)
    px = d["px"].astype(float)
    tk = d["sum_taker_long_short_vol_ratio"].astype(float)

    oi_ret_30m = oi.pct_change(BARS_30M)
    r = oi_ret_30m
    mu = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    oi_z = (r - mu) / sd.replace(0.0, np.nan)

    mu_t = tk.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd_t = tk.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    tk_z = (tk - mu_t) / sd_t.replace(0.0, np.nan)

    px_ret_30m = px.pct_change(BARS_30M)

    trigger = ((oi_z >= cfg.oi_exp_z_min) & (oi_ret_30m > 0)
               & (tk_z >= cfg.taker_z_min)
               & (px_ret_30m >= cfg.px_thrust_min) & px.notna())

    events, last_row = [], -10**9
    for i in np.flatnonzero(trigger.values):
        if i - last_row < cfg.min_gap_bars:
            continue
        last_row = i
        events.append({
            "row": int(i),
            "event_time": d["create_time"].iloc[i],
            "kind": "FLOW_IGNITION",
            "oi_drop_30m": float(oi_ret_30m.iloc[i]),      # >0 ici (expansion)
            "oi_drop_z": float(oi_z.iloc[i]),
            "px_ret_30m": float(px_ret_30m.iloc[i]),
            "taker_z_at": float(tk_z.iloc[i]),
            "px": float(px.iloc[i]),
        })
    return pd.DataFrame(events)
