from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class ActionType(str, Enum):
    TIGHTEN_THRESHOLDS = "TIGHTEN_THRESHOLDS"
    INCREASE_COOLDOWN = "INCREASE_COOLDOWN"
    REDUCE_LEVERAGE_CAPS = "REDUCE_LEVERAGE_CAPS"
    REDUCE_SCALER_CAP = "REDUCE_SCALER_CAP"
    FREEZE_SYMBOL = "FREEZE_SYMBOL"
    FREEZE_BOOK = "FREEZE_BOOK"
    FORCE_EXECUTION_MODE = "FORCE_EXECUTION_MODE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


@dataclass
class Action:
    action_type: ActionType
    scope: Dict[str, str]
    params: Dict[str, object]
    ttl_seconds: int
    reason_code: str
    severity: str


@dataclass
class ActionPlan:
    event_time: object
    run_id: str
    actions: List[Action]
