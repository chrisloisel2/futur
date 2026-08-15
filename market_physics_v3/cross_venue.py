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

    def __post_init__(self) -> None:
        if self.event_ts_ns <= 0 or self.mid <= 0:
            raise ValueError("invalid venue quote")
        if self.spread_bps < 0 or self.depth_10bps_usd < 0:
            raise ValueError("spread/depth cannot be negative")


def quote_quality_weight(q: VenueQuote, asof_ns: int, half_life_ms: float = 500.0) -> float:
    if asof_ns < q.event_ts_ns:
        return 0.0
    age_ms = (asof_ns - q.event_ts_ns) / 1e6
    freshness = np.exp(-np.log(2.0) * age_ms / max(half_life_ms, 1e-9))
    spread_penalty = 1.0 / max(q.spread_bps, 0.05)
    depth_reward = np.sqrt(max(q.depth_10bps_usd, 0.0))
    return float(freshness * spread_penalty * depth_reward)


def fair_value(quotes: Sequence[VenueQuote], asof_ns: int, half_life_ms: float = 500.0) -> Dict[str, object]:
    valid = [q for q in quotes if q.event_ts_ns <= asof_ns]
    if not valid:
        raise ValueError("no causal quotes at asof_ns")
    weights = np.array([quote_quality_weight(q, asof_ns, half_life_ms) for q in valid], dtype=float)
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
