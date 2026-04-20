#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from infra.config.loader import load_config
from pipeline.research.backtest_engine import BacktestConfig, EventDrivenBacktester
from pipeline.research.cost_model import CostModel, CostModelConfig
from pipeline.research.execution_sim import ExecutionSimConfig, ExecutionSimulator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run event-driven backtest")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", default="taker")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    orders_path = Path(cfg["data"].get("orders_path", ""))
    if orders_path.exists():
        orders = pd.read_parquet(orders_path)
    else:
        orders = pd.DataFrame(
            {
                "event_time": pd.date_range(args.start, periods=10, freq="5min"),
                "side": ["buy", "sell"] * 5,
                "qty": 1.0,
                "entry_px": 100.0,
                "exit_px": 101.0,
                "symbol": args.symbol,
            }
        )
    cost_model = CostModel(CostModelConfig(**cfg["costs"]))
    exec_sim = ExecutionSimulator(ExecutionSimConfig(**cfg["execution"]))
    backtester = EventDrivenBacktester(cost_model=cost_model, execution_sim=exec_sim, config=BacktestConfig(**cfg["backtest"]))
    backtester.run(orders=orders, run_id=args.run_id)


if __name__ == "__main__":
    main()
