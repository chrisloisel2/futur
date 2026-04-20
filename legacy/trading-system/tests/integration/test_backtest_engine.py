import pandas as pd

from pipeline.research.backtest_engine import BacktestConfig, EventDrivenBacktester
from pipeline.research.cost_model import CostModel, CostModelConfig
from pipeline.research.execution_sim import ExecutionSimConfig, ExecutionSimulator


def test_backtester_runs_and_writes(tmp_path):
    orders = pd.DataFrame(
        {
            "event_time": pd.date_range("2024-01-01", periods=3, freq="1H"),
            "side": ["buy", "sell", "buy"],
            "qty": [1.0, 1.0, 2.0],
            "entry_px": [100.0, 101.0, 102.0],
            "exit_px": [101.0, 100.0, 104.0],
            "symbol": "BTCUSDT",
        }
    )
    backtester = EventDrivenBacktester(
        cost_model=CostModel(CostModelConfig()),
        execution_sim=ExecutionSimulator(ExecutionSimConfig(fill_probability=1.0)),
        config=BacktestConfig(artifacts_dir=str(tmp_path)),
    )
    paths = backtester.run(orders, run_id="test", output_dir=tmp_path)
    assert paths["trades"].exists()
    trades = pd.read_parquet(paths["trades"])
    assert "net_pnl" in trades.columns
