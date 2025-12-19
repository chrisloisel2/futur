"""
Advanced Metrics: 30+ KPIs pour évaluation complète du modèle
Inclut trading metrics, classification, regression, et distribution analysis
"""

from typing import Dict, List, Any, Tuple
import numpy as np
from scipy import stats
from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
    cohen_kappa_score,
    r2_score,
    mean_absolute_error,
    mean_squared_error,
)


# ==========================================
# A. TRADING PERFORMANCE METRICS
# ==========================================

def compute_sharpe_ratio(returns: np.ndarray, periods_per_year: int = 525600) -> float:
    """
    Sharpe Ratio annualisé
    Pour 1-minute data: 525600 minutes/year = 365.25 * 24 * 60
    """
    if len(returns) == 0 or np.std(returns) == 0:
        return 0.0

    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    sharpe = (mean_ret / std_ret) * np.sqrt(periods_per_year)
    return float(sharpe)


def compute_sortino_ratio(returns: np.ndarray, periods_per_year: int = 525600) -> float:
    """
    Sortino Ratio annualisé (downside deviation only)
    """
    if len(returns) == 0:
        return 0.0

    mean_ret = np.mean(returns)
    downside_returns = returns[returns < 0]

    if len(downside_returns) == 0 or np.std(downside_returns) == 0:
        return 0.0

    downside_std = np.std(downside_returns)
    sortino = (mean_ret / downside_std) * np.sqrt(periods_per_year)
    return float(sortino)


def compute_maximum_drawdown(returns: np.ndarray) -> Tuple[float, int, int]:
    """
    Maximum Drawdown (MDD)
    Returns: (mdd_pct, start_idx, end_idx)
    """
    if len(returns) == 0:
        return 0.0, 0, 0

    # Compute cumulative returns
    cum_returns = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - running_max) / running_max

    mdd = np.min(drawdown)
    end_idx = np.argmin(drawdown)

    # Find start of drawdown
    start_idx = np.argmax(cum_returns[:end_idx]) if end_idx > 0 else 0

    return float(mdd), int(start_idx), int(end_idx)


def compute_calmar_ratio(returns: np.ndarray, periods_per_year: int = 525600) -> float:
    """
    Calmar Ratio = Annualized Return / abs(MDD)
    """
    if len(returns) == 0:
        return 0.0

    annual_return = np.mean(returns) * periods_per_year
    mdd, _, _ = compute_maximum_drawdown(returns)

    if mdd == 0:
        return 0.0

    calmar = annual_return / abs(mdd)
    return float(calmar)


def compute_win_rate(returns: np.ndarray) -> float:
    """
    Win Rate = % of positive returns
    """
    if len(returns) == 0:
        return 0.0

    n_wins = np.sum(returns > 0)
    win_rate = n_wins / len(returns)
    return float(win_rate)


def compute_profit_factor(returns: np.ndarray) -> float:
    """
    Profit Factor = sum(gains) / abs(sum(losses))
    """
    gains = returns[returns > 0]
    losses = returns[returns < 0]

    total_gains = np.sum(gains) if len(gains) > 0 else 0
    total_losses = np.sum(losses) if len(losses) > 0 else 0

    if total_losses == 0:
        return float('inf') if total_gains > 0 else 0.0

    profit_factor = total_gains / abs(total_losses)
    return float(profit_factor)


def compute_avg_win_loss_ratio(returns: np.ndarray) -> float:
    """
    Average Win / Average Loss Ratio
    """
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    avg_win = np.mean(wins) if len(wins) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0

    if avg_loss == 0:
        return float('inf') if avg_win > 0 else 0.0

    ratio = avg_win / abs(avg_loss)
    return float(ratio)


