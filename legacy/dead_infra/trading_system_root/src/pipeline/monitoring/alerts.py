from __future__ import annotations

from typing import List

from domain.monitoring.alerts import AlertEvent, AlertSeverity


class AlertRouter:
    def __init__(self, config: dict):
        self.config = config

    def build_alerts(self, reports: dict, action_plan) -> List[AlertEvent]:
        alerts: List[AlertEvent] = []
        for symbol, perf in reports.get("perf_drift", {}).get("by_symbol", {}).items():
            if perf.get("severity") == "CRIT":
                alerts.append(
                    AlertEvent(
                        event_time=reports.get("event_time"),
                        severity=AlertSeverity.CRIT,
                        incident_id=f"incident_{symbol}",
                        title=f"Performance drift {symbol}",
                        details={"symbol": symbol, **perf},
                        links={"action_plan_ref": getattr(action_plan, "run_id", "")},
                    )
                )
        return alerts
