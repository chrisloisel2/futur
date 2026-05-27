from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DriftMetric:
    psi: float = 0.0
    js: float = 0.0
    ks_pvalue: float = 1.0
    zshift: float = 0.0
    missing_rate: float = 0.0


@dataclass
class DriftScore:
    feature: str
    metric: DriftMetric
    severity: str = "OK"


@dataclass
class DataDriftReport:
    window: str
    by_symbol: Dict[str, Dict[str, DriftMetric]]
    severity: str = "OK"


@dataclass
class PredictionDriftReport:
    window: str
    by_symbol: Dict[str, Dict[str, float]]
    severity: str = "OK"


@dataclass
class PerformanceDriftReport:
    window: str
    by_symbol: Dict[str, Dict[str, float]]
    severity: str = "OK"


@dataclass
class RegimeDriftReport:
    window: str
    global_stats: Dict[str, float]
    severity: str = "OK"


@dataclass
class DriftReport:
    event_time: object
    run_id: str
    data_drift: DataDriftReport
    pred_drift: PredictionDriftReport
    perf_drift: PerformanceDriftReport
    regime_drift: RegimeDriftReport