def compute_trading_metrics(
    y_true_ret: np.ndarray,
    y_pred_ret: np.ndarray,
    periods_per_year: int = 525600
) -> Dict[str, float]:
    """
    Compute tous les trading metrics (10 KPIs)

    Args:
        y_true_ret: True returns [N, horizon] ou [N]
        y_pred_ret: Predicted returns [N, horizon] ou [N]
        periods_per_year: Minutes per year for annualization

    Returns:
        Dict avec 10 trading metrics
    """
    # Si multi-horizon, prendre la moyenne sur l'horizon
    if y_pred_ret.ndim == 2:
        y_pred_ret = np.mean(y_pred_ret, axis=1)
    if y_true_ret.ndim == 2:
        y_true_ret = np.mean(y_true_ret, axis=1)

    # Use predicted returns for strategy simulation
    returns = y_pred_ret

    metrics = {
        'sharpe_ratio': compute_sharpe_ratio(returns, periods_per_year),
        'sortino_ratio': compute_sortino_ratio(returns, periods_per_year),
        'max_drawdown': compute_maximum_drawdown(returns)[0],
        'calmar_ratio': compute_calmar_ratio(returns, periods_per_year),
        'win_rate': compute_win_rate(returns),
        'profit_factor': compute_profit_factor(returns),
        'avg_win_loss_ratio': compute_avg_win_loss_ratio(returns),
        'total_return': float(np.sum(returns)),
        'annualized_return': float(np.mean(returns) * periods_per_year),
        'volatility': float(np.std(returns) * np.sqrt(periods_per_year)),
    }

    return metrics


# ==========================================
# B. CLASSIFICATION METRICS
# ==========================================

def compute_classification_metrics(
    y_true_dir: np.ndarray,
    y_pred_dir: np.ndarray
) -> Dict[str, Any]:
    """
    Compute classification metrics (8 KPIs)

    Args:
        y_true_dir: True direction labels [N] (0=down, 1=flat, 2=up)
        y_pred_dir: Predicted direction (probabilities [N, 3] or labels [N])

    Returns:
        Dict avec classification metrics + confusion matrix
    """
    # Si y_pred_dir est des probabilités, prendre argmax
    if y_pred_dir.ndim == 2:
        y_pred_labels = np.argmax(y_pred_dir, axis=1)
    else:
        y_pred_labels = y_pred_dir

    # Accuracy
    accuracy = np.mean(y_true_dir == y_pred_labels)

    # Confusion Matrix
    cm = confusion_matrix(y_true_dir, y_pred_labels, labels=[0, 1, 2])

    # Precision, Recall, F1 per class
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true_dir, y_pred_labels, labels=[0, 1, 2], zero_division=0
    )

    # Macro and Weighted F1
    _, _, f1_macro, _ = precision_recall_fscore_support(
        y_true_dir, y_pred_labels, average='macro', zero_division=0
    )
    _, _, f1_weighted, _ = precision_recall_fscore_support(
        y_true_dir, y_pred_labels, average='weighted', zero_division=0
    )

    # Cohen's Kappa
    kappa = cohen_kappa_score(y_true_dir, y_pred_labels)

    metrics = {
        'accuracy': float(accuracy),
        'confusion_matrix': cm.tolist(),
        'precision_per_class': precision.tolist(),
        'recall_per_class': recall.tolist(),
        'f1_per_class': f1.tolist(),
        'macro_f1': float(f1_macro),
        'weighted_f1': float(f1_weighted),
        'cohens_kappa': float(kappa),
    }

    return metrics


# ==========================================
# C. REGRESSION METRICS
# ==========================================

def compute_regression_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    name: str = "metric"
) -> Dict[str, float]:
    """
    Compute regression metrics (3 KPIs per target)

    Args:
        y_true: True values [N] ou [N, H]
        y_pred: Predicted values [N] ou [N, H]
        name: Nom de la métrique (returns, volatility, etc.)

    Returns:
        Dict avec MAE, RMSE, R²
    """
    # Flatten si multi-horizon
    y_true_flat = y_true.flatten()
    y_pred_flat = y_pred.flatten()

    mae = mean_absolute_error(y_true_flat, y_pred_flat)
    rmse = np.sqrt(mean_squared_error(y_true_flat, y_pred_flat))

    # R² can be negative if model is worse than mean
    try:
        r2 = r2_score(y_true_flat, y_pred_flat)
    except:
        r2 = -float('inf')

    metrics = {
        f'mae_{name}': float(mae),
        f'rmse_{name}': float(rmse),
        f'r2_{name}': float(r2),
    }

    return metrics


# ==========================================
# D. DISTRIBUTION ANALYSIS
# ==========================================

