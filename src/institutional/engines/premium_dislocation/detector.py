"""
src/institutional/engines/premium_dislocation/detector.py
─────────────────────────────────────────────────────────────────────────────
Détection CAUSALE de dislocations du premium perp/index.

Event = premium 5-min z ≤ −z_min (vs 7j glissants passés) ET premium sous un
plancher absolu (économiquement significatif). Long-only : on ne joue que la
CAPITULATION PERP (premium très négatif → perp survendu vs index → reversion).
Le premium très positif (FOMO) est détecté mais marqué kind=PREM_FOMO pour
diagnostic — SHORT interdit dans ce projet.

Émet les colonnes d'intensité génériques du pipeline + prem_at / prem_z_at.
Causalité : rolling passé only (shift(1)), warm-up 3j.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
PREMIUM_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_premium"

BARS_30M = 6
ROLL_7D = 2016


@dataclass
class PremiumConfig:
    z_min: float = 4.0               # |z| de dislocation
    prem_floor: float = -0.0010      # premium ≤ −10 bps (capitulation)
    min_gap_bars: int = 24           # 2h min entre events
    roll_bars: int = ROLL_7D
    min_warmup_bars: int = 864


def load_premium(symbol: str):
    p = PREMIUM_DIR / f"{symbol}_premium_5m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)[["ts", "premium", "premium_low"]]
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values("ts")


def detect_premium_dislocations(d: pd.DataFrame,
                                cfg: PremiumConfig = PremiumConfig()) -> pd.DataFrame:
    sym = str(d["symbol"].iloc[0]) if "symbol" in d.columns else None
    prem = load_premium(sym) if sym else None
    if prem is None:
        return pd.DataFrame()

    d = d.copy()
    d = d.merge(prem, left_on="create_time", right_on="ts", how="left")
    pr = d["premium"].astype(float)
    px = d["px"].astype(float)
    oi = d["sum_open_interest"].astype(float)

    mu = pr.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd = pr.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    pz = (pr - mu) / sd.replace(0.0, np.nan)

    oi_ret_30m = oi.pct_change(BARS_30M)
    r = oi_ret_30m
    mu_o = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).mean()
    sd_o = r.shift(1).rolling(cfg.roll_bars, min_periods=cfg.min_warmup_bars).std()
    oi_drop_z = (r - mu_o) / sd_o.replace(0.0, np.nan)
    px_ret_30m = px.pct_change(BARS_30M)

    capit = (pz <= -cfg.z_min) & (pr <= cfg.prem_floor) & px.notna()
    fomo = (pz >= cfg.z_min) & (pr >= -cfg.prem_floor) & px.notna()   # diagnostic

    events, last_row = [], -10**9
    for i in np.flatnonzero((capit | fomo).values):
        if i - last_row < cfg.min_gap_bars:
            continue
        last_row = i
        events.append({
            "row": int(i),
            "event_time": d["create_time"].iloc[i],
            "kind": "PREM_CAPITULATION" if bool(capit.iloc[i]) else "PREM_FOMO",
            "prem_at": float(pr.iloc[i]),
            "prem_z_at": float(pz.iloc[i]),
            "oi_drop_30m": float(oi_ret_30m.iloc[i]) if np.isfinite(oi_ret_30m.iloc[i]) else np.nan,
            "oi_drop_z": float(oi_drop_z.iloc[i]) if np.isfinite(oi_drop_z.iloc[i]) else np.nan,
            "px_ret_30m": float(px_ret_30m.iloc[i]) if np.isfinite(px_ret_30m.iloc[i]) else np.nan,
            "px": float(px.iloc[i]),
        })
    return pd.DataFrame(events)
