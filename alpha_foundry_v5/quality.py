from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import pandas as pd


@dataclass(frozen=True)
class PITAuditResult:
    rows: int
    future_availability_violations: int
    nonmonotonic_asof: int
    duplicate_keys: int
    checked_availability_columns: Tuple[str, ...]

    @property
    def structural_clean(self) -> bool:
        """Structural chronology is clean, independent of availability proof."""
        return self.future_availability_violations == 0 and self.nonmonotonic_asof == 0 and self.duplicate_keys == 0

    @property
    def availability_proved(self) -> bool:
        """At least one explicit receive/availability clock was audited."""
        return len(self.checked_availability_columns) > 0

    @property
    def clean(self) -> bool:
        """Full PIT proof requires both structural cleanliness and audited clocks."""
        return self.structural_clean and self.availability_proved

    @property
    def proof_level(self) -> str:
        if not self.structural_clean:
            return "FAILED"
        if self.availability_proved:
            return "FULL_AVAILABILITY"
        return "STRUCTURAL_ONLY"


def audit_point_in_time(frame: pd.DataFrame, asof_col: str = "asof_ns", availability_columns: Sequence[str] = (), key_columns: Sequence[str] = ("asof_ns", "symbol")) -> PITAuditResult:
    if asof_col not in frame:
        raise ValueError("missing asof column")
    asof = pd.to_numeric(frame[asof_col], errors="coerce")
    if "symbol" not in frame:
        nonmono = int((asof.diff().dropna() < 0).sum())
    else:
        nonmono = int(sum((pd.to_numeric(g[asof_col], errors="coerce").diff().dropna() < 0).sum() for _, g in frame.groupby("symbol", sort=False)))
    if availability_columns:
        cols = tuple(c for c in availability_columns if c in frame)
    else:
        cols = tuple(c for c in frame.columns if c.endswith("_available_ts_ns") or c.endswith("_receive_ts_ns"))
    violations = 0
    for c in cols:
        available = pd.to_numeric(frame[c], errors="coerce")
        mask = available.notna() & asof.notna()
        violations += int((available[mask] > asof[mask]).sum())
    keys = [c for c in key_columns if c in frame]
    duplicates = int(frame.duplicated(keys, keep=False).sum()) if keys else 0
    return PITAuditResult(len(frame), violations, nonmono, duplicates, cols)


def require_pit_clean(result: PITAuditResult) -> None:
    if not result.clean:
        raise ValueError(
            "PIT audit failed: proof_level=%s future=%d nonmonotonic=%d duplicates=%d checked_clocks=%d"
            % (
                result.proof_level,
                result.future_availability_violations,
                result.nonmonotonic_asof,
                result.duplicate_keys,
                len(result.checked_availability_columns),
            )
        )
