"""Every paper decision, accepted or refused, in one hash-chained file.

Refusals are the point.  A journal that only records what was traded cannot
answer the question that matters after thirty days: how often did the rule fire
and get stopped by cost, latency or risk, rather than by the absence of signal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research_kernel.ledger import RunLedger

DEFAULT_JOURNAL_PATH = Path("reports/paper_live/journal.jsonl")

DECISION_FIELDS = (
    "timestamp",
    "mechanism_id",
    "exchange",
    "symbol",
    "side",
    "gross_edge_bps_est",
    "cost_bps_est",
    "net_edge_bps_est",
    "position_notional_paper",
    "decision",
    "reject_reason",
    "data_latency_ms",
    "risk_state",
)


@dataclass
class Journal:
    path: Path = DEFAULT_JOURNAL_PATH

    def __post_init__(self) -> None:
        self._ledger = RunLedger(Path(self.path))

    def record(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        missing = [f for f in DECISION_FIELDS if f not in entry]
        if missing:
            raise ValueError("journal entry missing %s" % ", ".join(missing))
        return self._ledger.append(entry)

    def read(self) -> List[Dict[str, Any]]:
        return self._ledger.read()

    def verify(self) -> bool:
        return self._ledger.verify()

    def for_day(self, day: str) -> List[Dict[str, Any]]:
        return [e for e in self.read() if str(e.get("timestamp", ""))[:10] == day]
