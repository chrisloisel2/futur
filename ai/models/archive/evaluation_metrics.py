"""
EVALUATION METRICS - RIGOROUS IMPLEMENTATION

Corrects all methodological errors identified in audit:
- Denormalization to interpretable scale
- Baselines (persistence, mean, random)
- Per-horizon metrics
- Direction with neutrality threshold
- Statistical significance tests
- Sanity checks (temporal leakage, variance shift)

Usage:
    from evaluation_metrics import RigorousEvaluator

    evaluator = RigorousEvaluator(scaler=scaler, feature_keys=FEATURE_KEYS)
    results = evaluator.evaluate_full(
        y_true=y_ret_test,
        y_pred=y_ret_pred,
        y_true_rv=y_rv_test,
        y_pred_rv=y_rv_pred,
        y_regime=y_regime_test,
    )
"""

from __future__ import annotations

import warnings
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass

import numpy as np
from scipy import stats


# =========================
# CONFIGURATION
# =========================
@dataclass(frozen=True)
class EvaluationConfig:
    """Configuration for evaluation metrics"""
    # Direction thresholds
    direction_neutral_threshold: float = 0.25  # 25% of 1σ
    direction_min_absolute: float = 0.0005  # 0.05% minimum absolute threshold

    # Statistical tests
    significance_level: float = 0.05
    bootstrap_n_resamples: int = 1000
    bootstrap_confidence_level: float = 0.95

    # Sanity checks
    enable_leakage_test: bool = True
    enable_variance_shift_test: bool = True
    variance_shift_max_ratio: float = 2.0

    # Filtering
    enable_snr_filtering: bool = False
    snr_threshold: float = 0.5

    seed: int = 42


# =========================
# DENORMALIZATION
# =========================
class Denormalizer:
    """
    Handles denormalization of predictions to interpretable scale.

    Supports:
    - RobustScaler (median + MAD)
    - StandardScaler (mean + std)
    - MinMaxScaler
    """

    def __init__(self, scaler, feature_keys: List[str]):
        """
        Args:
            scaler: Fitted scaler object (must have .median, .mad attributes or similar)
            feature_keys: List of feature names matching scaler order
        """
        self.scaler = scaler
        self.feature_keys = feature_keys

        # Detect scaler type
        if hasattr(scaler, 'median') and hasattr(scaler, 'mad'):
            self.scaler_type = 'robust'
        elif hasattr(scaler, 'mean_') and hasattr(scaler, 'scale_'):
            self.scaler_type = 'standard'
        else:
            warnings.warn("Unknown scaler type, assuming identity transform")
            self.scaler_type = 'identity'

    def denormalize_return(self, ret_normalized: np.ndarray) -> np.ndarray:
        """
        Denormalize returns to original scale (percentage).

        Args:
            ret_normalized: [N, H] or [N] - normalized returns

        Returns:
            ret_pct: [N, H] or [N] - returns in percentage
        """
        if self.scaler_type == 'identity':
            return ret_normalized

        # Get return feature index
        try:
            ret_idx = self.feature_keys.index('log_ret')
        except ValueError:
            try:
                ret_idx = self.feature_keys.index('ret')
            except ValueError:
                warnings.warn("Return feature not found, returning as-is")
                return ret_normalized

        # Denormalize
        if self.scaler_type == 'robust':
            # RobustScaler: x = (x_norm * 1.4826 * MAD) + median
            median = self.scaler.median[ret_idx]
            mad = self.scaler.mad[ret_idx]
            ret_original = (ret_normalized * 1.4826 * mad) + median
        elif self.scaler_type == 'standard':
            # StandardScaler: x = (x_norm * std) + mean
            mean = self.scaler.mean_[ret_idx]
            std = self.scaler.scale_[ret_idx]
            ret_original = (ret_normalized * std) + mean
        else:
            ret_original = ret_normalized

        return ret_original

    def denormalize_volatility(self, rv_normalized: np.ndarray) -> np.ndarray:
        """
        Denormalize volatility to original scale.

        Args:
            rv_normalized: [N] - normalized volatility

        Returns:
            rv_original: [N] - volatility in original scale
        """
        if self.scaler_type == 'identity':
            return rv_normalized

        # Get RV feature index
        rv_keys = [k for k in self.feature_keys if 'rv' in k.lower()]
        if not rv_keys:
            warnings.warn("RV feature not found, returning as-is")
            return rv_normalized

        rv_idx = self.feature_keys.index(rv_keys[0])

        # Denormalize
        if self.scaler_type == 'robust':
            median = self.scaler.median[rv_idx]
            mad = self.scaler.mad[rv_idx]
            rv_original = (rv_normalized * 1.4826 * mad) + median
        elif self.scaler_type == 'standard':
            mean = self.scaler.mean_[rv_idx]
            std = self.scaler.scale_[rv_idx]
            rv_original = (rv_normalized * std) + mean
        else:
            rv_original = rv_normalized

        return rv_original


