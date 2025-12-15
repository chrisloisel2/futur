"""
Trading performance metrics.

Standard ML metrics (accuracy, MSE) are insufficient for evaluating trading models.
This module implements metrics that matter for real trading:
- PnL (with transaction costs)
- Sharpe ratio
- Maximum drawdown
- Win rate & profit factor
- Turnover
"""
import logging
from typing import Optional

import numpy as np
import torch

logger = logging.getLogger(__name__)


def compute_pnl(
    predictions: torch.Tensor,
    true_returns: torch.Tensor,
    trading_fee: float = 0.001,
    initial_capital: float = 1.0
) -> tuple[torch.Tensor, dict]:
    """
    Compute cumulative PnL with trading costs.

    Args:
        predictions: [N] predicted returns
        true_returns: [N] actual returns
        trading_fee: Transaction cost as fraction (e.g., 0.001 = 0.1%)
        initial_capital: Initial capital

    Returns:
        cumulative_pnl: [N] cumulative PnL over time
        metrics: dict with summary statistics
    """
    # Positions from predictions (-1, 0, 1)
    positions = torch.sign(predictions)

    # Realized returns (position * true_return)
    realized_returns = positions * true_returns

    # Trading costs (incurred when position changes)
    position_changes = torch.abs(torch.diff(positions, prepend=torch.tensor([0.0])))
    trading_costs = position_changes * trading_fee

    # Net returns after costs
    net_returns = realized_returns - trading_costs

    # Cumulative PnL
    cumulative_pnl = torch.cumsum(net_returns, dim=0) * initial_capital

    # Final PnL
    final_pnl = cumulative_pnl[-1].item()
    total_return = final_pnl / initial_capital

    # Number of trades
    num_trades = position_changes.sum().item()

    metrics = {
        'final_pnl': final_pnl,
        'total_return': total_return,
        'total_return_pct': total_return * 100,
        'num_trades': int(num_trades),
        'total_trading_costs': (trading_costs.sum() * initial_capital).item()
    }

    return cumulative_pnl, metrics


def compute_sharpe_ratio(
    predictions: torch.Tensor,
    true_returns: torch.Tensor,
    risk_free_rate: float = 0.0,
    annualization_factor: Optional[float] = None
) -> float:
    """
    Compute Sharpe ratio.

    Args:
        predictions: [N] predicted returns
        true_returns: [N] actual returns
        risk_free_rate: Annual risk-free rate (e.g., 0.02 = 2%)
        annualization_factor: Factor for annualization (default: sqrt(252*390) for 1-min bars)

    Returns:
        sharpe_ratio: Annualized Sharpe ratio
    """
    # Positions
    positions = torch.sign(predictions)

    # Realized returns
    realized_returns = positions * true_returns

    # Mean and std
    mean_return = realized_returns.mean().item()
    std_return = realized_returns.std().item()

    if std_return < 1e-8:
        return 0.0

    # Sharpe ratio
    sharpe = (mean_return - risk_free_rate) / std_return

    # Annualize (default: 252 days * 6.5 hours * 60 minutes)
    if annualization_factor is None:
        annualization_factor = np.sqrt(252 * 6.5 * 60)

    sharpe *= annualization_factor

    return sharpe


def compute_max_drawdown(
    cumulative_pnl: torch.Tensor
) -> tuple[float, dict]:
    """
    Compute maximum drawdown.

    Args:
        cumulative_pnl: [N] cumulative PnL over time

    Returns:
        max_drawdown: Maximum drawdown (as fraction)
        drawdown_info: dict with drawdown details
    """
    # Running maximum
    running_max = torch.cummax(cumulative_pnl, dim=0)[0]

    # Drawdown at each point (as fraction of running max)
    drawdown = (running_max - cumulative_pnl) / (running_max + 1e-8)

    # Maximum drawdown
    max_dd = drawdown.max().item()

    # Find drawdown period
    max_dd_idx = drawdown.argmax().item()

    # Find peak before max drawdown
    peak_idx = None
    for i in range(max_dd_idx, -1, -1):
        if cumulative_pnl[i] == running_max[max_dd_idx]:
            peak_idx = i
            break

    drawdown_info = {
        'max_drawdown': max_dd,
        'max_drawdown_pct': max_dd * 100,
        'peak_idx': peak_idx,
        'trough_idx': max_dd_idx,
        'drawdown_length': max_dd_idx - peak_idx if peak_idx is not None else None
    }

    return max_dd, drawdown_info


