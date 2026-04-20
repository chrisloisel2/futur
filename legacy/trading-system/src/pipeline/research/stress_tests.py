from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger
from pipeline.research.backtest_engine import EventDrivenBacktester

logger = get_logger(__name__)


class StressScenario(BaseModel):
    name: str
    type: str  # flash_crash|wick|gap|spread_x10|outage|partial_fills
    magnitude: float
    duration_s: int = 60


@dataclass
class StressResult:
    scenario: str
    net_pnl: float
    max_drawdown: float
    fills: int


class StressTestRunner:
    def __init__(self, scenarios: List[StressScenario], backtester: EventDrivenBacktester):
        self.scenarios = scenarios
        self.backtester = backtester

    def run(self, price_path: pd.DataFrame, orders: pd.DataFrame, run_id: str, output_dir: Path) -> Dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        results: List[StressResult] = []
        reports: Dict[str, Path] = {}
        for scenario in self.scenarios:
            shocked_orders = self._apply_scenario(price_path, orders.copy(), scenario)
            paths = self.backtester.run(shocked_orders, run_id=f"{run_id}_{scenario.name}", output_dir=output_dir / scenario.name)
            metrics = json.loads((paths["metrics"]).read_text())
            results.append(
                StressResult(
                    scenario=scenario.name,
                    net_pnl=metrics.get("net_pnl", 0.0),
                    max_drawdown=metrics.get("max_drawdown", 0.0),
                    fills=len(pd.read_parquet(paths["fills"])),
                )
            )
            reports[scenario.name] = paths["metrics"]
        summary = pd.DataFrame([r.__dict__ for r in results])
        report_path = output_dir / "stress_report.json"
        report_path.write_text(summary.to_json(orient="records", indent=2))
        logger.info({"msg": "stress tests complete", "scenarios": len(results)})
        return reports

    def _apply_scenario(self, price_path: pd.DataFrame, orders: pd.DataFrame, scenario: StressScenario) -> pd.DataFrame:
        orders = orders.copy()
        if scenario.type == "flash_crash":
            orders["exit_px"] = orders.get("exit_px", orders.get("entry_px", 0)) * (1 - scenario.magnitude)
        elif scenario.type == "wick":
            orders["exit_px"] = orders.get("exit_px", orders.get("entry_px", 0)) * (1 - scenario.magnitude / 2)
        elif scenario.type == "gap":
            orders["entry_px"] = orders.get("entry_px", orders.get("price", 0)) * (1 - scenario.magnitude)
        elif scenario.type == "spread_x10":
            orders["slippage_bps"] = orders.get("slippage_bps", 0) + scenario.magnitude * 10_000
        elif scenario.type == "outage":
            cutoff = price_path["event_time"].min() + pd.Timedelta(seconds=scenario.duration_s)
            orders = orders[orders["event_time"] <= cutoff]
        elif scenario.type == "partial_fills":
            orders["qty"] = orders["qty"] * 0.5
        return orders
