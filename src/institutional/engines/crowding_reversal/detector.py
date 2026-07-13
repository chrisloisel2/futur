"""
src/institutional/engines/crowding_reversal/detector.py
─────────────────────────────────────────────────────────────────────────────
Détection CAUSALE d'états de CAPITULATION DE POSITIONNEMENT (washout).

Un washout = les top-traders ont capitulé (ratio long/short z ≤ −2 vs 7j
passés) PENDANT que l'OI s'est purgé sur 24h (deleveraging accumulé, pas un
crash 30-min — c'est ce qui le distingue de LIQ_CASCADE). Thèse contrarian :
le positionnement vendeur est épuisé → rebond à horizon 24h.

État (pas event ponctuel) → gap minimal 24h par symbole. Émet les mêmes
colonnes d'intensité que le détecteur cascade (oi_drop_30m/z, px_ret_30m)
pour brancher le MÊME pipeline features/labels (dataset.build_event_dataset).
Causalité : stats glissantes passées uniquement (shift(1)), warm-up 3j.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BARS_30M = 6
BARS_24H = 288
ROLL_7D = 2016


@dataclass
class WashoutConfig:
    toptrader_z_max: float = -2.0    # capitulation des top traders
    oi_ret_24h_max: float = -0.05    # purge OI accumulée sur 24h (−5%)
    min_gap_bars: int = 288          # 1 état / 24h max par symbole
    roll_bars: int = ROLL_7D
    min_warmup_bars: int = 864


def detect_washouts(d: pd.DataFrame,
                    cfg: WashoutConfig = WashoutConfig()) -> pd.DataFrame:
    d = d.copy()
    oi = d["sum_open_interest"].astype(float)
    px = d["px"].astype(float)
    tt = d["sum_toptrader_long_short_ratio"].astype(float)

    mu = tt.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd = tt.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    tt_z = (tt - mu) / sd.replace(0.0, np.nan)
    oi_ret_24h = oi.pct_change(BARS_24H)

    # intensités génériques (mêmes colonnes que le détecteur cascade)
    oi_ret_30m = oi.pct_change(BARS_30M)
    r = oi_ret_30m
    mu_o = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd_o = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    oi_drop_z = (r - mu_o) / sd_o.replace(0.0, np.nan)
    px_ret_30m = px.pct_change(BARS_30M)

    trigger = ((tt_z <= cfg.toptrader_z_max)
               & (oi_ret_24h <= cfg.oi_ret_24h_max)
               & px.notna() & tt_z.notna())

    events, last_row = [], -10**9
    for i in np.flatnonzero(trigger.values):
        if i - last_row < cfg.min_gap_bars:
            continue
        last_row = i
        events.append({
            "row": int(i),
            "event_time": d["create_time"].iloc[i],
            "kind": "CROWD_WASHOUT",
            "toptrader_z_at": float(tt_z.iloc[i]),
            "oi_drop_30m": float(oi_ret_30m.iloc[i]),
            "oi_drop_z": float(oi_drop_z.iloc[i]) if np.isfinite(oi_drop_z.iloc[i]) else np.nan,
            "px_ret_30m": float(px_ret_30m.iloc[i]),
            "px": float(px.iloc[i]),
        })
    return pd.DataFrame(events)
