from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.state.targets import TargetPosition


@dataclass
class BookBConvexityConfig:
    max_loss_per_structure_usd: float = 5_000.0
    overlay_weight_cap: float = 0.2
    enabled: bool = False


class BookBConvexity:
    def __init__(self, config: BookBConvexityConfig):
        self.config = config

    def propose_targets(self, symbol: str, state: pd.Series, signal: Dict, alloc: Dict, risk_state: Dict, budgets: Dict) -> List[TargetPosition]:
        targets: List[TargetPosition] = []
        if not self.config.enabled:
            return targets
        regime_entropy = float(signal.get("regime_entropy", 0))
        if regime_entropy < 0.5:
            overlay_notional = alloc.get("scale", 0) * alloc.get("asset_weights", {}).get(symbol, 0) * 0.1 * float(alloc.get("equity", state.get("equity", 0) or 0))
            overlay_notional = min(overlay_notional, self.config.max_loss_per_structure_usd)
            targets.append(
                TargetPosition(
                    event_time=signal.get("event_time"),
                    book="book_b",
                    symbol=symbol,
                    instrument_type="option",
                    side="LONG",
                    notional_usd=overlay_notional,
                    leverage=1.0,
                    entry_style="hybrid",
                    risk_hints={"max_loss_usd": self.config.max_loss_per_structure_usd},
                    cluster_id=alloc.get("cluster_id", "default"),
                    expected_utility=signal.get("p_hit", 0.5) - 0.5,
                    cost_estimate_bps=signal.get("cost_estimate_bps", 0.0),
                    reasons=[],
                )
            )
        return targets
