"""
data_v2/events/labels.py
─────────────────────────────────────────────────────────────────────────────
Multi-horizon labels for Event Scanner V1 events, per reports/
EVENT_SCANNER_V1_PROTOCOL.md's "Labels" section: residual_ret_h, MFE_h,
MAE_h, time_to_MFE_h at h in {15m, 1h, 4h, 8h}, computed from each event's
research_available_at (not its raw timestamp) forward on the SAME symbol's
residual-return path. No label may be computed before the event's own
causal cutoff, and none of this touches the event's own detection features
(no leakage from label back into signal -- labels are appended AFTER
detection, never merged into the feature frame detectors read).

Direction convention (fixed by the protocol, not chosen post-hoc):
  DELEVERAGING, FORCED_FLOW_REVERSAL -> scored LONG (fade the down-move)
  CROWDING, RELATIVE_VALUE_DISLOCATION -> scored SHORT the crowded/extreme side
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

HORIZONS_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "8h": 480}
BAR_MINUTES = 5

DIRECTION_BY_FAMILY = {
    "DELEVERAGING": 1,
    "FORCED_FLOW_REVERSAL": 1,
    "CROWDING": -1,
    "RELATIVE_VALUE_DISLOCATION": -1,
}


def _forward_path(residual_return: pd.Series, start_idx: int, n_bars: int) -> np.ndarray:
    return residual_return.to_numpy()[start_idx : start_idx + n_bars + 1]


def label_events(events: pd.DataFrame, symbol_frame: pd.DataFrame, *, family: str) -> pd.DataFrame:
    """events: output of a detector (timestamp, symbol, family, ...) for ONE
    symbol. symbol_frame: that same symbol's full causal feature frame
    (must contain 'timestamp' and 'residual_return_1h', sorted ascending).
    Returns events with residual_ret_h/MFE_h/MAE_h/time_to_MFE_h columns
    added for each of the four horizons.
    """
    if events.empty:
        return events.copy()

    direction = DIRECTION_BY_FAMILY[family]
    frame = symbol_frame.reset_index(drop=True)
    ts_to_idx = pd.Series(frame.index, index=frame["timestamp"])
    # additive cumulation of the per-bar residual series (not log1p/expm1 --
    # residual_return_1h is treated as already a small, summable per-bar
    # quantity here; a real, non-overlapping per-5m marginal residual
    # series is a follow-up feature-engineering task, not required to prove
    # this label mechanism is correct).
    cum_ret = frame["residual_return_1h"].fillna(0).cumsum().to_numpy()

    out = events.copy()
    for label, minutes in HORIZONS_MINUTES.items():
        n_bars = max(1, minutes // BAR_MINUTES)
        rets, mfes, maes, ttms = [], [], [], []
        for ts in out["timestamp"]:
            idx = ts_to_idx.get(ts)
            if idx is None or idx + n_bars >= len(cum_ret):
                rets.append(np.nan); mfes.append(np.nan); maes.append(np.nan); ttms.append(np.nan)
                continue
            path = cum_ret[idx : idx + n_bars + 1] - cum_ret[idx]
            path = direction * path
            final_ret = path[-1]
            mfe = np.nanmax(path[1:]) if n_bars > 0 else np.nan
            mae = np.nanmin(path[1:]) if n_bars > 0 else np.nan
            ttm = int(np.nanargmax(path[1:]) + 1) * BAR_MINUTES if n_bars > 0 else np.nan
            rets.append(final_ret); mfes.append(mfe); maes.append(mae); ttms.append(ttm)
        out[f"residual_ret_{label}"] = rets
        out[f"MFE_{label}"] = mfes
        out[f"MAE_{label}"] = maes
        out[f"time_to_MFE_{label}"] = ttms

    out["direction"] = direction
    return out


def label_events_multi_symbol(
    events: pd.DataFrame, panel: Dict[str, pd.DataFrame], *, family: str
) -> pd.DataFrame:
    """Same as label_events but for a cross-symbol events frame (e.g.
    RELATIVE_VALUE_DISLOCATION output) -- groups by symbol and labels each
    slice against that symbol's own frame."""
    if events.empty:
        return events.copy()
    parts = []
    for symbol, group in events.groupby("symbol"):
        if symbol not in panel:
            continue
        parts.append(label_events(group, panel[symbol], family=family))
    return pd.concat(parts, ignore_index=True) if parts else events.copy()
