from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from domain.risk.scenario_result import ScenarioResult
from domain.state.books import BookId


@dataclass
class BookRiskSnapshot:
    var95_usd: float
    cvar95_usd: float
    gross_exposure_usd: float
    net_exposure_usd: float
    dd: float
    kelly_multiplier: float
    downscale_factor: float


@dataclass
class RiskState:
    event_time: object
    killswitch_active: bool
    risk_off_mode: bool
    portfolio_var95_usd: float
    portfolio_cvar95_usd: float
    portfolio_leverage: float
    cluster_exposure_usd: Dict[str, float]
    book_risk: Dict[BookId, BookRiskSnapshot]
    scenario_results: List[ScenarioResult]
    caps_applied: List[str] = field(default_factory=list)
    actions_taken: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    run_id: str | None = None
