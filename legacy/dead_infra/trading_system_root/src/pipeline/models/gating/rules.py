from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.signal.signal import SignalDirection, TradeMode


@dataclass
class GatingDecision:
    tradeable: bool
    mode: TradeMode
    coarse_direction: SignalDirection
    veto_reasons: List[str]


class GatingRules:
    def __init__(self, config: Dict[str, any]):
        self.config = config

    def apply(self, state: pd.Series) -> GatingDecision:
        reasons: List[str] = []
        tradeable = True
        mode = TradeMode.TAKER
        coarse_direction = SignalDirection.FLAT

        quality_flags = int(state.get("quality_flags", 0))
        if quality_flags != 0:
            tradeable = False
            reasons.append("bad_quality_flags")
        spread = float(state.get("x_fast_spread", state.get("spread", 0) or 0))
        max_spread = self.config.get("max_spread_bps", 500.0)
        if spread * 10_000 > max_spread:
            tradeable = False
            reasons.append("spread_too_wide")
        staleness = float(state.get("staleness_ms", 0))
        if staleness > self.config.get("max_staleness_ms", 30_000):
            tradeable = False
            reasons.append("stale")
        required = self.config.get("required_features", [])
        for feat in required:
            if pd.isna(state.get(feat)):
                tradeable = False
                reasons.append(f"missing_{feat}")
        if not tradeable:
            mode = TradeMode.OFF
            coarse_direction = SignalDirection.FLAT
        return GatingDecision(tradeable=tradeable, mode=mode, coarse_direction=coarse_direction, veto_reasons=reasons)
