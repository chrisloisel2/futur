"""
Custom Keras Callbacks pour entraînement avancé
Inclut: Trading metrics, memory monitoring, prediction analysis, etc.
"""

import os
import csv
import json
import time
import subprocess
from typing import Dict, Any, Optional
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import tensorflow as tf
import psutil

from ai.advanced_metrics import compute_all_metrics, print_metrics_summary
from ai.evaluation_suite import (
    generate_evaluation_report,
    print_evaluation_summary,
    save_evaluation_report
)
from ai.training_callbacks_extended import (
    DetailedEvaluationCallback,
    EpochProgressLogger,
    BestModelTracker
)


class TradingMetricsCallback(tf.keras.callbacks.Callback):
    """
    Calcule tous les KPIs avancés à chaque epoch
    Log dans TensorBoard + CSV + Console
    """

    def __init__(
        self,
        validation_data: tf.data.Dataset,
        log_dir: str,
        csv_path: str,
        periods_per_year: int = 525600,
        verbose: bool = True
    ):
        super().__init__()
        self.validation_data = validation_data
        self.log_dir = log_dir
        self.csv_path = csv_path
        self.periods_per_year = periods_per_year
        self.verbose = verbose

        # TensorBoard writer
        self.writer = tf.summary.create_file_writer(log_dir)

        # CSV file
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        self.csv_file = None
        self.csv_writer = None

    def on_train_begin(self, logs=None):
        """Initialize CSV"""
        self.csv_file = open(self.csv_path, 'w', newline='')
        fieldnames = ['epoch', 'timestamp'] + self._get_metric_names()
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=fieldnames)
        self.csv_writer.writeheader()

    def on_epoch_end(self, epoch, logs=None):
        """Compute and log all metrics"""
        if self.verbose:
            print(f"\n{'='*80}")
            print(f"Computing advanced metrics for epoch {epoch + 1}...")
            print(f"{'='*80}")

        # Collect predictions and ground truth
        y_true_dict = {'ret': [], 'dir': [], 'rv': []}
        y_pred_dict = {'ret': [], 'dir': [], 'rv': []}

        for X_batch, y_batch in self.validation_data:
            y_pred = self.model(X_batch, training=False)

            y_true_dict['ret'].append(y_batch['ret'].numpy())
            y_true_dict['dir'].append(y_batch['dir'].numpy())
            y_true_dict['rv'].append(y_batch['rv'].numpy())

            y_pred_dict['ret'].append(y_pred['ret'].numpy())
            y_pred_dict['dir'].append(y_pred['dir'].numpy())
            y_pred_dict['rv'].append(y_pred['rv'].numpy())

        # Concatenate batches
        y_true_dict = {k: np.concatenate(v, axis=0) for k, v in y_true_dict.items()}
        y_pred_dict = {k: np.concatenate(v, axis=0) for k, v in y_pred_dict.items()}

        # Compute all metrics
        metrics = compute_all_metrics(y_true_dict, y_pred_dict, self.periods_per_year)

        # Log to TensorBoard
        self._log_to_tensorboard(metrics, epoch)

        # Log to CSV
        self._log_to_csv(metrics, epoch)

        # Print summary
        if self.verbose:
            print_metrics_summary(metrics, f"VALIDATION METRICS (Epoch {epoch + 1})")

    def _get_metric_names(self) -> list:
        """Get list of all metric names for CSV header"""
        return [
            # Trading
            'sharpe_ratio', 'sortino_ratio', 'max_drawdown', 'calmar_ratio',
            'win_rate', 'profit_factor', 'avg_win_loss_ratio',
            'total_return', 'annualized_return', 'volatility',
            # Classification
            'accuracy', 'macro_f1', 'weighted_f1', 'cohens_kappa',
            # Regression
            'mae_returns', 'rmse_returns', 'r2_returns',
            'mae_volatility', 'rmse_volatility', 'r2_volatility',
            # Distribution
            'prediction_bias', 'error_skewness', 'error_kurtosis',
            'error_95th_percentile',
        ]

    def _log_to_tensorboard(self, metrics: Dict, epoch: int):
        """Log metrics to TensorBoard"""
        with self.writer.as_default():
            # Trading metrics
            tf.summary.scalar('trading/sharpe_ratio', metrics['sharpe_ratio'], step=epoch)
            tf.summary.scalar('trading/sortino_ratio', metrics['sortino_ratio'], step=epoch)
            tf.summary.scalar('trading/max_drawdown', metrics['max_drawdown'], step=epoch)
            tf.summary.scalar('trading/calmar_ratio', metrics['calmar_ratio'], step=epoch)
            tf.summary.scalar('trading/win_rate', metrics['win_rate'], step=epoch)
            tf.summary.scalar('trading/profit_factor', metrics['profit_factor'], step=epoch)

            # Classification
            tf.summary.scalar('classification/accuracy', metrics['accuracy'], step=epoch)
            tf.summary.scalar('classification/macro_f1', metrics['macro_f1'], step=epoch)
            tf.summary.scalar('classification/cohens_kappa', metrics['cohens_kappa'], step=epoch)

            # Regression
            tf.summary.scalar('regression/mae_returns', metrics['mae_returns'], step=epoch)
            tf.summary.scalar('regression/r2_returns', metrics['r2_returns'], step=epoch)
            tf.summary.scalar('regression/mae_volatility', metrics['mae_volatility'], step=epoch)

        self.writer.flush()

    def _log_to_csv(self, metrics: Dict, epoch: int):
        """Log metrics to CSV"""
        row = {
            'epoch': epoch + 1,
            'timestamp': datetime.now().isoformat(),
        }

        # Add metric values (only scalars, not lists)
        for key in self._get_metric_names():
            value = metrics.get(key, 0)
            # Handle numpy types
            if isinstance(value, (np.integer, np.floating)):
                value = float(value)
            row[key] = value

        self.csv_writer.writerow(row)
        self.csv_file.flush()

    def on_train_end(self, logs=None):
        """Close CSV file"""
        if self.csv_file:
            self.csv_file.close()


