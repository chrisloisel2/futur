"""
Automatic ML Visualization
===========================

Auto-generate plots and diagnostics for training monitoring.
"""

from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend


def plot_training_curves(
    epochs: List[int],
    train_losses: List[float],
    val_losses: List[float],
    output_path: str
):
    """Plot loss curves."""
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(epochs, train_losses, label='Train Loss', marker='o')
    ax.plot(epochs, val_losses, label='Val Loss', marker='s')

    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.set_title('Training & Validation Loss')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_sharpe_evolution(
    epochs: List[int],
    sharpe_raw: List[float],
    sharpe_realistic: List[float],
    output_path: str
):
    """Plot Sharpe ratio evolution."""
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(epochs, sharpe_raw, label='Raw Signal', marker='o', alpha=0.7)
    ax.plot(epochs, sharpe_realistic, label='With Costs', marker='s', alpha=0.7)

    ax.axhline(y=1.0, color='green', linestyle='--', alpha=0.5, label='Target')
    ax.axhline(y=0, color='red', linestyle='--', alpha=0.5)

    ax.set_xlabel('Epoch')
    ax.set_ylabel('Sharpe Ratio')
    ax.set_title('Sharpe Ratio Evolution')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_roi_cumulative(
    epochs: List[int],
    roi_raw: List[float],
    roi_realistic: List[float],
    output_path: str
):
    """Plot cumulative ROI."""
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(epochs, roi_raw, label='Raw Signal', marker='o')
    ax.plot(epochs, roi_realistic, label='With Costs', marker='s')

    ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)

    ax.set_xlabel('Epoch')
    ax.set_ylabel('ROI')
    ax.set_title('Cumulative ROI by Epoch')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Format y-axis as percentage
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1%}'))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_calibration_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int,
    output_path: str
):
    """Plot calibration curve."""
    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.digitize(y_prob, bins[1:-1])

    bin_means = []
    bin_accs = []

    for i in range(n_bins):
        mask = bin_indices == i
        if mask.sum() == 0:
            continue

        bin_means.append(y_prob[mask].mean())
        bin_accs.append(y_true[mask].mean())

    fig, ax = plt.subplots(figsize=(8, 8))

    ax.plot([0, 1], [0, 1], 'k--', label='Perfect Calibration')
    ax.scatter(bin_means, bin_accs, s=100, alpha=0.7, label='Model')

    ax.set_xlabel('Mean Predicted Probability')
    ax.set_ylabel('Fraction of Positives')
    ax.set_title('Calibration Curve')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_prediction_distribution(
    predictions: np.ndarray,
    output_path: str,
    bins: int = 50
):
    """Plot distribution of model predictions."""
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(predictions, bins=bins, alpha=0.7, edgecolor='black')

    ax.set_xlabel('Prediction Value')
    ax.set_ylabel('Frequency')
    ax.set_title('Distribution of Model Predictions')
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_equity_curves_comparison(
    equity_curves: List[np.ndarray],
    epochs: List[int],
    output_path: str,
    max_curves: int = 10
):
    """Plot multiple equity curves for comparison."""
    fig, ax = plt.subplots(figsize=(12, 7))

    # Sample curves if too many
    if len(equity_curves) > max_curves:
        indices = np.linspace(0, len(equity_curves) - 1, max_curves, dtype=int)
        equity_curves = [equity_curves[i] for i in indices]
        epochs = [epochs[i] for i in indices]

    for equity, epoch in zip(equity_curves, epochs):
        alpha = 0.3 + 0.7 * (epoch / max(epochs)) if epochs else 0.7
        ax.plot(equity, alpha=alpha, label=f'Epoch {epoch}')

    ax.set_xlabel('Time')
    ax.set_ylabel('Cumulative PnL')
    ax.set_title('Equity Curves by Epoch')
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_comprehensive_dashboard(
    run_dir: Path,
    output_path: str
):
    """Generate comprehensive dashboard from run directory."""
    # Load all metrics
    metrics_dir = run_dir / "metrics"
    if not metrics_dir.exists():
        return

    metrics_files = sorted(metrics_dir.glob("epoch_*.json"))
    if not metrics_files:
        return

    data = []
    for f in metrics_files:
        with open(f) as fp:
            data.append(json.load(fp))

    epochs = [d['epoch'] for d in data]
    train_losses = [d['train_loss'] for d in data]
    val_losses = [d['val_loss'] for d in data]

    sharpe_raw = [d['paper_test_raw']['sharpe'] for d in data]
    sharpe_real = [d['paper_test_realistic']['sharpe'] for d in data]

    roi_raw = [d['paper_test_raw']['roi'] for d in data]
    roi_real = [d['paper_test_realistic']['roi'] for d in data]

    ece = [d['ece'] for d in data]
    brier = [d['brier'] for d in data]

    # Create 2x3 dashboard
    fig = plt.figure(figsize=(18, 12))

    # 1. Loss curves
    ax1 = plt.subplot(2, 3, 1)
    ax1.plot(epochs, train_losses, label='Train', marker='o')
    ax1.plot(epochs, val_losses, label='Val', marker='s')
    ax1.set_xlabel('Epoch')
    ax1.set_ylabel('Loss')
    ax1.set_title('Training Loss')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # 2. Sharpe
    ax2 = plt.subplot(2, 3, 2)
    ax2.plot(epochs, sharpe_raw, label='Raw', marker='o')
    ax2.plot(epochs, sharpe_real, label='Realistic', marker='s')
    ax2.axhline(y=1.0, color='green', linestyle='--', alpha=0.5)
    ax2.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    ax2.set_xlabel('Epoch')
    ax2.set_ylabel('Sharpe')
    ax2.set_title('Sharpe Ratio')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # 3. ROI
    ax3 = plt.subplot(2, 3, 3)
    ax3.plot(epochs, roi_raw, label='Raw', marker='o')
    ax3.plot(epochs, roi_real, label='Realistic', marker='s')
    ax3.axhline(y=0, color='black', linestyle='--', alpha=0.3)
    ax3.set_xlabel('Epoch')
    ax3.set_ylabel('ROI')
    ax3.set_title('Cumulative ROI')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1%}'))

    # 4. Calibration metrics
    ax4 = plt.subplot(2, 3, 4)
    ax4.plot(epochs, ece, label='ECE', marker='o')
    ax4.plot(epochs, brier, label='Brier', marker='s')
    ax4.set_xlabel('Epoch')
    ax4.set_ylabel('Score')
    ax4.set_title('Calibration Metrics')
    ax4.legend()
    ax4.grid(True, alpha=0.3)

    # 5. Hit rate
    hit_rate = [d['paper_test_realistic']['hit_rate'] for d in data]
    ax5 = plt.subplot(2, 3, 5)
    ax5.plot(epochs, hit_rate, marker='o', color='purple')
    ax5.axhline(y=0.5, color='gray', linestyle='--', alpha=0.5)
    ax5.set_xlabel('Epoch')
    ax5.set_ylabel('Hit Rate')
    ax5.set_title('Trade Win Rate')
    ax5.grid(True, alpha=0.3)
    ax5.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1%}'))

    # 6. Max Drawdown
    max_dd = [d['paper_test_realistic']['max_drawdown'] for d in data]
    ax6 = plt.subplot(2, 3, 6)
    ax6.plot(epochs, max_dd, marker='o', color='red')
    ax6.axhline(y=-0.20, color='darkred', linestyle='--', alpha=0.5, label='Danger')
    ax6.set_xlabel('Epoch')
    ax6.set_ylabel('Max Drawdown')
    ax6.set_title('Maximum Drawdown')
    ax6.legend()
    ax6.grid(True, alpha=0.3)
    ax6.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1%}'))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def generate_all_visualizations(run_dir: Path):
    """Generate all standard visualizations for a training run."""
    vis_dir = run_dir / "visualizations"
    vis_dir.mkdir(exist_ok=True)

    # Comprehensive dashboard
    plot_comprehensive_dashboard(run_dir, str(vis_dir / "dashboard.png"))

    print(f"✅ Visualizations saved to {vis_dir}")
