"""
Instrumented Training Wrapper
==============================

Fully monitored training loop with automatic validation, paper trading,
and early stopping.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Tuple, Dict, Any
import time

import numpy as np
import tensorflow as tf

from .ml_instrumentation import (
    StructuredLogger,
    DataLeakageReport,
    PaperTradingConfig,
    TradingMetrics,
    TrainingSnapshot,
    EarlyStoppingCriteria,
    ArtifactManager,
    expected_calibration_error,
    brier_score,
    prediction_entropy,
    validate_data,
    run_paper_test,
)
from .ml_visualization import generate_all_visualizations


@dataclass
class InstrumentedTrainingConfig:
    """Configuration for instrumented training."""
    run_id: str
    model_name: str
    artifact_dir: str = "artifacts/training_runs"

    # Early stopping
    min_sharpe: float = 0.5
    max_drawdown: float = -0.20
    warmup_epochs: int = 5
    min_roi: float = -0.10

    # Paper trading
    fee_rate: float = 0.001
    spread_bps: float = 5.0
    latency_bars: int = 1

    # Misc
    calibration_bins: int = 10
    check_data_leakage: bool = True


class InstrumentedTrainer:
    """
    Fully monitored training orchestrator.

    Usage:
        trainer = InstrumentedTrainer(config, model, optimizer)

        for epoch in range(num_epochs):
            # Training step
            train_loss, grad_norm = trainer.train_epoch(train_data)

            # Validation & monitoring
            stop_now, reason = trainer.validate_and_monitor(
                epoch=epoch,
                val_data=val_data,
                test_data=test_data,
                get_predictions=lambda data: model.predict(data)
            )

            if stop_now:
                print(f"EARLY STOP: {reason}")
                break

        # Finalize
        trainer.finalize()
    """

    def __init__(
        self,
        config: InstrumentedTrainingConfig,
        model: tf.keras.Model,
        optimizer: tf.keras.optimizers.Optimizer
    ):
        self.config = config
        self.model = model
        self.optimizer = optimizer

        # Initialize components
        self.artifact_manager = ArtifactManager(config.artifact_dir, config.run_id)
        self.logger = StructuredLogger(
            str(self.artifact_manager.base_dir / "logs.jsonl"),
            config.run_id,
            config.model_name
        )
        self.early_stopping = EarlyStoppingCriteria(
            min_sharpe=config.min_sharpe,
            max_drawdown=config.max_drawdown,
            warmup_epochs=config.warmup_epochs,
            min_roi=config.min_roi
        )
        self.paper_config = PaperTradingConfig(
            fee_rate=config.fee_rate,
            spread_bps=config.spread_bps,
            latency_bars=config.latency_bars
        )

        # State
        self.snapshots = []
        self.prev_predictions = None
        self.start_time = time.time()

        # Save initial metadata
        self.artifact_manager.save_git_info()

    def log_startup_info(self, training_config: Dict[str, Any], dataset_info: Dict[str, Any]):
        """Log comprehensive startup information."""
        startup_data = {
            "model_name": self.config.model_name,
            "num_parameters": int(sum(tf.size(w).numpy() for w in self.model.trainable_variables)),
            "training_config": training_config,
            "dataset_info": dataset_info,
        }

        self.logger.log("training_start", startup_data)
        config_hash = self.artifact_manager.save_config({**training_config, **dataset_info})

        print(f"🚀 Training started: {self.config.run_id}")
        print(f"   Config hash: {config_hash}")
        print(f"   Model params: {startup_data['num_parameters']:,}")

    def check_data_quality(
        self,
        X_train: np.ndarray,
        X_val: np.ndarray,
        train_indices: np.ndarray,
        val_indices: np.ndarray,
        feature_names: list
    ) -> DataLeakageReport:
        """Run comprehensive data validation."""
        if not self.config.check_data_leakage:
            return DataLeakageReport(
                has_leakage=False,
                issues=[],
                train_test_overlap=False,
                future_features_detected=False,
                nan_ratio=0.0,
                constant_features=[]
            )

        report = validate_data(X_train, X_val, train_indices, val_indices, feature_names)

        self.logger.log("data_validation", {
            "has_leakage": report.has_leakage,
            "issues": report.issues,
            "nan_ratio": report.nan_ratio,
            "constant_features": report.constant_features
        })

        if report.has_leakage:
            raise RuntimeError(f"DATA LEAKAGE DETECTED: {report.issues}")

        if len(report.issues) > 0:
            print(f"⚠️  Data quality warnings: {len(report.issues)}")
            for issue in report.issues:
                print(f"   - {issue}")

        return report

    def train_step(
        self,
        batch_X: tf.Tensor,
        batch_y: tf.Tensor,
        loss_fn: Callable
    ) -> Tuple[float, float]:
        """Single training step with gradient tracking."""
        with tf.GradientTape() as tape:
            predictions = self.model(batch_X, training=True)
            loss = loss_fn(batch_y, predictions)

        gradients = tape.gradient(loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.model.trainable_variables))

        # Compute gradient norm
        grad_norm = tf.sqrt(sum(tf.reduce_sum(tf.square(g)) for g in gradients if g is not None))

        return float(loss.numpy()), float(grad_norm.numpy())

    def validate_and_monitor(
        self,
        epoch: int,
        val_data: Tuple[np.ndarray, np.ndarray, np.ndarray],  # (X, y, returns)
        test_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]],
        train_loss: float,
        grad_norm: float,
        get_predictions: Callable[[np.ndarray], np.ndarray]
    ) -> Tuple[bool, Optional[str]]:
        """
        Complete validation and monitoring pipeline.

        Returns:
            (should_stop, stop_reason)
        """
        epoch_start = time.time()

        X_val, y_val, returns_val = val_data

        # Get predictions
        val_preds = get_predictions(X_val)

        # Compute losses
        val_loss = float(tf.keras.losses.binary_crossentropy(y_val, val_preds).numpy().mean())

        test_loss = None
        if test_data is not None:
            X_test, y_test, _ = test_data
            test_preds = get_predictions(X_test)
            test_loss = float(tf.keras.losses.binary_crossentropy(y_test, test_preds).numpy().mean())

        # Calibration metrics
        y_val_binary = (y_val > 0.5).astype(np.float32)
        ece = expected_calibration_error(y_val_binary, val_preds, self.config.calibration_bins)
        brier = brier_score(y_val_binary, val_preds)
        entropy = prediction_entropy(np.stack([1 - val_preds, val_preds], axis=-1))

        # Stability metrics
        pred_corr = None
        flip_rate = None
        if self.prev_predictions is not None and len(self.prev_predictions) == len(val_preds):
            pred_corr = float(np.corrcoef(self.prev_predictions, val_preds)[0, 1])
            flips = np.sign(self.prev_predictions - 0.5) != np.sign(val_preds - 0.5)
            flip_rate = float(flips.mean())

        self.prev_predictions = val_preds.copy()

        # Convert to trading signals (binary → directional)
        signals = (val_preds - 0.5) * 2  # Scale to [-1, 1]

        # Paper test #1: Raw signal
        paper_raw = run_paper_test(
            signals=signals,
            returns=returns_val,
            config=PaperTradingConfig(fee_rate=0, spread_bps=0, latency_bars=0),
            name="raw"
        )

        # Paper test #2: Realistic
        paper_realistic = run_paper_test(
            signals=signals,
            returns=returns_val,
            config=self.paper_config,
            name="realistic"
        )

        # Create snapshot
        snapshot = TrainingSnapshot(
            epoch=epoch,
            train_loss=train_loss,
            val_loss=val_loss,
            test_loss=test_loss,
            gradient_norm=grad_norm,
            learning_rate=float(self.optimizer.learning_rate.numpy()),
            time_s=time.time() - epoch_start,
            ece=ece,
            brier=brier,
            entropy=entropy,
            pred_correlation=pred_corr,
            flip_rate=flip_rate,
            paper_test_raw=paper_raw,
            paper_test_realistic=paper_realistic
        )

        self.snapshots.append(snapshot)

        # Log everything
        self._log_snapshot(snapshot)

        # Save artifacts
        self.artifact_manager.save_epoch_metrics(epoch, snapshot)
        self.artifact_manager.save_paper_test(epoch, paper_raw, "raw")
        self.artifact_manager.save_paper_test(epoch, paper_realistic, "realistic")
        self.artifact_manager.save_equity_curve(epoch, paper_realistic.equity_curve, "realistic")

        # Print summary
        self._print_epoch_summary(snapshot)

        # Check early stopping
        should_stop, reason = self.early_stopping.should_stop(snapshot)

        if should_stop:
            self.logger.log("early_stop", {"epoch": epoch, "reason": reason}, epoch=epoch)

        return should_stop, reason

    def finalize(self):
        """Finalize training and generate reports."""
        print("\n" + "=" * 80)
        print("FINALIZING TRAINING")
        print("=" * 80)

        # Generate report
        report = self.artifact_manager.generate_report(self.snapshots)
        print(report)

        # Generate visualizations
        generate_all_visualizations(self.artifact_manager.base_dir)

        # Final log
        total_time = time.time() - self.start_time
        self.logger.log("training_complete", {
            "total_epochs": len(self.snapshots),
            "total_time_s": total_time,
            "final_snapshot": self.snapshots[-1] if self.snapshots else None
        })

        self.logger.close()

        print(f"\n✅ Training complete: {self.config.run_id}")
        print(f"   Total time: {total_time:.1f}s")
        print(f"   Artifacts: {self.artifact_manager.base_dir}")

    def _log_snapshot(self, snapshot: TrainingSnapshot):
        """Log complete snapshot."""
        self.logger.log("epoch_complete", {
            "train_loss": snapshot.train_loss,
            "val_loss": snapshot.val_loss,
            "test_loss": snapshot.test_loss,
            "gradient_norm": snapshot.gradient_norm,
            "learning_rate": snapshot.learning_rate,
            "ece": snapshot.ece,
            "brier": snapshot.brier,
            "entropy": snapshot.entropy,
            "pred_correlation": snapshot.pred_correlation,
            "flip_rate": snapshot.flip_rate,
            "sharpe_raw": snapshot.paper_test_raw.sharpe,
            "sharpe_realistic": snapshot.paper_test_realistic.sharpe,
            "roi_raw": snapshot.paper_test_raw.roi,
            "roi_realistic": snapshot.paper_test_realistic.roi,
            "max_dd": snapshot.paper_test_realistic.max_drawdown,
            "hit_rate": snapshot.paper_test_realistic.hit_rate,
            "num_trades": snapshot.paper_test_realistic.num_trades,
        }, epoch=snapshot.epoch)

    def _print_epoch_summary(self, snapshot: TrainingSnapshot):
        """Print concise epoch summary."""
        print(f"\n{'='*80}")
        print(f"EPOCH {snapshot.epoch}")
        print(f"{'='*80}")
        print(f"  Loss:     train={snapshot.train_loss:.4f}  val={snapshot.val_loss:.4f}  grad_norm={snapshot.gradient_norm:.4f}")
        print(f"  Calib:    ECE={snapshot.ece:.4f}  Brier={snapshot.brier:.4f}  Entropy={snapshot.entropy:.4f}")

        if snapshot.pred_correlation is not None:
            print(f"  Stability: corr={snapshot.pred_correlation:.3f}  flip_rate={snapshot.flip_rate:.2%}")

        print(f"\n  Paper Trading (Realistic):")
        print(f"    ROI:       {snapshot.paper_test_realistic.roi:+.2%}")
        print(f"    Sharpe:    {snapshot.paper_test_realistic.sharpe:.2f}")
        print(f"    Max DD:    {snapshot.paper_test_realistic.max_drawdown:.2%}")
        print(f"    Hit Rate:  {snapshot.paper_test_realistic.hit_rate:.2%}")
        print(f"    Trades:    {snapshot.paper_test_realistic.num_trades}")

        print(f"\n  Paper Trading (Raw Signal):")
        print(f"    ROI:       {snapshot.paper_test_raw.roi:+.2%}")
        print(f"    Sharpe:    {snapshot.paper_test_raw.sharpe:.2f}")

        print(f"\n  Time: {snapshot.time_s:.1f}s")
        print(f"{'='*80}\n")