class MemoryMonitorCallback(tf.keras.callbacks.Callback):
    """
    Monitor RAM and GPU memory usage
    """

    def __init__(self, log_dir: str, verbose: bool = True):
        super().__init__()
        self.log_dir = log_dir
        self.verbose = verbose
        self.writer = tf.summary.create_file_writer(log_dir)

    def on_epoch_end(self, epoch, logs=None):
        """Log memory usage"""
        # RAM usage
        process = psutil.Process()
        ram_used = process.memory_info().rss / 1024**3  # GB
        ram_percent = psutil.virtual_memory().percent

        # GPU memory
        gpu_memory_used = 0
        gpu_memory_total = 0

        try:
            # Try to get GPU info using nvidia-smi
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total',
                 '--format=csv,nounits,noheader'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    used, total = lines[0].split(',')
                    gpu_memory_used = float(used) / 1024  # GB
                    gpu_memory_total = float(total) / 1024  # GB
        except:
            pass

        # Log to TensorBoard
        with self.writer.as_default():
            tf.summary.scalar('memory/ram_usage_gb', ram_used, step=epoch)
            tf.summary.scalar('memory/ram_percent', ram_percent, step=epoch)

            if gpu_memory_total > 0:
                tf.summary.scalar('memory/gpu_usage_gb', gpu_memory_used, step=epoch)
                tf.summary.scalar('memory/gpu_percent',
                                  (gpu_memory_used / gpu_memory_total) * 100, step=epoch)

        self.writer.flush()

        # Print warning if memory usage is high
        if self.verbose:
            print(f"\nMemory:")
            print(f"  RAM Usage:           {ram_used:.2f} GB ({ram_percent:.1f}%)")

            if gpu_memory_total > 0:
                gpu_percent = (gpu_memory_used / gpu_memory_total) * 100
                print(f"  GPU Memory:          {gpu_memory_used:.2f} GB / {gpu_memory_total:.2f} GB ({gpu_percent:.1f}%)")

                if gpu_percent > 90:
                    print("  ⚠️  WARNING: GPU memory usage > 90%!")
            print()

        if ram_percent > 90:
            print("  ⚠️  WARNING: RAM usage > 90%!")


