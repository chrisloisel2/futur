from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from ..contracts import DataDomain, ExecutionStyle


@dataclass(frozen=True)
class LabSpec:
    lab_id: str
    name: str
    economic_source_id: str
    hypothesis_template: str
    payer: str
    domains: Tuple[DataDomain, ...]
    plugin: str
    default_target: str
    horizons_ms: Tuple[int, ...]
    execution_styles: Tuple[ExecutionStyle, ...]
    max_trials_per_family: int
    required_column_patterns: Tuple[str, ...]
    required_any_groups: Tuple[Tuple[str, ...], ...] = tuple()
    min_coverage: float = 0.50
    min_symbols: int = 1
    activity_requirements: Tuple[Tuple[str, int], ...] = tuple()


class LabPlugin(ABC):
    plugin_name = "base"

    def readiness(self, frame: pd.DataFrame, spec: LabSpec) -> Dict[str, object]:
        columns = tuple(str(c) for c in frame.columns)

        def is_audit_metadata(column: str) -> bool:
            return str(column).endswith(("_available_ts_ns", "_receive_ts_ns")) or str(column) == "asof_ns"

        def matches(pattern: str):
            return [c for c in columns if not is_audit_metadata(c) and fnmatch(c, pattern)]

        def coverage(column: str) -> float:
            series = frame[column]
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().sum() > 0:
                return float(numeric.notna().mean())
            return float(series.notna().mean())

        def active_count(column: str) -> int:
            numeric = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
            return int(np.sum(np.isfinite(numeric) & (np.abs(numeric) > 1e-15)))

        missing = []
        coverage_by_requirement = {}
        for pattern in spec.required_column_patterns:
            candidates = matches(pattern)
            best = max([coverage(c) for c in candidates], default=0.0)
            coverage_by_requirement[pattern] = best
            if best < float(spec.min_coverage):
                missing.append(pattern)

        missing_groups = []
        for group in spec.required_any_groups:
            candidates = [c for c in columns if not is_audit_metadata(c) and any(fnmatch(c, p) for p in group)]
            best = max([coverage(c) for c in candidates], default=0.0)
            coverage_by_requirement["ANY:" + "|".join(group)] = best
            if best < float(spec.min_coverage):
                missing_groups.append(group)

        activity = {}
        missing_activity = []
        for pattern, min_rows in spec.activity_requirements:
            candidates = matches(pattern)
            best = max([active_count(c) for c in candidates], default=0)
            activity[pattern] = {"active_rows": int(best), "min_active_rows": int(min_rows)}
            if best < int(min_rows):
                missing_activity.append(pattern)

        if "symbol" in frame.columns:
            symbol_count = int(frame["symbol"].dropna().astype(str).nunique())
        else:
            symbol_count = 1
        symbol_ready = symbol_count >= int(spec.min_symbols)
        data_ready = not missing and not missing_groups and not missing_activity
        return {
            "ready": bool(data_ready and symbol_ready),
            "data_ready": bool(data_ready),
            "symbol_ready": bool(symbol_ready),
            "symbol_count": int(symbol_count),
            "min_symbols": int(spec.min_symbols),
            "min_coverage": float(spec.min_coverage),
            "coverage": coverage_by_requirement,
            "activity": activity,
            "missing_patterns": tuple(missing),
            "missing_any_groups": tuple(missing_groups),
            "missing_activity": tuple(missing_activity),
        }

    @abstractmethod
    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        raise NotImplementedError
