"""
Suite d'évaluation complète pour le modèle TinyRecursiveMarketModel
Calcule des métriques prédictives détaillées et génère des rapports
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, List
from datetime import datetime
import json


def compute_prediction_accuracy_by_horizon(
    y_true_ret: np.ndarray,
    y_pred_ret: np.ndarray,
    horizon: int = 12
) -> Dict:
    """
    Analyse la précision des prédictions par horizon temporel

    Returns:
        Dict avec métriques par step de prédiction
    """
    metrics = {
        'mae_per_step': [],
        'rmse_per_step': [],
        'direction_accuracy_per_step': [],
        'mean_error_per_step': [],
        'std_error_per_step': [],
    }

    for h in range(horizon):
        true_h = y_true_ret[:, h]
        pred_h = y_pred_ret[:, h]

        # MAE
        mae = np.mean(np.abs(pred_h - true_h))
        metrics['mae_per_step'].append(float(mae))

        # RMSE
        rmse = np.sqrt(np.mean((pred_h - true_h) ** 2))
        metrics['rmse_per_step'].append(float(rmse))

        # Direction accuracy
        true_dir = np.sign(true_h)
        pred_dir = np.sign(pred_h)
        dir_acc = np.mean(true_dir == pred_dir)
        metrics['direction_accuracy_per_step'].append(float(dir_acc))

        # Error statistics
        errors = pred_h - true_h
        metrics['mean_error_per_step'].append(float(np.mean(errors)))
        metrics['std_error_per_step'].append(float(np.std(errors)))

    # Summary stats
    metrics['avg_mae'] = float(np.mean(metrics['mae_per_step']))
    metrics['avg_direction_accuracy'] = float(np.mean(metrics['direction_accuracy_per_step']))
    metrics['degradation_rate'] = float(
        (metrics['mae_per_step'][-1] - metrics['mae_per_step'][0]) / metrics['mae_per_step'][0]
    )

    return metrics


def compute_profit_simulation(
    y_true_ret: np.ndarray,
    y_pred_ret: np.ndarray,
    y_true_dir: np.ndarray,
    y_pred_dir: np.ndarray,
    initial_capital: float = 10000.0,
    transaction_cost: float = 0.001  # 0.1%
) -> Dict:
    """
    Simule une stratégie de trading basée sur les prédictions

    Returns:
        Dict avec résultats de simulation
    """
    # Use mean prediction over horizon
    if y_pred_ret.ndim == 2:
        pred_signal = np.mean(y_pred_ret, axis=1)
    else:
        pred_signal = y_pred_ret

    if y_true_ret.ndim == 2:
        true_returns = np.mean(y_true_ret, axis=1)
    else:
        true_returns = y_true_ret

    # Convert dir probs to labels if needed
    if y_pred_dir.ndim == 2:
        pred_dir_label = np.argmax(y_pred_dir, axis=1)
    else:
        pred_dir_label = y_pred_dir

    # Trading strategy: long if UP (1), short if DOWN (0)
    # CORRECTED: Binary classification (0=DOWN, 1=UP)
    positions = np.where(pred_dir_label == 1, 1.0,   # Long if UP
                        np.where(pred_dir_label == 0, -1.0, 0.0))  # Short if DOWN

    # Calculate returns with transaction costs
    strategy_returns = positions * true_returns

    # Transaction costs on position changes
    position_changes = np.diff(np.concatenate([[0], positions]))
    costs = np.abs(position_changes) * transaction_cost
    # costs already has same length as strategy_returns (N)
    strategy_returns_net = strategy_returns - costs

    # Cumulative returns
    cumulative_returns = np.cumprod(1 + strategy_returns_net)
    final_capital = initial_capital * cumulative_returns[-1]

    # Calculate drawdown
    running_max = np.maximum.accumulate(cumulative_returns)
    drawdown = (cumulative_returns - running_max) / running_max
    max_drawdown = np.min(drawdown)

    # Win rate
    winning_trades = np.sum(strategy_returns_net > 0)
    total_trades = np.sum(np.abs(position_changes) > 0)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    return {
        'initial_capital': initial_capital,
        'final_capital': float(final_capital),
        'total_return': float((final_capital - initial_capital) / initial_capital),
        'total_return_pct': float(100 * (final_capital - initial_capital) / initial_capital),
        'max_drawdown': float(max_drawdown),
        'max_drawdown_pct': float(100 * max_drawdown),
        'total_trades': int(total_trades),
        'winning_trades': int(winning_trades),
        'win_rate': float(win_rate),
        'sharpe_ratio': float(np.mean(strategy_returns_net) / np.std(strategy_returns_net) * np.sqrt(252)) if np.std(strategy_returns_net) > 0 else 0,
        'avg_trade_return': float(np.mean(strategy_returns_net[np.abs(position_changes) > 0])) if total_trades > 0 else 0,
    }


def analyze_prediction_quality(
    y_true_ret: np.ndarray,
    y_pred_ret: np.ndarray,
    y_true_dir: np.ndarray,
    y_pred_dir: np.ndarray
) -> Dict:
    """
    Analyse détaillée de la qualité des prédictions
    """
    # Mean prediction over horizon
    if y_pred_ret.ndim == 2:
        pred_mean = np.mean(y_pred_ret, axis=1)
    else:
        pred_mean = y_pred_ret

    if y_true_ret.ndim == 2:
        true_mean = np.mean(y_true_ret, axis=1)
    else:
        true_mean = y_true_ret

    # Correlation
    correlation = np.corrcoef(true_mean, pred_mean)[0, 1]

    # R² (coefficient of determination)
    ss_res = np.sum((true_mean - pred_mean) ** 2)
    ss_tot = np.sum((true_mean - np.mean(true_mean)) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    # Direction prediction accuracy
    true_dir_binary = np.sign(true_mean)
    pred_dir_binary = np.sign(pred_mean)
    direction_accuracy = np.mean(true_dir_binary == pred_dir_binary)

    # Magnitude prediction (for moves > threshold)
    threshold = np.std(true_mean) * 0.5
    large_moves = np.abs(true_mean) > threshold
    if np.sum(large_moves) > 0:
        large_move_mae = np.mean(np.abs(true_mean[large_moves] - pred_mean[large_moves]))
        large_move_dir_acc = np.mean((true_dir_binary[large_moves] == pred_dir_binary[large_moves]))
    else:
        large_move_mae = 0
        large_move_dir_acc = 0

    # Calibration (sont les prédictions bien calibrées?)
    pred_quantiles = [10, 25, 50, 75, 90]
    calibration_scores = []
    for q in pred_quantiles:
        pred_q = np.percentile(pred_mean, q)
        true_q = np.percentile(true_mean, q)
        calibration_scores.append(abs(pred_q - true_q))

    return {
        'correlation': float(correlation),
        'r2_score': float(r2),
        'direction_accuracy': float(direction_accuracy),
        'mae_overall': float(np.mean(np.abs(true_mean - pred_mean))),
        'rmse_overall': float(np.sqrt(np.mean((true_mean - pred_mean) ** 2))),
        'large_move_threshold': float(threshold),
        'large_move_mae': float(large_move_mae),
        'large_move_direction_accuracy': float(large_move_dir_acc),
        'mean_prediction': float(np.mean(pred_mean)),
        'std_prediction': float(np.std(pred_mean)),
        'mean_actual': float(np.mean(true_mean)),
        'std_actual': float(np.std(true_mean)),
        'calibration_error': float(np.mean(calibration_scores)),
    }


def compute_confidence_intervals(
    y_true_ret: np.ndarray,
    y_pred_ret: np.ndarray,
    confidence_level: float = 0.95
) -> Dict:
    """
    Calcule des intervalles de confiance pour les prédictions
    """
    if y_pred_ret.ndim == 2:
        pred_mean = np.mean(y_pred_ret, axis=1)
        pred_std = np.std(y_pred_ret, axis=1)
    else:
        pred_mean = y_pred_ret
        pred_std = np.std(y_pred_ret) * np.ones_like(pred_mean)

    if y_true_ret.ndim == 2:
        true_mean = np.mean(y_true_ret, axis=1)
    else:
        true_mean = y_true_ret

    # Z-score for confidence level
    from scipy import stats
    z_score = stats.norm.ppf((1 + confidence_level) / 2)

    # Confidence intervals
    ci_lower = pred_mean - z_score * pred_std
    ci_upper = pred_mean + z_score * pred_std

    # Coverage (% of true values within CI)
    within_ci = (true_mean >= ci_lower) & (true_mean <= ci_upper)
    coverage = np.mean(within_ci)

    return {
        'confidence_level': confidence_level,
        'coverage': float(coverage),
        'mean_ci_width': float(np.mean(ci_upper - ci_lower)),
        'target_coverage': confidence_level,
        'calibration_ratio': float(coverage / confidence_level),
    }


def generate_evaluation_report(
    y_true_dict: Dict[str, np.ndarray],
    y_pred_dict: Dict[str, np.ndarray],
    epoch: int = 0,
    verbose: bool = True
) -> Dict:
    """
    Génère un rapport d'évaluation complet
    """
    report = {
        'epoch': epoch,
        'timestamp': datetime.now().isoformat(),
    }

    # 1. Horizon analysis
    if verbose:
        print("\n  📊 Analysing prediction accuracy by horizon...")
    horizon_metrics = compute_prediction_accuracy_by_horizon(
        y_true_dict['ret'],
        y_pred_dict['ret']
    )
    report['horizon_analysis'] = horizon_metrics

    # 2. Prediction quality
    if verbose:
        print("  📊 Analysing prediction quality...")
    quality_metrics = analyze_prediction_quality(
        y_true_dict['ret'],
        y_pred_dict['ret'],
        y_true_dict['dir'],
        y_pred_dict['dir']
    )
    report['prediction_quality'] = quality_metrics

    # 3. Trading simulation
    if verbose:
        print("  💰 Running profit simulation...")
    profit_sim = compute_profit_simulation(
        y_true_dict['ret'],
        y_pred_dict['ret'],
        y_true_dict['dir'],
        y_pred_dict['dir']
    )
    report['trading_simulation'] = profit_sim

    # 4. Confidence intervals
    if verbose:
        print("  📈 Computing confidence intervals...")
    ci_metrics = compute_confidence_intervals(
        y_true_dict['ret'],
        y_pred_dict['ret']
    )
    report['confidence_intervals'] = ci_metrics

    return report


def print_evaluation_summary(report: Dict, title: str = "EVALUATION REPORT"):
    """
    Affiche un résumé formaté du rapport d'évaluation
    """
    print(f"\n{'='*80}")
    print(f"{title:^80}")
    print(f"{'='*80}")

    # Horizon Analysis
    print("\n📊 HORIZON ANALYSIS:")
    ha = report['horizon_analysis']
    print(f"  Average MAE:              {ha['avg_mae']:.6f}")
    print(f"  Average Direction Acc:    {ha['avg_direction_accuracy']:.2%}")
    print(f"  Degradation Rate:         {ha['degradation_rate']:.2%}")
    print(f"  MAE t+1:                  {ha['mae_per_step'][0]:.6f}")
    print(f"  MAE t+12:                 {ha['mae_per_step'][-1]:.6f}")

    # Prediction Quality
    print("\n🎯 PREDICTION QUALITY:")
    pq = report['prediction_quality']
    print(f"  Correlation:              {pq['correlation']:.4f}")
    print(f"  R² Score:                 {pq['r2_score']:.4f}")
    print(f"  Direction Accuracy:       {pq['direction_accuracy']:.2%}")
    print(f"  MAE Overall:              {pq['mae_overall']:.6f}")
    print(f"  Large Move Dir Acc:       {pq['large_move_direction_accuracy']:.2%}")
    print(f"  Calibration Error:        {pq['calibration_error']:.6f}")

    # Trading Simulation
    print("\n💰 TRADING SIMULATION:")
    ts = report['trading_simulation']
    print(f"  Initial Capital:          ${ts['initial_capital']:,.2f}")
    print(f"  Final Capital:            ${ts['final_capital']:,.2f}")
    print(f"  Total Return:             {ts['total_return_pct']:.2f}%")
    print(f"  Max Drawdown:             {ts['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe Ratio:             {ts['sharpe_ratio']:.4f}")
    print(f"  Total Trades:             {ts['total_trades']}")
    print(f"  Win Rate:                 {ts['win_rate']:.2%}")
    print(f"  Avg Trade Return:         {ts['avg_trade_return']:.6f}")

    # Confidence Intervals
    print("\n📈 CONFIDENCE INTERVALS:")
    ci = report['confidence_intervals']
    print(f"  Confidence Level:         {ci['confidence_level']:.0%}")
    print(f"  Actual Coverage:          {ci['coverage']:.2%}")
    print(f"  Calibration Ratio:        {ci['calibration_ratio']:.4f}")
    print(f"  Mean CI Width:            {ci['mean_ci_width']:.6f}")

    print(f"{'='*80}\n")


def save_evaluation_report(report: Dict, filepath: str):
    """Sauvegarde le rapport en JSON"""
    with open(filepath, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"  ✓ Report saved to: {filepath}")


if __name__ == "__main__":
    # Test avec données simulées
    print("Testing Evaluation Suite...")

    N = 10000
    H = 12

    # Simulate predictions with some correlation
    np.random.seed(42)
    y_true_ret = np.random.randn(N, H) * 0.001
    y_pred_ret = y_true_ret * 0.7 + np.random.randn(N, H) * 0.0005

    y_true_dir = np.random.randint(0, 3, N)
    y_pred_dir = np.random.dirichlet([1, 1, 1], N)

    y_true_dict = {'ret': y_true_ret, 'dir': y_true_dir, 'rv': np.abs(np.random.randn(N, H)) * 0.01}
    y_pred_dict = {'ret': y_pred_ret, 'dir': y_pred_dir, 'rv': np.abs(np.random.randn(N, H)) * 0.01}

    # Generate report
    report = generate_evaluation_report(y_true_dict, y_pred_dict, epoch=5, verbose=True)

    # Print summary
    print_evaluation_summary(report, "TEST EVALUATION")

    # Save
    save_evaluation_report(report, "test_evaluation_report.json")

    print("\n✓ Evaluation Suite test complete!")
