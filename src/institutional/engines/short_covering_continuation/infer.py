"""
src/institutional/engines/short_covering_continuation/infer.py
─────────────────────────────────────────────────────────────────────────────
SHORT_COVERING_CONTINUATION_V1 (Live Alpha Lab) -- AlphaEngine implementation.

Reconstructs reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/
REPORT.md rank 2 ("price up + OI down (short covering) -> continuation, vs
baseline non conditionné", net excess +9.2bps full-sample / +19.0bps OOS,
n=23,422/7,217) as a continuous state-conditioned engine.

Why AlphaEngine and not the LIQ_CASCADE_REPEAT_V1 event-detector pattern:
this mechanism isn't triggered by a discrete event -- it's a per-hour
classification of the market's (price, OI) state, evaluated continuously
across the whole universe. That's exactly AlphaEngine.generate(asset, start,
end) -> one Opportunity per hourly bar, decision_zone A/B/C via
`classify_zone` -- the same coding pattern as
src/institutional/engines/cross_sectional_long/infer.py's percentile-ranked
scoring (`score_short_covering` here plays the role `momentum_score`'s
cross-sectional rank plays there, just computed as a per-symbol TIME-SERIES
trailing percentile instead of a cross-sectional one -- see state.py).

CRITICAL data provenance: this reads data/derivatives_raw/ (the LIVE
derivatives collector, scripts/run_derivatives_collector.py) via
live_data.py, NOT data_v2/normalized/event_feature_panel (the original
discovery dataset -- a static backfill that lives only in the separate
futur-data-v2 worktree, not continuously updated). See
reports/live_alpha_lab/SHORT_COVERING_CONTINUATION_V1/freeze_spec.json
`data_reconstruction_notes` for the full honesty accounting of what is/isn't
verified equivalent between the two.

Direction is LONG-only by construction (never SHORT_HEDGE/short-anything) --
SHORT is institutionally SHORT_REJECTED for directional shorts; this
mechanism only ever proposes buying the continuation.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity
from src.institutional.engines.base import AlphaEngine, EngineConfig
from src.institutional.engines.short_covering_continuation.live_data import (
    load_open_interest_raw, to_hourly_bars)
from src.institutional.engines.short_covering_continuation.state import (
    PCTILE_LOOKBACK_HOURS, classify_state_df, rolling_causal_percentile)
from src.institutional.portfolio.zones import classify_zone

logger = logging.getLogger(__name__)


class ShortCoveringContinuationEngine(AlphaEngine):
    """SHORT_COVERING_CONTINUATION_V1 -- see module docstring.

    `universe` has NO baked-in default here (unlike CrossSectionalLongEngine's
    DEFAULT_UNIVERSE): the frozen 50-symbol list MUST come from
    configs/portfolio_v1_1_parallel_50.yaml via the caller (the Mode A
    runner script), never duplicated inside engine code -- a second,
    driftable copy of the universe is exactly the "universe drift" bug class
    documented in run_liq_cascade_repeat_shadow.py.
    """

    def __init__(
        self,
        status: str = "SHADOW",
        universe: Optional[List[str]] = None,
        horizon_hours: float = 4.0,       # fwd_4h, matches the source report's horizon
        cost_bps: float = 14.0,           # round-trip taker ~14bps, matches COST_RT convention
        tau_a: float = 0.90,              # == PRICE_PCTILE_HI: A_TRADE exactly == classify_state SHORT_COVERING
        tau_b: float = 0.75,              # near-miss "worth watching" band (judgment call, see state.py)
        pctile_lookback_hours: int = PCTILE_LOOKBACK_HOURS,
        expected_move: float = 0.006,     # conservative, order-of-magnitude of the measured net excess edge
        engine_id: str = "SHORT_COVERING_CONTINUATION_V1",
    ):
        if not universe:
            raise ValueError(
                "ShortCoveringContinuationEngine requires an explicit `universe` "
                "(the frozen 50-symbol list from configs/portfolio_v1_1_parallel_50.yaml) "
                "-- refusing a baked-in default to avoid a second, driftable source of truth."
            )
        super().__init__(EngineConfig(
            engine_id=engine_id, status=status, horizon_hours=horizon_hours,
            cost_bps=cost_bps, assets=list(universe), max_position_fraction=0.15,
        ))
        self.tau_a = tau_a
        self.tau_b = tau_b
        self.pctile_lookback_hours = pctile_lookback_hours
        self.expected_move = expected_move

    def thresholds_for(self, asset: str):
        return self.tau_a, self.tau_b

    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        """One Opportunity per hourly bar in [start, end] for `asset` --
        CASH direction (never SHORT_HEDGE) whenever the state isn't
        short-covering-like enough to clear tau_b. Returns [] (not an
        exception) if `asset` has no live derivatives_raw data in this
        window -- callers (AlphaEngine.generate_all / the runner script)
        must treat that as "no signal available", not as a crash."""
        start_ts = _to_utc_ts(start)
        end_ts = _to_utc_ts(end)
        lookback_start = start_ts - pd.Timedelta(hours=self.pctile_lookback_hours + 24)

        raw = load_open_interest_raw(asset, lookback_start, end_ts)
        if raw.empty:
            logger.warning(
                "[%s] %s: no live derivatives_raw open_interest data in this window -- "
                "symbol likely absent from the live collector (see freeze_spec.json "
                "data_reconstruction_notes for the known 3/50-symbol gap).",
                self.engine_id, asset,
            )
            return []

        hourly = to_hourly_bars(raw, lookback_start, end_ts)
        hourly["price_ret_1h"] = hourly["mark_price"].pct_change(1)
        hourly["oi_delta_pct_1h"] = hourly["open_interest"].pct_change(1)
        hourly["price_ret_pctile"] = rolling_causal_percentile(
            hourly["price_ret_1h"], window=self.pctile_lookback_hours)
        hourly["oi_delta_pctile"] = rolling_causal_percentile(
            hourly["oi_delta_pct_1h"], window=self.pctile_lookback_hours)
        hourly["realized_vol_1h"] = hourly["price_ret_1h"].rolling(24, min_periods=8).std()

        window = hourly[(hourly["ts"] >= start_ts) & (hourly["ts"] <= end_ts)].copy()
        if window.empty:
            return []
        window = classify_state_df(window)

        cost = self.cost_fraction
        out: List[Opportunity] = []
        for _, row in window.iterrows():
            score = float(row["score"]) if pd.notna(row["score"]) else 0.0
            zone, reason = classify_zone(score, self.tau_a, self.tau_b)
            direction = "LONG" if zone != "C_REJECT" else "CASH"
            er = max(0.0, (score - self.tau_b)) / max(1 - self.tau_b, 1e-6) * self.expected_move
            rv = row.get("realized_vol_1h")
            expected_vol = (
                float(np.clip(rv * np.sqrt(8760.0), 0.1, 3.0))
                if rv is not None and np.isfinite(rv) and rv > 0 else 0.5
            )
            out.append(Opportunity(
                timestamp=row["ts"], engine_id=self.engine_id, asset=asset, direction=direction,
                status=self.status, p_success=score, expected_return=er, expected_vol=expected_vol,
                expected_holding_hours=self.horizon_hours, expected_cost=cost,
                score_raw=score, score_net=er - cost, confidence=float(abs(score - 0.5) * 2.0),
                regime=str(row["state"]), correlation_bucket=self.bucket(asset),
                max_position_fraction=self.config.max_position_fraction,
                stop_loss=0.02, take_profit=self.expected_move,
                decision_zone=zone, reason=reason.value if hasattr(reason, "value") else str(reason),
            ))
        return out


def _to_utc_ts(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
