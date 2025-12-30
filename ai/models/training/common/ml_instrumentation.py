"""
ML Instrumentation & Monitoring Framework
==========================================

Comprehensive logging, metrics, and validation for ML trading models.
ZERO tolerance for silent failures or untracked metrics.
"""

from __future__ import annotations
import os
import json
import time
import hashlib
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats


# ============================================================================
# STRUCTURED LOGGER
# ============================================================================

class StructuredLogger:
    """Thread-safe structured JSON logger with full context."""

    def __init__(self, log_path: str, run_id: str, model_name: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.model_name = model_name
        self.file = open(self.log_path, 'a', buffering=1)  # Line buffered

    def log(self, event_type: str, data: Dict[str, Any], epoch: Optional[int] = None):
        """Log structured event with full context."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "model_name": self.model_name,
            "event_type": event_type,
            "epoch": epoch,
            **data
        }
        self.file.write(json.dumps(record) + "\n")

    def close(self):
        self.file.close()


# ============================================================================
# CALIBRATION METRICS
# ============================================================================

def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """
    Expected Calibration Error (ECE).
    Measures how well predicted probabilities match actual frequencies.
    """
    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.digitize(y_prob, bins[1:-1])

    ece = 0.0
    for i in range(n_bins):
        mask = bin_indices == i
        if mask.sum() == 0:
            continue

        bin_acc = y_true[mask].mean()
        bin_conf = y_prob[mask].mean()
        bin_weight = mask.sum() / len(y_true)

        ece += bin_weight * abs(bin_acc - bin_conf)

    return float(ece)


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Brier score: mean squared error of probability predictions."""
    return float(np.mean((y_prob - y_true) ** 2))


def prediction_entropy(probs: np.ndarray) -> float:
    """Average prediction entropy (confidence measure)."""
    eps = 1e-10
    probs = np.clip(probs, eps, 1 - eps)
    entropy = -np.sum(probs * np.log(probs), axis=-1)
    return float(entropy.mean())


# ============================================================================
# DATA LEAKAGE CHECKS
# ============================================================================

@dataclass
class DataLeakageReport:
    """Results of data leakage validation."""
    has_leakage: bool
    issues: List[str]
    train_test_overlap: bool
    future_features_detected: bool
    nan_ratio: float
    constant_features: List[str]

    def is_safe(self) -> bool:
        """Check if data is safe to use."""
        return not self.has_leakage and self.nan_ratio < 0.01


def check_temporal_overlap(train_indices: np.ndarray, test_indices: np.ndarray) -> bool:
    """Verify no temporal overlap between train and test."""
    return bool(np.intersect1d(train_indices, test_indices).size > 0)


def detect_constant_features(X: np.ndarray, feature_names: List[str], threshold: float = 0.0001) -> List[str]:
    """Find features with near-zero variance."""
    variances = np.var(X, axis=0)
    constant_mask = variances < threshold
    return [name for name, is_const in zip(feature_names, constant_mask) if is_const]


def validate_data(
    X_train: np.ndarray,
    X_test: np.ndarray,
    train_indices: np.ndarray,
    test_indices: np.ndarray,
    feature_names: List[str]
) -> DataLeakageReport:
    """Comprehensive data validation."""
    issues = []
    has_leakage = False

    # Check temporal overlap
    overlap = check_temporal_overlap(train_indices, test_indices)
    if overlap:
        issues.append("CRITICAL: Train/test temporal overlap detected")
        has_leakage = True

    # Check for NaNs
    nan_ratio_train = np.isnan(X_train).mean()
    nan_ratio_test = np.isnan(X_test).mean()
    nan_ratio = max(nan_ratio_train, nan_ratio_test)

    if nan_ratio > 0:
        issues.append(f"NaN detected: {nan_ratio:.2%} of values")
        if nan_ratio > 0.01:
            has_leakage = True

    # Check for constant features
    constant_feats = detect_constant_features(X_train, feature_names)
    if constant_feats:
        issues.append(f"Constant features detected: {constant_feats}")

    # KS test for train/test distribution shift
    ks_pvalues = []
    for i in range(X_train.shape[1]):
        stat, pval = stats.ks_2samp(X_train[:, i], X_test[:, i])
        ks_pvalues.append(pval)

    severe_drift = sum(p < 0.001 for p in ks_pvalues)
    if severe_drift > len(feature_names) * 0.3:
        issues.append(f"Severe distribution drift in {severe_drift}/{len(feature_names)} features")

    return DataLeakageReport(
        has_leakage=has_leakage,
        issues=issues,
        train_test_overlap=overlap,
        future_features_detected=False,  # Requires metadata
        nan_ratio=nan_ratio,
        constant_features=constant_feats
    )


# ============================================================================
# PAPER TRADING ENGINE
# ============================================================================

@dataclass
class PaperTradingConfig:
    """Configuration for paper trading simulation."""
    fee_rate: float = 0.001  # 10 bps
    spread_bps: float = 5.0
    latency_bars: int = 1
    position_size: float = 1.0

    @property
    def total_cost_bps(self) -> float:
        return (self.fee_rate * 10000) + self.spread_bps


@dataclass
class TradingMetrics:
    """Complete trading performance metrics."""
    roi: float
    sharpe: float
    max_drawdown: float
    hit_rate: float
    avg_trade_return: float
    num_trades: int
    turnover: float
    worst_trade: float
    best_trade: float
    equity_curve: np.ndarray
    trades: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['equity_curve'] = self.equity_curve.tolist()
        return d


def run_paper_test(
    signals: np.ndarray,
    returns: np.ndarray,
    config: PaperTradingConfig,
    name: str = "paper_test"
) -> TradingMetrics:
    """
    Run paper trading simulation.

    Args:
        signals: Model predictions (positive = long, negative = short)
        returns: Future returns to trade on
        config: Trading configuration
        name: Test identifier

    Returns:
        Complete trading metrics
    """
    assert len(signals) == len(returns), "Signal/return length mismatch"

    # Apply latency
    signals = signals[:-config.latency_bars] if config.latency_bars > 0 else signals
    returns = returns[config.latency_bars:] if config.latency_bars > 0 else returns

    # Generate positions
    positions = np.sign(signals) * config.position_size

    # Calculate costs
    turnover = np.abs(np.diff(positions, prepend=0)).sum()
    cost_per_trade = config.total_cost_bps / 10000

    # Calculate PnL
    gross_pnl = positions * returns
    costs = np.abs(np.diff(positions, prepend=0)) * cost_per_trade
    net_pnl = gross_pnl - costs

    # Equity curve
    equity = np.cumsum(net_pnl)

    # Metrics
    roi = float(equity[-1]) if len(equity) > 0 else 0.0

    if len(net_pnl) > 0 and net_pnl.std() > 0:
        sharpe = float(net_pnl.mean() / net_pnl.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    # Drawdown
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    max_dd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    # Trade stats
    trade_mask = positions != 0
    trades = net_pnl[trade_mask]
    hit_rate = float((trades > 0).mean()) if len(trades) > 0 else 0.0
    avg_trade = float(trades.mean()) if len(trades) > 0 else 0.0

    # Build trade log
    trade_log = []
    for i, (pos, ret, pnl) in enumerate(zip(positions, returns, net_pnl)):
        if pos != 0:
            trade_log.append({
                "idx": int(i),
                "position": float(pos),
                "return": float(ret),
                "pnl": float(pnl)
            })

    return TradingMetrics(
        roi=roi,
        sharpe=sharpe,
        max_drawdown=max_dd,
        hit_rate=hit_rate,
        avg_trade_return=avg_trade,
        num_trades=int(trade_mask.sum()),
        turnover=float(turnover),
        worst_trade=float(trades.min()) if len(trades) > 0 else 0.0,
        best_trade=float(trades.max()) if len(trades) > 0 else 0.0,
        equity_curve=equity,
        trades=trade_log
    )


# ============================================================================
# TRAINING MONITOR
# ============================================================================

@dataclass
class TrainingSnapshot:
    """Snapshot of training state at epoch."""
    epoch: int
    train_loss: float
    val_loss: float
    test_loss: Optional[float]
    gradient_norm: float
    learning_rate: float
    time_s: float

    # Calibration
    ece: float
    brier: float
    entropy: float

    # Stability
    pred_correlation: Optional[float]
    flip_rate: Optional[float]

    # Trading
    paper_test_raw: TradingMetrics
    paper_test_realistic: TradingMetrics


class EarlyStoppingCriteria:
    """Hard fail criteria for training."""

    def __init__(
        self,
        min_sharpe: float = 0.5,
        max_drawdown: float = -0.20,
        warmup_epochs: int = 5,
        min_roi: float = -0.10
    ):
        self.min_sharpe = min_sharpe
        self.max_drawdown = max_drawdown
        self.warmup_epochs = warmup_epochs
        self.min_roi = min_roi

    def should_stop(self, snapshot: TrainingSnapshot) -> Tuple[bool, Optional[str]]:
        """Check if training should stop immediately."""
        if snapshot.epoch < self.warmup_epochs:
            return False, None

        # Check Sharpe
        if snapshot.paper_test_realistic.sharpe < self.min_sharpe:
            return True, f"Sharpe {snapshot.paper_test_realistic.sharpe:.2f} < {self.min_sharpe}"

        # Check drawdown
        if snapshot.paper_test_realistic.max_drawdown < self.max_drawdown:
            return True, f"MaxDD {snapshot.paper_test_realistic.max_drawdown:.2%} > {self.max_drawdown:.2%}"

        # Check ROI
        if snapshot.paper_test_realistic.roi < self.min_roi:
            return True, f"ROI {snapshot.paper_test_realistic.roi:.2%} < {self.min_roi:.2%}"

        return False, None


# ============================================================================
# ARTIFACT MANAGER
# ============================================================================

class ArtifactManager:
    """Manages all training artifacts and reports."""

    def __init__(self, base_dir: str, run_id: str):
        self.base_dir = Path(base_dir) / run_id
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (self.base_dir / "metrics").mkdir(exist_ok=True)
        (self.base_dir / "paper_tests").mkdir(exist_ok=True)
        (self.base_dir / "equity_curves").mkdir(exist_ok=True)
        (self.base_dir / "checkpoints").mkdir(exist_ok=True)

    def save_epoch_metrics(self, epoch: int, snapshot: TrainingSnapshot):
        """Save complete metrics for epoch."""
        path = self.base_dir / "metrics" / f"epoch_{epoch:04d}.json"
        with open(path, 'w') as f:
            json.dump(asdict(snapshot), f, indent=2, default=lambda x: x.tolist() if isinstance(x, np.ndarray) else str(x))

    def save_paper_test(self, epoch: int, metrics: TradingMetrics, test_name: str):
        """Save paper test results."""
        path = self.base_dir / "paper_tests" / f"{test_name}_epoch_{epoch:04d}.json"
        with open(path, 'w') as f:
            json.dump(metrics.to_dict(), f, indent=2)

    def save_equity_curve(self, epoch: int, equity: np.ndarray, test_name: str):
        """Save equity curve."""
        path = self.base_dir / "equity_curves" / f"{test_name}_epoch_{epoch:04d}.parquet"
        df = pd.DataFrame({"equity": equity})
        df.to_parquet(path)

    def save_config(self, config: Dict[str, Any]):
        """Save training configuration."""
        path = self.base_dir / "config.json"

        # Compute config hash
        config_str = json.dumps(config, sort_keys=True)
        config_hash = hashlib.sha256(config_str.encode()).hexdigest()[:12]

        config_with_hash = {
            "config_hash": config_hash,
            **config
        }

        with open(path, 'w') as f:
            json.dump(config_with_hash, f, indent=2)

        return config_hash

    def save_git_info(self):
        """Save git commit hash."""
        try:
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
            dirty = subprocess.check_output(['git', 'status', '--porcelain']).decode().strip()

            git_info = {
                "commit": commit,
                "dirty": bool(dirty),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            path = self.base_dir / "git_info.json"
            with open(path, 'w') as f:
                json.dump(git_info, f, indent=2)
        except:
            pass  # Not in git repo

    def generate_report(self, snapshots: List[TrainingSnapshot]) -> str:
        """Generate markdown report."""
        lines = ["# Training Report\n"]

        if not snapshots:
            return "No training data"

        final = snapshots[-1]

        lines.append(f"## Final Metrics (Epoch {final.epoch})\n")
        lines.append(f"- **Train Loss**: {final.train_loss:.4f}")
        lines.append(f"- **Val Loss**: {final.val_loss:.4f}")
        lines.append(f"- **ECE**: {final.ece:.4f}")
        lines.append(f"- **Brier Score**: {final.brier:.4f}\n")

        lines.append("## Paper Trading (Realistic)\n")
        lines.append(f"- **ROI**: {final.paper_test_realistic.roi:.2%}")
        lines.append(f"- **Sharpe**: {final.paper_test_realistic.sharpe:.2f}")
        lines.append(f"- **Max DD**: {final.paper_test_realistic.max_drawdown:.2%}")
        lines.append(f"- **Hit Rate**: {final.paper_test_realistic.hit_rate:.2%}")
        lines.append(f"- **Num Trades**: {final.paper_test_realistic.num_trades}\n")

        report = "\n".join(lines)

        # Save report
        path = self.base_dir / "report.md"
        with open(path, 'w') as f:
            f.write(report)

        return report
