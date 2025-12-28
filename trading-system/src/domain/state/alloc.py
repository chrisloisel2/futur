from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from domain.signal.signal import TradeMode


@dataclass
class Alloc:
    event_time: object
    run_id: str
    model_stack: str
    feature_set: str
    scope: str = "portfolio"
    symbol: str | None = None
    scale: float = 0.0
    leverage_target: float = 0.0
    trade_mode: TradeMode = TradeMode.OFF
    asset_weights: Dict[str, float] = field(default_factory=dict)
    cooldowns: Dict[str, int] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=dict)
    coherence_score: float = 0.0
    reasons: List[str] = field(default_factory=list)
