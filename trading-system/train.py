#!/usr/bin/env python3
"""
UNIFIED PRODUCTION TRAINER (HARDENED)
ADAPTED VERSION:
- Loads PROCESSED FEATURES directly from S3 parquets (no compute_features)
- Enforces the 39 model feature columns
- Prints:
    1) columns loaded
    2) the 39 required feature columns
- Audit now works on the processed dataframe (datetime index enforced)
- No-lookahead proof removed (not applicable when loading already-engineered features)
  (If you still want it, it must be done at dataset generation time, not here.)
"""

import sys
import json
import time
import random
import argparse
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from dataclasses import asdict, is_dataclass

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

# Local imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from pipeline.models.edge.forecaster import EdgeForecasterModel
from pipeline.models.edge.net import EdgeForecasterConfig, EdgeForecasterNet
from pipeline.models.edge.calibrator import BinaryCalibrator
from pipeline.models.edge.artifacts import save_artifact
from pipeline.models.regime.classifier import RegimeClassifierModel
from training_config import UnifiedTrainingConfig
from common.backtest.pnl_calculator import backtest_strategy_minute


# ============================================================================
# LOGGING
# ============================================================================
def setup_logger(level: str = "INFO"):
    import logging
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


logger = setup_logger()


# ============================================================================
# SEEDING
# ============================================================================
def seed_everything(seed: int, deterministic: bool = False):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        torch.use_deterministic_algorithms(True)


# ============================================================================
# CONFIG MERGE
# ============================================================================
def _is_mapping(x) -> bool:
    return isinstance(x, dict)


def _deep_update(target: Any, patch: Any, path: str = "") -> Any:
    if patch is None:
        return target

    if is_dataclass(target):
        for k, v in patch.items():
            if not hasattr(target, k):
                raise KeyError(f"Unknown config field: {path + k}")
            cur = getattr(target, k)
            if _is_mapping(v) and (is_dataclass(cur) or isinstance(cur, dict)):
                _deep_update(cur, v, path + k + ".")
            else:
                setattr(target, k, v)
        return target

    if isinstance(target, dict):
        for k, v in patch.items():
            if k in target and _is_mapping(v) and _is_mapping(target[k]):
                _deep_update(target[k], v, path + k + ".")
            else:
                target[k] = v
        return target

    for k, v in patch.items():
        if not hasattr(target, k):
            raise KeyError(f"Unknown config field: {path + k}")
        cur = getattr(target, k)
        if _is_mapping(v) and (is_dataclass(cur) or isinstance(cur, dict)):
            _deep_update(cur, v, path + k + ".")
        else:
            setattr(target, k, v)
    return target


def load_and_merge_config(cfg: UnifiedTrainingConfig, config_path: str) -> UnifiedTrainingConfig:
    with open(config_path, "r") as f:
        cfg_patch = json.load(f)
    _deep_update(cfg, cfg_patch)
    logger.info(f"Loaded + merged config from {config_path}")
    return cfg


# ============================================================================
# MODEL FEATURES (39)
# ============================================================================
def get_model_feature_columns_39() -> List[str]:
    return [
        # OHLCV
        "open", "high", "low", "close", "volume",
        # Returns
        "returns_1", "returns_5", "returns_10", "log_returns_1",
        # RV + RV_ANN
        "rv_5", "rv_15", "rv_30", "rv_60",
        "rv_5_ann", "rv_15_ann", "rv_30_ann", "rv_60_ann",
        # ATR + Volume + Regime
        "atr_14", "atr_20", "atr_pct_14", "atr_pct_20",
        "volume_ma_20", "volume_std_20",
        "vol_regime",
        # EMA 12/26/50
        "ema_12", "ema_12_slope", "ema_12_dist",
        "ema_26", "ema_26_slope", "ema_26_dist",
        "ema_50", "ema_50_slope", "ema_50_dist",
        # Momentum
        "rsi_14",
        # Other
        "high_low_range", "close_open_ret",
        "trend_regime",
        "month_sin", "month_cos",
    ]


# ============================================================================
# DUPLICATE COLUMNS GUARD
# ============================================================================
def assert_no_duplicate_columns(df: pd.DataFrame, context: str = "") -> None:
    """
    Ensures DataFrame has no duplicate column names.

    Args:
        df: DataFrame to check
        context: Description of where this check occurs (for error message)

    Raises:
        ValueError: If duplicate columns are found, with detailed message
    """
    if df is None or df.empty:
        return

    duplicates = df.columns[df.columns.duplicated()].unique().tolist()

    if duplicates:
        # Count occurrences of each duplicate
        dup_counts = {col: df.columns.tolist().count(col) for col in duplicates}
        error_msg = (
            f"DUPLICATE COLUMNS DETECTED {f'[{context}]' if context else ''}\n"
            f"  Location: {context}\n"
            f"  Duplicates found: {len(duplicates)} unique column(s)\n"
            f"  Details:\n"
        )
        for col, count in dup_counts.items():
            error_msg += f"    - '{col}': appears {count} times\n"
        error_msg += f"\n  This indicates a concat/merge bug. Check data pipeline logic."
        raise ValueError(error_msg)

    logger.debug(f"✓ No duplicate columns {f'[{context}]' if context else ''} ({len(df.columns)} cols)")


def print_columns(title: str, cols: List[str]):
    print("\n" + "=" * 90)
    print(title)
    print(f"{len(cols)} colonnes")
    for c in cols:
        print("-", c)
    print("=" * 90 + "\n")