class PredictionAnalysisCallback(tf.keras.callbacks.Callback):
    """
    Analyze predictions and save visualizations
    """

    def __init__(
        self,
        validation_data: tf.data.Dataset,
        output_dir: str,
        n_samples: int = 1000,
        save_every: int = 5
    ):
        super().__init__()
        self.validation_data = validation_data
        self.output_dir = output_dir
        self.n_samples = n_samples
        self.save_every = save_every

        os.makedirs(output_dir, exist_ok=True)

    def on_epoch_end(self, epoch, logs=None):
        """Save prediction analysis plots"""
        if (epoch + 1) % self.save_every != 0:
            return

        print(f"\n  Generating prediction analysis plots...")

        # Sample predictions
        y_true_ret = []
        y_pred_ret = []

        n_collected = 0
        for X_batch, y_batch in self.validation_data:
            if n_collected >= self.n_samples:
                break

            y_pred = self.model(X_batch, training=False)

            batch_size = min(len(y_batch['ret']), self.n_samples - n_collected)
            y_true_ret.append(y_batch['ret'][:batch_size].numpy())
            y_pred_ret.append(y_pred['ret'][:batch_size].numpy())

            n_collected += batch_size

        y_true_ret = np.concatenate(y_true_ret, axis=0)
        y_pred_ret = np.concatenate(y_pred_ret, axis=0)

        # Take mean over horizon
        y_true_mean = np.mean(y_true_ret, axis=1)
        y_pred_mean = np.mean(y_pred_ret, axis=1)

        # 1. True vs Pred scatter
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))

        axes[0].scatter(y_true_mean, y_pred_mean, alpha=0.5, s=10)
        axes[0].plot([y_true_mean.min(), y_true_mean.max()],
                     [y_true_mean.min(), y_true_mean.max()],
                     'r--', lw=2, label='Perfect prediction')
        axes[0].set_xlabel('True Return')
        axes[0].set_ylabel('Predicted Return')
        axes[0].set_title(f'True vs Predicted (Epoch {epoch + 1})')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 2. Error distribution
        errors = y_pred_mean - y_true_mean
        axes[1].hist(errors, bins=50, alpha=0.7, edgecolor='black')
        axes[1].axvline(0, color='r', linestyle='--', lw=2, label='Zero error')
        axes[1].axvline(np.mean(errors), color='g', linestyle='--', lw=2,
                        label=f'Mean error: {np.mean(errors):.6f}')
        axes[1].set_xlabel('Prediction Error')
        axes[1].set_ylabel('Frequency')
        axes[1].set_title('Error Distribution')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(os.path.join(self.output_dir, f'predictions_epoch_{epoch + 1:03d}.png'),
                    dpi=150, bbox_inches='tight')
        plt.close()

        print(f"  Saved: {self.output_dir}/predictions_epoch_{epoch + 1:03d}.png")


class DetailedCSVLogger(tf.keras.callbacks.CSVLogger):
    """
    Extended CSV logger with additional info
    """

    def __init__(self, filename: str, separator: str = ',', append: bool = False):
        super().__init__(filename, separator=separator, append=append)
        self.start_time = None

    def on_train_begin(self, logs=None):
        self.start_time = time.time()
        super().on_train_begin(logs)

    def on_epoch_end(self, epoch, logs=None):
        """Add timestamp and elapsed time"""
        logs = logs or {}

        # Add extra info
        logs['timestamp'] = datetime.now().isoformat()
        logs['elapsed_time'] = time.time() - self.start_time

        # Add learning rate if available
        if hasattr(self.model.optimizer, 'learning_rate'):
            lr = self.model.optimizer.learning_rate
            if isinstance(lr, tf.keras.optimizers.schedules.LearningRateSchedule):
                logs['learning_rate'] = float(lr(self.model.optimizer.iterations))
            else:
                logs['learning_rate'] = float(lr)

        super().on_epoch_end(epoch, logs)