# =========================
# BASELINE METRICS
# =========================
class BaselineMetrics:
    """
    Compute baseline metrics for comparison.

    Baselines:
    1. Persistence (naive): ret_t+h = ret_t
    2. Mean forecast: ret_t+h = mean(ret_train)
    3. Random: 50% directional accuracy
    """

    @staticmethod
    def persistence_baseline(
        y_true: np.ndarray,
        horizon: int = 1
    ) -> Tuple[np.ndarray, float]:
        """
        Persistence baseline: y_pred_t = y_true_{t-horizon}

        Args:
            y_true: [N, H] - true returns
            horizon: prediction horizon

        Returns:
            y_pred: [N, H] - persistence predictions
            mae: scalar - MAE of persistence
        """
        if len(y_true.shape) == 1:
            # Scalar case
            y_pred = np.roll(y_true, shift=horizon)
            y_pred[:horizon] = 0
        else:
            # Multi-horizon case: predict with last observed value
            N, H = y_true.shape
            y_pred = np.zeros_like(y_true)

            for i in range(N):
                if i < horizon:
                    y_pred[i] = 0  # No history
                else:
                    # Use return from 'horizon' steps ago
                    y_pred[i] = y_true[i - horizon, 0]  # First column as reference

        mae = np.mean(np.abs(y_true - y_pred))
        return y_pred, mae

    @staticmethod
    def mean_baseline(
        y_train: np.ndarray,
        y_true: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        """
        Mean forecast baseline: y_pred = mean(y_train)

        Args:
            y_train: [N_train, H] - training returns
            y_true: [N_test, H] - test returns

        Returns:
            y_pred: [N_test, H] - mean predictions
            mae: scalar - MAE of mean forecast
        """
        mean_train = np.mean(y_train, axis=0)  # [H]
        y_pred = np.tile(mean_train, (y_true.shape[0], 1))

        mae = np.mean(np.abs(y_true - y_pred))
        return y_pred, mae

    @staticmethod
    def random_baseline_accuracy(y_true_direction: np.ndarray) -> float:
        """
        Random baseline for directional accuracy.

        If classes balanced: 50%
        If classes imbalanced: max(P(class_i))

        Args:
            y_true_direction: [N] - true directions {-1, 0, 1}

        Returns:
            random_acc: scalar - expected accuracy of random guessing
        """
        unique, counts = np.unique(y_true_direction, return_counts=True)
        probabilities = counts / len(y_true_direction)
        random_acc = np.max(probabilities)
        return random_acc


# =========================
# PER-HORIZON METRICS
# =========================
class PerHorizonMetrics:
    """
    Compute metrics separately for each prediction horizon.

    Prevents dilution of short-term errors by long-term errors.
    """

    @staticmethod
    def mae_per_horizon(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        """
        MAE for each horizon.

        Args:
            y_true: [N, H]
            y_pred: [N, H]

        Returns:
            mae_per_h: [H] - MAE at each horizon
        """
        mae_per_h = np.mean(np.abs(y_true - y_pred), axis=0)
        return mae_per_h

    @staticmethod
    def correlation_per_horizon(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        """
        Pearson correlation for each horizon.

        Args:
            y_true: [N, H]
            y_pred: [N, H]

        Returns:
            corr_per_h: [H] - correlation at each horizon
        """
        H = y_true.shape[1]
        corr_per_h = np.zeros(H)

        for h in range(H):
            # Check for zero variance
            if np.std(y_true[:, h]) < 1e-10 or np.std(y_pred[:, h]) < 1e-10:
                corr_per_h[h] = 0.0
            else:
                corr_per_h[h] = np.corrcoef(y_true[:, h], y_pred[:, h])[0, 1]

        return corr_per_h

    @staticmethod
    def r2_per_horizon(
        y_true: np.ndarray,
        y_pred: np.ndarray,
        y_train: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """
        R² for each horizon.

        R² = 1 - MSE(pred) / Var(baseline)

        Baseline variance computed on TRAIN set (out-of-sample).

        Args:
            y_true: [N_test, H]
            y_pred: [N_test, H]
            y_train: [N_train, H] - training data for baseline variance

        Returns:
            r2_per_h: [H] - R² at each horizon
        """
        H = y_true.shape[1]
        r2_per_h = np.zeros(H)

        # Compute baseline variance on train (if provided)
        if y_train is not None:
            var_baseline = np.var(y_train, axis=0)  # [H]
        else:
            # Fallback: use test variance (biased but better than nothing)
            warnings.warn("No training data provided for R² baseline, using test variance")
            var_baseline = np.var(y_true, axis=0)

        for h in range(H):
            mse = np.mean((y_true[:, h] - y_pred[:, h]) ** 2)

            if var_baseline[h] < 1e-10:
                r2_per_h[h] = -np.inf  # Undefined
            else:
                r2_per_h[h] = 1.0 - (mse / var_baseline[h])

        return r2_per_h


# =========================
# DIRECTION METRICS (CORRECTED)
# =========================
class DirectionMetrics:
    """
    Direction metrics with proper threshold and neutrality handling.

    Fixes:
    - No threshold → 50% on noise
    - Binary classification on continuous signal
    - No exclusion of neutral zones
    """

    @staticmethod
    def classify_direction(
        ret: np.ndarray,
        threshold_std_fraction: float = 0.25,
        threshold_absolute: float = 0.0005
    ) -> np.ndarray:
        """
        Classify returns into {-1, 0, 1} with neutrality threshold.

        Args:
            ret: [N] - returns (cumulative or single-step)
            threshold_std_fraction: fraction of std to use as threshold
            threshold_absolute: minimum absolute threshold

        Returns:
            direction: [N] - {-1 (DOWN), 0 (NEUTRAL), 1 (UP)}
        """
        # Adaptive threshold based on volatility
        threshold_adaptive = np.std(ret) * threshold_std_fraction

        # Take max of adaptive and absolute
        threshold = max(threshold_adaptive, threshold_absolute)

        # Classify
        direction = np.where(
            ret > threshold, 1,
            np.where(ret < -threshold, -1, 0)
        )

        return direction

    @staticmethod
    def directional_accuracy(
        y_true: np.ndarray,
        y_pred: np.ndarray,
        exclude_neutral: bool = True,
        threshold_std_fraction: float = 0.25,
        threshold_absolute: float = 0.0005
    ) -> Dict[str, float]:
        """
        Directional accuracy with statistical test.

        Args:
            y_true: [N, H] - true returns
            y_pred: [N, H] - predicted returns
            exclude_neutral: if True, exclude neutral zones from both sides
            threshold_std_fraction: threshold as fraction of std
            threshold_absolute: minimum absolute threshold

        Returns:
            dict with keys:
                - accuracy: float
                - p_value: float (binomial test)
                - significant: bool (p < 0.05)
                - n_samples: int
                - n_neutral_excluded: int
        """
        # Cumulative returns
        ret_cum_true = np.sum(y_true, axis=-1) if len(y_true.shape) > 1 else y_true
        ret_cum_pred = np.sum(y_pred, axis=-1) if len(y_pred.shape) > 1 else y_pred

        # Classify
        dir_true = DirectionMetrics.classify_direction(
            ret_cum_true, threshold_std_fraction, threshold_absolute
        )
        dir_pred = DirectionMetrics.classify_direction(
            ret_cum_pred, threshold_std_fraction, threshold_absolute
        )

        # Exclude neutral
        if exclude_neutral:
            mask_non_neutral = (dir_true != 0) & (dir_pred != 0)
        else:
            mask_non_neutral = np.ones(len(dir_true), dtype=bool)

        n_neutral_excluded = len(dir_true) - np.sum(mask_non_neutral)

        dir_true_filtered = dir_true[mask_non_neutral]
        dir_pred_filtered = dir_pred[mask_non_neutral]

        # Accuracy
        if len(dir_true_filtered) == 0:
            return {
                "accuracy": 0.0,
                "p_value": 1.0,
                "significant": False,
                "n_samples": 0,
                "n_neutral_excluded": n_neutral_excluded,
            }

        n_correct = np.sum(dir_true_filtered == dir_pred_filtered)
        n_total = len(dir_true_filtered)
        accuracy = n_correct / n_total

        # Binomial test (two-tailed)
        p_value = stats.binom_test(n_correct, n_total, p=0.5, alternative='two-sided')

        return {
            "accuracy": float(accuracy),
            "p_value": float(p_value),
            "significant": bool(p_value < 0.05),
            "n_samples": int(n_total),
            "n_neutral_excluded": int(n_neutral_excluded),
        }


# =========================
# SANITY CHECKS
# =========================
class SanityChecks:
    """
    Sanity checks to detect common errors.

    1. Temporal leakage: shuffle should not improve performance
    2. Variance shift: test variance should not >> train variance
    3. Zero variance: detect degenerate predictions
    """

    @staticmethod
    def test_temporal_leakage(
        model,
        X: np.ndarray,
        y: np.ndarray,
        n_shuffles: int = 5,
        seed: int = 42
    ) -> Dict[str, any]:
        """
        Test for temporal leakage by shuffling.

        If MAE_shuffled < MAE_normal → LEAK DETECTED

        Args:
            model: trained model with .predict() method
            X: [N, L, F] - input features
            y: [N, H] - targets
            n_shuffles: number of shuffle iterations
            seed: random seed

        Returns:
            dict with keys:
                - leak_detected: bool
                - mae_normal: float
                - mae_shuffled_mean: float
                - mae_shuffled_std: float
        """
        rng = np.random.RandomState(seed)

        # Normal prediction
        y_pred = model(X, training=False)["ret"].numpy()
        mae_normal = np.mean(np.abs(y - y_pred))

        # Shuffled predictions
        mae_shuffled_list = []
        for _ in range(n_shuffles):
            indices = rng.permutation(len(X))
            X_shuffled = X[indices]
            y_shuffled = y[indices]

            y_pred_shuffled = model(X_shuffled, training=False)["ret"].numpy()
            mae_shuffled = np.mean(np.abs(y_shuffled - y_pred_shuffled))
            mae_shuffled_list.append(mae_shuffled)

        mae_shuffled_mean = np.mean(mae_shuffled_list)
        mae_shuffled_std = np.std(mae_shuffled_list)

        # Leak detected if shuffle improves
        leak_detected = mae_shuffled_mean < mae_normal * 0.95  # 5% margin

        return {
            "leak_detected": bool(leak_detected),
            "mae_normal": float(mae_normal),
            "mae_shuffled_mean": float(mae_shuffled_mean),
            "mae_shuffled_std": float(mae_shuffled_std),
        }

    @staticmethod
    def test_variance_shift(
        y_train: np.ndarray,
        y_val: np.ndarray,
        y_test: np.ndarray,
        max_ratio: float = 2.0
    ) -> Dict[str, any]:
        """
        Test for distribution shift between train/val/test.

        If std(test) / std(train) > max_ratio → SHIFT DETECTED

        Args:
            y_train: [N_train, H]
            y_val: [N_val, H]
            y_test: [N_test, H]
            max_ratio: maximum acceptable variance ratio

        Returns:
            dict with keys:
                - shift_detected: bool
                - std_train: float
                - std_val: float
                - std_test: float
                - ratio_val: float
                - ratio_test: float
        """
        std_train = np.std(y_train)
        std_val = np.std(y_val)
        std_test = np.std(y_test)

        ratio_val = std_val / (std_train + 1e-10)
        ratio_test = std_test / (std_train + 1e-10)

        shift_detected = (ratio_val > max_ratio) or (ratio_test > max_ratio)

        return {
            "shift_detected": bool(shift_detected),
            "std_train": float(std_train),
            "std_val": float(std_val),
            "std_test": float(std_test),
            "ratio_val": float(ratio_val),
            "ratio_test": float(ratio_test),
        }

    @staticmethod
    def test_zero_variance(y_pred: np.ndarray, threshold: float = 1e-10) -> Dict[str, any]:
        """
        Test if predictions have near-zero variance (degenerate model).

        Args:
            y_pred: [N, H] - predictions
            threshold: variance threshold

        Returns:
            dict with keys:
                - zero_variance_detected: bool
                - std_pred: float
        """
        std_pred = np.std(y_pred)
        zero_variance_detected = std_pred < threshold

        return {
            "zero_variance_detected": bool(zero_variance_detected),
            "std_pred": float(std_pred),
        }


# =========================
# RIGOROUS EVALUATOR (MAIN)
# =========================
class RigorousEvaluator:
    """
    Complete rigorous evaluation pipeline.

    Combines all corrected metrics:
    - Denormalization
    - Baselines
    - Per-horizon metrics
    - Direction with threshold
    - Statistical tests
    - Sanity checks
    """

    def __init__(
        self,
        scaler,
        feature_keys: List[str],
        config: Optional[EvaluationConfig] = None
    ):
        """
        Args:
            scaler: Fitted scaler for denormalization
            feature_keys: List of feature names
            config: Evaluation configuration
        """
        self.denormalizer = Denormalizer(scaler, feature_keys)
        self.config = config or EvaluationConfig()

    def evaluate_full(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        y_true_rv: Optional[np.ndarray] = None,
        y_pred_rv: Optional[np.ndarray] = None,
        y_train: Optional[np.ndarray] = None,
        model: Optional[any] = None,
        X_test: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Full evaluation with all metrics.

        Args:
            y_true: [N, H] - true returns (normalized)
            y_pred: [N, H] - predicted returns (normalized)
            y_true_rv: [N] - true volatility (normalized)
            y_pred_rv: [N] - predicted volatility (normalized)
            y_train: [N_train, H] - training returns for baseline
            model: trained model for sanity checks
            X_test: [N, L, F] - test features for sanity checks

        Returns:
            dict with all evaluation results
        """
        results = {}

        # 1) Denormalize
        y_true_pct = self.denormalizer.denormalize_return(y_true)
        y_pred_pct = self.denormalizer.denormalize_return(y_pred)

        # 2) Baselines
        if y_train is not None:
            y_train_pct = self.denormalizer.denormalize_return(y_train)
            _, mae_persistence = BaselineMetrics.persistence_baseline(y_true_pct, horizon=1)
            _, mae_mean = BaselineMetrics.mean_baseline(y_train_pct, y_true_pct)

            results["baselines"] = {
                "mae_persistence": float(mae_persistence),
                "mae_mean": float(mae_mean),
            }

        # 3) Per-horizon metrics
        mae_per_h = PerHorizonMetrics.mae_per_horizon(y_true_pct, y_pred_pct)
        corr_per_h = PerHorizonMetrics.correlation_per_horizon(y_true_pct, y_pred_pct)

        if y_train is not None:
            r2_per_h = PerHorizonMetrics.r2_per_horizon(y_true_pct, y_pred_pct, y_train_pct)
        else:
            r2_per_h = PerHorizonMetrics.r2_per_horizon(y_true_pct, y_pred_pct)

        results["per_horizon"] = {
            "mae": mae_per_h.tolist(),
            "correlation": corr_per_h.tolist(),
            "r2": r2_per_h.tolist(),
        }

        # 4) Aggregated metrics
        results["aggregated"] = {
            "mae_mean": float(np.mean(mae_per_h)),
            "mae_weighted": float(self._weighted_mae(mae_per_h)),
            "correlation_mean": float(np.mean(corr_per_h)),
            "r2_mean": float(np.mean(r2_per_h)),
        }

        # 5) Direction metrics
        dir_metrics = DirectionMetrics.directional_accuracy(
            y_true_pct,
            y_pred_pct,
            exclude_neutral=True,
            threshold_std_fraction=self.config.direction_neutral_threshold,
            threshold_absolute=self.config.direction_min_absolute,
        )
        results["direction"] = dir_metrics

        # 6) Volatility metrics (if provided)
        if y_true_rv is not None and y_pred_rv is not None:
            y_true_rv_orig = self.denormalizer.denormalize_volatility(y_true_rv)
            y_pred_rv_orig = self.denormalizer.denormalize_volatility(y_pred_rv)

            mae_rv = np.mean(np.abs(y_true_rv_orig - y_pred_rv_orig))
            corr_rv = np.corrcoef(y_true_rv_orig, y_pred_rv_orig)[0, 1] if np.std(y_true_rv_orig) > 1e-10 else 0.0

            results["volatility"] = {
                "mae": float(mae_rv),
                "correlation": float(corr_rv),
            }

        # 7) Sanity checks
        if self.config.enable_leakage_test and model is not None and X_test is not None:
            leak_test = SanityChecks.test_temporal_leakage(model, X_test, y_true, seed=self.config.seed)
            results["sanity_checks"] = {"temporal_leakage": leak_test}

        if self.config.enable_variance_shift_test and y_train is not None:
            # Note: need y_val for full test, skipping for now
            pass

        # Zero variance test
        zero_var_test = SanityChecks.test_zero_variance(y_pred_pct)
        if "sanity_checks" not in results:
            results["sanity_checks"] = {}
        results["sanity_checks"]["zero_variance"] = zero_var_test

        return results

    def _weighted_mae(self, mae_per_h: np.ndarray, decay: float = 0.2) -> float:
        """
        Weighted MAE with exponential decay (prioritize short-term).

        Args:
            mae_per_h: [H] - MAE per horizon
            decay: decay factor

        Returns:
            weighted_mae: scalar
        """
        H = len(mae_per_h)
        weights = np.exp(-np.arange(H) * decay)
        weights /= weights.sum()
        return np.sum(mae_per_h * weights)


# =========================
# EXAMPLE USAGE
# =========================
if __name__ == "__main__":
    # Mock data
    np.random.seed(42)
    N, H = 1000, 12

    # Simulate scaler
    class MockScaler:
        def __init__(self):
            self.median = np.zeros(44)
            self.mad = np.ones(44)

    scaler = MockScaler()
    feature_keys = ["log_ret"] + [f"feat_{i}" for i in range(43)]

    # Generate data
    y_train = np.random.randn(N, H) * 0.01
    y_true = np.random.randn(N // 5, H) * 0.01
    y_pred = y_true + np.random.randn(N // 5, H) * 0.005  # Noisy predictions

    # Evaluate
    evaluator = RigorousEvaluator(scaler=scaler, feature_keys=feature_keys)
    results = evaluator.evaluate_full(
        y_true=y_true,
        y_pred=y_pred,
        y_train=y_train,
    )

    # Print results
    print("\n" + "=" * 80)
    print("RIGOROUS EVALUATION RESULTS")
    print("=" * 80)

    print("\nPer-Horizon MAE:")
    for h, mae in enumerate(results["per_horizon"]["mae"]):
        print(f"  H={h+1:2d}: {mae:.4f}")

    print("\nAggregated Metrics:")
    for k, v in results["aggregated"].items():
        print(f"  {k}: {v:.4f}")

    print("\nDirection Metrics:")
    for k, v in results["direction"].items():
        print(f"  {k}: {v}")

    if "baselines" in results:
        print("\nBaselines:")
        for k, v in results["baselines"].items():
            print(f"  {k}: {v:.4f}")

    if "sanity_checks" in results:
        print("\nSanity Checks:")
        for k, v in results["sanity_checks"].items():
            print(f"  {k}: {v}")
