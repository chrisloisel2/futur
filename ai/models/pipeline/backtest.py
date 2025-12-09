"""
Advanced Backtesting Engine for Crypto Trading.

Features:
- Realistic transaction costs (maker/taker fees)
- Stochastic slippage based on volume
- Execution latency simulation
- Comprehensive metrics (Sharpe, Calmar, Sortino, etc.)
- Walk-forward validation with recalibration
- Interactive visualization with plotly
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from collections import deque
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""

    # Trading costs
    maker_fee: float = 0.001  # 0.1%
    taker_fee: float = 0.001  # 0.1%

    # Slippage
    base_slippage: float = 0.0005  # 0.05% base slippage
    volume_impact: float = 0.0001  # Additional slippage per $1M volume

    # Execution
    min_latency_ms: float = 500.0  # Minimum execution latency (ms)
    max_latency_ms: float = 2000.0  # Maximum execution latency (ms)

    # Initial capital
    initial_capital: float = 10000.0  # $10,000

    # Position sizing
    max_position_size: float = 1.0  # Maximum position (1.0 = 100% capital)

    # Risk management
    max_drawdown_stop: Optional[float] = None  # Stop trading if DD exceeds this

    # Walk-forward
    training_window: int = 1000  # Timesteps for training
    validation_window: int = 200  # Timesteps for validation
    retraining_frequency: int = 200  # Retrain every N timesteps


@dataclass
class Trade:
    """Record of a single trade."""

    timestamp: pd.Timestamp
    action: str  # 'buy', 'sell', 'close'
    price: float
    quantity: float
    value: float
    fee: float
    slippage: float
    latency_ms: float
    position_before: float
    position_after: float
    cash_before: float
    cash_after: float


@dataclass
class BacktestResults:
    """Results from backtesting."""

    # Equity curve
    timestamps: List[pd.Timestamp] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)
    positions: List[float] = field(default_factory=list)
    cash: List[float] = field(default_factory=list)

    # Trades
    trades: List[Trade] = field(default_factory=list)

    # Metrics
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0

    # Trade statistics
    num_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    # Costs
    total_fees: float = 0.0
    total_slippage: float = 0.0


class Backtester:
    """
    Advanced backtesting engine.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        """
        Initialize backtester.

        Args:
            config: Backtest configuration
        """
        self.config = config or BacktestConfig()
        self.reset()

    def reset(self):
        """Reset backtester state."""
        self.cash = self.config.initial_capital
        self.position_qty = 0.0  # Asset units (can be negative for shorts)
        self.equity_history = []
        self.position_history = []
        self.cash_history = []
        self.timestamp_history = []
        self.trades = []
        self._return_window = deque(maxlen=200)
        self._bar_seconds: Optional[float] = None
        self._last_price: Optional[float] = None

    def _simulate_slippage(
        self,
        price: float,
        volume: float,
        action: str,
        market_volume: Optional[float] = None,
    ) -> float:
        """
        Simulate realistic slippage.

        Args:
            price: Market price
            volume: Trade volume ($)
            action: 'buy' or 'sell'
            market_volume: Observed traded volume on the bar (optional, used for liquidity impact)

        Returns:
            Execution price including slippage
        """
        # Base slippage
        base_slip = self.config.base_slippage

        # Volume-dependent slippage
        volume_millions = volume / 1_000_000
        volume_slip = self.config.volume_impact * volume_millions

        # Liquidity adjustment: higher slippage when consuming large fraction of bar volume
        if market_volume is not None and market_volume > 0:
            dollar_liquidity = market_volume * price
            liquidity_ratio = volume / max(dollar_liquidity, 1e-9)
            volume_slip *= (1 + min(liquidity_ratio, 10.0))

        # Total slippage (in percentage)
        total_slip = base_slip + volume_slip

        # Add randomness
        total_slip *= np.random.uniform(0.5, 1.5)

        # Apply slippage (worse price)
        if action == 'buy':
            execution_price = price * (1 + total_slip)
        else:
            execution_price = price * (1 - total_slip)

        return execution_price

    def _simulate_latency(self) -> float:
        """
        Simulate execution latency.

        Returns:
            Latency in milliseconds
        """
        return np.random.uniform(
            self.config.min_latency_ms,
            self.config.max_latency_ms
        )

    def _calculate_fee(
        self,
        value: float,
        is_maker: bool = False,
    ) -> float:
        """
        Calculate trading fee.

        Args:
            value: Trade value
            is_maker: Whether this is a maker order

        Returns:
            Fee amount
        """
        fee_rate = self.config.maker_fee if is_maker else self.config.taker_fee
        return value * fee_rate

    def _infer_bar_seconds(self, prices: pd.Series) -> float:
        """Infer bar duration (in seconds) from the price index."""
        if len(prices) < 2 or not isinstance(prices.index, pd.DatetimeIndex):
            return 60.0

        diffs = prices.index.to_series().diff().dropna().dt.total_seconds()
        if len(diffs) == 0:
            return 60.0

        return float(np.median(diffs))

    def _apply_latency_impact(
        self,
        price: float,
        latency_ms: float,
    ) -> float:
        """
        Apply a stochastic price move during execution latency using local volatility.

        Args:
            price: Current observed price
            latency_ms: Simulated latency in milliseconds

        Returns:
            Adjusted price after latency impact
        """
        bar_seconds = max(self._bar_seconds or 60.0, 1.0)
        window_std = np.std(self._return_window) if len(self._return_window) > 1 else 0.0005

        per_second_vol = window_std / np.sqrt(bar_seconds)
        horizon_seconds = max(latency_ms / 1000.0, 1e-6)
        shock = np.random.normal(0, per_second_vol * np.sqrt(horizon_seconds))
        shock = float(np.clip(shock, -0.02, 0.02))  # cap extreme intrabar moves

        return price * (1 + shock)

    def execute_trade(
        self,
        timestamp: pd.Timestamp,
        price: float,
        target_position: float,
        is_maker: bool = False,
        market_volume: Optional[float] = None,
    ) -> Optional[Trade]:
        """
        Execute a trade with realistic simulation.

        Args:
            timestamp: Current timestamp
            price: Market price
            target_position: Target position (-1 to +1, as fraction of equity)
            is_maker: Whether order is maker
            market_volume: Observed traded volume on the bar (used for slippage)

        Returns:
            Trade object if trade executed, None otherwise
        """
        target_position = float(
            np.clip(target_position, -self.config.max_position_size, self.config.max_position_size)
        )

        equity_before = self.cash + self.position_qty * price
        if equity_before <= 0:
            return None

        current_exposure = (self.position_qty * price) / equity_before if equity_before != 0 else 0.0
        position_change = target_position - current_exposure
        if abs(position_change) < 0.001:
            return None

        action = 'buy' if position_change > 0 else 'sell'

        target_value = target_position * equity_before
        current_value = self.position_qty * price
        trade_value = target_value - current_value  # signed
        notional = abs(trade_value)
        if notional == 0:
            return None

        # Simulate latency (in practice, price could move)
        latency = self._simulate_latency()
        latency_price = self._apply_latency_impact(price, latency)

        # Simulate slippage
        execution_price = self._simulate_slippage(latency_price, notional, action, market_volume)

        # Calculate quantity (signed)
        quantity = trade_value / execution_price

        # Calculate fee
        fee = self._calculate_fee(notional, is_maker)

        # If we cannot cover the trade (e.g., due to fees), downscale buy size
        if action == 'buy' and notional + fee > self.cash:
            affordable = max(self.cash - fee, 0.0)
            if affordable <= 0:
                return None
            quantity = affordable / execution_price
            notional = affordable
            fee = self._calculate_fee(notional, is_maker)

        slippage_cost = abs(execution_price - latency_price) * abs(quantity)

        # Update position and cash
        cash_before = self.cash

        if action == 'buy':
            self.cash -= (notional + fee)
        else:
            self.cash += (notional - fee)

        self.position_qty += quantity

        equity_after = self.cash + self.position_qty * price
        exposure_after = (self.position_qty * price) / equity_after if equity_after != 0 else 0.0
        exposure_before = current_exposure

        # Record trade
        trade = Trade(
            timestamp=timestamp,
            action=action,
            price=execution_price,
            quantity=quantity,
            value=notional,
            fee=fee,
            slippage=slippage_cost,
            latency_ms=latency,
            position_before=exposure_before,
            position_after=exposure_after,
            cash_before=cash_before,
            cash_after=self.cash,
        )

        self.trades.append(trade)

        return trade

    def update_equity(
        self,
        timestamp: pd.Timestamp,
        price: float,
    ):
        """
        Update equity tracking.

        Args:
            timestamp: Current timestamp
            price: Current market price
        """
        position_value = self.position_qty * price
        equity = self.cash + position_value
        exposure = position_value / equity if equity != 0 else 0.0

        # Record
        self.timestamp_history.append(timestamp)
        self.equity_history.append(equity)
        self.position_history.append(exposure)
        self.cash_history.append(self.cash)

    def run(
        self,
        prices: pd.Series,
        signals: pd.Series,
        is_maker: Optional[pd.Series] = None,
        volumes: Optional[pd.Series] = None,
    ) -> BacktestResults:
        """
        Run backtest.

        Args:
            prices: Price series (index = timestamps)
            signals: Trading signals (-1 to +1, index = timestamps)
            is_maker: Whether each trade is maker (optional)
            volumes: Optional traded volume series aligned to prices (used for slippage)

        Returns:
            BacktestResults object
        """
        self.reset()

        # Align indices
        prices = prices.sort_index()
        signals = signals.reindex(prices.index, fill_value=0.0)
        volumes = volumes.reindex(prices.index) if volumes is not None else None

        if is_maker is None:
            is_maker = pd.Series(False, index=prices.index)
        else:
            is_maker = is_maker.reindex(prices.index, fill_value=False)

        self._bar_seconds = self._infer_bar_seconds(prices)

        # Iterate through time
        for timestamp, price in prices.items():
            if self._last_price is not None and self._last_price != 0:
                self._return_window.append(price / self._last_price - 1)
            self._last_price = price

            signal = signals.loc[timestamp]
            maker = is_maker.loc[timestamp]
            bar_volume = volumes.loc[timestamp] if volumes is not None else None

            # Execute trade if signal changed
            self.execute_trade(timestamp, price, signal, maker, bar_volume)

            # Update equity
            self.update_equity(timestamp, price)

            # Check drawdown stop
            if self.config.max_drawdown_stop is not None:
                current_dd = self._calculate_current_drawdown()
                if current_dd > self.config.max_drawdown_stop:
                    print(f"Max drawdown stop triggered: {current_dd:.2%}")
                    break

        # Calculate metrics
        results = self._calculate_metrics()

        return results

    def _calculate_current_drawdown(self) -> float:
        """Calculate current drawdown."""
        if len(self.equity_history) == 0:
            return 0.0

        equity_array = np.array(self.equity_history)
        running_max = np.maximum.accumulate(equity_array)
        drawdown = (equity_array - running_max) / running_max

        return abs(drawdown[-1])

    def _calculate_metrics(self) -> BacktestResults:
        """Calculate backtest metrics."""
        results = BacktestResults()

        # Store histories
        results.timestamps = self.timestamp_history
        results.equity = self.equity_history
        results.positions = self.position_history
        results.cash = self.cash_history
        results.trades = self.trades

        if len(self.equity_history) == 0:
            return results

        # Convert to arrays
        equity_array = np.array(self.equity_history)

        # Returns
        returns = np.diff(equity_array) / equity_array[:-1]

        # Total return
        results.total_return = (equity_array[-1] - self.config.initial_capital) / self.config.initial_capital

        # Annualized return (assume hourly data)
        n_periods = len(equity_array)
        years = n_periods / (365 * 24)
        results.annualized_return = (1 + results.total_return) ** (1 / years) - 1 if years > 0 else 0.0

        # Sharpe ratio (annualized)
        if len(returns) > 0:
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            if std_return > 0:
                results.sharpe_ratio = mean_return / std_return * np.sqrt(365 * 24)  # Annualized

        # Sortino ratio (annualized)
        if len(returns) > 0:
            downside_returns = returns[returns < 0]
            if len(downside_returns) > 0:
                downside_std = np.std(downside_returns)
                if downside_std > 0:
                    results.sortino_ratio = mean_return / downside_std * np.sqrt(365 * 24)

        # Max drawdown
        running_max = np.maximum.accumulate(equity_array)
        drawdown = (equity_array - running_max) / running_max
        results.max_drawdown = abs(drawdown.min())

        # Max drawdown duration
        is_drawdown = drawdown < 0
        if is_drawdown.any():
            dd_lengths = []
            current_length = 0
            for dd in is_drawdown:
                if dd:
                    current_length += 1
                else:
                    if current_length > 0:
                        dd_lengths.append(current_length)
                    current_length = 0
            if current_length > 0:
                dd_lengths.append(current_length)
            results.max_drawdown_duration = max(dd_lengths) if dd_lengths else 0

        # Calmar ratio
        if results.max_drawdown > 0:
            results.calmar_ratio = results.annualized_return / results.max_drawdown

        # Trade statistics
        results.num_trades = len(self.trades)

        if results.num_trades > 0:
            # Trade P&L
            trade_pnl = []
            for i, trade in enumerate(self.trades):
                if i == 0:
                    continue
                pnl = trade.cash_after - self.trades[i-1].cash_after
                trade_pnl.append(pnl)

            if len(trade_pnl) > 0:
                winning_trades = [p for p in trade_pnl if p > 0]
                losing_trades = [p for p in trade_pnl if p < 0]

                results.win_rate = len(winning_trades) / len(trade_pnl) if len(trade_pnl) > 0 else 0.0
                results.avg_win = np.mean(winning_trades) if len(winning_trades) > 0 else 0.0
                results.avg_loss = np.mean(losing_trades) if len(losing_trades) > 0 else 0.0

                total_wins = sum(winning_trades)
                total_losses = abs(sum(losing_trades))
                results.profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

        # Costs
        results.total_fees = sum(trade.fee for trade in self.trades)
        results.total_slippage = sum(trade.slippage for trade in self.trades)

        return results


