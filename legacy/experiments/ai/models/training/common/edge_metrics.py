"""
Edge Forecaster - Complete Metrics Per Epoch
=============================================

IMPLEMENTS EXACTLY what was requested:

Training logs:
- loss_total, loss_q, loss_p_hit, loss_rv
- grad_norm, lr, step_time_ms, throughput_samples_s

Sanity checks:
- nan_ratio, q_monotonicity_rate
- pred_q50_mean/std, pred_p_hit_mean/std/min/max
- target_p_hit_rate

Validation metrics:
- MAE_q50, RMSE_q50, corr_q50_vs_return_fwd
- Brier_p_hit, AUC_p_hit, ECE_p_hit
- tail_violation_rate

Paper tests (2 mandatory):
- Test 1: directional simple (q50)
- Test 2: filtered by proba (p_hit)

Hard gates:
- ROI_net <= 0 for 3 consecutive epochs → STOP
- Sharpe < 0.1 after epoch 5 → STOP
- MaxDD > limit → STOP
- q_monotonicity_rate < 0.99 → STOP
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
import numpy as np
from sklearn.metrics import roc_auc_score, brier_score_loss
import time


@dataclass
class EdgeForecasterMetrics:
    """Complete metrics for one epoch."""

    # Training
    loss_total: float
    loss_q: float  # Quantile loss
    loss_p_hit: float  # BCE for hit probability
    loss_rv: float  # MSE for realized volatility
    grad_norm: float
    lr: float
    step_time_ms: float
    throughput_samples_s: float

    # Sanity checks
    nan_ratio: float
    q_monotonicity_rate: float  # fraction where q05 <= q50 <= q95
    pred_q50_mean: float
    pred_q50_std: float
    pred_p_hit_mean: float
    pred_p_hit_std: float
    pred_p_hit_min: float
    pred_p_hit_max: float
    target_p_hit_rate: float  # base rate in targets

    # Validation
    MAE_q50: float
    RMSE_q50: float
    corr_q50_vs_return_fwd: float
    Brier_p_hit: float
    AUC_p_hit: float
    ECE_p_hit: float
    tail_violation_rate: float  # fraction where return_fwd < q05

    # Paper test 1 (directional)
    paper1_roi_net: float
    paper1_sharpe: float
    paper1_max_dd: float
    paper1_hit_rate: float
    paper1_avg_trade_return: float
    paper1_turnover: float
    paper1_trades_per_day: float
    paper1_fees_paid: float
    paper1_exposure_time_pct: float

    # Paper test 2 (filtered)
    paper2_roi_net: float
    paper2_sharpe: float
    paper2_max_dd: float
    paper2_hit_rate: float
    paper2_avg_trade_return: float
    paper2_turnover: float
    paper2_trades_per_day: float
    paper2_fees_paid: float
    paper2_exposure_time_pct: float
    paper2_coverage: float  # fraction of time where p_hit > threshold

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            # Training
            "loss_total": float(self.loss_total),
            "loss_q": float(self.loss_q),
            "loss_p_hit": float(self.loss_p_hit),
            "loss_rv": float(self.loss_rv),
            "grad_norm": float(self.grad_norm),
            "lr": float(self.lr),
            "step_time_ms": float(self.step_time_ms),
            "throughput_samples_s": float(self.throughput_samples_s),

            # Sanity
            "nan_ratio": float(self.nan_ratio),
            "q_monotonicity_rate": float(self.q_monotonicity_rate),
            "pred_q50_mean": float(self.pred_q50_mean),
            "pred_q50_std": float(self.pred_q50_std),
            "pred_p_hit_mean": float(self.pred_p_hit_mean),
            "pred_p_hit_std": float(self.pred_p_hit_std),
            "pred_p_hit_min": float(self.pred_p_hit_min),
            "pred_p_hit_max": float(self.pred_p_hit_max),
            "target_p_hit_rate": float(self.target_p_hit_rate),

            # Validation
            "MAE_q50": float(self.MAE_q50),
            "RMSE_q50": float(self.RMSE_q50),
            "corr_q50_vs_return_fwd": float(self.corr_q50_vs_return_fwd),
            "Brier_p_hit": float(self.Brier_p_hit),
            "AUC_p_hit": float(self.AUC_p_hit),
            "ECE_p_hit": float(self.ECE_p_hit),
            "tail_violation_rate": float(self.tail_violation_rate),

            # Paper 1
            "paper1_roi_net": float(self.paper1_roi_net),
            "paper1_sharpe": float(self.paper1_sharpe),
            "paper1_max_dd": float(self.paper1_max_dd),
            "paper1_hit_rate": float(self.paper1_hit_rate),
            "paper1_avg_trade_return": float(self.paper1_avg_trade_return),
            "paper1_turnover": float(self.paper1_turnover),
            "paper1_trades_per_day": float(self.paper1_trades_per_day),
            "paper1_fees_paid": float(self.paper1_fees_paid),
            "paper1_exposure_time_pct": float(self.paper1_exposure_time_pct),

            # Paper 2
            "paper2_roi_net": float(self.paper2_roi_net),
            "paper2_sharpe": float(self.paper2_sharpe),
            "paper2_max_dd": float(self.paper2_max_dd),
            "paper2_hit_rate": float(self.paper2_hit_rate),
            "paper2_avg_trade_return": float(self.paper2_avg_trade_return),
            "paper2_turnover": float(self.paper2_turnover),
            "paper2_trades_per_day": float(self.paper2_trades_per_day),
            "paper2_fees_paid": float(self.paper2_fees_paid),
            "paper2_exposure_time_pct": float(self.paper2_exposure_time_pct),
            "paper2_coverage": float(self.paper2_coverage),
        }


def compute_quantile_monotonicity(q05: np.ndarray, q50: np.ndarray, q95: np.ndarray) -> float:
    """
    Compute fraction of samples where q05 <= q50 <= q95.

    CRITICAL GATE: Must be >= 0.99 for valid quantile predictions.
    """
    valid_1 = q05 <= q50
    valid_2 = q50 <= q95
    valid = valid_1 & valid_2

    monotonicity_rate = float(valid.mean())
    return monotonicity_rate


def compute_tail_violation_rate(return_fwd: np.ndarray, q05: np.ndarray) -> float:
    """
    Compute fraction of samples where actual return < q05.

    Should be ~5% if calibrated (since q05 is 5th percentile).
    """
    violations = return_fwd < q05
    violation_rate = float(violations.mean())
    return violation_rate


def compute_ece_binary(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """
    Expected Calibration Error for binary classification (p_hit).
    """
    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.digitize(y_prob, bins[1:-1])

    ece = 0.0
    for i in range(n_bins):
        mask = bin_indices == i
        if mask.sum() == 0:
            continue

        bin_accuracy = float(y_true[mask].mean())
        bin_confidence = float(y_prob[mask].mean())
        bin_weight = mask.sum() / len(y_true)

        ece += bin_weight * abs(bin_accuracy - bin_confidence)

    return float(ece)


def run_paper_test_directional(
    q50: np.ndarray,
    return_fwd: np.ndarray,
    threshold: float = 0.0002,
    fee_rate: float = 0.001,
    spread_bps: float = 5.0
) -> Dict[str, float]:
    """
    Paper Test 1: Directional simple (q50).

    Rules:
    - long if q50 > +threshold
    - short if q50 < -threshold
    - flat otherwise

    Returns metrics dict.
    """
    # Generate signals
    signals = np.zeros_like(q50)
    signals[q50 > threshold] = 1.0  # Long
    signals[q50 < -threshold] = -1.0  # Short

    # Compute PnL
    pnl = signals * return_fwd

    # Apply costs
    position_changes = np.abs(np.diff(signals, prepend=0))
    turnover = position_changes.sum()
    fees = turnover * fee_rate
    spread_cost = turnover * (spread_bps / 10000.0)
    total_costs = fees + spread_cost

    pnl_net = pnl - (fees + spread_cost) / len(pnl)  # Distribute costs

    # Metrics
    roi_net = float(pnl_net.sum())
    sharpe = float(pnl_net.mean() / (pnl_net.std() + 1e-8) * np.sqrt(252 * 24 * 60))  # Annualized

    # Max drawdown
    cumulative = np.cumsum(pnl_net)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    max_dd = float(drawdown.min())

    # Hit rate
    trades = pnl[signals != 0]
    hit_rate = float((trades > 0).mean()) if len(trades) > 0 else 0.0

    # Other metrics
    avg_trade_return = float(trades.mean()) if len(trades) > 0 else 0.0
    trades_per_day = len(trades) / (len(pnl) / (24 * 60))  # Assuming 1min bars
    exposure_time_pct = float((signals != 0).mean())

    return {
        "roi_net": roi_net,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "hit_rate": hit_rate,
        "avg_trade_return": avg_trade_return,
        "turnover": float(turnover),
        "trades_per_day": trades_per_day,
        "fees_paid": fees,
        "exposure_time_pct": exposure_time_pct
    }


def run_paper_test_filtered(
    q50: np.ndarray,
    p_hit: np.ndarray,
    return_fwd: np.ndarray,
    p_threshold: float = 0.55,
    fee_rate: float = 0.001,
    spread_bps: float = 5.0
) -> Dict[str, float]:
    """
    Paper Test 2: Filtered by probability (p_hit).

    Rules:
    - long if p_hit > p_threshold AND q50 > 0
    - short if p_hit > p_threshold AND q50 < 0
    - flat otherwise

    Returns metrics dict + coverage.
    """
    # Generate signals
    signals = np.zeros_like(q50)

    long_mask = (p_hit > p_threshold) & (q50 > 0)
    short_mask = (p_hit > p_threshold) & (q50 < 0)

    signals[long_mask] = 1.0
    signals[short_mask] = -1.0

    # Coverage
    coverage = float((signals != 0).mean())

    # Compute PnL
    pnl = signals * return_fwd

    # Apply costs
    position_changes = np.abs(np.diff(signals, prepend=0))
    turnover = position_changes.sum()
    fees = turnover * fee_rate
    spread_cost = turnover * (spread_bps / 10000.0)

    pnl_net = pnl - (fees + spread_cost) / len(pnl)

    # Metrics
    roi_net = float(pnl_net.sum())
    sharpe = float(pnl_net.mean() / (pnl_net.std() + 1e-8) * np.sqrt(252 * 24 * 60))

    cumulative = np.cumsum(pnl_net)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    max_dd = float(drawdown.min())

    trades = pnl[signals != 0]
    hit_rate = float((trades > 0).mean()) if len(trades) > 0 else 0.0
    avg_trade_return = float(trades.mean()) if len(trades) > 0 else 0.0
    trades_per_day = len(trades) / (len(pnl) / (24 * 60))
    exposure_time_pct = float((signals != 0).mean())

    return {
        "roi_net": roi_net,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "hit_rate": hit_rate,
        "avg_trade_return": avg_trade_return,
        "turnover": float(turnover),
        "trades_per_day": trades_per_day,
        "fees_paid": fees,
        "exposure_time_pct": exposure_time_pct,
        "coverage": coverage
    }


def compute_edge_forecaster_metrics(
    # Predictions
    q05_pred: np.ndarray,
    q50_pred: np.ndarray,
    q95_pred: np.ndarray,
    p_hit_pred: np.ndarray,
    rv_pred: np.ndarray,

    # Targets
    return_fwd: np.ndarray,
    hit_fwd: np.ndarray,  # Binary: did we hit TP before SL?
    rv_fwd: np.ndarray,

    # Training stats
    loss_total: float,
    loss_q: float,
    loss_p_hit: float,
    loss_rv: float,
    grad_norm: float,
    lr: float,
    step_time_ms: float,
    throughput_samples_s: float

) -> EdgeForecasterMetrics:
    """
    Compute ALL metrics for one epoch.

    Returns EdgeForecasterMetrics dataclass.
    """

    # ========================================================================
    # SANITY CHECKS
    # ========================================================================
    nan_ratio = float(np.isnan(q50_pred).mean())

    q_monotonicity_rate = compute_quantile_monotonicity(q05_pred, q50_pred, q95_pred)

    pred_q50_mean = float(q50_pred.mean())
    pred_q50_std = float(q50_pred.std())

    pred_p_hit_mean = float(p_hit_pred.mean())
    pred_p_hit_std = float(p_hit_pred.std())
    pred_p_hit_min = float(p_hit_pred.min())
    pred_p_hit_max = float(p_hit_pred.max())

    target_p_hit_rate = float(hit_fwd.mean())

    # ========================================================================
    # VALIDATION METRICS
    # ========================================================================
    MAE_q50 = float(np.abs(q50_pred - return_fwd).mean())
    RMSE_q50 = float(np.sqrt(((q50_pred - return_fwd) ** 2).mean()))

    corr_q50_vs_return_fwd = float(np.corrcoef(q50_pred, return_fwd)[0, 1])

    Brier_p_hit = float(brier_score_loss(hit_fwd, p_hit_pred))
    AUC_p_hit = float(roc_auc_score(hit_fwd, p_hit_pred))
    ECE_p_hit = compute_ece_binary(hit_fwd, p_hit_pred)

    tail_violation_rate = compute_tail_violation_rate(return_fwd, q05_pred)

    # ========================================================================
    # PAPER TEST 1: DIRECTIONAL
    # ========================================================================
    paper1 = run_paper_test_directional(q50_pred, return_fwd)

    # ========================================================================
    # PAPER TEST 2: FILTERED
    # ========================================================================
    paper2 = run_paper_test_filtered(q50_pred, p_hit_pred, return_fwd)

    # ========================================================================
    # RETURN METRICS
    # ========================================================================
    return EdgeForecasterMetrics(
        # Training
        loss_total=loss_total,
        loss_q=loss_q,
        loss_p_hit=loss_p_hit,
        loss_rv=loss_rv,
        grad_norm=grad_norm,
        lr=lr,
        step_time_ms=step_time_ms,
        throughput_samples_s=throughput_samples_s,

        # Sanity
        nan_ratio=nan_ratio,
        q_monotonicity_rate=q_monotonicity_rate,
        pred_q50_mean=pred_q50_mean,
        pred_q50_std=pred_q50_std,
        pred_p_hit_mean=pred_p_hit_mean,
        pred_p_hit_std=pred_p_hit_std,
        pred_p_hit_min=pred_p_hit_min,
        pred_p_hit_max=pred_p_hit_max,
        target_p_hit_rate=target_p_hit_rate,

        # Validation
        MAE_q50=MAE_q50,
        RMSE_q50=RMSE_q50,
        corr_q50_vs_return_fwd=corr_q50_vs_return_fwd,
        Brier_p_hit=Brier_p_hit,
        AUC_p_hit=AUC_p_hit,
        ECE_p_hit=ECE_p_hit,
        tail_violation_rate=tail_violation_rate,

        # Paper 1
        paper1_roi_net=paper1["roi_net"],
        paper1_sharpe=paper1["sharpe"],
        paper1_max_dd=paper1["max_dd"],
        paper1_hit_rate=paper1["hit_rate"],
        paper1_avg_trade_return=paper1["avg_trade_return"],
        paper1_turnover=paper1["turnover"],
        paper1_trades_per_day=paper1["trades_per_day"],
        paper1_fees_paid=paper1["fees_paid"],
        paper1_exposure_time_pct=paper1["exposure_time_pct"],

        # Paper 2
        paper2_roi_net=paper2["roi_net"],
        paper2_sharpe=paper2["sharpe"],
        paper2_max_dd=paper2["max_dd"],
        paper2_hit_rate=paper2["hit_rate"],
        paper2_avg_trade_return=paper2["avg_trade_return"],
        paper2_turnover=paper2["turnover"],
        paper2_trades_per_day=paper2["trades_per_day"],
        paper2_fees_paid=paper2["fees_paid"],
        paper2_exposure_time_pct=paper2["exposure_time_pct"],
        paper2_coverage=paper2["coverage"],
    )


def print_edge_metrics_report(metrics: EdgeForecasterMetrics, epoch: int):
    """Print human-readable report for one epoch."""
    print(f"\n{'='*80}")
    print(f"EDGE FORECASTER - EPOCH {epoch}")
    print(f"{'='*80}")

    print(f"\n📊 Training:")
    print(f"  Loss Total: {metrics.loss_total:.6f}")
    print(f"  Loss Q:     {metrics.loss_q:.6f}")
    print(f"  Loss P_hit: {metrics.loss_p_hit:.6f}")
    print(f"  Loss RV:    {metrics.loss_rv:.6f}")
    print(f"  Grad Norm:  {metrics.grad_norm:.4f}")
    print(f"  LR:         {metrics.lr:.2e}")
    print(f"  Throughput: {metrics.throughput_samples_s:.1f} samples/s")

    print(f"\n🔍 Sanity Checks:")
    mono_status = "✅" if metrics.q_monotonicity_rate >= 0.99 else "❌"
    print(f"  {mono_status} Monotonicity: {metrics.q_monotonicity_rate:.4f}")
    print(f"  NaN Ratio:        {metrics.nan_ratio:.4f}")
    print(f"  Q50 Mean/Std:     {metrics.pred_q50_mean:.6f} / {metrics.pred_q50_std:.6f}")
    print(f"  P_hit Mean/Std:   {metrics.pred_p_hit_mean:.4f} / {metrics.pred_p_hit_std:.4f}")
    print(f"  P_hit Min/Max:    {metrics.pred_p_hit_min:.4f} / {metrics.pred_p_hit_max:.4f}")
    print(f"  Target Hit Rate:  {metrics.target_p_hit_rate:.4f}")

    print(f"\n📈 Validation:")
    print(f"  MAE Q50:     {metrics.MAE_q50:.6f}")
    print(f"  RMSE Q50:    {metrics.RMSE_q50:.6f}")
    print(f"  Corr Q50:    {metrics.corr_q50_vs_return_fwd:.4f}")
    print(f"  Brier P_hit: {metrics.Brier_p_hit:.4f}")
    print(f"  AUC P_hit:   {metrics.AUC_p_hit:.4f}")
    print(f"  ECE P_hit:   {metrics.ECE_p_hit:.4f}")
    print(f"  Tail Viol:   {metrics.tail_violation_rate:.4f} (should be ~0.05)")

    print(f"\n💰 Paper Test 1 (Directional):")
    print(f"  ROI Net:     {metrics.paper1_roi_net:>8.2%}")
    print(f"  Sharpe:      {metrics.paper1_sharpe:>8.2f}")
    print(f"  Max DD:      {metrics.paper1_max_dd:>8.2%}")
    print(f"  Hit Rate:    {metrics.paper1_hit_rate:>8.2%}")
    print(f"  Avg Trade:   {metrics.paper1_avg_trade_return:>8.4%}")
    print(f"  Trades/Day:  {metrics.paper1_trades_per_day:>8.1f}")
    print(f"  Fees Paid:   {metrics.paper1_fees_paid:>8.4%}")
    print(f"  Exposure:    {metrics.paper1_exposure_time_pct:>8.2%}")

    print(f"\n💎 Paper Test 2 (Filtered p_hit > 0.55):")
    print(f"  ROI Net:     {metrics.paper2_roi_net:>8.2%}")
    print(f"  Sharpe:      {metrics.paper2_sharpe:>8.2f}")
    print(f"  Max DD:      {metrics.paper2_max_dd:>8.2%}")
    print(f"  Hit Rate:    {metrics.paper2_hit_rate:>8.2%}")
    print(f"  Coverage:    {metrics.paper2_coverage:>8.2%}")
    print(f"  Trades/Day:  {metrics.paper2_trades_per_day:>8.1f}")

    print(f"\n{'='*80}\n")


# ============================================================================
# HARD GATES
# ============================================================================

class EdgeHardGates:
    """
    Hard gates for Edge Forecaster.

    STOP training if:
    - ROI_net <= 0 for 3 consecutive epochs
    - Sharpe < 0.1 after epoch 5
    - MaxDD > dd_limit
    - q_monotonicity_rate < 0.99
    """

    def __init__(
        self,
        min_sharpe_after_epoch: int = 5,
        min_sharpe: float = 0.1,
        max_dd_limit: float = -0.30,
        min_monotonicity: float = 0.99,
        max_consecutive_negative_roi: int = 3
    ):
        self.min_sharpe_after_epoch = min_sharpe_after_epoch
        self.min_sharpe = min_sharpe
        self.max_dd_limit = max_dd_limit
        self.min_monotonicity = min_monotonicity
        self.max_consecutive_negative_roi = max_consecutive_negative_roi

        self.consecutive_negative_roi_count = 0

    def check(self, epoch: int, metrics: EdgeForecasterMetrics) -> tuple[bool, str]:
        """
        Check gates. Returns (should_stop, reason).
        """
        # Gate 1: Monotonicity
        if metrics.q_monotonicity_rate < self.min_monotonicity:
            return True, f"Monotonicity {metrics.q_monotonicity_rate:.4f} < {self.min_monotonicity} - QUANTILES BROKEN"

        # Gate 2: Consecutive negative ROI
        if metrics.paper1_roi_net <= 0:
            self.consecutive_negative_roi_count += 1
        else:
            self.consecutive_negative_roi_count = 0

        if self.consecutive_negative_roi_count >= self.max_consecutive_negative_roi:
            return True, f"ROI <= 0 for {self.consecutive_negative_roi_count} consecutive epochs - NO EDGE"

        # Gate 3: Sharpe after warmup
        if epoch >= self.min_sharpe_after_epoch:
            if metrics.paper1_sharpe < self.min_sharpe:
                return True, f"Sharpe {metrics.paper1_sharpe:.2f} < {self.min_sharpe} after epoch {epoch}"

        # Gate 4: Max DD
        if metrics.paper1_max_dd < self.max_dd_limit:
            return True, f"Max DD {metrics.paper1_max_dd:.2%} < {self.max_dd_limit:.2%} - RISK TOO HIGH"

        return False, ""


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Synthetic data
    np.random.seed(42)
    n_samples = 10000

    q05_pred = np.random.randn(n_samples) * 0.005 - 0.002
    q50_pred = np.random.randn(n_samples) * 0.005
    q95_pred = np.random.randn(n_samples) * 0.005 + 0.002
    p_hit_pred = np.random.rand(n_samples)
    rv_pred = np.abs(np.random.randn(n_samples)) * 0.01

    return_fwd = np.random.randn(n_samples) * 0.01
    hit_fwd = (np.random.rand(n_samples) > 0.5).astype(float)
    rv_fwd = np.abs(np.random.randn(n_samples)) * 0.01

    # Compute metrics
    metrics = compute_edge_forecaster_metrics(
        q05_pred=q05_pred,
        q50_pred=q50_pred,
        q95_pred=q95_pred,
        p_hit_pred=p_hit_pred,
        rv_pred=rv_pred,
        return_fwd=return_fwd,
        hit_fwd=hit_fwd,
        rv_fwd=rv_fwd,
        loss_total=0.00234,
        loss_q=0.00123,
        loss_p_hit=0.00089,
        loss_rv=0.00022,
        grad_norm=1.234,
        lr=3e-4,
        step_time_ms=12.34,
        throughput_samples_s=5123.4
    )

    # Print report
    print_edge_metrics_report(metrics, epoch=10)

    # Check gates
    gates = EdgeHardGates()
    should_stop, reason = gates.check(epoch=10, metrics=metrics)

    if should_stop:
        print(f"🛑 STOP: {reason}")
    else:
        print(f"✅ Gates passed")
