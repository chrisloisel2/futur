"""
TRM evaluation module.
"""
from .backtest import TRMBacktester, WalkForwardValidator, compare_models
from .metrics import (
    compute_all_metrics,
    compute_calmar_ratio,
    compute_max_drawdown,
    compute_pnl,
    compute_sharpe_ratio,
    compute_sortino_ratio,
    compute_turnover,
    compute_win_rate_and_profit_factor,
    print_metrics_report,
)

__all__ = [
    # Backtesting
    'TRMBacktester',
    'WalkForwardValidator',
    'compare_models',
    # Metrics
    'compute_all_metrics',
    'compute_pnl',
    'compute_sharpe_ratio',
    'compute_sortino_ratio',
    'compute_max_drawdown',
    'compute_win_rate_and_profit_factor',
    'compute_turnover',
    'compute_calmar_ratio',
    'print_metrics_report',
]
