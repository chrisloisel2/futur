from __future__ import annotations

import numpy as np
import pandas as pd

from domain.monitoring.drift import RegimeDriftReport


class RegimeDriftDetector:
    def __init__(self, config: dict):
        self.config = config

    def compute(self, regime_window: pd.DataFrame, baseline: pd.DataFrame) -> RegimeDriftReport:
        if regime_window.empty:
            return RegimeDriftReport(window=self.config.get("window", ""), global_stats={}, severity="OK")
        counts = regime_window.get("regime", pd.Series(dtype=str)).value_counts(normalize=True)
        base_counts = baseline.get("regime", pd.Series(dtype=str)).value_counts(normalize=True) if not baseline.empty else pd.Series(dtype=float)
        js = float(((counts - base_counts).fillna(0) ** 2).sum()) if not base_counts.empty else 0.0
        transitions = regime_window.get("regime", pd.Series(dtype=str)).ne(regime_window.get("regime", pd.Series(dtype=str)).shift()).mean()
        stats = {"regime_dist_js": js, "transition_rate": float(transitions)}
        return RegimeDriftReport(window=self.config.get("window", ""), global_stats=stats, severity="OK")