class WalkForwardValidator:
    """
    Walk-forward validation with model retraining.
    """

    def __init__(
        self,
        backtester: Backtester,
        model_trainer: Callable,
        config: Optional[BacktestConfig] = None,
    ):
        """
        Initialize walk-forward validator.

        Args:
            backtester: Backtester instance
            model_trainer: Function that trains model on data
                           signature: model_trainer(train_data) -> trained_model
            config: Backtest configuration
        """
        self.backtester = backtester
        self.model_trainer = model_trainer
        self.config = config or BacktestConfig()

    def run(
        self,
        prices: pd.Series,
        features: pd.DataFrame,
    ) -> Tuple[BacktestResults, List[BacktestResults]]:
        """
        Run walk-forward validation.

        Args:
            prices: Price series
            features: Feature matrix (same index as prices)

        Returns:
            (overall_results, window_results)
        """
        training_window = self.config.training_window
        validation_window = self.config.validation_window
        retrain_freq = self.config.retraining_frequency

        window_results = []
        all_signals = pd.Series(0.0, index=prices.index)

        # Iterate through walk-forward windows
        start_idx = 0

        while start_idx + training_window + validation_window <= len(prices):
            # Define windows
            train_end = start_idx + training_window
            val_end = train_end + validation_window

            # Extract data
            train_prices = prices.iloc[start_idx:train_end]
            train_features = features.iloc[start_idx:train_end]

            val_prices = prices.iloc[train_end:val_end]
            val_features = features.iloc[train_end:val_end]

            # Train model
            print(f"\nTraining on {train_prices.index[0]} to {train_prices.index[-1]}")
            model = self.model_trainer(train_features, train_prices)

            # Generate signals on validation set
            print(f"Validating on {val_prices.index[0]} to {val_prices.index[-1]}")
            val_signals = model.predict(val_features)

            # Run backtest on validation window
            window_result = self.backtester.run(val_prices, val_signals)
            window_results.append(window_result)

            # Store signals
            all_signals.iloc[train_end:val_end] = val_signals

            # Move to next window
            start_idx += retrain_freq

            print(f"Window {len(window_results)}: Return={window_result.total_return:.2%}, "
                  f"Sharpe={window_result.sharpe_ratio:.2f}, DD={window_result.max_drawdown:.2%}")

        # Overall backtest
        overall_result = self.backtester.run(prices, all_signals)

        return overall_result, window_results


