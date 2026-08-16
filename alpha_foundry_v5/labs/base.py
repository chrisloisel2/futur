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


class LabPlugin(ABC):
    plugin_name = "base"

    def readiness(self, frame: pd.DataFrame, spec: LabSpec) -> Dict[str, object]:
        columns = tuple(str(c) for c in frame.columns)
        missing = []
        for pattern in spec.required_column_patterns:
            if not any(fnmatch(c, pattern) for c in columns):
                missing.append(pattern)
        missing_groups = []
        for group in spec.required_any_groups:
            if not any(any(fnmatch(c, p) for c in columns) for p in group):
                missing_groups.append(group)
        return {"ready": not missing and not missing_groups, "missing_patterns": tuple(missing), "missing_any_groups": tuple(missing_groups)}

    @abstractmethod
    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        raise NotImplementedError
