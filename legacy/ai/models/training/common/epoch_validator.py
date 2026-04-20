"""
Per-Epoch Validation Wrapper with Production Gates
===================================================

Implements MANDATORY per-epoch validation with:
- Complete metric logging (train/test)
- Paper tests (raw + filtered) - OBLIGATOIRE
- Hard gate validation
- Automatic stopping when gates fail

Usage:
    validator = EpochValidator(
        gates=EdgeForecasterGates(min_sharpe=0.5),
        logger=structured_logger,
        artifact_manager=manager
    )

    should_stop, reason = validator.validate_epoch(
        epoch=epoch,
        model=model,
        train_metrics={"loss": 0.234, "grad_norm": 1.2},
        val_data=(X_val, y_val, returns_val),
        test_data=(X_test, y_test, returns_test),
        get_predictions=lambda X: model.predict(X)
    )
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Tuple, Callable, Optional
import numpy as np
import time

from .ml_instrumentation import (
    StructuredLogger,
    ArtifactManager,
    PaperTradingConfig,
    run_paper_test,
    expected_calibration_error,
    brier_score,
    prediction_entropy
)
from .production_gates import (
    RegimeClassifierGates,
    EdgeForecasterGates,
    check_quantile_monotonicity
)


@dataclass
class ValidationConfig:
    """Configuration for per-epoch validation."""
    # Paper test configs
    raw_paper_test: PaperTradingConfig
    filtered_paper_test: Optional[PaperTradingConfig] = None

    # Validation behavior
    save_equity_curves: bool = True
    save_predictions: bool = False  # Can be large
    compute_calibration: bool = True

    # Early stopping
    warmup_epochs: int = 3
    patience: int = 10  # Epochs without improvement

    # Task type
    task_type: str = "regression"  # "regression" or "classification"


class EpochValidator:
    """
    Comprehensive per-epoch validator with production gates.

    This class implements the MANDATORY validation requested:
    - Per-epoch logging (train + test)
    - Paper Test #1 (raw signal)
    - Paper Test #2 (filtered by probability/threshold)
    - Hard gate validation
    - Automatic stopping
    """

    def __init__(
        self,
        config: ValidationConfig,
        gates: Any,  # RegimeClassifierGates or EdgeForecasterGates
        logger: StructuredLogger,
        artifact_manager: ArtifactManager
    ):
        self.config = config
        self.gates = gates
        self.logger = logger
        self.artifact_manager = artifact_manager

        self.best_metric = -np.inf
        self.epochs_without_improvement = 0
        self.prev_predictions = None

    def validate_epoch(
        self,
        epoch: int,
        model: Any,
        train_metrics: Dict[str, float],
        val_data: Tuple[np.ndarray, np.ndarray, np.ndarray],
        test_data: Tuple[np.ndarray, np.ndarray, np.ndarray],
        get_predictions: Callable[[np.ndarray], np.ndarray],
        get_probabilities: Optional[Callable[[np.ndarray], np.ndarray]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a single epoch with complete instrumentation.

        Args:
            epoch: Current epoch number
            model: The model being trained
            train_metrics: Dict with "loss", "grad_norm", "lr"
            val_data: (X_val, y_val, returns_val)
            test_data: (X_test, y_test, returns_test)
            get_predictions: Function that takes X and returns predictions
            get_probabilities: Optional function for classification probabilities

        Returns:
            (should_stop, reason)
        """
        epoch_start = time.time()

        X_val, y_val, returns_val = val_data
        X_test, y_test, returns_test = test_data

        # ====================================================================
        # 1. GET PREDICTIONS
        # ====================================================================
        val_pred = get_predictions(X_val)
        test_pred = get_predictions(X_test)

        val_prob = get_probabilities(X_val) if get_probabilities else None
        test_prob = get_probabilities(X_test) if get_probabilities else None

        # ====================================================================
        # 2. BASIC METRICS
        # ====================================================================
        metrics = {
            "epoch": epoch,
            "train_loss": train_metrics.get("loss", 0.0),
            "gradient_norm": train_metrics.get("grad_norm", 0.0),
            "learning_rate": train_metrics.get("lr", 0.0),
        }

        # Validation metrics
        if self.config.task_type == "regression":
            val_mae = float(np.abs(val_pred - y_val).mean())
            test_mae = float(np.abs(test_pred - y_test).mean())
            metrics["val_mae"] = val_mae
            metrics["test_mae"] = test_mae
        else:  # classification
            val_acc = float((val_pred.argmax(axis=1) == y_val).mean())
            test_acc = float((test_pred.argmax(axis=1) == y_test).mean())
            metrics["val_accuracy"] = val_acc
            metrics["test_accuracy"] = test_acc

        # ====================================================================
        # 3. CALIBRATION METRICS (if classification)
        # ====================================================================
        if self.config.compute_calibration and val_prob is not None:
            val_ece = expected_calibration_error(y_val, val_prob)
            val_brier = brier_score(y_val, val_prob)
            val_entropy = prediction_entropy(val_prob)

            test_ece = expected_calibration_error(y_test, test_prob)
            test_brier = brier_score(y_test, test_prob)
            test_entropy = prediction_entropy(test_prob)

            metrics.update({
                "val_ece": float(val_ece),
                "val_brier": float(val_brier),
                "val_entropy": float(val_entropy),
                "test_ece": float(test_ece),
                "test_brier": float(test_brier),
                "test_entropy": float(test_entropy),
            })

        # ====================================================================
        # 4. STABILITY METRICS
        # ====================================================================
        if self.prev_predictions is not None:
            pred_corr = float(np.corrcoef(self.prev_predictions, test_pred)[0, 1])

            if self.config.task_type == "regression":
                sign_prev = np.sign(self.prev_predictions)
                sign_curr = np.sign(test_pred)
                flip_rate = float((sign_prev != sign_curr).mean())
            else:
                class_prev = self.prev_predictions.argmax(axis=1)
                class_curr = test_pred.argmax(axis=1)
                flip_rate = float((class_prev != class_curr).mean())

            metrics.update({
                "pred_correlation": pred_corr,
                "flip_rate": flip_rate
            })

        self.prev_predictions = test_pred.copy()

        # ====================================================================
        # 5. PAPER TEST #1: RAW SIGNAL (MANDATORY)
        # ====================================================================
        if self.config.task_type == "regression":
            # For regression, predictions are signals
            test_signals = test_pred
        else:
            # For classification, convert to directional signals
            # Assuming binary or multi-class with class 0 = negative, class 1+ = positive
            test_signals = (test_pred.argmax(axis=1) - 1.0).astype(float)

        paper_test_raw = run_paper_test(
            signals=test_signals,
            returns=returns_test,
            config=self.config.raw_paper_test,
            name="raw"
        )

        metrics.update({
            "paper_raw_roi": paper_test_raw.roi,
            "paper_raw_sharpe": paper_test_raw.sharpe,
            "paper_raw_max_dd": paper_test_raw.max_drawdown,
            "paper_raw_hit_rate": paper_test_raw.hit_rate,
            "paper_raw_num_trades": paper_test_raw.num_trades,
        })

        # Save equity curve
        if self.config.save_equity_curves:
            self.artifact_manager.save_equity_curve(
                epoch, paper_test_raw.equity_curve, "raw"
            )

        # ====================================================================
        # 6. PAPER TEST #2: FILTERED (if applicable)
        # ====================================================================
        if self.config.filtered_paper_test and test_prob is not None:
            # Filter by confidence
            max_prob = test_prob.max(axis=1)
            confidence_threshold = 0.6  # Only trade when confident

            filtered_signals = test_signals.copy()
            filtered_signals[max_prob < confidence_threshold] = 0.0

            paper_test_filtered = run_paper_test(
                signals=filtered_signals,
                returns=returns_test,
                config=self.config.filtered_paper_test,
                name="filtered"
            )

            metrics.update({
                "paper_filtered_roi": paper_test_filtered.roi,
                "paper_filtered_sharpe": paper_test_filtered.sharpe,
                "paper_filtered_max_dd": paper_test_filtered.max_drawdown,
                "paper_filtered_hit_rate": paper_test_filtered.hit_rate,
                "paper_filtered_num_trades": paper_test_filtered.num_trades,
                "paper_filtered_coverage": float((max_prob >= confidence_threshold).mean()),
            })

            if self.config.save_equity_curves:
                self.artifact_manager.save_equity_curve(
                    epoch, paper_test_filtered.equity_curve, "filtered"
                )

        # ====================================================================
        # 7. QUANTILE MONOTONICITY (for forecasters with quantiles)
        # ====================================================================
        if hasattr(model, 'predict_quantiles'):
            # Assume predict_quantiles returns (q05, q50, q95)
            q05, q50, q95 = model.predict_quantiles(X_test)
            mono_rate = check_quantile_monotonicity(q05, q50, q95)
            metrics["monotonicity_rate"] = float(mono_rate)

        # ====================================================================
        # 8. SAVE METRICS
        # ====================================================================
        metrics["time_validation_s"] = time.time() - epoch_start

        self.artifact_manager.save_epoch_metrics(epoch, metrics)
        self.logger.log("epoch_validation", metrics, epoch=epoch)

        # ====================================================================
        # 9. PRODUCTION GATE VALIDATION
        # ====================================================================
        passed, reason = self.gates.validate(
            metrics=metrics,
            epoch=epoch,
            warmup_epochs=self.config.warmup_epochs
        )

        if not passed:
            self.logger.log("gate_failure", {
                "reason": reason,
                "epoch": epoch,
                "metrics": metrics
            }, epoch=epoch)
            return True, f"GATE FAILURE: {reason}"

        # ====================================================================
        # 10. EARLY STOPPING (patience-based)
        # ====================================================================
        # Use Sharpe as primary metric
        current_metric = metrics.get("paper_raw_sharpe", -np.inf)

        if current_metric > self.best_metric:
            self.best_metric = current_metric
            self.epochs_without_improvement = 0

            self.logger.log("new_best", {
                "metric": "sharpe",
                "value": current_metric,
                "epoch": epoch
            }, epoch=epoch)

        else:
            self.epochs_without_improvement += 1

            if self.epochs_without_improvement >= self.config.patience:
                return True, f"Early stopping: {self.config.patience} epochs without improvement"

        # ====================================================================
        # 11. PRINT SUMMARY
        # ====================================================================
        self._print_epoch_summary(epoch, metrics)

        return False, None

    def _print_epoch_summary(self, epoch: int, metrics: Dict[str, Any]):
        """Print human-readable epoch summary."""
        print(f"\n{'='*80}")
        print(f"EPOCH {epoch} VALIDATION SUMMARY")
        print(f"{'='*80}")

        print(f"\n📊 Training:")
        print(f"  Loss:      {metrics.get('train_loss', 0):.6f}")
        print(f"  Grad Norm: {metrics.get('gradient_norm', 0):.4f}")

        if self.config.task_type == "regression":
            print(f"\n📈 Validation:")
            print(f"  MAE (val):  {metrics.get('val_mae', 0):.6f}")
            print(f"  MAE (test): {metrics.get('test_mae', 0):.6f}")
        else:
            print(f"\n📈 Validation:")
            print(f"  Accuracy (val):  {metrics.get('val_accuracy', 0):.4f}")
            print(f"  Accuracy (test): {metrics.get('test_accuracy', 0):.4f}")

            if "val_ece" in metrics:
                print(f"\n🎯 Calibration:")
                print(f"  ECE (test):   {metrics.get('test_ece', 0):.4f}")
                print(f"  Brier (test): {metrics.get('test_brier', 0):.4f}")

        print(f"\n💰 Paper Test (Raw):")
        print(f"  ROI:       {metrics.get('paper_raw_roi', 0):>8.2%}")
        print(f"  Sharpe:    {metrics.get('paper_raw_sharpe', 0):>8.2f}")
        print(f"  Max DD:    {metrics.get('paper_raw_max_dd', 0):>8.2%}")
        print(f"  Hit Rate:  {metrics.get('paper_raw_hit_rate', 0):>8.2%}")
        print(f"  Trades:    {metrics.get('paper_raw_num_trades', 0):>8.0f}")

        if "paper_filtered_roi" in metrics:
            print(f"\n💎 Paper Test (Filtered):")
            print(f"  ROI:       {metrics.get('paper_filtered_roi', 0):>8.2%}")
            print(f"  Sharpe:    {metrics.get('paper_filtered_sharpe', 0):>8.2f}")
            print(f"  Coverage:  {metrics.get('paper_filtered_coverage', 0):>8.2%}")

        if "flip_rate" in metrics:
            print(f"\n🔄 Stability:")
            print(f"  Flip Rate: {metrics.get('flip_rate', 0):>8.2%}")
            print(f"  Pred Corr: {metrics.get('pred_correlation', 0):>8.4f}")

        if "monotonicity_rate" in metrics:
            mono = metrics.get('monotonicity_rate', 0)
            status = "✅" if mono >= 0.99 else "❌"
            print(f"\n📐 Quantiles:")
            print(f"  {status} Monotonicity: {mono:.4f}")

        print(f"\n{'='*80}\n")


