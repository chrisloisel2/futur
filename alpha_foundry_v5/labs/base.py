from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, Tuple

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


class LabPlugin(ABC):
    plugin_name = "base"

    def readiness(self, frame: pd.DataFrame, spec: LabSpec) -> Dict[str, object]:
        columns = tuple(str(c) for c in frame.columns)

        def coverage(column: str) -> float:
            series = frame[column]
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().sum() > 0:
                return float(numeric.notna().mean())
            return float(series.notna().mean())

        missing = []
        coverage_by_requirement = {}
        for pattern in spec.required_column_patterns:
            matches = [c for c in columns if fnmatch(c, pattern)]
            best = max([coverage(c) for c in matches], default=0.0)
            coverage_by_requirement[pattern] = best
            if best < float(spec.min_coverage):
                missing.append(pattern)
        missing_groups = []
        for group in spec.required_any_groups:
            matches = [c for c in columns if any(fnmatch(c, p) for p in group)]
            best = max([coverage(c) for c in matches], default=0.0)
            coverage_by_requirement["ANY:" + "|".join(group)] = best
            if best < float(spec.min_coverage):
                missing_groups.append(group)
        return {"ready": not missing and not missing_groups, "min_coverage": float(spec.min_coverage), "coverage": coverage_by_requirement, "missing_patterns": tuple(missing), "missing_any_groups": tuple(missing_groups)}

    @abstractmethod
    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        raise NotImplementedError