# ============================================================================
# DATA HYGIENE
# ============================================================================
def enforce_numeric_feature_matrix(df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
    X = df[feature_cols].copy()
    X = X.replace([np.inf, -np.inf], np.nan)

    all_nan_cols = [c for c in feature_cols if X[c].isna().all()]
    if all_nan_cols:
        logger.warning(f"Dropping {len(all_nan_cols)} all-NaN feature cols")
        X = X.drop(columns=all_nan_cols)

    variances = X.var(skipna=True)
    zero_var_cols = variances[variances <= 1e-12].index.tolist()
    if zero_var_cols:
        logger.warning(f"Dropping {len(zero_var_cols)} near-zero-variance feature cols")
        X = X.drop(columns=zero_var_cols)

    return X


def drop_invalid_rows(df: pd.DataFrame, required_cols: List[str]) -> pd.DataFrame:
    before = len(df)
    df2 = df.copy()
    df2 = df2.replace([np.inf, -np.inf], np.nan)
    df2 = df2.dropna(subset=required_cols)
    after = len(df2)
    if after < before:
        logger.info(f"Dropped {before - after} rows due to NaN/Inf in {required_cols}")
    if after == 0:
        raise ValueError("All rows removed after NaN/Inf cleanup")
    return df2


def assert_monotonic_time_index(df: pd.DataFrame) -> None:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Expected df indexed by datetime (DatetimeIndex)")
    if df.index.tz is None:
        # allow naive but consistent; best is UTC tz-aware
        pass
    if not df.index.is_monotonic_increasing:
        raise ValueError("Datetime index is not monotonic increasing")
    if df.index.has_duplicates:
        raise ValueError("Datetime index contains duplicates")


# ============================================================================
# DATASET AUDIT (S3 COMPLETENESS + SANITY)
# ============================================================================
def audit_s3_dataset(
    df: pd.DataFrame,
    expected_bar_minutes: int = 1,
    max_gap_minutes: int = 5,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Audit on dataframe indexed by datetime.
    """
    if df is None or df.empty:
        raise ValueError("audit_s3_dataset: df is empty")

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("audit_s3_dataset: df must be indexed by datetime")

    dt = df.index
    if dt.isna().any():
        msg = "audit_s3_dataset: invalid timestamps in index"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)

    if not dt.is_monotonic_increasing:
        msg = "audit_s3_dataset: timestamps are not monotonic increasing"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)

    if dt.has_duplicates:
        msg = f"audit_s3_dataset: duplicated timestamps: {int(dt.duplicated(keep=False).sum())}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)

    # gaps
    deltas = dt.to_series().diff().dropna().dt.total_seconds().to_numpy()
    if len(deltas) == 0:
        raise ValueError("audit_s3_dataset: dataset has only 1 row")

    expected_delta_sec = expected_bar_minutes * 60
    median_delta_sec = int(np.median(deltas))

    gap_mask = deltas > expected_delta_sec
    gap_count = int(gap_mask.sum())

    major_gaps = []
    if gap_count > 0:
        idxs = np.where(gap_mask)[0]
        for i in idxs[:50]:
            # deltas array is aligned from second element; map back to dt indices
            start = dt[i]
            end = dt[i + 1]
            gap_min = int((end - start).total_seconds() // 60)
            if gap_min >= max_gap_minutes:
                major_gaps.append({"start": str(start), "end": str(end), "gap_minutes": gap_min})

    t0, t1 = dt[0], dt[-1]
    total_minutes_span = int((t1 - t0).total_seconds() // 60) + 1
    expected_bars = max(1, total_minutes_span // expected_bar_minutes)
    actual_bars = int(len(df))
    coverage_ratio = float(actual_bars / expected_bars) if expected_bars > 0 else 0.0

    report = {
        "rows": actual_bars,
        "start": str(t0),
        "end": str(t1),
        "span_minutes": int(total_minutes_span),
        "expected_bar_minutes": int(expected_bar_minutes),
        "expected_bars": int(expected_bars),
        "actual_bars": int(actual_bars),
        "coverage_ratio": float(coverage_ratio),
        "median_delta_sec": int(median_delta_sec),
        "expected_delta_sec": int(expected_delta_sec),
        "gap_count": int(gap_count),
        "major_gaps": major_gaps,
    }

    failures = []
    if median_delta_sec != expected_delta_sec:
        failures.append(f"Frequency mismatch: median delta {median_delta_sec}s != expected {expected_delta_sec}s")
    if coverage_ratio < 0.98:
        failures.append(f"Coverage too low: {coverage_ratio:.3f} (<0.98)")

    if failures:
        msg = "S3 DATASET AUDIT FAILED:\n  - " + "\n  - ".join(failures)
        if strict:
            raise RuntimeError(msg)
        logger.warning(msg)

    logger.info(
        f"S3 DATASET AUDIT OK | rows={actual_bars} | expected≈{expected_bars} | "
        f"coverage={coverage_ratio:.3f} | median_delta={median_delta_sec}s | gaps={gap_count}"
    )
    if major_gaps:
        logger.warning(f"Major gaps (first 10): {major_gaps[:10]}")

    return report


# ============================================================================
# LABELS GENERATION
# ============================================================================
def generate_labels(
    df: pd.DataFrame,
    horizon: int,
    tp_k: float,
    sl_k: float,
    adaptive: bool = False,
) -> pd.DataFrame:
    from scripts.train_edge_forecaster import generate_forward_labels

    return generate_forward_labels(
        df=df,
        horizon_minutes=horizon,
        bar_duration_minutes=1,
        tp_k=tp_k,
        sl_k=sl_k,
        adaptive_tp=adaptive,
    )


# ============================================================================
# DATASET
# ============================================================================
class SequenceDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int, stride: int = 1):
        if X.ndim != 2:
            raise ValueError(f"X must be 2D (N,F). Got {X.shape}")
        if y.ndim != 2:
            raise ValueError(f"y must be 2D (N,T). Got {y.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(f"X and y length mismatch: {X.shape[0]} vs {y.shape[0]}")
        if X.shape[0] < seq_len:
            raise ValueError(f"Not enough data: {X.shape[0]} < {seq_len}")

        self.X = X
        self.y = y
        self.seq_len = seq_len
        self.stride = stride
        self.indices = list(range(seq_len - 1, X.shape[0], stride))

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, idx):
        end_idx = self.indices[idx]
        start_idx = end_idx - self.seq_len + 1
        X_seq = self.X[start_idx : end_idx + 1]
        y_t = self.y[end_idx]
        return torch.from_numpy(X_seq), torch.from_numpy(y_t)


# ============================================================================
# TRAINING METRICS
# ============================================================================
def compute_proxy_metrics(
    q50: np.ndarray,
    p_dir_hit: np.ndarray,
    tp_threshold: np.ndarray,
    sl_threshold: np.ndarray,
    return_fwd: np.ndarray,
    cfg: UnifiedTrainingConfig,
) -> Dict[str, Any]:
    from scripts.train_edge_forecaster import compute_realistic_proxy_metrics

    return compute_realistic_proxy_metrics(
        q50=q50,
        p_dir_hit=p_dir_hit,
        tp_threshold=tp_threshold,
        sl_threshold=sl_threshold,
        return_fwd=return_fwd,
        threshold_percentile=cfg.proxy.threshold_percentile,
        fee_rate=cfg.market.fee_bps / 10000.0,
        max_trades_per_day=cfg.proxy.max_trades_per_day,
        val_days=cfg.proxy.val_days,
        bootstrap_samples=cfg.proxy.bootstrap_samples,
    )


def compute_calibration_metrics(logits: np.ndarray, targets: np.ndarray, bins: int = 10) -> Dict[str, float]:
    probs = 1.0 / (1.0 + np.exp(-logits))
    probs = np.clip(probs, 1e-8, 1.0 - 1e-8)

    brier = float(np.mean((probs - targets) ** 2))

    bin_edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    n = len(probs)
    for i in range(bins):
        mask = (probs >= bin_edges[i]) & (probs < bin_edges[i + 1])
        if mask.sum() > 0:
            bin_acc = float(targets[mask].mean())
            bin_conf = float(probs[mask].mean())
            ece += (mask.sum() / n) * abs(bin_acc - bin_conf)

    return {"brier": float(brier), "ece": float(ece)}


# ============================================================================
# METRICS SANITY CHECKS
# ============================================================================
def sanity_check_training_history(history: List[Dict[str, Any]], cfg: UnifiedTrainingConfig, strict: bool = True) -> None:
    if not history:
        raise ValueError("sanity_check_training_history: empty history")

    errors = []
    for row in history:
        e = int(row.get("epoch", -1))
        train_loss = float(row.get("train_loss", np.nan))
        val_loss = float(row.get("val_loss", np.nan))
        ece = float(row.get("ece", np.nan))
        brier = float(row.get("brier", np.nan))
        sharpe = float(row.get("sharpe", np.nan))
        n_trades = int(row.get("n_trades", -1))

        if not np.isfinite(train_loss) or train_loss <= 0:
            errors.append(f"[epoch {e}] invalid train_loss={train_loss}")
        if not np.isfinite(val_loss) or val_loss <= 0:
            errors.append(f"[epoch {e}] invalid val_loss={val_loss}")
        if not np.isfinite(ece) or ece < 0 or ece > 1.0:
            errors.append(f"[epoch {e}] invalid ece={ece} (must be in [0,1])")
        if not np.isfinite(brier) or brier < 0 or brier > 1.0:
            errors.append(f"[epoch {e}] invalid brier={brier} (must be in [0,1])")
        if not np.isfinite(sharpe) or abs(sharpe) > 20:
            errors.append(f"[epoch {e}] sharpe outlier={sharpe} (abs>20)")
        if n_trades < 0:
            errors.append(f"[epoch {e}] invalid n_trades={n_trades}")

    last = history[-1]
    if int(last.get("n_trades", 0)) == 0:
        errors.append("[final] n_trades=0 on validation (proxy cannot be trusted)")
    if float(last.get("ece", 0.0)) > 0.25:
        errors.append(f"[final] high ECE={float(last.get('ece', 0.0)):.3f} (calibration likely bad)")
    if float(last.get("val_loss", 1e9)) > 10.0:
        errors.append(f"[final] val_loss too high={float(last.get('val_loss', 0.0)):.3f}")

    if errors:
        msg = "TRAINING METRICS SANITY CHECK FAILED:\n  - " + "\n  - ".join(errors)
        if strict:
            raise RuntimeError(msg)
        logger.warning(msg)
    else:
        logger.info("TRAINING METRICS SANITY CHECK OK")


def calibrate_model_temperature(net: nn.Module, loader: DataLoader, device: torch.device) -> Dict[str, Optional[float]]:
    net.eval()
    logits_dir_all = []
    targets_dir_all = []

    with torch.no_grad():
        for Xb, yb in loader:
            Xb = Xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            out = net.predict_full_outputs(Xb)
            logits_dir_all.append(out["logits_dir"].detach().cpu().numpy())
            targets_dir_all.append(yb[:, 1].detach().cpu().numpy())  # dir_hit

    logits_dir = np.concatenate(logits_dir_all, axis=0)
    targets_dir = np.concatenate(targets_dir_all, axis=0)

    calibrator = BinaryCalibrator(method="temperature")
    calibrator.fit(logits_dir, targets_dir)

    return {"temperature_dir_hit": calibrator.temperature, "temperature_up": None}


# ============================================================================
# EMA
# ============================================================================
@torch.no_grad()
def ema_update(ema_net: nn.Module, net: nn.Module, decay: float):
    for p_ema, p in zip(ema_net.parameters(), net.parameters()):
        p_ema.data.mul_(decay).add_(p.data, alpha=1.0 - decay)


def get_eval_net(net: nn.Module, ema: Optional[Dict[str, Any]]) -> nn.Module:
    if ema and ema.get("net") is not None:
        return ema["net"]
    return net


# ============================================================================
# REGIME TRAINER (ADAPTED TO YOUR 39-FEATURE SET)
# ============================================================================
def train_regime_classifier(df: pd.DataFrame, n_train: int, cfg: UnifiedTrainingConfig) -> Optional[RegimeClassifierModel]:
    if not cfg.train_regime:
        logger.info("Skipping regime classifier (disabled in config)")
        return None

    logger.info("=" * 80)
    logger.info("TRAINING REGIME CLASSIFIER")
    logger.info("=" * 80)

    # Uses engineered features from the processed parquet
    required = ["rv_60", "ema_12_dist"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"Regime training skipped: missing column {col}")
            return None

    df = df.copy()

    df["vol_regime_cls"] = pd.qcut(df["rv_60"], q=3, labels=["calm", "normal", "volatile"], duplicates="drop")
    df["trend_regime_cls"] = pd.qcut(df["ema_12_dist"], q=3, labels=["down", "neutral", "up"], duplicates="drop")
    df["regime"] = df["vol_regime_cls"].astype(str) + "_" + df["trend_regime_cls"].astype(str)

    df_train = df.iloc[:n_train].copy()
    df_train = df_train.dropna(subset=["regime"])

    classes = sorted(df_train["regime"].unique().tolist())
    if not classes:
        logger.warning("Regime training skipped: no valid regimes after cleanup")
        return None

    model = RegimeClassifierModel(classes=classes, feature_cols=None)

    try:
        model.fit(df_train, df_train["regime"])
        logger.info(f"Regime classifier trained: {len(model.feature_cols)} features | {len(classes)} classes")
    except Exception as e:
        logger.warning(f"Regime classifier failed: {e}")
        return None

    output_path = Path(cfg.output_dir) / "regime" / cfg.run_id
    output_path.mkdir(parents=True, exist_ok=True)
    model.save(str(output_path / "model.pkl"))
    logger.info(f"Saved regime model: {output_path}")

    return model


# ============================================================================
# PAPER TRADING TEST (FULL YEAR)
# ============================================================================
def run_paper_test_full_year(
    net: EdgeForecasterNet,
    s3_loader: S3MarketDataLoader,
    feature_cols: List[str],
    cfg: UnifiedTrainingConfig,
    epoch: int,
    device: torch.device,
) -> Dict[str, Any]:
    """
    Run full paper trading backtest on 1 year of recent data.
    Called every 500 epochs to monitor real trading performance.
    """
    logger.info(f"\n{'═' * 80}")
    logger.info(f"PAPER TRADING TEST - EPOCH {epoch + 1}")
    logger.info(f"{'═' * 80}\n")

    try:
        # Load 1 year of recent data (2024-01-01 to 2024-12-31)
        logger.info("  📊 Loading 1 year of test data...")
        df_paper = s3_loader.load_processed_features(
            symbol="bitcoin",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        if df_paper is None or len(df_paper) < 1000:
            logger.warning("  ⚠️  Insufficient paper test data, skipping...")
            return {}

        logger.info(f"     Loaded {len(df_paper):,} bars from {df_paper.index[0]} to {df_paper.index[-1]}")

        # Ensure we have all required columns
        missing_cols = set(feature_cols) - set(df_paper.columns)
        if missing_cols:
            logger.warning(f"  ⚠️  Missing feature columns: {missing_cols}, skipping...")
            return {}

        # Prepare features
        X_paper = df_paper[feature_cols].to_numpy(dtype=np.float32, copy=True)

        # Create sequences
        seq_len = cfg.edge.seq_len
        n_samples = len(X_paper) - seq_len + 1

        logger.info(f"  🔮 Running inference on {n_samples:,} sequences...")

        # Inference in batches
        net.eval()
        predictions_list = []
        batch_size = 512

        with torch.no_grad():
            for i in range(0, n_samples, batch_size):
                end_i = min(i + batch_size, n_samples)
                batch_seqs = []

                for j in range(i, end_i):
                    seq = X_paper[j : j + seq_len]
                    batch_seqs.append(seq)

                Xb = torch.from_numpy(np.stack(batch_seqs, axis=0)).to(device)
                out = net.predict_full_outputs(Xb)

                predictions_list.append({
                    "p_hit": out["p_dir_hit"].cpu().numpy(),
                    "q50": out["quantile_50"].cpu().numpy(),
                    "q05": out["quantile_05"].cpu().numpy(),
                    "q95": out["quantile_95"].cpu().numpy(),
                })

        # Concatenate predictions
        p_hit = np.concatenate([p["p_hit"] for p in predictions_list], axis=0)
        q50 = np.concatenate([p["q50"] for p in predictions_list], axis=0)
        q05 = np.concatenate([p["q05"] for p in predictions_list], axis=0)
        q95 = np.concatenate([p["q95"] for p in predictions_list], axis=0)

        # Align with dataframe (predictions correspond to bars after seq_len-1)
        df_aligned = df_paper.iloc[seq_len - 1 : seq_len - 1 + len(p_hit)].copy()

        # Create predictions dataframe
        predictions_df = pd.DataFrame({
            "p_hit_calibrated": p_hit,
            "p_hit": p_hit,
            "q50": q50,
            "q05": q05,
            "q95": q95,
            "tp_threshold_used": df_aligned["tp_threshold_used"].values if "tp_threshold_used" in df_aligned.columns else 0.01,
            "sl_threshold_used": df_aligned["sl_threshold_used"].values if "sl_threshold_used" in df_aligned.columns else 0.01,
        }, index=df_aligned.index)

        logger.info(f"  📈 Running backtest with multiple configurations...")

        # Test multiple configurations
        test_configs = [
            {"name": "Conservative (t=0.70, long-only)", "threshold": 0.70, "use_shorts": False},
            {"name": "Balanced (t=0.65, long-only)", "threshold": 0.65, "use_shorts": False},
            {"name": "Aggressive (t=0.60, long-only)", "threshold": 0.60, "use_shorts": False},
            {"name": "Conservative (t=0.70, long+short)", "threshold": 0.70, "use_shorts": True},
            {"name": "Balanced (t=0.65, long+short)", "threshold": 0.65, "use_shorts": True},
            {"name": "Aggressive (t=0.60, long+short)", "threshold": 0.60, "use_shorts": True},
        ]

        results = []

        for config in test_configs:
            equity_df, trades_df, metrics = backtest_strategy_minute(
                df=df_aligned[["close", "high", "low"]],
                predictions=predictions_df,
                entry_threshold=config["threshold"],
                use_shorts=config["use_shorts"],
                fee_rate=0.0004,  # 4 bps
                slippage_bps=1.0,  # 1 bp
                position_mode="binary",
                cooldown_bars=60,
                holding_min_bars=15,
                holding_max_bars=60,
                min_edge=0.05,
                tp_mode="thresholds",
                intrabar_policy="pessimistic",
            )

            results.append({
                "config": config["name"],
                "metrics": metrics,
            })

        # Log all results
        logger.info(f"\n  {'─' * 78}")
        logger.info(f"  PAPER TRADING RESULTS - EPOCH {epoch + 1}")
        logger.info(f"  {'─' * 78}")

        for res in results:
            m = res["metrics"]
            logger.info(f"\n  📊 {res['config']}")
            logger.info(f"     Trades: {m['n_trades']:4d} | ROI: {m['roi']:7.2%} | Sharpe: {m['sharpe_1m']:6.2f} | Sortino: {m['sortino_1m']:6.2f}")
            logger.info(f"     Win Rate: {m['win_rate']:6.2%} | PF: {m['profit_factor']:6.2f} | MaxDD: {m['max_dd']:7.2%}")
            logger.info(f"     Avg Trade: {m['avg_trade_pct']:7.4%} | Exposure: {m['exposure']:6.2%} | Turnover/day: {m['turnover_per_day']:5.2f}")
            logger.info(f"     Exits → TP: {m['exit_tp']:4d} | SL: {m['exit_sl']:4d} | Time: {m['exit_time']:4d}")
            logger.info(f"     Costs → Fees: {m['total_fees_pct']:7.4%} | Slippage: {m['total_slippage_pct']:7.4%}")

        # Find best config by Sharpe
        best_result = max(results, key=lambda r: r["metrics"]["sharpe_1m"])
        logger.info(f"\n  ✨ BEST CONFIG: {best_result['config']}")
        logger.info(f"     Sharpe: {best_result['metrics']['sharpe_1m']:.2f} | ROI: {best_result['metrics']['roi']:.2%} | Trades: {best_result['metrics']['n_trades']}")
        logger.info(f"  {'─' * 78}\n")

        # Save detailed results
        paper_dir = Path(cfg.output_dir) / "paper" / f"epoch_{epoch + 1}"
        paper_dir.mkdir(parents=True, exist_ok=True)

        # Save summary
        summary = {
            "epoch": epoch + 1,
            "test_period": f"{df_aligned.index[0]} to {df_aligned.index[-1]}",
            "n_bars": len(df_aligned),
            "configs": [
                {
                    "name": r["config"],
                    "metrics": r["metrics"],
                }
                for r in results
            ],
            "best_config": best_result["config"],
            "best_sharpe": float(best_result["metrics"]["sharpe_1m"]),
        }

        with open(paper_dir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"  💾 Saved paper test results: {paper_dir}")

        return best_result["metrics"]

    except Exception as e:
        logger.error(f"  ❌ Paper test failed: {e}")
        import traceback
        traceback.print_exc()
        return {}


# ============================================================================
# EDGE FORECASTER TRAINER
# ============================================================================
def train_edge_forecaster(
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    df_full: pd.DataFrame,
    n_train: int,
    n_val: int,
    n_test: int,
    feature_cols: List[str],
    cfg: UnifiedTrainingConfig,
    s3_loader: S3MarketDataLoader = None,
) -> Tuple[EdgeForecasterModel, Dict[str, Any]]:
    logger.info("=" * 80)
    logger.info("TRAINING EDGE FORECASTER")
    logger.info("=" * 80)

    device = torch.device(cfg.edge.device)
    input_dim = len(feature_cols)

    edge_cfg = EdgeForecasterConfig(
        seq_len=cfg.edge.seq_len,
        feature_cols=feature_cols,
        d_model=cfg.edge.d_model,
        n_heads=cfg.edge.n_heads,
        n_layers=cfg.edge.n_layers,
        d_ff=cfg.edge.d_ff,
        dropout=cfg.edge.dropout,
        attn_dropout=cfg.edge.attn_dropout,
        device=cfg.edge.device,
        use_regime_cond=False,
    )

    logger.info(f"\n{'─' * 80}")
    logger.info("MODEL ARCHITECTURE")
    logger.info(f"{'─' * 80}")
    logger.info(f"  Input dim:       {input_dim} features")
    logger.info(f"  Sequence length: {cfg.edge.seq_len} bars")
    logger.info(f"  d_model:         {cfg.edge.d_model}")
    logger.info(f"  Transformer:     {cfg.edge.n_layers} layers × {cfg.edge.n_heads} heads")
    logger.info(f"  Feed-forward:    {cfg.edge.d_ff} units")
    logger.info(f"  Dropout:         {cfg.edge.dropout:.2%} (attn: {cfg.edge.attn_dropout:.2%})")
    logger.info(f"  Device:          {cfg.edge.device}")

    net = EdgeForecasterNet(input_dim=input_dim, cfg=edge_cfg).to(device)
    total_params = sum(p.numel() for p in net.parameters())
    trainable_params = sum(p.numel() for p in net.parameters() if p.requires_grad)
    logger.info(f"\n  Total parameters:     {total_params:,}")
    logger.info(f"  Trainable parameters: {trainable_params:,}")
    logger.info(f"{'─' * 80}\n")

    logger.info(f"{'─' * 80}")
    logger.info("PREPARING DATASETS")
    logger.info(f"{'─' * 80}")

    X_train = features_df.iloc[:n_train].to_numpy(dtype=np.float32, copy=True)
    y_train = labels_df.iloc[:n_train].to_numpy(dtype=np.float32, copy=True)
    X_val = features_df.iloc[n_train : n_train + n_val].to_numpy(dtype=np.float32, copy=True)
    y_val = labels_df.iloc[n_train : n_train + n_val].to_numpy(dtype=np.float32, copy=True)

    logger.info(f"  Train: X={X_train.shape} y={y_train.shape}")
    logger.info(f"  Val:   X={X_val.shape} y={y_val.shape}")

    expected_label_cols = ["return_fwd", "dir_hit", "is_up", "is_tp_up_hit", "rv_fwd_mean"]
    if list(labels_df.columns) != expected_label_cols:
        raise ValueError(f"labels_df columns mismatch.\nExpected: {expected_label_cols}\nGot: {list(labels_df.columns)}")
    if y_train.shape[1] != len(expected_label_cols):
        raise ValueError(f"labels dimension mismatch: {y_train.shape[1]} vs {len(expected_label_cols)}")

    ds_train = SequenceDataset(X_train, y_train, cfg.edge.seq_len, stride=1)
    ds_val = SequenceDataset(X_val, y_val, cfg.edge.seq_len, stride=1)

    dl_train = DataLoader(
        ds_train,
        batch_size=cfg.edge.batch_size,
        shuffle=True,
        num_workers=cfg.edge.num_workers if hasattr(cfg.edge, "num_workers") else 0,
        drop_last=True,
        pin_memory=(device.type == "cuda"),
    )
    dl_val = DataLoader(
        ds_val,
        batch_size=cfg.edge.batch_size,
        shuffle=False,
        num_workers=cfg.edge.num_workers if hasattr(cfg.edge, "num_workers") else 0,
        drop_last=False,
        pin_memory=(device.type == "cuda"),
    )

    logger.info(f"  Train batches: {len(dl_train)} (batch_size={cfg.edge.batch_size})")
    logger.info(f"  Val batches:   {len(dl_val)} (batch_size={cfg.edge.batch_size})")
    logger.info(f"{'─' * 80}\n")

    logger.info(f"{'─' * 80}")
    logger.info("OPTIMIZER & SCHEDULER")
    logger.info(f"{'─' * 80}")

    optimizer = torch.optim.AdamW(
        net.parameters(),
        lr=cfg.edge.lr,
        weight_decay=cfg.edge.weight_decay,
    )

    total_steps = len(dl_train) * cfg.edge.epochs
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer,
        max_lr=cfg.edge.lr,
        total_steps=total_steps,
        pct_start=cfg.edge.warmup_pct,
        anneal_strategy="cos",
    )

    logger.info(f"  Optimizer:    AdamW")
    logger.info(f"  Max LR:       {cfg.edge.lr:.6f}")
    logger.info(f"  Weight decay: {cfg.edge.weight_decay:.6f}")
    logger.info(f"  Scheduler:    OneCycleLR")
    logger.info(f"  Total steps:  {total_steps:,}")
    logger.info(f"  Warmup:       {cfg.edge.warmup_pct:.1%} ({int(total_steps * cfg.edge.warmup_pct)} steps)")
    logger.info(f"  Grad clip:    {cfg.edge.grad_clip}")
    logger.info(f"{'─' * 80}\n")

    scaler = torch.cuda.amp.GradScaler() if (cfg.edge.amp and device.type == "cuda") else None

    ema = None
    if getattr(cfg.edge, "ema_decay", 0.0) and cfg.edge.ema_decay > 0:
        from copy import deepcopy
        ema_net = deepcopy(net).to(device)
        ema_net.eval()
        ema = {"net": ema_net, "decay": float(cfg.edge.ema_decay)}
        logger.info(f"EMA enabled: decay={ema['decay']}")

    best_val_loss = float("inf")
    best_trading_score = float("-inf")
    patience_counter = 0

    checkpoints = {"best_trading": None, "best_val_loss": None}
    metrics_history: List[Dict[str, Any]] = []

    # Training loop with verbose logging
    logger.info(f"\n{'=' * 80}")
    logger.info(f"STARTING TRAINING: {cfg.edge.epochs} epochs | {len(dl_train)} batches/epoch")
    logger.info(f"{'=' * 80}\n")

    for epoch in range(cfg.edge.epochs):
        t0 = time.time()
        net.train()
        train_loss = 0.0
        batch_losses = []

        logger.info(f"\n{'─' * 80}")
        logger.info(f"EPOCH {epoch + 1}/{cfg.edge.epochs} - TRAINING PHASE")
        logger.info(f"{'─' * 80}")

        for batch_idx, (Xb, yb) in enumerate(dl_train):
            batch_t0 = time.time()

            Xb = Xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)

            optimizer.zero_grad(set_to_none=True)

            if scaler:
                with torch.cuda.amp.autocast():
                    loss = net.compute_loss(Xb, yb, label_smoothing=cfg.edge.label_smoothing)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(net.parameters(), cfg.edge.grad_clip)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss = net.compute_loss(Xb, yb, label_smoothing=cfg.edge.label_smoothing)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), cfg.edge.grad_clip)
                optimizer.step()

            scheduler.step()
            batch_loss = float(loss.item())

            # Protection contre divergence (NaN/Inf)
            if not np.isfinite(batch_loss) or np.isnan(batch_loss) or np.isinf(batch_loss):
                logger.error(f"\n⚠️  DIVERGENCE DETECTED at epoch {epoch + 1}, batch {batch_idx + 1}")
                logger.error(f"   Loss value: {batch_loss} (NaN: {np.isnan(batch_loss)}, Inf: {np.isinf(batch_loss)})")
                logger.error("   Stopping training and saving last known good state...")

                # Sauvegarde d'urgence du dernier état sain
                emergency_path = Path(cfg.output_dir) / "emergency" / f"{cfg.run_id}_divergence_epoch_{epoch+1}.pt"
                emergency_path.parent.mkdir(parents=True, exist_ok=True)

                checkpoint_emergency = {
                    "epoch": int(epoch + 1),
                    "batch": int(batch_idx),
                    "model_state_dict": net.state_dict(),
                    "ema_state_dict": ema["net"].state_dict() if ema else None,
                    "optimizer_state_dict": optimizer.state_dict(),
                    "config": asdict(edge_cfg),
                    "input_dim": int(input_dim),
                    "feature_cols": feature_cols,
                    "divergence_info": {
                        "loss_value": str(batch_loss),
                        "is_nan": bool(np.isnan(batch_loss)),
                        "is_inf": bool(np.isinf(batch_loss)),
                    }
                }
                torch.save(checkpoint_emergency, emergency_path)
                logger.error(f"   Emergency checkpoint saved: {emergency_path}")
                raise RuntimeError(f"Training diverged with loss={batch_loss}")

            train_loss += batch_loss
            batch_losses.append(batch_loss)

            if ema:
                ema_update(ema["net"], net, decay=ema["decay"])

            # ULTRA VERBOSE: Log every 5 batches (or every batch for first 3 epochs)
            if epoch < 3 or batch_idx % 5 == 0 or batch_idx == len(dl_train) - 1:
                current_lr = optimizer.param_groups[0]['lr']
                batch_time = time.time() - batch_t0
                avg_loss = sum(batch_losses) / len(batch_losses)
                min_loss = min(batch_losses)
                max_loss = max(batch_losses)
                std_loss = np.std(batch_losses) if len(batch_losses) > 1 else 0.0

                # Gradient stats
                total_grad_norm = 0.0
                max_grad_param = 0.0
                for p in net.parameters():
                    if p.grad is not None:
                        param_norm = p.grad.data.norm(2).item()
                        total_grad_norm += param_norm ** 2
                        max_grad_param = max(max_grad_param, param_norm)
                total_grad_norm = total_grad_norm ** 0.5

                # Weight stats
                total_weight_norm = sum(p.data.norm(2).item() ** 2 for p in net.parameters()) ** 0.5

                # NaN/Inf check in batch
                has_nan_x = torch.isnan(Xb).any().item()
                has_inf_x = torch.isinf(Xb).any().item()
                has_nan_y = torch.isnan(yb).any().item()
                has_inf_y = torch.isinf(yb).any().item()
                data_quality = "✓"
                if has_nan_x or has_inf_x or has_nan_y or has_inf_y:
                    data_quality = f"⚠ NaN/Inf detected (X:{has_nan_x}/{has_inf_x} Y:{has_nan_y}/{has_inf_y})"

                # Memory stats (if CUDA)
                mem_info = ""
                if device.type == "cuda":
                    mem_alloc = torch.cuda.memory_allocated(device) / 1024**3
                    mem_reserved = torch.cuda.memory_reserved(device) / 1024**3
                    mem_info = f" | GPU: {mem_alloc:.2f}GB/{mem_reserved:.2f}GB"

                # Scaler state (if AMP)
                scaler_info = ""
                if scaler:
                    scaler_info = f" | AMP_scale: {scaler.get_scale():.1f}"

                logger.info(
                    f"  Batch [{batch_idx + 1:4d}/{len(dl_train)}] | "
                    f"Loss: {batch_loss:.6f} [min:{min_loss:.6f} max:{max_loss:.6f} std:{std_loss:.6f}] | "
                    f"Avg: {avg_loss:.6f}"
                )
                logger.info(
                    f"    LR: {current_lr:.8f} | "
                    f"GradNorm: {total_grad_norm:.6f} (max_param:{max_grad_param:.6f}) | "
                    f"WeightNorm: {total_weight_norm:.6f}"
                )
                logger.info(
                    f"    Data: {data_quality} | "
                    f"Batch_size: {Xb.shape[0]} | "
                    f"Time: {batch_time:.4f}s{mem_info}{scaler_info}"
                )

        train_loss /= max(1, len(dl_train))
        epoch_train_time = time.time() - t0

        logger.info(f"\n  ✓ Training complete: avg_loss={train_loss:.4f} | time={epoch_train_time:.1f}s")

        logger.info(f"\n{'─' * 80}")
        logger.info(f"EPOCH {epoch + 1}/{cfg.edge.epochs} - VALIDATION PHASE")
        logger.info(f"{'─' * 80}")

        eval_net = get_eval_net(net, ema)
        eval_net.eval()

        val_loss = 0.0
        q50_all, pdh_all, logits_dir_all, ret_all, dirhit_all = [], [], [], [], []

        val_t0 = time.time()
        with torch.no_grad():
            for val_batch_idx, (Xb, yb) in enumerate(dl_val):
                Xb = Xb.to(device, non_blocking=True)
                yb = yb.to(device, non_blocking=True)

                loss = eval_net.compute_loss(Xb, yb, label_smoothing=0.0)
                val_loss += float(loss.item())

                out = eval_net.predict_full_outputs(Xb)
                q50_all.append(out["quantile_50"].detach().cpu().numpy())
                pdh_all.append(out["p_dir_hit"].detach().cpu().numpy())
                logits_dir_all.append(out["logits_dir"].detach().cpu().numpy())

                ret_all.append(yb[:, 0].detach().cpu().numpy())
                dirhit_all.append(yb[:, 1].detach().cpu().numpy())

                # Log every 25 batches during validation
                if val_batch_idx % 25 == 0 or val_batch_idx == len(dl_val) - 1:
                    logger.info(
                        f"  Val Batch [{val_batch_idx + 1:4d}/{len(dl_val)}] | "
                        f"Loss: {loss.item():.4f}"
                    )

        val_loss /= max(1, len(dl_val))
        val_time = time.time() - val_t0

        logger.info(f"\n  ✓ Validation complete: avg_loss={val_loss:.4f} | time={val_time:.1f}s")

        logger.info(f"\n{'─' * 80}")
        logger.info(f"EPOCH {epoch + 1}/{cfg.edge.epochs} - METRICS COMPUTATION")
        logger.info(f"{'─' * 80}")

        q50_all = np.concatenate(q50_all, axis=0)
        pdh_all = np.concatenate(pdh_all, axis=0)
        logits_dir_all = np.concatenate(logits_dir_all, axis=0)
        ret_all = np.concatenate(ret_all, axis=0)
        dirhit_all = np.concatenate(dirhit_all, axis=0)

        logger.info(f"  Computing calibration metrics...")
        cal_metrics = compute_calibration_metrics(logits_dir_all, dirhit_all, bins=cfg.validation.ece_bins)
        logger.info(f"    → Brier: {cal_metrics['brier']:.4f} | ECE: {cal_metrics['ece']:.4f}")

        # Align df for proxy (df_full is the labeled df with thresholds)
        start = n_train + cfg.edge.seq_len - 1
        df_val_aligned = df_full.iloc[start : start + len(q50_all)]

        logger.info(f"  Computing trading proxy metrics...")
        proxy = compute_proxy_metrics(
            q50=q50_all,
            p_dir_hit=pdh_all,
            tp_threshold=df_val_aligned["tp_threshold_used"].to_numpy(),
            sl_threshold=df_val_aligned["sl_threshold_used"].to_numpy(),
            return_fwd=ret_all,
            cfg=cfg,
        )

        trading_score = float(proxy["proxy_score"] - 2.0 * cal_metrics["ece"])

        logger.info(f"    → Sharpe: {proxy['sharpe']:.2f} | Trades: {proxy['n_trades']} | ROI: {proxy.get('roi', 0):.2%}")
        logger.info(f"    → Win Rate: {proxy.get('win_rate', 0):.2%} | Proxy Score: {proxy['proxy_score']:.2f}")
        logger.info(f"    → Trading Score: {trading_score:.2f}")

        total_epoch_time = time.time() - t0
        logger.info(f"\n{'═' * 80}")
        logger.info(
            f"EPOCH {epoch + 1}/{cfg.edge.epochs} SUMMARY | "
            f"Time: {total_epoch_time:.1f}s | "
            f"Train: {train_loss:.4f} | "
            f"Val: {val_loss:.4f}"
        )
        logger.info(
            f"  Calibration → ECE: {cal_metrics['ece']:.3f} | Brier: {cal_metrics['brier']:.3f}"
        )
        logger.info(
            f"  Trading → Sharpe: {proxy['sharpe']:.2f} [{proxy['sharpe_ci_lower']:.2f}, {proxy['sharpe_ci_upper']:.2f}] | "
            f"Trades: {proxy['n_trades']} | Score: {trading_score:.2f}"
        )
        logger.info(f"{'═' * 80}\n")

        # ════════════════════════════════════════════════════════════════════════
        # PAPER TRADING TEST - Every 500 epochs
        # ════════════════════════════════════════════════════════════════════════
        if (epoch + 1) % 500 == 0 and s3_loader is not None:
            paper_metrics = run_paper_test_full_year(
                net=get_eval_net(net, ema),
                s3_loader=s3_loader,
                feature_cols=feature_cols,
                cfg=cfg,
                epoch=epoch,
                device=device,
            )
        else:
            paper_metrics = {}

        metrics_history.append(
            {
                "epoch": int(epoch + 1),
                "train_loss": float(train_loss),
                "val_loss": float(val_loss),
                "ece": float(cal_metrics["ece"]),
                "brier": float(cal_metrics["brier"]),
                "sharpe": float(proxy["sharpe"]),
                "n_trades": int(proxy["n_trades"]),
                "trading_score": float(trading_score),
                "paper_sharpe": paper_metrics.get("sharpe_1m", 0.0) if paper_metrics else 0.0,
                "paper_roi": paper_metrics.get("roi", 0.0) if paper_metrics else 0.0,
            }
        )

        checkpoint_base = {
            "epoch": int(epoch + 1),
            "model_state_dict": net.state_dict(),
            "ema_state_dict": ema["net"].state_dict() if ema else None,
            "optimizer_state_dict": optimizer.state_dict(),
            "config": asdict(edge_cfg),
            "input_dim": int(input_dim),
            "feature_cols": feature_cols,
        }

        if proxy["n_trades"] >= cfg.proxy.min_trades and trading_score > best_trading_score:
            best_trading_score = trading_score
            checkpoints["best_trading"] = checkpoint_base
            logger.info(f"\n  ✨ NEW BEST TRADING SCORE: {trading_score:.2f} (prev: {checkpoints.get('best_trading', {}).get('epoch', 'N/A')})")
            logger.info(f"     Sharpe: {proxy['sharpe']:.2f} | Trades: {proxy['n_trades']} | ECE: {cal_metrics['ece']:.3f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            checkpoints["best_val_loss"] = checkpoint_base
            patience_counter = 0
            logger.info(f"\n  ✨ NEW BEST VAL LOSS: {val_loss:.4f} (improvement: {best_val_loss - val_loss:.4f})")
        else:
            patience_counter += 1
            logger.info(f"\n  ⏳ Patience: {patience_counter}/{cfg.edge.patience} (best val loss: {best_val_loss:.4f})")

        if patience_counter >= cfg.edge.patience:
            logger.info(f"\n⛔ EARLY STOPPING triggered at epoch {epoch + 1} (patience exhausted)")
            break

    logger.info(f"\n{'=' * 80}")
    logger.info("POST-TRAINING: CALIBRATION & SAVING")
    logger.info(f"{'=' * 80}\n")

    net_to_save = get_eval_net(net, ema)

    if cfg.edge.temperature_scaling:
        logger.info("  📊 Calibrating probabilities (temperature scaling)...")
        calib = calibrate_model_temperature(net=net_to_save, loader=dl_val, device=device)
        logger.info(f"     Temperature: {calib.get('temperature_dir_hit', 'N/A')}")
    else:
        calib = {"temperature_dir_hit": None, "temperature_up": None}
        logger.info("  ⏭️  Temperature scaling disabled")

    output_path = Path(cfg.output_dir) / "edge" / f"{cfg.run_id}.pt"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"\n  💾 Saving final model...")
    save_artifact(
        path=str(output_path),
        net=net_to_save,
        cfg=edge_cfg,
        feature_cols=feature_cols,
        calibration=calib,
        metadata={"production_grade": True, "ema_used": bool(ema)},
    )
    logger.info(f"     ✓ Saved: {output_path}")

    def _save_checkpoint(tag: str, cp: Dict[str, Any]):
        logger.info(f"\n  💾 Saving checkpoint: {tag} (epoch {cp['epoch']})...")
        cp_path = Path(cfg.output_dir) / "edge" / f"{cfg.run_id}_{tag}.pt"
        temp_net = EdgeForecasterNet(input_dim=input_dim, cfg=edge_cfg).to(device)

        state = cp["ema_state_dict"] if (cp.get("ema_state_dict") is not None) else cp["model_state_dict"]
        temp_net.load_state_dict(state)
        temp_net.eval()

        if cfg.edge.temperature_scaling:
            temp_calib = calibrate_model_temperature(net=temp_net, loader=dl_val, device=device)
        else:
            temp_calib = {"temperature_dir_hit": None, "temperature_up": None}

        save_artifact(
            path=str(cp_path),
            net=temp_net,
            cfg=edge_cfg,
            feature_cols=feature_cols,
            calibration=temp_calib,
            metadata={"checkpoint": tag, "epoch": cp["epoch"], "ema_used": cp.get("ema_state_dict") is not None},
        )
        logger.info(f"     ✓ Saved: {cp_path}")

    if checkpoints["best_trading"]:
        _save_checkpoint("best_trading", checkpoints["best_trading"])
    if checkpoints["best_val_loss"]:
        _save_checkpoint("best_val_loss", checkpoints["best_val_loss"])

    metrics_path = output_path.parent / f"{cfg.run_id}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics_history, f, indent=2)
    logger.info(f"Saved metrics: {metrics_path}")

    model = EdgeForecasterModel(cfg=edge_cfg)
    model.net = net_to_save
    model.input_dim = input_dim
    model.feature_cols = feature_cols

    return model, {"history": metrics_history, "best_trading_score": float(best_trading_score)}


# ============================================================================
# MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Unified Production Trainer (Hardened) - Processed Features")
    parser.add_argument("--config", type=str, help="Override config (JSON file)")
    parser.add_argument("--symbol", type=str, help="Override symbol")
    parser.add_argument("--start-date", type=str, help="Override start date")
    parser.add_argument("--end-date", type=str, help="Override end date")
    parser.add_argument("--device", type=str, help="Override device")
    parser.add_argument("--run-id", type=str, help="Override run ID")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level")
    parser.add_argument("--debug-overfit", action="store_true", help="Enable debug overfit mode")
    args = parser.parse_args()

    global logger
    logger = setup_logger(args.log_level)

    cfg = UnifiedTrainingConfig()
    if args.config:
        cfg = load_and_merge_config(cfg, args.config)

    if args.symbol:
        cfg.data.symbol = args.symbol
    if args.start_date:
        cfg.data.start_date = args.start_date
    if args.end_date:
        cfg.data.end_date = args.end_date
    if args.device:
        cfg.edge.device = args.device
    if args.run_id:
        cfg.run_id = args.run_id

    # Debug overfit mode configuration
    if args.debug_overfit:
        logger.info("=== DEBUG OVERFIT MODE ENABLED ===")
        cfg.edge.dropout = 0.0
        cfg.edge.weight_decay = 0.0
        cfg.edge.grad_clip = 1000.0
        cfg.edge.lr = 1e-3
        cfg.edge.epochs = min(cfg.edge.epochs, 20)  # Limit to prevent excessive training
        cfg.edge.batch_size = 256  # Keep batch size reasonable
        cfg.run_id = f"{cfg.run_id}_debug_overfit"
        cfg._debug_overfit = True  # Flag pour plus tard
        logger.info(f"Debug overfit config: lr={cfg.edge.lr}, epochs={cfg.edge.epochs}, grad_clip={cfg.edge.grad_clip}")
        logger.info(f"Disabled: dropout={cfg.edge.dropout}, weight_decay={cfg.edge.weight_decay}")
    else:
        cfg._debug_overfit = False

    seed_everything(cfg.seed, cfg.deterministic)

    logger.info("=" * 80)
    logger.info("UNIFIED PRODUCTION TRAINING (HARDENED) - PROCESSED FEATURES")
    logger.info("=" * 80)
    logger.info(f"Symbol: {cfg.data.symbol}")
    logger.info(f"Period: {cfg.data.start_date} → {cfg.data.end_date}")
    logger.info(f"Run ID: {cfg.run_id}")
    logger.info(f"Device: {cfg.edge.device}")

    # ----------------------------------------------------------------------
    # Load processed features from S3 (NO compute_features)
    # ----------------------------------------------------------------------
    logger.info("\nLoading PROCESSED FEATURES from S3...")
    loader = S3MarketDataLoader()

    required_features_39 = get_model_feature_columns_39()
    print_columns("MODEL REQUIRED FEATURES (39)", required_features_39)
    df_features = loader.load_processed_features(
        cfg.data.symbol,
        cfg.data.start_date,
        cfg.data.end_date,
        strict_model_features=True,
        print_columns=True,
    )


    if df_features.empty:
        raise ValueError("No processed features loaded")

    # df_features is indexed by datetime and contains ONLY the 39 features
    assert_monotonic_time_index(df_features)
    logger.info(f"Loaded processed features: rows={len(df_features)} cols={df_features.shape[1]}")

    # Debug overfit mode: limit dataset to 256 samples
    if hasattr(cfg, '_debug_overfit') and cfg._debug_overfit:
        original_len = len(df_features)
        df_features = df_features.tail(256).copy()
        logger.info(f"DEBUG OVERFIT: Limited dataset from {original_len} to {len(df_features)} samples")

    # CHECKPOINT 1: Verify no duplicates after loading
    assert_no_duplicate_columns(df_features, context="after load_processed_features")

    # ----------------------------------------------------------------------
    # Audit dataset completeness (minute bars)
    # ----------------------------------------------------------------------
    logger.info("\nRunning S3 dataset audit on processed features...")
    audit_report = audit_s3_dataset(
        df=df_features,
        expected_bar_minutes=1,
        max_gap_minutes=5,
        strict=True,
    )
    logger.info(f"S3 audit report: {json.dumps(audit_report, indent=2)[:4000]}")

    # ----------------------------------------------------------------------
    # Generate labels (adds thresholds + targets to df)
    # ----------------------------------------------------------------------
    logger.info("Generating labels...")
    df = generate_labels(
        df_features,
        horizon=cfg.edge.horizon_minutes,
        tp_k=cfg.edge.tp_k,
        sl_k=cfg.edge.sl_k,
        adaptive=cfg.edge.adaptive_tp,
    )

    if df.empty:
        raise ValueError("Labels dataframe is empty")

    # CHECKPOINT 2: Verify no duplicates after label generation
    assert_no_duplicate_columns(df, context="after generate_labels")

    # Required columns for strict cleanup
    required_cols = [
        # base OHLCV needed by label generation
        "open", "high", "low", "close", "volume",
        # labels / thresholds
        "return_fwd", "dir_hit", "tp_threshold_used", "sl_threshold_used", "rv_fwd_mean", "tp_up_hit",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns after label generation: {missing}")

    df = drop_invalid_rows(df, required_cols)

    # ----------------------------------------------------------------------
    # Feature selection is now FIXED to the 39 features
    # ----------------------------------------------------------------------
    feature_cols = required_features_39
    logger.info(f"Using fixed model feature set: {len(feature_cols)} features")

    # ----------------------------------------------------------------------
    # BUILD CLEAN SLICES (NO CONCAT - df is the source of truth)
    # ----------------------------------------------------------------------
    # Strategy: df already contains all 39 features + labels + thresholds
    # We build features_df and labels_df as VIEWS/SLICES from df (no duplication)

    # Strict NaN/Inf cleanup on df directly (all columns needed downstream)
    full_required = (
        feature_cols  # 39 features
        + ["return_fwd", "dir_hit", "tp_up_hit", "rv_fwd_mean"]  # labels
        + ["tp_threshold_used", "sl_threshold_used", "high", "low", "close"]  # thresholds + OHLC
    )

    # Replace inf with nan, then drop rows with any NaN in required columns
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=full_required)

    if df.empty:
        raise ValueError("All rows removed after final NaN/Inf cleanup")

    logger.info(f"After final cleanup: {len(df)} rows remaining")

    # Now build clean slices from the single source of truth (df)
    # features_df = numeric feature matrix (39 cols)
    features_df = enforce_numeric_feature_matrix(df, feature_cols)
    feature_cols = list(features_df.columns)

    # Hard-check: you must still have 39 columns, otherwise fail
    if len(feature_cols) != 39:
        raise ValueError(f"Feature set drift: expected 39, got {len(feature_cols)}. Dropped: {set(required_features_39) - set(feature_cols)}")

    # labels_df = target variables (5 cols)
    labels_df = pd.DataFrame(
        {
            "return_fwd": df["return_fwd"].astype(np.float32),
            "dir_hit": df["dir_hit"].astype(np.float32),
            "is_up": (df["return_fwd"] > 0).astype(np.float32),
            "is_tp_up_hit": (df["tp_up_hit"] == 1).astype(np.float32),
            "rv_fwd_mean": df["rv_fwd_mean"].astype(np.float32),
        },
        index=df.index,
    )

    # Sanity: all three DataFrames must have identical indices
    if not (df.index.equals(features_df.index) and df.index.equals(labels_df.index)):
        raise ValueError("Index mismatch between df, features_df, and labels_df")

    # CHECKPOINT 3: No duplicates before training
    assert_no_duplicate_columns(df, context="final df (before training)")
    assert_no_duplicate_columns(features_df, context="features_df (before training)")
    assert_no_duplicate_columns(labels_df, context="labels_df (before training)")

    # ----------------------------------------------------------------------
    # Temporal split
    # ----------------------------------------------------------------------
    n_total = len(features_df)
    if n_total <= (cfg.edge.seq_len + 1000):
        raise ValueError(f"Not enough data after cleanup: {n_total} rows")

    n_test = int(n_total * cfg.data.test_pct)
    n_val = int(n_total * cfg.data.val_pct)
    n_train = n_total - n_val - n_test

    if n_train <= cfg.edge.seq_len or n_val <= cfg.edge.seq_len or n_test <= cfg.edge.seq_len:
        raise ValueError(
            f"Split too small for seq_len={cfg.edge.seq_len}: train={n_train}, val={n_val}, test={n_test}"
        )

    logger.info(
        f"\nTemporal split: train={n_train} ({cfg.data.train_pct:.1%}) | "
        f"val={n_val} ({cfg.data.val_pct:.1%}) | "
        f"test={n_test} ({cfg.data.test_pct:.1%})"
    )

    # ----------------------------------------------------------------------
    # Regime classifier (optional)
    # ----------------------------------------------------------------------
    # CHECKPOINT 4: Before regime training
    assert_no_duplicate_columns(df, context="before train_regime_classifier")

    _ = train_regime_classifier(df, n_train, cfg)

    # ----------------------------------------------------------------------
    # Edge forecaster
    # ----------------------------------------------------------------------
    # CHECKPOINT 5: Before edge training
    assert_no_duplicate_columns(features_df, context="before train_edge_forecaster (features)")
    assert_no_duplicate_columns(labels_df, context="before train_edge_forecaster (labels)")
    assert_no_duplicate_columns(df, context="before train_edge_forecaster (df_full)")

    edge_model, _edge_metrics = train_edge_forecaster(
        features_df=features_df,
        labels_df=labels_df,
        df_full=df,
        n_train=n_train,
        n_val=n_val,
        n_test=n_test,
        feature_cols=feature_cols,
        cfg=cfg,
        s3_loader=loader,
    )

    sanity_check_training_history(_edge_metrics["history"], cfg=cfg, strict=True)

    # ----------------------------------------------------------------------
    # Held-out test evaluation
    # ----------------------------------------------------------------------
    logger.info("\n" + "=" * 80)
    logger.info("HELD-OUT TEST SET EVALUATION")
    logger.info("=" * 80)

    X_test = features_df.iloc[n_train + n_val :].to_numpy(dtype=np.float32, copy=True)
    y_test = labels_df.iloc[n_train + n_val :].to_numpy(dtype=np.float32, copy=True)
    df_test = df.iloc[n_train + n_val :].copy()

    ds_test = SequenceDataset(X_test, y_test, cfg.edge.seq_len, stride=1)
    dl_test = DataLoader(
        ds_test,
        batch_size=cfg.edge.batch_size,
        shuffle=False,
        num_workers=cfg.edge.num_workers if hasattr(cfg.edge, "num_workers") else 0,
        pin_memory=(torch.device(cfg.edge.device).type == "cuda"),
    )

    eval_net = edge_model.net
    eval_net.eval()
    model_device = next(eval_net.parameters()).device

    q50_test, pdh_test, pup_test = [], [], []
    with torch.no_grad():
        for Xb, _ in dl_test:
            Xb = Xb.to(model_device, non_blocking=True)
            out = eval_net.predict_full_outputs(Xb)
            q50_test.append(out["quantile_50"].detach().cpu().numpy())
            pdh_test.append(out["p_dir_hit"].detach().cpu().numpy())
            pup_test.append(out["p_up"].detach().cpu().numpy())

    q50_test = np.concatenate(q50_test, axis=0)
    pdh_test = np.concatenate(pdh_test, axis=0)
    pup_test = np.concatenate(pup_test, axis=0)

    df_test_aligned = df_test.iloc[cfg.edge.seq_len - 1 : cfg.edge.seq_len - 1 + len(q50_test)]

    test_acc = float((pdh_test > 0.5).sum() / len(pdh_test))
    test_mean_conf = float(pdh_test.mean())

    logger.info(f"Test Samples: {len(q50_test)}")
    logger.info(f"Test Accuracy (dir_hit>0.5): {test_acc:.2%}")
    logger.info(f"Test Mean Confidence: {test_mean_conf:.3f}")
    logger.info(f"Test Q50 Mean: {q50_test.mean():.4f}")
    logger.info(f"Test Q50 Std: {q50_test.std():.4f}")

    logger.info("\n" + "=" * 80)
    logger.info("TRAINING COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Models saved to: {cfg.output_dir}/{cfg.run_id}")


if __name__ == "__main__":
    main()