def compute_distribution_metrics(errors: np.ndarray) -> Dict[str, float]:
    """
    Compute distribution metrics on errors (4 KPIs)

    Args:
        errors: Prediction errors [N]

    Returns:
        Dict avec bias, skewness, kurtosis, 95th percentile
    """
    if len(errors) == 0:
        return {
            'prediction_bias': 0.0,
            'error_skewness': 0.0,
            'error_kurtosis': 0.0,
            'error_95th_percentile': 0.0,
        }

    bias = float(np.mean(errors))
    skewness = float(stats.skew(errors))
    kurtosis = float(stats.kurtosis(errors))
    percentile_95 = float(np.percentile(np.abs(errors), 95))

    metrics = {
        'prediction_bias': bias,
        'error_skewness': skewness,
        'error_kurtosis': kurtosis,
        'error_95th_percentile': percentile_95,
    }

    return metrics


# ==========================================
# E. HORIZON-SPECIFIC METRICS
# ==========================================

def compute_horizon_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_true_dir: np.ndarray,
    y_pred_dir: np.ndarray,
) -> Dict[str, List[float]]:
    """
    Compute metrics per horizon step (2 KPIs × H steps)

    Args:
        y_true: True returns [N, H]
        y_pred: Predicted returns [N, H]
        y_true_dir: True directions [N]
        y_pred_dir: Predicted directions [N, 3] (probabilities)

    Returns:
        Dict avec MAE et accuracy par horizon
    """
    if y_true.ndim == 1:
        # Single horizon
        mae_per_horizon = [float(mean_absolute_error(y_true, y_pred))]
        dir_acc_per_horizon = [float(np.mean(y_true_dir == np.argmax(y_pred_dir, axis=1)))]
    else:
        # Multi-horizon
        H = y_true.shape[1]
        mae_per_horizon = []
        dir_acc_per_horizon = []

        for h in range(H):
            mae_h = float(mean_absolute_error(y_true[:, h], y_pred[:, h]))
            mae_per_horizon.append(mae_h)

            # Direction accuracy at horizon h (based on sign of return)
            true_dir_h = np.sign(y_true[:, h])
            pred_dir_h = np.sign(y_pred[:, h])
            acc_h = float(np.mean(true_dir_h == pred_dir_h))
            dir_acc_per_horizon.append(acc_h)

    metrics = {
        'mae_per_horizon': mae_per_horizon,
        'directional_accuracy_per_horizon': dir_acc_per_horizon,
    }

    return metrics


# ==========================================
# COMPUTE ALL METRICS
# ==========================================

def compute_all_metrics(
    y_true_dict: Dict[str, np.ndarray],
    y_pred_dict: Dict[str, np.ndarray],
    periods_per_year: int = 525600
) -> Dict[str, Any]:
    """
    Compute TOUS les 30+ KPIs

    Args:
        y_true_dict: {'ret': [N, H], 'dir': [N], 'rv': [N, H]}
        y_pred_dict: {'ret': [N, H], 'dir': [N, 3], 'rv': [N, H]}
        periods_per_year: For annualization

    Returns:
        Dict avec tous les metrics
    """
    all_metrics = {}

    # A. Trading Metrics (10 KPIs)
    trading_metrics = compute_trading_metrics(
        y_true_dict['ret'],
        y_pred_dict['ret'],
        periods_per_year
    )
    all_metrics.update(trading_metrics)

    # B. Classification Metrics (8 KPIs)
    classification_metrics = compute_classification_metrics(
        y_true_dict['dir'],
        y_pred_dict['dir']
    )
    all_metrics.update(classification_metrics)

    # C. Regression Metrics for Returns (3 KPIs)
    regression_ret = compute_regression_metrics(
        y_true_dict['ret'],
        y_pred_dict['ret'],
        name='returns'
    )
    all_metrics.update(regression_ret)

    # C. Regression Metrics for Volatility (3 KPIs)
    regression_rv = compute_regression_metrics(
        y_true_dict['rv'],
        y_pred_dict['rv'],
        name='volatility'
    )
    all_metrics.update(regression_rv)

    # D. Distribution Analysis (4 KPIs)
    errors_ret = (y_pred_dict['ret'] - y_true_dict['ret']).flatten()
    distribution_metrics = compute_distribution_metrics(errors_ret)
    all_metrics.update(distribution_metrics)

    # E. Horizon-Specific Metrics (2 × H KPIs)
    horizon_metrics = compute_horizon_metrics(
        y_true_dict['ret'],
        y_pred_dict['ret'],
        y_true_dict['dir'],
        y_pred_dict['dir']
    )
    all_metrics.update(horizon_metrics)

    return all_metrics


