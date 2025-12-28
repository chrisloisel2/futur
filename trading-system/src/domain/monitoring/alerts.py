from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRIT = "CRIT"


@dataclass
class AlertEvent:
    event_time: object
    severity: AlertSeverity
    incident_id: str
    title: str
    details: Dict[str, object]
    links: Dict[str, str]
