from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.state.targets import TargetPosition


@dataclass
class BookCStructuralConfig:
    funding_z_trigger: float = 2.0
    basis_z_trigger: float = 2.0
    max_inventory_usd: float = 20_000.0
    mm_enabled: bool = False


class BookCStructural:
    def __init__(self, config: BookCStructuralConfig):
        self.config = config

    def propose_targets(self, symbol: str, state: pd.Series, alloc: Dict, risk_state: Dict, budgets: Dict) -> List[TargetPosition]:
        targets: List[TargetPosition] = []
        funding_z = float(state.get("s_slow_funding_z", 0) or 0)
        basis_z = float(state.get("s_slow_basis_z", 0) or 0)
        if abs(funding_z) >= self.config.funding_z_trigger:
            side = "LONG" if funding_z < 0 else "SHORT"
            targets.append(
                TargetPosition(
                    event_time=state.get("event_time"),
                    book="book_c",
                    symbol=symbol,
                    instrument_type="perp",
                    side=side,
                    notional_usd=min(self.config.max_inventory_usd, alloc.get("scale", 0) * alloc.get("asset_weights", {}).get(symbol, 0) * float(alloc.get("equity", state.get("equity", 0) or 0))),
                    leverage=1.0,
                    entry_style="maker",
                    risk_hints={"funding_capture": True},
                    cluster_id=alloc.get("cluster_id", "default"),
                    expected_utility=abs(funding_z) * 0.001,
                    cost_estimate_bps=alloc.get("cost_estimate_bps", 0.0),
                    reasons=["funding_extreme_capture"],
                )
            )
        if abs(basis_z) >= self.config.basis_z_trigger:
            side = "SHORT" if basis_z > 0 else "LONG"
            targets.append(
                TargetPosition(
                    event_time=state.get("event_time"),
                    book="book_c",
                    symbol=symbol,
                    instrument_type="perp",
                    side=side,
                    notional_usd=min(self.config.max_inventory_usd, alloc.get("scale", 0) * 0.5 * float(alloc.get("equity", state.get("equity", 0) or 0))),
                    leverage=1.0,
                    entry_style="maker",
                    risk_hints={"basis_capture": True},
                    cluster_id=alloc.get("cluster_id", "default"),
                    expected_utility=abs(basis_z) * 0.001,
                    cost_estimate_bps=alloc.get("cost_estimate_bps", 0.0),
                    reasons=["basis_capture"],
                )
            )
        return targets
