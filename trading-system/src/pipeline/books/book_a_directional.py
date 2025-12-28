from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.state.targets import TargetPosition


@dataclass
class BookADirectionalConfig:
    tp_multiple: float = 2.0
    sl_multiple: float = 1.0
    time_stop_seconds: int = 900
    max_positions: int = 5
    max_risk_per_trade_usd: float = 10_000.0


class BookADirectional:
    def __init__(self, config: BookADirectionalConfig):
        self.config = config

    def propose_targets(self, symbol: str, state: pd.Series, signal: Dict, alloc: Dict, risk_state: Dict, budgets: Dict) -> List[TargetPosition]:
        targets: List[TargetPosition] = []
        if not signal.get("tradeable", False):
            return targets
        if signal.get("decision_status") != "CONFIRM":
            return targets
        if alloc.get("trade_mode") == "OFF" or alloc.get("scale", 0) <= 0:
            return targets
        direction = signal.get("direction", "FLAT")
        side = "LONG" if direction == "LONG" else "SHORT" if direction == "SHORT" else "FLAT"
        if side == "FLAT":
            return targets
        equity = float(alloc.get("equity", state.get("equity", 0))) if alloc else float(state.get("equity", 0))
        weight = alloc.get("asset_weights", {}).get(symbol, 0.0)
        base_notional = equity * alloc.get("scale", 0.0) * weight
        rv = float(signal.get("rv_fwd_q50", signal.get("quantiles", {}).get("Q50", 0.0)))
        es = abs(float(signal.get("expected_shortfall", 0.001))) or 0.001
        risk_unit = max(es, 0.0001)
        size = min(base_notional, self.config.max_risk_per_trade_usd / risk_unit)
        tp = rv * self.config.tp_multiple
        sl = es * self.config.sl_multiple
        targets.append(
            TargetPosition(
                event_time=signal.get("event_time"),
                book="book_a",
                symbol=symbol,
                instrument_type="perp",
                side=side,
                notional_usd=size,
                leverage=alloc.get("leverage_target", 1.0),
                entry_style="taker" if alloc.get("trade_mode") == "TAKER" else "maker",
                risk_hints={"tp": tp, "sl": sl, "time_stop_s": self.config.time_stop_seconds},
                cluster_id=alloc.get("cluster_id", "default"),
                expected_utility=rv - signal.get("cost_estimate_bps", 0) / 10_000,
                cost_estimate_bps=signal.get("cost_estimate_bps", 0.0),
                reasons=[],
            )
        )
        return targets
