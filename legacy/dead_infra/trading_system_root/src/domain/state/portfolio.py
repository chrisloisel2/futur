from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PositionState:
    symbol: str
    notional_usd: float
    side: str
    entry_price: float
    mark_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: float = 0.0
    last_update_time: Optional[object] = None


@dataclass
class PortfolioState:
    equity: float
    cash: float
    exposure_gross: float
    exposure_net: float
    leverage: float
    drawdown: float
    dd_peak_equity: float = 0.0
    margin_used: float = 0.0
    positions_summary: Optional[Dict[str, Any]] = None
    positions: Dict[str, PositionState] = field(default_factory=dict)
