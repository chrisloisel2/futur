#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from infra.config.loader import load_config
from pipeline.research.backtest_engine import BacktestConfig, EventDrivenBacktester
from pipeline.research.cost_model import CostModel, CostModelConfig
from pipeline.research.execution_sim import ExecutionSimConfig, ExecutionSimulator
from pipeline.research.stress_tests import StressScenario, StressTestRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stress tests")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    scenarios = [StressScenario(**s) for s in cfg["scenarios"]]
    price_path = pd.DataFrame({"event_time": pd.date_range(args.start, args.end, freq="1min"), "mid_price": 100.0})
    orders = pd.DataFrame(
        {
            "event_time": pd.date_range(args.start, periods=5, freq="10min"),
            "side": ["buy", "sell", "buy", "sell", "buy"],
            "qty": 1.0,
            "entry_px": 100.0,
            "exit_px": 101.0,
            "symbol": args.symbol,
        }
    )
    backtester = EventDrivenBacktester(
        cost_model=CostModel(CostModelConfig(**cfg["costs"])),
        execution_sim=ExecutionSimulator(ExecutionSimConfig(**cfg["execution"])),
        config=BacktestConfig(**cfg["backtest"]),
    )
    runner = StressTestRunner(scenarios, backtester)
    runner.run(price_path, orders, run_id=args.run_id, output_dir=Path(cfg["artifacts"]["stress_dir"]))


if __name__ == "__main__":
    main()
