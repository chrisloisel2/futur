from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AllocatorDecision:
    event_time: object
    book_weights: Dict[str, float]
    book_caps_applied: List[str] = field(default_factory=list)
    cluster_caps_applied: List[str] = field(default_factory=list)
    dropped_targets: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
