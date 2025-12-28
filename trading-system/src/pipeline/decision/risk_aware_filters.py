from __future__ import annotations

from typing import Dict

from domain.signal.signal import DecisionStatus, Signal, SignalDirection, TradeMode


class RiskAwareFilters:
    def __init__(self, config: Dict[str, float]):
        self.config = config

    def apply(self, signal: Signal, state_row) -> Signal:
        reasons = list(signal.reasons)
        spread = float(state_row.get("x_fast_spread", 0) or 0)
        max_spread = self.config.get("max_spread_bps", 500.0) / 10_000
        if spread > max_spread:
            signal.decision_status = DecisionStatus.INVALIDATE
            signal.tradeable = False
            signal.mode = TradeMode.OFF
            signal.direction = SignalDirection.FLAT
            reasons.append("risk_spread")
        funding = float(state_row.get("funding_rate", 0) or 0)
        funding_lim = self.config.get("funding_extreme", 0.05)
        if funding > funding_lim and signal.direction == SignalDirection.LONG:
            signal.direction = SignalDirection.FLAT
            reasons.append("funding_extreme_long")
        if funding < -funding_lim and signal.direction == SignalDirection.SHORT:
            signal.direction = SignalDirection.FLAT
            reasons.append("funding_extreme_short")
        signal.reasons = reasons
        return signal