def compute_win_rate_and_profit_factor(
    predictions: torch.Tensor,
    true_returns: torch.Tensor
) -> dict:
    """
    Compute win rate and profit factor.

    Args:
        predictions: [N] predicted returns
        true_returns: [N] actual returns

    Returns:
        metrics: dict with win_rate and profit_factor
    """
    # Positions
    positions = torch.sign(predictions)

    # Realized returns
    realized_returns = positions * true_returns

    # Wins and losses
    wins = realized_returns[realized_returns > 0]
    losses = realized_returns[realized_returns < 0]

    # Win rate
    num_wins = len(wins)
    num_losses = len(losses)
    total_trades = num_wins + num_losses

    win_rate = num_wins / total_trades if total_trades > 0 else 0.0

    # Profit factor
    total_profit = wins.sum().item() if len(wins) > 0 else 0.0
    total_loss = torch.abs(losses.sum()).item() if len(losses) > 0 else 0.0

    profit_factor = total_profit / total_loss if total_loss > 1e-8 else float('inf')

    # Average win/loss
    avg_win = wins.mean().item() if len(wins) > 0 else 0.0
    avg_loss = losses.mean().item() if len(losses) > 0 else 0.0

    metrics = {
        'win_rate': win_rate,
        'win_rate_pct': win_rate * 100,
        'profit_factor': profit_factor,
        'num_wins': num_wins,
        'num_losses': num_losses,
        'total_trades': total_trades,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'expectancy': avg_win * win_rate + avg_loss * (1 - win_rate)
    }

    return metrics


def compute_turnover(
    predictions: torch.Tensor,
    total_periods: Optional[int] = None
) -> float:
    """
    Compute turnover (trading frequency).

    Args:
        predictions: [N] predicted returns
        total_periods: Total number of periods (default: len(predictions))

    Returns:
        turnover: Average number of position changes per period
    """
    positions = torch.sign(predictions)

    # Position changes
    position_changes = torch.abs(torch.diff(positions, prepend=torch.tensor([0.0])))

    # Total changes
    total_changes = position_changes.sum().item()

    # Turnover
    if total_periods is None:
        total_periods = len(predictions)

    turnover = total_changes / total_periods

    return turnover


def compute_sortino_ratio(
    predictions: torch.Tensor,
    true_returns: torch.Tensor,
    risk_free_rate: float = 0.0,
    annualization_factor: Optional[float] = None
) -> float:
    """
    Compute Sortino ratio (Sharpe ratio using only downside deviation).

    Args:
        predictions: [N] predicted returns
        true_returns: [N] actual returns
        risk_free_rate: Annual risk-free rate
        annualization_factor: Factor for annualization

    Returns:
        sortino_ratio: Annualized Sortino ratio
    """
    # Positions
    positions = torch.sign(predictions)

    # Realized returns
    realized_returns = positions * true_returns

    # Mean return
    mean_return = realized_returns.mean().item()

    # Downside returns (only negative)
    downside_returns = realized_returns[realized_returns < 0]

    if len(downside_returns) == 0:
        return float('inf')

    # Downside deviation
    downside_std = downside_returns.std().item()

    if downside_std < 1e-8:
        return float('inf')

    # Sortino ratio
    sortino = (mean_return - risk_free_rate) / downside_std

    # Annualize
    if annualization_factor is None:
        annualization_factor = np.sqrt(252 * 6.5 * 60)

    sortino *= annualization_factor

    return sortino


def compute_calmar_ratio(
    cumulative_pnl: torch.Tensor,
    max_drawdown: float,
    total_periods: int,
    periods_per_year: int = 252 * 6.5 * 60
) -> float:
    """
    Compute Calmar ratio (annualized return / max drawdown).

    Args:
        cumulative_pnl: [N] cumulative PnL
        max_drawdown: Maximum drawdown
        total_periods: Total number of periods
        periods_per_year: Number of periods per year

    Returns:
        calmar_ratio: Calmar ratio
    """
    # Total return
    total_return = cumulative_pnl[-1].item()

    # Annualize return
    years = total_periods / periods_per_year
    annualized_return = total_return / years if years > 0 else 0.0

    # Calmar ratio
    if max_drawdown < 1e-8:
        return float('inf')

    calmar = annualized_return / max_drawdown

    return calmar