def plot_backtest_results(
    results: BacktestResults,
    prices: Optional[pd.Series] = None,
    title: str = "Backtest Results",
) -> go.Figure:
    """
    Create interactive plotly visualization.

    Args:
        results: BacktestResults object
        prices: Optional price series to overlay
        title: Plot title

    Returns:
        Plotly figure
    """
    # Create subplots
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=(
            f"{title} - Equity Curve",
            "Position Size",
            "Drawdown",
            "Returns Distribution",
        ),
        row_heights=[0.4, 0.2, 0.2, 0.2],
    )

    timestamps = results.timestamps
    equity = np.array(results.equity)
    positions = np.array(results.positions)

    # 1. Equity curve
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=equity,
            mode='lines',
            name='Equity',
            line=dict(color='blue', width=2),
        ),
        row=1, col=1,
    )

    # Add price (normalized) if provided
    if prices is not None:
        prices_aligned = prices.reindex(timestamps)
        prices_norm = prices_aligned / prices_aligned.iloc[0] * equity[0]
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=prices_norm,
                mode='lines',
                name='Buy & Hold',
                line=dict(color='gray', width=1, dash='dash'),
            ),
            row=1, col=1,
        )

    # 2. Position size
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=positions,
            mode='lines',
            name='Position',
            line=dict(color='orange', width=1),
            fill='tozeroy',
        ),
        row=2, col=1,
    )

    # 3. Drawdown
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max * 100

    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=drawdown,
            mode='lines',
            name='Drawdown',
            line=dict(color='red', width=1),
            fill='tozeroy',
        ),
        row=3, col=1,
    )

    # 4. Returns distribution
    returns = np.diff(equity) / equity[:-1] * 100

    fig.add_trace(
        go.Histogram(
            x=returns,
            name='Returns',
            marker=dict(color='green'),
            nbinsx=50,
        ),
        row=4, col=1,
    )

    # Update layout
    fig.update_xaxes(title_text="Date", row=4, col=1)
    fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
    fig.update_yaxes(title_text="Position", row=2, col=1)
    fig.update_yaxes(title_text="Drawdown (%)", row=3, col=1)
    fig.update_yaxes(title_text="Frequency", row=4, col=1)
    fig.update_xaxes(title_text="Return (%)", row=4, col=1)

    fig.update_layout(
        height=1000,
        showlegend=True,
        title_text=title,
        hovermode='x unified',
    )

    return fig


