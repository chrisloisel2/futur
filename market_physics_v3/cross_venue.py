from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    event_ts_ns: int
    mid: float
    spread_bps: float
    depth_10bps_usd: float
    # Local availability timestamp. Legacy/synthetic callers may omit it, in
    # which case market event time is used as a conservative compatibility path.
    receive_ts_ns: int = 0

    def __post_init__(self) -> None:
        if self.event_ts_ns <= 0 or self.mid <= 0:
            raise ValueError("invalid venue quote")
        if self.receive_ts_ns < 0:
            raise ValueError("receive_ts_ns cannot be negative")
        if self.receive_ts_ns and self.receive_ts_ns < self.event_ts_ns:
            raise ValueError("receive_ts_ns cannot precede event_ts_ns")
        if self.spread_bps < 0 or self.depth_10bps_usd < 0:
            raise ValueError("spread/depth cannot be negative")

    @property
    def available_ts_ns(self) -> int:
        return int(self.receive_ts_ns or self.event_ts_ns)

    @property
    def transport_lag_ms(self) -> float:
        return max(0.0, (self.available_ts_ns - int(self.event_ts_ns)) / 1e6)


def quote_quality_weight(
    q: VenueQuote,
    asof_ns: int,
    half_life_ms: float = 500.0,
    transport_half_life_ms: float = 2000.0,
) -> float:
    """Weight an already-received quote by local age, transport lag and depth.

    Receive-time age measures how stale our local observation is. Transport lag
    separately penalizes messages that were already old when they reached us.
    This prevents a delayed venue from receiving a high weight merely because
    its exchange timestamp happened in the past.
    """
    if asof_ns < q.available_ts_ns:
        return 0.0
    age_ms = (asof_ns - q.available_ts_ns) / 1e6
    local_freshness = np.exp(-np.log(2.0) * age_ms / max(half_life_ms, 1e-9))
    transport_quality = np.exp(
        -np.log(2.0) * q.transport_lag_ms / max(transport_half_life_ms, 1e-9)
    )
    spread_penalty = 1.0 / max(q.spread_bps, 0.05)
    depth_reward = np.sqrt(max(q.depth_10bps_usd, 0.0))
    return float(local_freshness * transport_quality * spread_penalty * depth_reward)


def fair_value(
    quotes: Sequence[VenueQuote],
    asof_ns: int,
    half_life_ms: float = 500.0,
    transport_half_life_ms: float = 2000.0,
) -> Dict[str, object]:
    valid = [q for q in quotes if q.available_ts_ns <= asof_ns]
    if not valid:
        raise ValueError("no point-in-time available quotes at asof_ns")
    weights = np.array([
        quote_quality_weight(q, asof_ns, half_life_ms, transport_half_life_ms)
        for q in valid
    ], dtype=float)
    mids = np.array([q.mid for q in valid], dtype=float)
    if weights.sum() <= 0:
        weights = np.ones_like(weights)
    weights = weights / weights.sum()
    fv = float(np.dot(weights, mids))
    dislocations = {q.venue: float(1e4 * (q.mid - fv) / fv) for q in valid}
    return {
        "fair_value": fv,
        "weights": {q.venue: float(w) for q, w in zip(valid, weights)},
        "dislocation_bps": dislocations,
        "dispersion_bps": float(np.std([1e4 * (q.mid - fv) / fv for q in valid])),
        "receive_age_ms": {
            q.venue: float(max(0, asof_ns - q.available_ts_ns) / 1e6) for q in valid
        },
        "transport_lag_ms": {q.venue: float(q.transport_lag_ms) for q in valid},
    }


def trailing_lead_lag(returns: pd.DataFrame, max_lag: int = 6) -> pd.DataFrame:
    """Research-only trailing lead/lag matrix. Positive lag means row venue leads column venue."""
    cols = list(returns.columns)
    rows = []
    for leader in cols:
        for follower in cols:
            if leader == follower:
                continue
            best_lag = 0
            best_corr = np.nan
            best_abs = -1.0
            for lag in range(1, max_lag + 1):
                x = returns[leader].shift(lag)
                y = returns[follower]
                corr = x.corr(y)
                if pd.notna(corr) and abs(corr) > best_abs:
                    best_abs = abs(corr)
                    best_corr = float(corr)
                    best_lag = lag
            rows.append({"leader": leader, "follower": follower, "lag": best_lag, "corr": best_corr})
    return pd.DataFrame(rows)
