"""
src/institutional/engines/btc_spillover/detector.py
─────────────────────────────────────────────────────────────────────────────
Détection CAUSALE de retards de propagation BTC → alt.

Event = BTC a fait un thrust 1h ≥ +1.5% ET l'alt (beta>0 historique implicite)
n'a capté que ≤ 40% du mouvement sur la même fenêtre → on achète le
RETARDATAIRE, thèse : rattrapage dans les heures qui suivent.

BTC est chargé une fois (cache module). Le symbole BTCUSDT lui-même est exclu.
Émet les colonnes d'intensité génériques. Causalité : fenêtres passées only,
BTC aligné par merge_asof backward sur la barre de l'alt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

BARS_1H = 12
_BTC_CACHE: dict = {}


@dataclass
class SpilloverConfig:
    btc_thrust_min: float = 0.015    # thrust BTC 1h ≥ +1.5%
    lag_ratio_max: float = 0.40      # l'alt a capté ≤ 40% du mouvement BTC
    min_gap_bars: int = 24           # 2h min entre events par symbole
    min_warmup_bars: int = 864


def _btc_frame() -> Optional[pd.DataFrame]:
    if "df" not in _BTC_CACHE:
        from src.institutional.engines.liq_cascade.detector import load_metrics
        b = load_metrics("BTCUSDT")
        if b is None:
            _BTC_CACHE["df"] = None
        else:
            px = b["px"].astype(float)
            _BTC_CACHE["df"] = pd.DataFrame({
                "t": b["create_time"],
                "btc_ret_1h_lead": px.pct_change(BARS_1H),
            }).sort_values("t")
    return _BTC_CACHE["df"]


def detect_spillovers(d: pd.DataFrame,
                      cfg: SpilloverConfig = SpilloverConfig()) -> pd.DataFrame:
    sym = str(d["symbol"].iloc[0]) if "symbol" in d.columns else ""
    if sym == "BTCUSDT":
        return pd.DataFrame()
    btc = _btc_frame()
    if btc is None:
        return pd.DataFrame()

    d = d.copy()
    d = pd.merge_asof(d.sort_values("create_time"), btc,
                      left_on="create_time", right_on="t", direction="backward")
    px = d["px"].astype(float)
    oi = d["sum_open_interest"].astype(float)
    alt_ret_1h = px.pct_change(BARS_1H)
    btc_ret = d["btc_ret_1h_lead"].astype(float)

    oi_ret_30m = oi.pct_change(6)
    px_ret_30m = px.pct_change(6)

    trigger = ((btc_ret >= cfg.btc_thrust_min)
               & (alt_ret_1h <= cfg.lag_ratio_max * btc_ret)
               & (alt_ret_1h > -cfg.btc_thrust_min)   # pas un alt en chute libre
               & px.notna())
    # warm-up : pas d'event avant min_warmup_bars
    trigger.iloc[:cfg.min_warmup_bars] = False

    events, last_row = [], -10**9
    for i in np.flatnonzero(trigger.values):
        if i - last_row < cfg.min_gap_bars:
            continue
        last_row = i
        events.append({
            "row": int(i),
            "event_time": d["create_time"].iloc[i],
            "kind": "BTC_SPILLOVER_LAG",
            "btc_ret_1h_at": float(btc_ret.iloc[i]),
            "alt_ret_1h_at": float(alt_ret_1h.iloc[i]),
            "lag_gap": float(btc_ret.iloc[i] - alt_ret_1h.iloc[i]),
            "oi_drop_30m": float(oi_ret_30m.iloc[i]) if np.isfinite(oi_ret_30m.iloc[i]) else np.nan,
            "oi_drop_z": np.nan,
            "px_ret_30m": float(px_ret_30m.iloc[i]) if np.isfinite(px_ret_30m.iloc[i]) else np.nan,
            "px": float(px.iloc[i]),
        })
    return pd.DataFrame(events)
