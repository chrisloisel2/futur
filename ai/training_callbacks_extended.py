"""
Extended Callbacks avec évaluation détaillée de la capacité prédictive
"""

import os
import tensorflow as tf
import numpy as np
from datetime import datetime

from ai.evaluation_suite import (
    generate_evaluation_report,
    print_evaluation_summary,
    save_evaluation_report
)


class DetailedEvaluationCallback(tf.keras.callbacks.Callback):
    """
    Callback qui évalue en profondeur la capacité prédictive du modèle
    Génère des rapports détaillés avec:
    - Analyse par horizon
    - Simulation de trading
    - Intervalles de confiance
    - Corrélation et R²
    """

    def __init__(
        self,
        validation_data: tf.data.Dataset,
        output_dir: str,
        evaluate_every: int = 1,
        verbose: bool = True
    ):
        super().__init__()
        self.validation_data = validation_data
        self.output_dir = output_dir
        self.evaluate_every = evaluate_every
        self.verbose = verbose

        os.makedirs(output_dir, exist_ok=True)

        # Track evolution over epochs
        self.evolution_history = {
            'epoch': [],
            'direction_accuracy': [],
            'correlation': [],
            'r2_score': [],
            'sharpe_ratio': [],
            'total_return_pct': [],
            'max_drawdown_pct': [],
            'win_rate': [],
            'avg_mae': [],
            'calibration_ratio': [],
        }

    def on_epoch_end(self, epoch, logs=None):
        """Évaluation détaillée à chaque epoch"""
        if (epoch + 1) % self.evaluate_every != 0:
            return

        if self.verbose:
            print(f"\n{'='*80}")
            print(f"DETAILED EVALUATION - Epoch {epoch + 1}")
            print(f"{'='*80}")

        # Collect predictions
        y_true_dict = {'ret': [], 'dir': [], 'rv': []}
        y_pred_dict = {'ret': [], 'dir': [], 'rv': []}

        if self.verbose:
            print("\n  Collecting predictions on validation set...")

        for X_batch, y_batch in self.validation_data:
            y_pred = self.model(X_batch, training=False)

            y_true_dict['ret'].append(y_batch['ret'].numpy())
            y_true_dict['dir'].append(y_batch['dir'].numpy())
            y_true_dict['rv'].append(y_batch['rv'].numpy())

            y_pred_dict['ret'].append(y_pred['ret'].numpy())
            y_pred_dict['dir'].append(y_pred['dir'].numpy())
            y_pred_dict['rv'].append(y_pred['rv'].numpy())

        # Concatenate
        y_true_dict = {k: np.concatenate(v, axis=0) for k, v in y_true_dict.items()}
        y_pred_dict = {k: np.concatenate(v, axis=0) for k, v in y_pred_dict.items()}

        if self.verbose:
            print(f"  Collected {len(y_true_dict['ret'])} samples\n")

        # Generate evaluation report
        report = generate_evaluation_report(
            y_true_dict,
            y_pred_dict,
            epoch=epoch + 1,
            verbose=self.verbose
        )

        # Print summary
        print_evaluation_summary(report, f"EVALUATION EPOCH {epoch + 1}")

        # Save report
        report_path = os.path.join(self.output_dir, f"evaluation_epoch_{epoch + 1:03d}.json")
        save_evaluation_report(report, report_path)

        # Update evolution history
        self.evolution_history['epoch'].append(epoch + 1)
        self.evolution_history['direction_accuracy'].append(
            report['prediction_quality']['direction_accuracy']
        )
        self.evolution_history['correlation'].append(
            report['prediction_quality']['correlation']
        )
        self.evolution_history['r2_score'].append(
            report['prediction_quality']['r2_score']
        )
        self.evolution_history['sharpe_ratio'].append(
            report['trading_simulation']['sharpe_ratio']
        )
        self.evolution_history['total_return_pct'].append(
            report['trading_simulation']['total_return_pct']
        )
        self.evolution_history['max_drawdown_pct'].append(
            report['trading_simulation']['max_drawdown_pct']
        )
        self.evolution_history['win_rate'].append(
            report['trading_simulation']['win_rate']
        )
        self.evolution_history['avg_mae'].append(
            report['horizon_analysis']['avg_mae']
        )
        self.evolution_history['calibration_ratio'].append(
            report['confidence_intervals']['calibration_ratio']
        )

    def on_train_end(self, logs=None):
        """Sauvegarde l'historique d'évolution"""
        import json
        import pandas as pd

        # Save as JSON
        history_path = os.path.join(self.output_dir, "evolution_history.json")
        with open(history_path, 'w') as f:
            json.dump(self.evolution_history, f, indent=2)

        # Save as CSV for easy plotting
        csv_path = os.path.join(self.output_dir, "evolution_history.csv")
        pd.DataFrame(self.evolution_history).to_csv(csv_path, index=False)

        print(f"\n  ✓ Evolution history saved to: {self.output_dir}")

        # Print final summary
        if len(self.evolution_history['epoch']) > 0:
            print(f"\n{'='*80}")
            print("TRAINING EVOLUTION SUMMARY")
            print(f"{'='*80}")

            print(f"\nDirection Accuracy:")
            print(f"  Initial:  {self.evolution_history['direction_accuracy'][0]:.2%}")
            print(f"  Final:    {self.evolution_history['direction_accuracy'][-1]:.2%}")
            print(f"  Best:     {max(self.evolution_history['direction_accuracy']):.2%}")

            print(f"\nCorrelation:")
            print(f"  Initial:  {self.evolution_history['correlation'][0]:.4f}")
            print(f"  Final:    {self.evolution_history['correlation'][-1]:.4f}")
            print(f"  Best:     {max(self.evolution_history['correlation']):.4f}")

            print(f"\nR² Score:")
            print(f"  Initial:  {self.evolution_history['r2_score'][0]:.4f}")
            print(f"  Final:    {self.evolution_history['r2_score'][-1]:.4f}")
            print(f"  Best:     {max(self.evolution_history['r2_score']):.4f}")

            print(f"\nTrading Performance:")
            print(f"  Initial Return:  {self.evolution_history['total_return_pct'][0]:+.2f}%")
            print(f"  Final Return:    {self.evolution_history['total_return_pct'][-1]:+.2f}%")
            print(f"  Best Return:     {max(self.evolution_history['total_return_pct']):+.2f}%")

            print(f"\nSharpe Ratio:")
            print(f"  Initial:  {self.evolution_history['sharpe_ratio'][0]:.4f}")
            print(f"  Final:    {self.evolution_history['sharpe_ratio'][-1]:.4f}")
            print(f"  Best:     {max(self.evolution_history['sharpe_ratio']):.4f}")

            print(f"{'='*80}\n")