def compute_all_metrics(
    predictions: torch.Tensor,
    true_returns: torch.Tensor,
    trading_fee: float = 0.001,
    initial_capital: float = 1.0,
    risk_free_rate: float = 0.0
) -> dict:
    """
    Compute all trading metrics.

    Args:
        predictions: [N] predicted returns
        true_returns: [N] actual returns
        trading_fee: Transaction cost
        initial_capital: Initial capital
        risk_free_rate: Risk-free rate

    Returns:
        metrics: dict with all metrics
    """
    # PnL
    cumulative_pnl, pnl_metrics = compute_pnl(
        predictions, true_returns, trading_fee, initial_capital
    )

    # Sharpe ratio
    sharpe = compute_sharpe_ratio(predictions, true_returns, risk_free_rate)

    # Sortino ratio
    sortino = compute_sortino_ratio(predictions, true_returns, risk_free_rate)

    # Max drawdown
    max_dd, dd_info = compute_max_drawdown(cumulative_pnl)

    # Win rate & profit factor
    win_metrics = compute_win_rate_and_profit_factor(predictions, true_returns)

    # Turnover
    turnover = compute_turnover(predictions)

    # Calmar ratio
    calmar = compute_calmar_ratio(cumulative_pnl, max_dd, len(predictions))

    # Combine all metrics
    all_metrics = {
        **pnl_metrics,
        'sharpe_ratio': sharpe,
        'sortino_ratio': sortino,
        'calmar_ratio': calmar,
        **dd_info,
        **win_metrics,
        'turnover': turnover,
        'trades_per_day': turnover * 6.5 * 60  # Assuming 1-min bars, 6.5h trading day
    }

    return all_metrics


def print_metrics_report(metrics: dict):
    """
    Print a formatted metrics report.

    Args:
        metrics: Metrics dict from compute_all_metrics
    """
    print("\n" + "=" * 60)
    print("TRADING PERFORMANCE METRICS")
    print("=" * 60)

    print("\nProfitability:")
    print(f"  Total Return:        {metrics['total_return_pct']:>8.2f}%")
    print(f"  Final PnL:           {metrics['final_pnl']:>8.4f}")
    print(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:>8.4f}")
    print(f"  Sortino Ratio:       {metrics['sortino_ratio']:>8.4f}")
    print(f"  Calmar Ratio:        {metrics['calmar_ratio']:>8.4f}")

    print("\nRisk:")
    print(f"  Max Drawdown:        {metrics['max_drawdown_pct']:>8.2f}%")
    print(f"  Drawdown Length:     {metrics['drawdown_length']:>8} periods")

    print("\nTrade Statistics:")
    print(f"  Win Rate:            {metrics['win_rate_pct']:>8.2f}%")
    print(f"  Profit Factor:       {metrics['profit_factor']:>8.4f}")
    print(f"  Total Trades:        {metrics['total_trades']:>8}")
    print(f"  Num Wins:            {metrics['num_wins']:>8}")
    print(f"  Num Losses:          {metrics['num_losses']:>8}")
    print(f"  Avg Win:             {metrics['avg_win']:>8.6f}")
    print(f"  Avg Loss:            {metrics['avg_loss']:>8.6f}")
    print(f"  Expectancy:          {metrics['expectancy']:>8.6f}")

    print("\nTrading Activity:")
    print(f"  Turnover:            {metrics['turnover']:>8.4f}")
    print(f"  Trades/Day:          {metrics['trades_per_day']:>8.2f}")
    print(f"  Total Trading Costs: {metrics['total_trading_costs']:>8.6f}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    # Test metrics
    logging.basicConfig(level=logging.INFO)

    # Create synthetic predictions and returns
    n = 10000
    torch.manual_seed(42)

    # Somewhat predictive model (60% directional accuracy)
    true_returns = torch.randn(n) * 0.001
    noise = torch.randn(n) * 0.0008
    predictions = true_returns * 0.6 + noise

    print("Testing trading metrics on synthetic data...")
    print(f"Data size: {n} samples")

    # Compute all metrics
    metrics = compute_all_metrics(
        predictions=predictions,
        true_returns=true_returns,
        trading_fee=0.001,
        initial_capital=1.0
    )

    # Print report
    print_metrics_report(metrics)

    # Verify metrics make sense
    assert 0 <= metrics['win_rate'] <= 1, "Win rate should be in [0, 1]"
    assert metrics['profit_factor'] >= 0, "Profit factor should be non-negative"
    assert 0 <= metrics['max_drawdown'] <= 1, "Max drawdown should be in [0, 1]"

    print("Metrics test passed!")