def create_regression_validator(
    logger: StructuredLogger,
    artifact_manager: ArtifactManager,
    min_sharpe: float = 0.5,
    max_drawdown: float = -0.20,
    warmup_epochs: int = 3
) -> EpochValidator:
    """
    Create validator for regression tasks (e.g., EdgeForecaster).
    """
    config = ValidationConfig(
        raw_paper_test=PaperTradingConfig(
            fee_rate=0.001,
            spread_bps=5.0,
            latency_bars=1,
            position_size=1.0
        ),
        filtered_paper_test=None,  # No filtering for pure regression
        save_equity_curves=True,
        compute_calibration=False,
        warmup_epochs=warmup_epochs,
        task_type="regression"
    )

    gates = EdgeForecasterGates(
        min_sharpe=min_sharpe,
        max_drawdown=max_drawdown,
        min_roi=-0.05,
        min_hit_rate=0.51,
        min_monotonicity_rate=0.99
    )

    return EpochValidator(config, gates, logger, artifact_manager)


def create_classification_validator(
    logger: StructuredLogger,
    artifact_manager: ArtifactManager,
    min_macro_f1: float = 0.35,
    max_brier: float = 0.20,
    warmup_epochs: int = 3
) -> EpochValidator:
    """
    Create validator for classification tasks (e.g., RegimeClassifier).
    """
    config = ValidationConfig(
        raw_paper_test=PaperTradingConfig(
            fee_rate=0.001,
            spread_bps=5.0,
            latency_bars=1,
            position_size=1.0
        ),
        filtered_paper_test=PaperTradingConfig(
            fee_rate=0.0005,  # Lower fees for filtered
            spread_bps=3.0,
            latency_bars=1,
            position_size=1.0
        ),
        save_equity_curves=True,
        compute_calibration=True,
        warmup_epochs=warmup_epochs,
        task_type="classification"
    )

    gates = RegimeClassifierGates(
        min_macro_f1=min_macro_f1,
        max_brier=max_brier,
        min_impulse_recall=0.35,
        min_reversal_recall=0.35,
        min_calm_recall=0.30,
        max_ece=0.08
    )

    return EpochValidator(config, gates, logger, artifact_manager)


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Example: Integrating EpochValidator into a training loop.
    """
    from .ml_instrumentation import StructuredLogger, ArtifactManager
    import numpy as np

    # Setup
    logger = StructuredLogger("logs.jsonl", "20251222-020000", "EdgeForecaster")
    artifact_manager = ArtifactManager("artifacts", "20251222-020000")

    validator = create_regression_validator(
        logger=logger,
        artifact_manager=artifact_manager,
        min_sharpe=0.5
    )

    # Mock data
    X_val = np.random.randn(1000, 48)
    y_val = np.random.randn(1000)
    returns_val = np.random.randn(1000) * 0.01

    X_test = np.random.randn(1000, 48)
    y_test = np.random.randn(1000)
    returns_test = np.random.randn(1000) * 0.01

    # Mock model
    class MockModel:
        def predict(self, X):
            return np.random.randn(len(X))

    model = MockModel()

    # Training loop
    for epoch in range(20):
        # ... training step ...
        train_metrics = {
            "loss": 0.234 - epoch * 0.01,
            "grad_norm": 1.2,
            "lr": 3e-4
        }

        # Validate
        should_stop, reason = validator.validate_epoch(
            epoch=epoch,
            model=model,
            train_metrics=train_metrics,
            val_data=(X_val, y_val, returns_val),
            test_data=(X_test, y_test, returns_test),
            get_predictions=lambda X: model.predict(X)
        )

        if should_stop:
            print(f"\n🛑 Stopping: {reason}")
            break

    print("\n✅ Training complete")
