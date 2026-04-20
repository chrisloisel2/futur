from __future__ import annotations

from collections import defaultdict
from typing import Dict


class ExecutionTelemetry:
    def __init__(self):
        self.counters: Dict[str, float] = defaultdict(float)

    def record_event(self, name: str, value: float = 1.0) -> None:
        self.counters[name] += value

    def export_prometheus(self) -> Dict[str, float]:
        return dict(self.counters)