def plot_walk_forward_results(
    window_results: List[BacktestResults],
) -> go.Figure:
    """
    Plot walk-forward validation results.

    Args:
        window_results: List of BacktestResults from each window

    Returns:
        Plotly figure
    """
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=(
            "Returns per Window",
            "Sharpe Ratio per Window",
            "Max Drawdown per Window",
            "Win Rate per Window",
        ),
    )

    window_nums = list(range(1, len(window_results) + 1))

    # Returns
    returns = [r.total_return * 100 for r in window_results]
    fig.add_trace(
        go.Bar(x=window_nums, y=returns, name='Return (%)', marker_color='blue'),
        row=1, col=1,
    )

    # Sharpe
    sharpe = [r.sharpe_ratio for r in window_results]
    fig.add_trace(
        go.Bar(x=window_nums, y=sharpe, name='Sharpe', marker_color='green'),
        row=1, col=2,
    )

    # Max DD
    max_dd = [r.max_drawdown * 100 for r in window_results]
    fig.add_trace(
        go.Bar(x=window_nums, y=max_dd, name='Max DD (%)', marker_color='red'),
        row=2, col=1,
    )

    # Win rate
    win_rate = [r.win_rate * 100 for r in window_results]
    fig.add_trace(
        go.Bar(x=window_nums, y=win_rate, name='Win Rate (%)', marker_color='orange'),
        row=2, col=2,
    )

    fig.update_xaxes(title_text="Window", row=1, col=1)
    fig.update_xaxes(title_text="Window", row=1, col=2)
    fig.update_xaxes(title_text="Window", row=2, col=1)
    fig.update_xaxes(title_text="Window", row=2, col=2)

    fig.update_layout(
        height=600,
        showlegend=False,
        title_text="Walk-Forward Validation Results",
    )

    return fig