def print_metrics_summary(metrics: Dict[str, Any], title: str = "METRICS SUMMARY") -> None:
    """
    Print formatted metrics summary
    """
    print(f"\n{'='*80}")
    print(f"{title:^80}")
    print(f"{'='*80}")

    # Trading Performance
    print("\nTrading Performance:")
    print(f"  Sharpe Ratio:        {metrics.get('sharpe_ratio', 0):.4f}")
    print(f"  Sortino Ratio:       {metrics.get('sortino_ratio', 0):.4f}")
    print(f"  Max Drawdown:        {metrics.get('max_drawdown', 0):.2%}")
    print(f"  Calmar Ratio:        {metrics.get('calmar_ratio', 0):.4f}")
    print(f"  Win Rate:            {metrics.get('win_rate', 0):.2%}")
    print(f"  Profit Factor:       {metrics.get('profit_factor', 0):.4f}")
    print(f"  Avg Win/Loss Ratio:  {metrics.get('avg_win_loss_ratio', 0):.4f}")
    print(f"  Annualized Return:   {metrics.get('annualized_return', 0):.4%}")
    print(f"  Volatility (annual): {metrics.get('volatility', 0):.4%}")

    # Classification
    print("\nClassification:")
    print(f"  Accuracy:            {metrics.get('accuracy', 0):.2%}")
    print(f"  Macro F1:            {metrics.get('macro_f1', 0):.4f}")
    print(f"  Weighted F1:         {metrics.get('weighted_f1', 0):.4f}")
    print(f"  Cohen's Kappa:       {metrics.get('cohens_kappa', 0):.4f}")

    # Regression
    print("\nRegression:")
    print(f"  MAE Returns:         {metrics.get('mae_returns', 0):.6f}")
    print(f"  R² Returns:          {metrics.get('r2_returns', 0):.4f}")
    print(f"  MAE Volatility:      {metrics.get('mae_volatility', 0):.6f}")
    print(f"  R² Volatility:       {metrics.get('r2_volatility', 0):.4f}")

    # Distribution
    print("\nDistribution Analysis:")
    print(f"  Prediction Bias:     {metrics.get('prediction_bias', 0):.6f}")
    print(f"  Error Skewness:      {metrics.get('error_skewness', 0):.4f}")
    print(f"  Error Kurtosis:      {metrics.get('error_kurtosis', 0):.4f}")
    print(f"  95th Percentile:     {metrics.get('error_95th_percentile', 0):.6f}")

    print(f"{'='*80}\n")


if __name__ == "__main__":
    # Test des métriques
    print("Testing Advanced Metrics...")

    # Simulate some predictions
    np.random.seed(42)
    N = 1000
    H = 12

    # Returns
    y_true_ret = np.random.randn(N, H) * 0.001
    y_pred_ret = y_true_ret + np.random.randn(N, H) * 0.0005

    # Direction
    y_true_dir = np.random.randint(0, 3, N)
    y_pred_dir_probs = np.random.dirichlet([1, 1, 1], N)

    # Volatility
    y_true_rv = np.abs(np.random.randn(N, H)) * 0.01
    y_pred_rv = y_true_rv + np.random.randn(N, H) * 0.002

    # Compute all metrics
    y_true_dict = {'ret': y_true_ret, 'dir': y_true_dir, 'rv': y_true_rv}
    y_pred_dict = {'ret': y_pred_ret, 'dir': y_pred_dir_probs, 'rv': y_pred_rv}

    metrics = compute_all_metrics(y_true_dict, y_pred_dict)

    # Print summary
    print_metrics_summary(metrics, "TEST METRICS")

    print(f"\nTotal KPIs computed: {len(metrics)}")
    print("Metrics keys:", list(metrics.keys()))