class EpochProgressLogger(tf.keras.callbacks.Callback):
    """
    Logger qui affiche des infos détaillées à chaque epoch
    """

    def __init__(self, total_epochs: int):
        super().__init__()
        self.total_epochs = total_epochs
        self.epoch_start_time = None

    def on_epoch_begin(self, epoch, logs=None):
        """Début de l'epoch"""
        self.epoch_start_time = datetime.now()
        print(f"\n{'='*80}")
        print(f"EPOCH {epoch + 1}/{self.total_epochs}")
        print(f"{'='*80}")

    def on_epoch_end(self, epoch, logs=None):
        """Fin de l'epoch"""
        if self.epoch_start_time:
            duration = (datetime.now() - self.epoch_start_time).total_seconds()
            print(f"\nEpoch {epoch + 1} completed in {duration:.1f}s")

            if logs:
                print(f"\nTraining Metrics:")
                print(f"  loss:         {logs.get('loss', 0):.6f}")
                print(f"  ret_loss:     {logs.get('ret_loss', 0):.6f}")
                print(f"  dir_loss:     {logs.get('dir_loss', 0):.6f}")
                print(f"  rv_loss:      {logs.get('rv_loss', 0):.6f}")

                print(f"\nValidation Metrics:")
                print(f"  val_loss:     {logs.get('val_loss', 0):.6f}")
                print(f"  val_ret_loss: {logs.get('val_ret_loss', 0):.6f}")
                print(f"  val_dir_loss: {logs.get('val_dir_loss', 0):.6f}")
                print(f"  val_rv_loss:  {logs.get('val_rv_loss', 0):.6f}")

                # Direction accuracy
                if 'val_dir_acc' in logs:
                    print(f"\n  Direction Accuracy: {logs['val_dir_acc']:.2%}")


class BestModelTracker(tf.keras.callbacks.Callback):
    """
    Track et affiche les meilleurs résultats atteints
    """

    def __init__(self, metrics_to_track: list = None):
        super().__init__()
        self.metrics_to_track = metrics_to_track or [
            'val_loss', 'val_dir_acc', 'val_ret_mae'
        ]
        self.best_values = {}
        self.best_epochs = {}

        for metric in self.metrics_to_track:
            self.best_values[metric] = float('inf') if 'loss' in metric else float('-inf')
            self.best_epochs[metric] = 0

    def on_epoch_end(self, epoch, logs=None):
        """Update best values"""
        if logs:
            updated = []
            for metric in self.metrics_to_track:
                if metric in logs:
                    value = logs[metric]
                    is_loss = 'loss' in metric
                    is_better = (value < self.best_values[metric]) if is_loss else (value > self.best_values[metric])

                    if is_better:
                        self.best_values[metric] = value
                        self.best_epochs[metric] = epoch + 1
                        updated.append(metric)

            if updated:
                print(f"\n  🏆 New best: {', '.join(updated)}")

    def on_train_end(self, logs=None):
        """Print best results summary"""
        print(f"\n{'='*80}")
        print("BEST RESULTS ACHIEVED")
        print(f"{'='*80}")

        for metric in self.metrics_to_track:
            if metric in self.best_values:
                value = self.best_values[metric]
                epoch = self.best_epochs[metric]
                print(f"  {metric:20s}: {value:.6f} (Epoch {epoch})")

        print(f"{'='*80}\n")