def print_metrics(results: BacktestResults):
    """
    Print backtest metrics.

    Args:
        results: BacktestResults object
    """
    print("\n" + "="*60)
    print("BACKTEST METRICS")
    print("="*60)

    print(f"\n📊 Returns:")
    print(f"  Total Return: {results.total_return:.2%}")
    print(f"  Annualized Return: {results.annualized_return:.2%}")

    print(f"\n📈 Risk-Adjusted:")
    print(f"  Sharpe Ratio: {results.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio: {results.sortino_ratio:.2f}")
    print(f"  Calmar Ratio: {results.calmar_ratio:.2f}")

    print(f"\n📉 Drawdown:")
    print(f"  Max Drawdown: {results.max_drawdown:.2%}")
    print(f"  Max DD Duration: {results.max_drawdown_duration} periods")

    print(f"\n💼 Trading:")
    print(f"  Number of Trades: {results.num_trades}")
    print(f"  Win Rate: {results.win_rate:.2%}")
    print(f"  Profit Factor: {results.profit_factor:.2f}")
    print(f"  Avg Win: ${results.avg_win:.2f}")
    print(f"  Avg Loss: ${results.avg_loss:.2f}")

    print(f"\n💸 Costs:")
    print(f"  Total Fees: ${results.total_fees:.2f}")
    print(f"  Total Slippage: ${results.total_slippage:.2f}")
    print(f"  Total Costs: ${results.total_fees + results.total_slippage:.2f}")

    if len(results.equity) > 0:
        print(f"\n💰 Final:")
        print(f"  Initial Capital: ${results.equity[0]:.2f}")
        print(f"  Final Equity: ${results.equity[-1]:.2f}")
        print(f"  Net Profit: ${results.equity[-1] - results.equity[0]:.2f}")