def build_callbacks(
    config: Any,
    validation_data: tf.data.Dataset,
    output_base_dir: str = "training_output"
) -> list:
    """
    Build all callbacks for training

    Args:
        config: Configuration object
        validation_data: Validation dataset
        output_base_dir: Base directory for outputs

    Returns:
        List of callbacks
    """
    # Create directories
    checkpoint_dir = os.path.join(output_base_dir, "checkpoints")
    tensorboard_dir = os.path.join(output_base_dir, "tensorboard")
    metrics_dir = os.path.join(output_base_dir, "metrics")
    logs_dir = os.path.join(output_base_dir, "logs")
    analysis_dir = os.path.join(logs_dir, "predictions_analysis")

    for d in [checkpoint_dir, tensorboard_dir, metrics_dir, logs_dir, analysis_dir]:
        os.makedirs(d, exist_ok=True)

    # Create evaluation output directory
    evaluation_dir = os.path.join(output_base_dir, "evaluation")
    os.makedirs(evaluation_dir, exist_ok=True)

    callbacks = [
        # 1. Epoch Progress Logger (shows detailed epoch info)
        EpochProgressLogger(
            total_epochs=config.epochs
        ),

        # 2. Detailed Evaluation Callback (comprehensive prediction analysis)
        DetailedEvaluationCallback(
            validation_data=validation_data,
            output_dir=evaluation_dir,
            evaluate_every=1,  # Evaluate every epoch
            verbose=True
        ),

        # 3. Best Model Tracker (tracks best metrics achieved)
        BestModelTracker(
            metrics_to_track=['val_loss', 'val_dir_acc', 'val_ret_mae']
        ),

        # 4. Trading Metrics Callback (30+ KPIs)
        TradingMetricsCallback(
            validation_data=validation_data,
            log_dir=tensorboard_dir,
            csv_path=os.path.join(metrics_dir, "trading_metrics.csv"),
            verbose=True
        ),

        # 5. Memory Monitor
        MemoryMonitorCallback(
            log_dir=tensorboard_dir,
            verbose=True
        ),

        # 6. Prediction Analysis (visualizations)
        PredictionAnalysisCallback(
            validation_data=validation_data,
            output_dir=analysis_dir,
            n_samples=1000,
            save_every=5
        ),

        # 7. Model Checkpoint (val_loss)
        tf.keras.callbacks.ModelCheckpoint(
            filepath=os.path.join(checkpoint_dir, "best_val_loss.keras"),
            monitor='val_loss',
            save_best_only=True,
            save_weights_only=False,
            verbose=1
        ),

        # 8. Model Checkpoint (epoch)
        tf.keras.callbacks.ModelCheckpoint(
            filepath=os.path.join(checkpoint_dir, "epoch_{epoch:03d}.keras"),
            save_freq='epoch',
            save_weights_only=False,
            verbose=0
        ),

        # 9. Early Stopping
        tf.keras.callbacks.EarlyStopping(
            monitor='val_loss',
            patience=5,
            restore_best_weights=True,
            verbose=1
        ),

        # 10. Terminate on NaN
        tf.keras.callbacks.TerminateOnNaN(),

        # 11. Detailed CSV Logger
        DetailedCSVLogger(
            filename=os.path.join(metrics_dir, "training_log.csv")
        ),

        # 12. TensorBoard (standard)
        tf.keras.callbacks.TensorBoard(
            log_dir=tensorboard_dir,
            histogram_freq=0,
            write_graph=False,
            update_freq='epoch'
        ),
    ]

    return callbacks


if __name__ == "__main__":
    print("Testing Callbacks...")

    # Create dummy validation data
    np.random.seed(42)
    N = 1000
    Xw = np.random.randn(N, 256, 50).astype(np.float32)
    y_ret = np.random.randn(N, 12).astype(np.float32) * 0.001
    y_dir = np.random.randint(0, 3, N).astype(np.int32)
    y_rv = np.abs(np.random.randn(N, 12).astype(np.float32)) * 0.01

    ds_val = tf.data.Dataset.from_tensor_slices((
        Xw,
        {"ret": y_ret, "dir": y_dir, "rv": y_rv}
    )).batch(32)

    # Create simple model
    from ai.models.model import TinyRecursiveMarketModel, TRMConfig

    config = TRMConfig()
    model = TinyRecursiveMarketModel(config, feature_dim=50)
    model.compile(
        optimizer='adam',
        loss={'ret': 'mse', 'dir': 'sparse_categorical_crossentropy', 'rv': 'mse'}
    )

    # Build callbacks
    callbacks = build_callbacks(config, ds_val, output_base_dir="test_output")

    print(f"\nBuilt {len(callbacks)} callbacks:")
    for cb in callbacks:
        print(f"  - {cb.__class__.__name__}")

    print("\nCallbacks test complete!")
