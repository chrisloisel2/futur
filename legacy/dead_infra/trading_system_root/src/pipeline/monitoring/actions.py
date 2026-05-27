from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from domain.monitoring.actions import Action, ActionPlan, ActionType


class ActionEngine:
    def __init__(self, config: dict):
        self.config = config

    def decide_actions(self, reports: dict, monitoring_state: dict) -> ActionPlan:
        actions: List[Action] = []
        now = reports.get("event_time", datetime.utcnow())
        for symbol, perf in reports.get("perf_drift", {}).get("by_symbol", {}).items():
            if perf.get("severity") == "CRIT":
                actions.append(
                    Action(
                        action_type=ActionType.FREEZE_SYMBOL,
                        scope={"level": "SYMBOL", "symbol": symbol},
                        params={"no_new_orders": True},
                        ttl_seconds=self.config.get("freeze_ttl_seconds", 900),
                        reason_code="PERF_DRIFT_CRIT",
                        severity="CRIT",
                    )
                )
        return ActionPlan(event_time=now, run_id=reports.get("run_id", ""), actions=actions)
