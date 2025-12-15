"""
Backtesting and walk-forward validation for TRM.

Key principles:
- Walk-forward analysis (no look-ahead bias)
- Temporal splits (train on past, test on future)
- Realistic trading simulation
"""
import logging
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from .metrics import compute_all_metrics, print_metrics_report

logger = logging.getLogger(__name__)


class TRMBacktester:
    """
    Backtester for TRM with walk-forward validation.
    """

    def __init__(
        self,
        model: nn.Module,
        test_loader: DataLoader,
        trading_fee: float = 0.001,
        initial_capital: float = 1.0,
        device: str = 'cuda' if torch.cuda.is_available() else 'cpu'
    ):
        """
        Args:
            model: Trained TRM model
            test_loader: Test data loader
            trading_fee: Transaction cost
            initial_capital: Initial capital
            device: Device to run on
        """
        self.model = model.to(device)
        self.test_loader = test_loader
        self.trading_fee = trading_fee
        self.initial_capital = initial_capital
        self.device = device

    @torch.no_grad()
    def run_backtest(self, verbose: bool = True) -> dict:
        """
        Run full backtest on test set.

        Args:
            verbose: Whether to print metrics report

        Returns:
            metrics: dict with all trading metrics
        """
        self.model.eval()

        all_predictions = []
        all_targets = []

        logger.info("Running backtest...")

        for X, y in self.test_loader:
            X, y = X.to(self.device), y.to(self.device)

            # Forward pass
            pred = self.model(X)

            all_predictions.append(pred.cpu())
            all_targets.append(y.cpu())

        # Concatenate all batches
        predictions = torch.cat(all_predictions)
        targets = torch.cat(all_targets)

        logger.info(f"Backtest complete: {len(predictions)} samples")

        # Compute metrics
        metrics = compute_all_metrics(
            predictions=predictions,
            true_returns=targets,
            trading_fee=self.trading_fee,
            initial_capital=self.initial_capital
        )

        if verbose:
            print_metrics_report(metrics)

        return metrics

    @torch.no_grad()
    def get_predictions_and_targets(self) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Get all predictions and targets from test set.

        Returns:
            predictions: [N] tensor
            targets: [N] tensor
        """
        self.model.eval()

        all_predictions = []
        all_targets = []

        for X, y in self.test_loader:
            X, y = X.to(self.device), y.to(self.device)
            pred = self.model(X)
            all_predictions.append(pred.cpu())
            all_targets.append(y.cpu())

        predictions = torch.cat(all_predictions)
        targets = torch.cat(all_targets)

        return predictions, targets


class WalkForwardValidator:
    """
    Walk-forward validation for TRM.

    Simulates realistic trading scenario:
    - Train on rolling window of past data
    - Test on next period
    - Advance window, retrain, test again
    """

    def __init__(
        self,
        train_window_size: int = 10000,  # Number of samples in training window
        test_window_size: int = 1000,     # Number of samples in test window
        step_size: int = 1000              # Step size between windows
    ):
        """
        Args:
            train_window_size: Size of training window
            test_window_size: Size of test window
            step_size: Step size for sliding window
        """
        self.train_window_size = train_window_size
        self.test_window_size = test_window_size
        self.step_size = step_size

        logger.info(
            f"Initialized WalkForwardValidator: "
            f"train_window={train_window_size}, "
            f"test_window={test_window_size}, "
            f"step={step_size}"
        )

    def validate(
        self,
        features: torch.Tensor,
        targets: torch.Tensor,
        model_factory,  # Function that returns a new model instance
        trainer_factory,  # Function that returns a new trainer instance
        max_windows: Optional[int] = None
    ) -> dict:
        """
        Run walk-forward validation.

        Args:
            features: [N, seq_len, num_features] feature tensor
            targets: [N] target tensor
            model_factory: Function that creates a new model
            trainer_factory: Function that creates a new trainer
            max_windows: Maximum number of windows to process (for testing)

        Returns:
            results: dict with walk-forward results
        """
        n_samples = len(features)
        window_results = []

        # Calculate number of windows
        max_start = n_samples - self.train_window_size - self.test_window_size
        num_windows = (max_start // self.step_size) + 1

        if max_windows is not None:
            num_windows = min(num_windows, max_windows)

        logger.info(f"Running walk-forward validation: {num_windows} windows")

        for window_idx in range(num_windows):
            start_idx = window_idx * self.step_size
            train_end = start_idx + self.train_window_size
            test_end = train_end + self.test_window_size

            if test_end > n_samples:
                break

            logger.info(f"\nWindow {window_idx + 1}/{num_windows}:")
            logger.info(f"  Train: [{start_idx}:{train_end}]")
            logger.info(f"  Test:  [{train_end}:{test_end}]")

            # Split data
            train_features = features[start_idx:train_end]
            train_targets = targets[start_idx:train_end]
            test_features = features[train_end:test_end]
            test_targets = targets[train_end:test_end]

            # Create model and trainer
            model = model_factory()
            trainer = trainer_factory(model, train_features, train_targets)

            # Train
            trainer.train()

            # Test
            model.eval()
            with torch.no_grad():
                test_predictions = model(test_features)

            # Compute metrics
            metrics = compute_all_metrics(
                predictions=test_predictions,
                true_returns=test_targets
            )

            window_results.append({
                'window_idx': window_idx,
                'train_start': start_idx,
                'train_end': train_end,
                'test_start': train_end,
                'test_end': test_end,
                **metrics
            })

            logger.info(f"  Test Sharpe: {metrics['sharpe_ratio']:.4f}")
            logger.info(f"  Test Return: {metrics['total_return_pct']:.2f}%")

        # Aggregate results
        avg_sharpe = sum(w['sharpe_ratio'] for w in window_results) / len(window_results)
        avg_return = sum(w['total_return_pct'] for w in window_results) / len(window_results)
        avg_max_dd = sum(w['max_drawdown_pct'] for w in window_results) / len(window_results)

        logger.info("\n" + "=" * 60)
        logger.info("WALK-FORWARD VALIDATION RESULTS")
        logger.info("=" * 60)
        logger.info(f"Number of windows: {len(window_results)}")
        logger.info(f"Average Sharpe:    {avg_sharpe:.4f}")
        logger.info(f"Average Return:    {avg_return:.2f}%")
        logger.info(f"Average Max DD:    {avg_max_dd:.2f}%")
        logger.info("=" * 60)

        results = {
            'window_results': window_results,
            'avg_sharpe': avg_sharpe,
            'avg_return': avg_return,
            'avg_max_dd': avg_max_dd,
            'num_windows': len(window_results)
        }

        return results


def compare_models(
    models: dict[str, nn.Module],
    test_loader: DataLoader,
    trading_fee: float = 0.001,
    device: str = 'cpu'
) -> dict:
    """
    Compare multiple models on same test set.

    Args:
        models: Dict of {model_name: model}
        test_loader: Test data loader
        trading_fee: Transaction cost
        device: Device to run on

    Returns:
        comparison: dict with metrics for each model
    """
    logger.info(f"Comparing {len(models)} models...")

    comparison = {}

    for model_name, model in models.items():
        logger.info(f"\nEvaluating: {model_name}")

        backtester = TRMBacktester(
            model=model,
            test_loader=test_loader,
            trading_fee=trading_fee,
            device=device
        )

        metrics = backtester.run_backtest(verbose=False)
        comparison[model_name] = metrics

        logger.info(f"  Sharpe: {metrics['sharpe_ratio']:.4f}")
        logger.info(f"  Return: {metrics['total_return_pct']:.2f}%")
        logger.info(f"  Max DD: {metrics['max_drawdown_pct']:.2f}%")

    # Print comparison table
    print("\n" + "=" * 80)
    print("MODEL COMPARISON")
    print("=" * 80)
    print(f"{'Model':<20} {'Sharpe':>10} {'Return %':>10} {'Max DD %':>10} {'Win Rate %':>12}")
    print("-" * 80)

    for model_name, metrics in comparison.items():
        print(
            f"{model_name:<20} "
            f"{metrics['sharpe_ratio']:>10.4f} "
            f"{metrics['total_return_pct']:>10.2f} "
            f"{metrics['max_drawdown_pct']:>10.2f} "
            f"{metrics['win_rate_pct']:>12.2f}"
        )

    print("=" * 80 + "\n")

    return comparison


if __name__ == "__main__":
    # Test backtester
    logging.basicConfig(level=logging.INFO)

    from torch.utils.data import TensorDataset

    # Create synthetic test data
    n_test = 5000
    seq_len = 60
    num_features = 10
    batch_size = 128

    X_test = torch.randn(n_test, seq_len, num_features)

    # Create somewhat predictive returns
    true_returns = torch.randn(n_test) * 0.001
    noise = torch.randn(n_test) * 0.0008
    predictions_synthetic = true_returns * 0.6 + noise

    test_dataset = TensorDataset(X_test, true_returns)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    # Create dummy model that returns synthetic predictions
    class DummyModel(nn.Module):
        def __init__(self, predictions):
            super().__init__()
            self.predictions = predictions
            self.idx = 0

        def forward(self, x):
            batch_size = x.shape[0]
            result = self.predictions[self.idx:self.idx + batch_size]
            self.idx += batch_size
            if self.idx >= len(self.predictions):
                self.idx = 0
            return result

    model = DummyModel(predictions_synthetic)

    # Run backtest
    backtester = TRMBacktester(
        model=model,
        test_loader=test_loader,
        device='cpu'
    )

    metrics = backtester.run_backtest(verbose=True)

    print("Backtest test passed!")
