"""Verdict objects — the only output of a mechanism run.

A verdict is a falsifiable statement about a mechanism, not a performance
report.  The dataclass refuses to be constructed in a promoted state without
the evidence that state requires, so a promotion cannot be produced by a typo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class VerdictStatus(str, Enum):
    REJECTED = "REJECTED"
    DATA_BROKEN = "DATA_BROKEN"
    COST_WALL = "COST_WALL"
    OVERFIT = "OVERFIT"
    PROMISING_NEEDS_FORWARD = "PROMISING_NEEDS_FORWARD"
    SEALED_FORWARD_ACTIVE = "SEALED_FORWARD_ACTIVE"
    FORWARD_FAILED = "FORWARD_FAILED"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    LIVE_MICRO_ELIGIBLE = "LIVE_MICRO_ELIGIBLE"


#: Statuses that close a mechanism.  Re-running one of these requires a new
#: mechanism_id and costs a fresh trial in the multiplicity family.
TERMINAL_STATUSES = frozenset(
    {
        VerdictStatus.REJECTED,
        VerdictStatus.DATA_BROKEN,
        VerdictStatus.COST_WALL,
        VerdictStatus.OVERFIT,
        VerdictStatus.FORWARD_FAILED,
    }
)

#: Statuses that allow capital of any kind, paper included.
PROMOTED_STATUSES = frozenset(
    {VerdictStatus.PAPER_ELIGIBLE, VerdictStatus.LIVE_MICRO_ELIGIBLE}
)

#: Minimum evidence for the two promoted statuses.  These are the numbers from
#: the plan, kept in one place so that no caller can soften them locally.
PAPER_MIN_INDEPENDENT_EVENTS = 200
LIVE_MIN_INDEPENDENT_EVENTS = 300
LIVE_MIN_PAPER_DAYS = 30
LIVE_MIN_PROFIT_FACTOR = 1.25


class VerdictIntegrityError(RuntimeError):
    """Raised when a verdict claims more than its evidence supports."""


@dataclass
class Verdict:
    mechanism_id: str
    status: VerdictStatus

    gross_edge_bps: float = 0.0
    cost_bps: float = 0.0
    net_edge_bps: float = 0.0
    net_edge_bps_cost_x2: float = 0.0
    profit_factor: float = 0.0
    profit_factor_cost_x2: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    n_raw: int = 0
    n_independent: int = 0
    t_stat: float = 0.0
    placebo_percentile: float = 0.0
    multiplicity_adjusted_threshold: float = 0.0
    passed_gates: List[str] = field(default_factory=list)
    failed_gates: List[str] = field(default_factory=list)
    decision: str = ""

    # --- provenance, not part of the required schema but never optional in practice
    multiplicity_family: str = ""
    rules_hash: str = ""
    forward_seal_id: Optional[str] = None
    forward_result_id: Optional[str] = None
    paper_days: int = 0
    kernel_version: str = ""
    code_commit_sha: str = ""
    created_at: str = ""
    data_manifests: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = VerdictStatus(self.status)
        self._check_promotion_evidence()

    # ------------------------------------------------------------------
    def _check_promotion_evidence(self) -> None:
        """Rule 5 — a forward seal or nothing."""
        if self.status not in PROMOTED_STATUSES:
            return

        if not self.forward_seal_id:
            raise VerdictIntegrityError(
                "%s claims %s without a sealed forward window"
                % (self.mechanism_id, self.status.value)
            )
        if not self.forward_result_id:
            raise VerdictIntegrityError(
                "%s claims %s without a recorded forward result"
                % (self.mechanism_id, self.status.value)
            )
        if self.net_edge_bps_cost_x2 <= 0.0:
            raise VerdictIntegrityError(
                "%s claims %s with net edge at double cost of %.3f bps"
                % (self.mechanism_id, self.status.value, self.net_edge_bps_cost_x2)
            )
        if self.n_independent < PAPER_MIN_INDEPENDENT_EVENTS:
            raise VerdictIntegrityError(
                "%s claims %s on %d independent events (minimum %d)"
                % (
                    self.mechanism_id,
                    self.status.value,
                    self.n_independent,
                    PAPER_MIN_INDEPENDENT_EVENTS,
                )
            )

        if self.status is VerdictStatus.LIVE_MICRO_ELIGIBLE:
            if self.n_independent < LIVE_MIN_INDEPENDENT_EVENTS:
                raise VerdictIntegrityError(
                    "LIVE_MICRO_ELIGIBLE needs %d independent events, got %d"
                    % (LIVE_MIN_INDEPENDENT_EVENTS, self.n_independent)
                )
            if self.paper_days < LIVE_MIN_PAPER_DAYS:
                raise VerdictIntegrityError(
                    "LIVE_MICRO_ELIGIBLE needs %d days of paper, got %d"
                    % (LIVE_MIN_PAPER_DAYS, self.paper_days)
                )
            if self.profit_factor < LIVE_MIN_PROFIT_FACTOR:
                raise VerdictIntegrityError(
                    "LIVE_MICRO_ELIGIBLE needs profit factor >= %.2f, got %.3f"
                    % (LIVE_MIN_PROFIT_FACTOR, self.profit_factor)
                )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["status"] = self.status.value
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Verdict":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
