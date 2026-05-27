from __future__ import annotations

import numpy as np
import pandas as pd

from domain.monitoring.drift import PredictionDriftReport


class PredictionDriftDetector:
    def __init__(self, config: dict):
        self.config = config

    def compute(self, signal_window: pd.DataFrame, baseline: pd.DataFrame) -> PredictionDriftReport:
        by_symbol = {}
        for symbol, df in signal_window.groupby("symbol"):
            p_hit = df.get("p_hit", pd.Series(dtype=float))
            base = baseline[baseline["symbol"] == symbol].get("p_hit", pd.Series(dtype=float)) if not baseline.empty else pd.Series(dtype=float)
            shift = float(p_hit.mean() - base.mean()) if not base.empty else 0.0
            entropy_shift = float(df.get("entropy", pd.Series(dtype=float)).mean() - baseline.get("entropy", pd.Series(dtype=float)).mean()) if not baseline.empty else 0.0
            by_symbol[symbol] = {"p_hit_shift": shift, "entropy_shift": entropy_shift, "severity": "OK"}
        return PredictionDriftReport(window=self.config.get("window", ""), by_symbol=by_symbol, severity="OK")
