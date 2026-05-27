# backtest/__init__.py
from .metrics import BacktestResult, compute_backtest_metrics
from .engine import run_backtest_side, run_backtest_combined, run_cost_sensitivity
