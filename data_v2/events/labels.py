"""
data_v2/events/labels.py
─────────────────────────────────────────────────────────────────────────────
Multi-horizon labels for Event Scanner V1 events, per reports/
EVENT_SCANNER_V1_PROTOCOL.md's "Labels" section: residual_ret_h, MFE_h,
MAE_h, time_to_MFE_h at h in {15m, 1h, 4h, 8h}.

Pre-unblinding fixes (2026-08-10, review round 3), all load-bearing:

1. Non-overlapping base increment. An earlier version cumsum'd
   residual_return_1h sampled every 5m bar as if each sample were an
   independent marginal return -- but a rolling 1h return computed every
   5m OVERLAPS its 11 neighbours 92% of the time, so that cumsum summed
   twelve heavily-overlapping 1h windows on top of each other, wildly
   inflating/distorting expectancy, MFE, MAE, PF. Fixed: the only thing
   ever summed now is residual_logret_5m (data_v2.events.residuals), a
   genuine non-overlapping 5m increment -- 15m = sum of the next 3, 1h =
   the next 12, 4h = the next 48, 8h = the next 96. expm1() at the end
   converts the summed log-return back to a simple return.

2. Entry point is research_available_at, not the triggering bar's own
   timestamp. A bar labelled 10:00 (open time) whose own
   research_available_at is ~10:05 (it isn't knowable until it closes --
   see data_v2.temporal.available_at) was, in an earlier version, used as
   the START of the forward-return path via frame["timestamp"] -- which
   means the very 10:00->10:05 move that produced the trigger condition
   was being counted a second time as if it were FORWARD performance.
   Fixed: entry_bar is the first bar whose own timestamp >= the event's
   research_available_at (i.e. skip the triggering bar's own 5m return;
   start summing from the bar after it).

3. Direction is no longer a fixed constant per family. DELEVERAGING is
   always a down-shock fade (+1, long). The other three can trigger on
   either side and must read back what actually happened at detection
   time (data_v2.events.detectors' captured crowded_side /
   trigger_residual_sign columns) -- see _direction_for_event. Getting
   this wrong can silently turn a real symmetric edge into a fake
   cancelled-out NO_EDGE.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from data_v2.events.costs import compute_event_cost

HORIZONS_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "8h": 480}
BAR_MINUTES = 5


def _direction_for_event(row: "pd.Series", family: str) -> int:
    if family == "DELEVERAGING":
        return 1
    if family in ("FORCED_FLOW_REVERSAL", "RELATIVE_VALUE_DISLOCATION"):
        return -int(np.sign(row["trigger_residual_sign"]))
    if family == "CROWDING":
        return -1 if row["crowded_side"] == "long" else 1
    raise ValueError(f"unknown family: {family}")


def label_events(
    events: pd.DataFrame, symbol_frame: pd.DataFrame, *, family: str, tick_size: Optional[float] = None
) -> pd.DataFrame:
    """events: output of a detector (timestamp, research_available_at,
    symbol, family, + family-specific trigger columns) for ONE symbol.
    symbol_frame: that same symbol's full causal feature frame (must
    contain 'timestamp', 'research_available_at', 'residual_logret_5m',
    'close', sorted ascending, regular 5m grid). Returns events with
    residual_ret_h/MFE_h/MAE_h/time_to_MFE_h added for each horizon, plus
    event_cost_x1/event_cost_x2 (data_v2.events.costs, per-event -- NaN if
    tick_size isn't provided, never silently a flat default).
    """
    if events.empty:
        return events.copy()

    frame = symbol_frame.reset_index(drop=True)
    bar_ts = frame["timestamp"].to_numpy()
    log_ret_5m = frame["residual_logret_5m"].fillna(0).to_numpy()
    close = frame["close"].to_numpy()
    n_frame = len(frame)

    out = events.copy()
    # first bar whose OWN timestamp >= the event's research_available_at --
    # this is what excludes the triggering bar's own 5m move (whose CLOSE
    # produced the trigger) from ever counting as "forward" performance.
    entry_idx = np.searchsorted(bar_ts, out["research_available_at"].to_numpy(), side="left")
    out["entry_idx"] = entry_idx
    out["entry_timestamp"] = [
        frame["timestamp"].iloc[i] if 0 <= i < n_frame else pd.NaT for i in entry_idx
    ]

    entry_price = np.array([close[i] if 0 <= i < n_frame else np.nan for i in entry_idx])
    event_costs = [
        compute_event_cost(p, tick_size) if tick_size is not None else (np.nan, np.nan)
        for p in entry_price
    ]
    out["entry_price"] = entry_price
    out["event_cost_x1"] = [c[0] for c in event_costs]
    out["event_cost_x2"] = [c[1] for c in event_costs]

    directions = np.array([_direction_for_event(row, family) for _, row in out.iterrows()])
    out["direction"] = directions

    for label, minutes in HORIZONS_MINUTES.items():
        n_bars = max(1, minutes // BAR_MINUTES)
        rets, mfes, maes, ttms = [], [], [], []
        for i, direction in zip(entry_idx, directions):
            if i < 0 or i + n_bars > n_frame:
                rets.append(np.nan); mfes.append(np.nan); maes.append(np.nan); ttms.append(np.nan)
                continue
            # non-overlapping 5m increments only, starting AT entry_idx
            increments = log_ret_5m[i : i + n_bars] * direction
            path = np.cumsum(increments)
            final_log_ret = path[-1]
            mfe = float(np.expm1(np.max(path)))
            mae = float(np.expm1(np.min(path)))
            ttm = int(np.argmax(path) + 1) * BAR_MINUTES
            rets.append(float(np.expm1(final_log_ret))); mfes.append(mfe); maes.append(mae); ttms.append(ttm)
        out[f"residual_ret_{label}"] = rets
        out[f"MFE_{label}"] = mfes
        out[f"MAE_{label}"] = maes
        out[f"time_to_MFE_{label}"] = ttms

    return out


def label_events_multi_symbol(
    events: pd.DataFrame, panel: Dict[str, pd.DataFrame], *, family: str,
    tick_size_by_symbol: Optional[Dict[str, float]] = None,
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
        tick_size = (tick_size_by_symbol or {}).get(symbol)
        parts.append(label_events(group, panel[symbol], family=family, tick_size=tick_size))
    return pd.concat(parts, ignore_index=True) if parts else events.copy()
