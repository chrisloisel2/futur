from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from domain.monitoring.actions import Action
from domain.monitoring.alerts import AlertSeverity


@dataclass
class MonitoringState:
    event_time: object
    run_id: str
    active_actions: List[Dict[str, object]] = field(default_factory=list)
    active_incidents: List[Dict[str, object]] = field(default_factory=list)
    last_baseline_refresh_time: object | None = None
