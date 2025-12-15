"""
Robustness tests for TRM.

Five critical tests to validate model generalization:
1. Timeframe change (1min → 5min → 15min)
2. Noise injection (price perturbations)
3. Data reduction (train on subset)
4. Asset transfer (BTC → ETH, etc.)
5. Crisis periods (high volatility)
"""
import logging
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from ..evaluation.metrics import compute_all_metrics, print_metrics_report

logger = logging.getLogger(__name__)


class RobustnessTest:
    """
    Base class for robustness tests.
    """

    def __init__(self, name: str):
        self.name = name

    def run(self, *args, **kwargs) -> dict:
        """
        Run the test.

        Returns:
            results: dict with test results
        """
        raise NotImplementedError


class TimeframeChangeTest(RobustnessTest):
    """
    Test 1: Model performance across different timeframes.

    Train on 1-minute bars, test on aggregated 5-min and 15-min bars.
    A robust model should degrade gracefully, not collapse.
    """

    def __init__(self):
        super().__init__("Timeframe Change Test")

    def aggregate_bars(
        self,
        features: torch.Tensor,
        targets: torch.Tensor,
        aggregation_factor: int
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Aggregate bars by taking every Nth sample.

        Args:
            features: [N, seq_len, num_features]
            targets: [N]
            aggregation_factor: Aggregation factor (e.g., 5 for 5-min)

        Returns:
            aggregated_features, aggregated_targets
        """
        # Simple downsampling (take every Nth sample)
        indices = torch.arange(0, len(features), aggregation_factor)
        agg_features = features[indices]
        agg_targets = targets[indices]

        return agg_features, agg_targets

    def run(
        self,
        model: nn.Module,
        test_features: torch.Tensor,
        test_targets: torch.Tensor,
        device: str = 'cpu'
    ) -> dict:
        """
        Run timeframe change test.

        Args:
            model: Trained model
            test_features: Test features [N, seq_len, num_features]
            test_targets: Test targets [N]
            device: Device

        Returns:
            results: dict with metrics for each timeframe
        """
        logger.info(f"\nRunning {self.name}...")

        model.eval()
        results = {}

        # Test on original timeframe (1-min)
        with torch.no_grad():
            X = test_features.to(device)
            y = test_targets.to(device)
            pred = model(X).cpu()

        metrics_1m = compute_all_metrics(pred, y.cpu())
        results['1min'] = metrics_1m
        logger.info(f"1-min bars: Sharpe={metrics_1m['sharpe_ratio']:.4f}")

        # Test on 5-min aggregation
        X_5m, y_5m = self.aggregate_bars(test_features, test_targets, aggregation_factor=5)
        with torch.no_grad():
            pred_5m = model(X_5m.to(device)).cpu()

        metrics_5m = compute_all_metrics(pred_5m, y_5m)
        results['5min'] = metrics_5m
        logger.info(f"5-min bars: Sharpe={metrics_5m['sharpe_ratio']:.4f}")

        # Test on 15-min aggregation
        X_15m, y_15m = self.aggregate_bars(test_features, test_targets, aggregation_factor=15)
        with torch.no_grad():
            pred_15m = model(X_15m.to(device)).cpu()

        metrics_15m = compute_all_metrics(pred_15m, y_15m)
        results['15min'] = metrics_15m
        logger.info(f"15-min bars: Sharpe={metrics_15m['sharpe_ratio']:.4f}")

        # Compute degradation
        sharpe_degradation_5m = (metrics_5m['sharpe_ratio'] - metrics_1m['sharpe_ratio']) / abs(metrics_1m['sharpe_ratio'] + 1e-8)
        sharpe_degradation_15m = (metrics_15m['sharpe_ratio'] - metrics_1m['sharpe_ratio']) / abs(metrics_1m['sharpe_ratio'] + 1e-8)

        results['sharpe_degradation_5m'] = sharpe_degradation_5m
        results['sharpe_degradation_15m'] = sharpe_degradation_15m

        logger.info(f"Sharpe degradation (5min): {sharpe_degradation_5m:.2%}")
        logger.info(f"Sharpe degradation (15min): {sharpe_degradation_15m:.2%}")

        # Pass criterion: degradation < 50%
        passed = abs(sharpe_degradation_5m) < 0.5 and abs(sharpe_degradation_15m) < 0.8
        results['passed'] = passed

        return results


class NoiseInjectionTest(RobustnessTest):
    """
    Test 2: Robustness to price noise.

    Add Gaussian noise to prices and check if model remains stable.
    """

    def __init__(self, noise_levels: list[float] = [0.0005, 0.001, 0.002]):
        super().__init__("Noise Injection Test")
        self.noise_levels = noise_levels

    def add_noise_to_features(
        self,
        features: torch.Tensor,
        noise_std: float
    ) -> torch.Tensor:
        """
        Add Gaussian noise to features.

        Args:
            features: [N, seq_len, num_features]
            noise_std: Standard deviation of noise

        Returns:
            noisy_features: [N, seq_len, num_features]
        """
        noise = torch.randn_like(features) * noise_std
        return features + noise

    def run(
        self,
        model: nn.Module,
        test_features: torch.Tensor,
        test_targets: torch.Tensor,
        device: str = 'cpu'
    ) -> dict:
        """
        Run noise injection test.

        Args:
            model: Trained model
            test_features: Test features
            test_targets: Test targets
            device: Device

        Returns:
            results: dict with metrics for each noise level
        """
        logger.info(f"\nRunning {self.name}...")

        model.eval()
        results = {}

        # Baseline (no noise)
        with torch.no_grad():
            pred_clean = model(test_features.to(device)).cpu()

        metrics_clean = compute_all_metrics(pred_clean, test_targets)
        results['noise_0.0'] = metrics_clean
        logger.info(f"Clean data: Sharpe={metrics_clean['sharpe_ratio']:.4f}")

        baseline_sharpe = metrics_clean['sharpe_ratio']

        # Test with different noise levels
        for noise_std in self.noise_levels:
            noisy_features = self.add_noise_to_features(test_features, noise_std)

            with torch.no_grad():
                pred_noisy = model(noisy_features.to(device)).cpu()

            metrics_noisy = compute_all_metrics(pred_noisy, test_targets)
            results[f'noise_{noise_std}'] = metrics_noisy

            degradation = (metrics_noisy['sharpe_ratio'] - baseline_sharpe) / abs(baseline_sharpe + 1e-8)
            logger.info(
                f"Noise std={noise_std}: Sharpe={metrics_noisy['sharpe_ratio']:.4f} "
                f"(degradation: {degradation:.2%})"
            )

        # Pass criterion: at 0.1% noise, Sharpe degrades < 30%
        if 0.001 in self.noise_levels:
            key = 'noise_0.001'
            degradation = (results[key]['sharpe_ratio'] - baseline_sharpe) / abs(baseline_sharpe + 1e-8)
            results['passed'] = abs(degradation) < 0.3
        else:
            results['passed'] = True

        return results


class DataReductionTest(RobustnessTest):
    """
    Test 3: Performance with reduced training data.

    A tiny model should not be data-hungry.
    Train on 10%, 25%, 50% of data and compare.
    """

    def __init__(self, data_fractions: list[float] = [0.1, 0.25, 0.5, 1.0]):
        super().__init__("Data Reduction Test")
        self.data_fractions = data_fractions

    def run(
        self,
        model_factory,  # Function that returns a new model
        train_features: torch.Tensor,
        train_targets: torch.Tensor,
        test_features: torch.Tensor,
        test_targets: torch.Tensor,
        trainer_factory,  # Function that returns a trainer
        device: str = 'cpu'
    ) -> dict:
        """
        Run data reduction test.

        Args:
            model_factory: Function that creates a new model
            train_features: Training features
            train_targets: Training targets
            test_features: Test features
            test_targets: Test targets
            trainer_factory: Function that creates a trainer
            device: Device

        Returns:
            results: dict with metrics for each data fraction
        """
        logger.info(f"\nRunning {self.name}...")

        results = {}

        for fraction in self.data_fractions:
            logger.info(f"\nTraining on {fraction:.0%} of data...")

            # Subsample training data
            n_train = int(len(train_features) * fraction)
            indices = torch.randperm(len(train_features))[:n_train]

            train_features_sub = train_features[indices]
            train_targets_sub = train_targets[indices]

            # Create new model
            model = model_factory()

            # Create trainer and train
            trainer = trainer_factory(model, train_features_sub, train_targets_sub)
            trainer.train()

            # Evaluate on test set
            model.eval()
            with torch.no_grad():
                pred = model(test_features.to(device)).cpu()

            metrics = compute_all_metrics(pred, test_targets)
            results[f'data_{fraction:.2f}'] = metrics

            logger.info(
                f"Data fraction {fraction:.0%}: Sharpe={metrics['sharpe_ratio']:.4f}, "
                f"Return={metrics['total_return_pct']:.2f}%"
            )

        # Pass criterion: 50% data achieves >80% of full data performance
        if 'data_0.50' in results and 'data_1.00' in results:
            sharpe_50 = results['data_0.50']['sharpe_ratio']
            sharpe_100 = results['data_1.00']['sharpe_ratio']
            ratio = sharpe_50 / (sharpe_100 + 1e-8)
            results['passed'] = ratio > 0.8
        else:
            results['passed'] = True

        return results


class AssetTransferTest(RobustnessTest):
    """
    Test 4: Transfer to different assets.

    Train on BTC, test on ETH, BNB, etc.
    Good model should capture general market dynamics.
    """

    def __init__(self):
        super().__init__("Asset Transfer Test")

    def run(
        self,
        model: nn.Module,
        test_loaders: dict[str, DataLoader],
        device: str = 'cpu'
    ) -> dict:
        """
        Run asset transfer test.

        Args:
            model: Model trained on one asset (e.g., BTC)
            test_loaders: Dict of {asset_name: test_loader}
            device: Device

        Returns:
            results: dict with metrics for each asset
        """
        logger.info(f"\nRunning {self.name}...")

        model.eval()
        results = {}

        for asset_name, test_loader in test_loaders.items():
            logger.info(f"\nTesting on {asset_name}...")

            all_preds = []
            all_targets = []

            with torch.no_grad():
                for X, y in test_loader:
                    X, y = X.to(device), y.to(device)
                    pred = model(X).cpu()
                    all_preds.append(pred)
                    all_targets.append(y.cpu())

            preds = torch.cat(all_preds)
            targets = torch.cat(all_targets)

            metrics = compute_all_metrics(preds, targets)
            results[asset_name] = metrics

            logger.info(
                f"{asset_name}: Sharpe={metrics['sharpe_ratio']:.4f}, "
                f"Return={metrics['total_return_pct']:.2f}%"
            )

        # Pass criterion: at least 2/3 of assets have positive Sharpe
        positive_sharpe_count = sum(1 for m in results.values() if m['sharpe_ratio'] > 0)
        results['passed'] = positive_sharpe_count >= len(results) * 0.67

        return results


class CrisisPeriodTest(RobustnessTest):
    """
    Test 5: Performance during crisis periods.

    Isolate high-volatility periods and measure max drawdown.
    Model should survive (not explode), even if underperforming.
    """

    def __init__(self, volatility_threshold: float = 0.005):
        super().__init__("Crisis Period Test")
        self.volatility_threshold = volatility_threshold

    def identify_crisis_periods(
        self,
        returns: torch.Tensor,
        window: int = 60
    ) -> torch.Tensor:
        """
        Identify crisis periods (high volatility).

        Args:
            returns: [N] returns
            window: Rolling window for volatility

        Returns:
            crisis_mask: [N] boolean mask (True = crisis)
        """
        # Compute rolling volatility
        returns_squared = returns ** 2
        volatility = torch.sqrt(
            torch.nn.functional.avg_pool1d(
                returns_squared.unsqueeze(0).unsqueeze(0),
                kernel_size=window,
                stride=1,
                padding=window // 2
            ).squeeze()
        )

        # Crisis = volatility > threshold
        crisis_mask = volatility > self.volatility_threshold

        return crisis_mask

    def run(
        self,
        model: nn.Module,
        test_features: torch.Tensor,
        test_targets: torch.Tensor,
        device: str = 'cpu'
    ) -> dict:
        """
        Run crisis period test.

        Args:
            model: Trained model
            test_features: Test features
            test_targets: Test targets
            device: Device

        Returns:
            results: dict with metrics for normal and crisis periods
        """
        logger.info(f"\nRunning {self.name}...")

        model.eval()

        # Get predictions
        with torch.no_grad():
            predictions = model(test_features.to(device)).cpu()

        # Identify crisis periods
        crisis_mask = self.identify_crisis_periods(test_targets)

        num_crisis = crisis_mask.sum().item()
        num_normal = (~crisis_mask).sum().item()

        logger.info(f"Crisis periods: {num_crisis}/{len(crisis_mask)} ({num_crisis/len(crisis_mask):.1%})")

        results = {}

        # Metrics for normal periods
        if num_normal > 0:
            normal_preds = predictions[~crisis_mask]
            normal_targets = test_targets[~crisis_mask]

            metrics_normal = compute_all_metrics(normal_preds, normal_targets)
            results['normal'] = metrics_normal
            logger.info(f"Normal periods: Sharpe={metrics_normal['sharpe_ratio']:.4f}")

        # Metrics for crisis periods
        if num_crisis > 0:
            crisis_preds = predictions[crisis_mask]
            crisis_targets = test_targets[crisis_mask]

            metrics_crisis = compute_all_metrics(crisis_preds, crisis_targets)
            results['crisis'] = metrics_crisis
            logger.info(
                f"Crisis periods: Sharpe={metrics_crisis['sharpe_ratio']:.4f}, "
                f"Max DD={metrics_crisis['max_drawdown_pct']:.2f}%"
            )

            # Pass criterion: max drawdown < 30% during crisis
            results['passed'] = metrics_crisis['max_drawdown_pct'] < 30.0
        else:
            logger.warning("No crisis periods detected")
            results['passed'] = True

        return results


def run_all_robustness_tests(
    model: nn.Module,
    test_features: torch.Tensor,
    test_targets: torch.Tensor,
    device: str = 'cpu'
) -> dict:
    """
    Run all robustness tests (simplified version without retraining).

    Args:
        model: Trained model
        test_features: Test features
        test_targets: Test targets
        device: Device

    Returns:
        all_results: dict with results from all tests
    """
    logger.info("\n" + "=" * 80)
    logger.info("RUNNING ROBUSTNESS TESTS")
    logger.info("=" * 80)

    all_results = {}

    # Test 1: Timeframe change
    test1 = TimeframeChangeTest()
    results1 = test1.run(model, test_features, test_targets, device)
    all_results['timeframe_change'] = results1

    # Test 2: Noise injection
    test2 = NoiseInjectionTest()
    results2 = test2.run(model, test_features, test_targets, device)
    all_results['noise_injection'] = results2

    # Test 5: Crisis periods
    test5 = CrisisPeriodTest()
    results5 = test5.run(model, test_features, test_targets, device)
    all_results['crisis_periods'] = results5

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("ROBUSTNESS TESTS SUMMARY")
    logger.info("=" * 80)

    tests_passed = sum(1 for r in all_results.values() if r.get('passed', False))
    tests_total = len(all_results)

    for test_name, results in all_results.items():
        status = "✓ PASS" if results.get('passed', False) else "✗ FAIL"
        logger.info(f"{test_name:<30} {status}")

    logger.info(f"\nTests passed: {tests_passed}/{tests_total}")
    logger.info("=" * 80)

    return all_results


if __name__ == "__main__":
    # Test robustness tests
    logging.basicConfig(level=logging.INFO)

    # Create synthetic data
    n_test = 5000
    seq_len = 60
    num_features = 10

    test_features = torch.randn(n_test, seq_len, num_features)
    test_targets = torch.randn(n_test) * 0.001

    # Create dummy model
    class DummyModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.linear = nn.Linear(num_features, 1)

        def forward(self, x):
            return self.linear(x[:, -1, :]).squeeze(-1)

    model = DummyModel()

    # Run tests
    results = run_all_robustness_tests(model, test_features, test_targets, 'cpu')

    print("\nRobustness tests completed!")
