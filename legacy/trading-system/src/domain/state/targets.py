from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TargetPosition:
    event_time: object
    book: str
    symbol: str
    instrument_type: str
    side: str
    notional_usd: float
    leverage: float
    entry_style: str
    risk_hints: Dict[str, object] = field(default_factory=dict)
    hedge: Dict[str, object] | None = None
    cluster_id: str = "default"
    expected_utility: float = 0.0
    cost_estimate_bps: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class TargetPositions:
    event_time: object
    run_id: str
    model_stack: str
    feature_set: str
    targets: List[TargetPosition]
    book_summary: Dict[str, Dict[str, float]] = field(default_factory=dict)
    allocator_reasons: List[str] = field(default_factory=list)
