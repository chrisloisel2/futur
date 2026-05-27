from __future__ import annotations

from datetime import datetime
from typing import Dict

import pandas as pd

from domain.monitoring.drift import DriftReport
from pipeline.monitoring.drift.data_drift import DataDriftDetector
from pipeline.monitoring.drift.pred_drift import PredictionDriftDetector
from pipeline.monitoring.drift.perf_drift import PerformanceDriftDetector
from pipeline.monitoring.drift.regime_drift import RegimeDriftDetector
from pipeline.monitoring.actions import ActionEngine
from pipeline.monitoring.alerts import AlertRouter
from pipeline.monitoring.dashboards import DashboardsExporter


class MonitoringPipeline:
    def __init__(self, config: Dict):
        self.config = config
        self.data_drift = DataDriftDetector(config.get("drift", {}))
        self.pred_drift = PredictionDriftDetector(config.get("drift", {}))
        self.perf_drift = PerformanceDriftDetector(config.get("drift", {}))
        self.regime_drift = RegimeDriftDetector(config.get("drift", {}))
        self.actions = ActionEngine(config.get("actions", {}))
        self.alerts = AlertRouter(config.get("alerts", {}))
        self.dash = DashboardsExporter()

    def step(self, now: datetime, state_df: pd.DataFrame, signal_df: pd.DataFrame, fills_df: pd.DataFrame, costs_df: pd.DataFrame, portfolio_df: pd.DataFrame, run_id: str) -> Dict:
        data_report = self.data_drift.compute(state_df, pd.DataFrame())
        pred_report = self.pred_drift.compute(signal_df, pd.DataFrame())
        perf_report = self.perf_drift.compute(fills_df, fills_df, costs_df, pd.DataFrame())
        regime_report = self.regime_drift.compute(signal_df, pd.DataFrame())
        reports = {
            "event_time": now,
            "run_id": run_id,
            "data_drift": data_report,
            "pred_drift": pred_report,
            "perf_drift": perf_report,
            "regime_drift": regime_report,
        }
        action_plan = self.actions.decide_actions(reports, {})
        alerts = self.alerts.build_alerts(reports, action_plan)
        self.dash.export(run_id, {"reports": self._serialize_reports(reports), "actions": [a.__dict__ for a in action_plan.actions]})
        return {"reports": reports, "action_plan": action_plan, "alerts": alerts}

    def _serialize_reports(self, reports: Dict) -> Dict:
        out = {}
        for k, v in reports.items():
            if hasattr(v, "__dict__"):
                out[k] = self._serialize_reports(v.__dict__)
            elif isinstance(v, dict):
                out[k] = self._serialize_reports(v)
            else:
                out[k] = v
        return out
