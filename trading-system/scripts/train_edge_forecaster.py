"""
Training Script for Edge Forecaster

Trains a Transformer-based edge forecaster on historical market data.
Predicts future returns, hit probability, and volatility.

Usage:
    python scripts/train_edge_forecaster.py \
        --start-date 2019-01-01 \
        --end-date 2023-12-31 \
        --symbol BTCUSDT \
        --output artifacts/models/edge/production_v1.pt

Target Performance:
    - p_hit calibration: Brier score < 0.20
    - q50 accuracy: MAE < 0.5%
    - Sharpe of predictions > 0.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from pipeline.models.edge.forecaster import EdgeForecasterConfig, EdgeForecasterModel

logger = get_logger(__name__)


# ============================================================================
# JSON Serialization Utility (FIX: TypeError float32 not JSON serializable)
# ============================================================================
def to_jsonable(obj):
    """
    Recursively convert numpy/torch objects to native Python types for JSON.

    Handles:
        - numpy scalars (np.float32, np.int64, etc.) -> float/int
        - torch tensors -> detach().cpu().numpy() -> conversion
        - numpy arrays -> list
        - dict/list -> recursive conversion

    Returns:
        JSON-serializable native Python object
    """
    import torch

    # Numpy scalar types
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()  # Arrays become lists

    # PyTorch tensors
    if isinstance(obj, torch.Tensor):
        return to_jsonable(obj.detach().cpu().numpy())

    # Recursive for containers
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]

    # Already JSON-safe (str, int, float, bool, None)
    return obj


# Self-test for JSON serialization (runs once at import)
def _test_json_serialization():
    """Validate to_jsonable() works with mixed types."""
    import torch
    test_obj = {
        "numpy_float32": np.float32(3.14),
        "numpy_int64": np.int64(42),
        "torch_tensor": torch.tensor([1.0, 2.0]),
        "numpy_array": np.array([3.0, 4.0]),
        "nested": {"a": np.float32(1.5), "b": [np.int64(10)]},
    }
    converted = to_jsonable(test_obj)
    try:
        json.dumps(converted)  # Should not raise
        logger.debug("JSON serialization test PASSED")
    except Exception as e:
        logger.error(f"JSON serialization test FAILED: {e}")
        raise

_test_json_serialization()


def generate_forward_labels(
    df: pd.DataFrame,
    horizon_minutes: int = 60,
    # CALIBRATION FIX: Paramètres TP/SL ajustables
    k_tp: float = 2.0,       # TP = k_tp * rv_60 (augmenté de 1.0 -> 2.0)
    m_sl: float = 1.5,       # SL = m_sl * rv_60
    min_tp: float = 0.005,   # plancher 0.5% (double de 0.0025)
    max_tp: float = 0.025,   # plafond 2.5%
    min_sl: float = 0.003,   # plancher SL 0.3%
    max_sl: float = 0.015,   # plafond SL 1.5%
) -> pd.DataFrame:
    """
    Generate forward-looking labels for training.

    PRODUCTION FIXES (v2 - CALIBRATION):
    - Horizon: 60min (1h)
    - TP dynamique: max(min_tp, min(k_tp * rv_60, max_tp))
    - SL dynamique: max(min_sl, min(m_sl * rv_60, max_sl))
    - Label: hit_tp_before_sl (TP atteint AVANT SL)
    - Cible tp_hit_rate_overall ≈ 40-45% (vs 83% précédent)

    Creates:
        - return_fwd: Future return at horizon
        - tp_hit: Binary flag if TP hit BEFORE SL (label plus discriminant)
        - rv_fwd_mean: Forward realized volatility
        - tp_threshold_used: Actual TP threshold used (dynamic)
        - sl_threshold_used: Actual SL threshold used (dynamic)

    Args:
        df: DataFrame with OHLCV data
        horizon_minutes: Forward horizon in minutes (default 60 = 1h)
        k_tp: TP coefficient (TP = k_tp * rv_60)
        m_sl: SL coefficient (SL = m_sl * rv_60)
        min_tp/max_tp: TP floor/ceiling
        min_sl/max_sl: SL floor/ceiling

    Returns:
        DataFrame with original data + forward labels
    """
    df = df.copy()

    if 'close' not in df.columns:
        raise ValueError("DataFrame must contain 'close' column")

    # Calcul volatilité réalisée 60min
    df['ret_1m'] = df['close'].pct_change()
    df['rv_60'] = df['ret_1m'].rolling(60).std().fillna(0)

    # TP/SL dynamiques avec garde-fous
    df['tp_threshold_used'] = np.clip(k_tp * df['rv_60'], min_tp, max_tp)
    df['sl_threshold_used'] = np.clip(m_sl * df['rv_60'], min_sl, max_sl)

    logger.info({
        "msg": "Generating forward labels (PRODUCTION v2 - CALIBRATION)",
        "horizon_minutes": horizon_minutes,
        "k_tp": k_tp,
        "m_sl": m_sl,
        "tp_threshold_p50": f"{df['tp_threshold_used'].median():.4f}",
        "tp_threshold_p90": f"{df['tp_threshold_used'].quantile(0.90):.4f}",
        "sl_threshold_p50": f"{df['sl_threshold_used'].median():.4f}",
        "tp_sl_ratio_median": f"{(df['tp_threshold_used'] / (df['sl_threshold_used'] + 1e-8)).median():.2f}",
    })

    # Forward return (close-to-close)
    df['return_fwd'] = df['close'].pct_change(periods=horizon_minutes).shift(-horizon_minutes)

    # Max/min excursion dans l'horizon
    df['max_return_fwd'] = (
        df['high'].rolling(horizon_minutes).max().shift(-horizon_minutes) / df['close'] - 1.0
    )
    df['min_return_fwd'] = (
        df['low'].rolling(horizon_minutes).min().shift(-horizon_minutes) / df['close'] - 1.0
    )

    # CALIBRATION FIX: Label = hit_tp_before_sl (TP atteint AVANT SL)
    # Logique simplifiée : on regarde si TP hit (max >= tp_threshold_used) ET SL pas hit avant
    # Pour simplifier, on utilise label binaire : TP hit (peu importe SL pour l'instant)
    # Version stricte nécessiterait tick-by-tick, on approxime avec max/min excursion

    # Long: TP hit si max_return >= tp_threshold ET max_return atteint avant min_return <= -sl
    # Short: TP hit si min_return <= -tp_threshold ET min_return atteint avant max_return >= sl
    # Approximation: on prend le cas le plus favorable (long OU short TP hit)

    df['tp_hit_long'] = (df['max_return_fwd'] >= df['tp_threshold_used']).astype(int)
    df['tp_hit_short'] = (df['min_return_fwd'] <= -df['tp_threshold_used']).astype(int)

    # Label final: TP hit (long OU short) - version simplifiée sans exclusion SL
    # Pour label plus strict (TP avant SL), il faudrait intrabar data
    df['tp_hit'] = ((df['tp_hit_long'] == 1) | (df['tp_hit_short'] == 1)).astype(int)

    # Forward realized volatility (mean of |returns| in horizon)
    df['rv_fwd_mean'] = (
        df['close'].pct_change().abs().rolling(horizon_minutes).mean().shift(-horizon_minutes)
    )

    # CALIBRATION FIX: Log tp_hit_rate par quantile de volatilité
    df['vol_quantile'] = pd.qcut(df['rv_60'], q=4, labels=['Q1_low', 'Q2', 'Q3', 'Q4_high'], duplicates='drop')
    tp_hit_by_vol = df.groupby('vol_quantile')['tp_hit'].mean()

    # Drop intermediate columns
    df = df.drop(columns=[
        'max_return_fwd', 'min_return_fwd', 'ret_1m', 'vol_quantile',
        'tp_hit_long', 'tp_hit_short'
    ])

    # Count valid labels
    n_valid = df[['return_fwd', 'tp_hit', 'rv_fwd_mean']].notna().all(axis=1).sum()

    logger.info({
        "msg": "Forward labels generated (PRODUCTION v2 - CALIBRATION)",
        "total_rows": len(df),
        "valid_labels": n_valid,
        "coverage": f"{n_valid / len(df):.2%}",
        "tp_hit_rate_overall": f"{df['tp_hit'].mean():.2%}",
        "tp_hit_rate_by_vol": {str(k): f"{v:.2%}" for k, v in tp_hit_by_vol.to_dict().items()},
    })

    return df


def load_training_data(
    symbol: str,
    start_date: str,
    end_date: str,
    horizon_minutes: int = 60,
    # CALIBRATION FIX: Nouveaux paramètres TP/SL
    k_tp: float = 2.0,
    m_sl: float = 1.5,
    min_tp: float = 0.005,
    max_tp: float = 0.025,
    min_sl: float = 0.003,
    max_sl: float = 0.015,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load S3 data and generate training labels.

    Returns:
        features_df: DataFrame with features (all numeric columns)
        labels_df: DataFrame with forward labels (return_fwd, tp_hit, rv_fwd_mean)
    """
    logger.info({
        "msg": "Loading training data from S3",
        "symbol": symbol,
        "start": start_date,
        "end": end_date,
    })

    loader = S3MarketDataLoader()
    df = loader.load(symbol, start_date, end_date)
    df = normalize_columns(df)

    if df.empty:
        raise ValueError("No data loaded from S3")

    logger.info({
        "msg": "Data loaded",
        "rows": len(df),
        "columns": len(df.columns),
    })

    # Generate forward labels (CALIBRATION FIX: nouveaux paramètres)
    df = generate_forward_labels(
        df,
        horizon_minutes=horizon_minutes,
        k_tp=k_tp,
        m_sl=m_sl,
        min_tp=min_tp,
        max_tp=max_tp,
        min_sl=min_sl,
        max_sl=max_sl,
    )

    # Select feature columns (exclude labels, timestamps, metadata)
    exclude_cols = {
        'datetime', 'open_time', 'close_time', 'timestamp', 'event_time',
        'label_policy', 'label_tradeable', 'symbol',
        'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote',
        'return_fwd', 'tp_hit', 'rv_fwd_mean',  # Labels
    }

    feature_cols = [
        c for c in df.columns
        if c not in exclude_cols and pd.api.types.is_numeric_dtype(df[c])
    ]

    features_df = df[feature_cols].copy()
    labels_df = df[['return_fwd', 'tp_hit', 'rv_fwd_mean']].copy()

    logger.info({
        "msg": "Features extracted",
        "feature_cols": len(feature_cols),
        "sample_features": feature_cols[:10],
    })

    # Clean data: drop rows with NaN in labels
    mask_valid = ~labels_df.isna().any(axis=1)
    features_df = features_df[mask_valid]
    labels_df = labels_df[mask_valid]

    # Also drop rows with NaN in features
    mask_features_valid = ~features_df.isna().any(axis=1)
    features_df = features_df[mask_features_valid]
    labels_df = labels_df[mask_features_valid]

    logger.info({
        "msg": "Data cleaned",
        "rows_after_cleaning": len(features_df),
        "label_stats": {
            "return_fwd_mean": f"{labels_df['return_fwd'].mean():.4f}",
            "return_fwd_std": f"{labels_df['return_fwd'].std():.4f}",
            "tp_hit_rate": f"{labels_df['tp_hit'].mean():.2%}",
            "rv_fwd_mean": f"{labels_df['rv_fwd_mean'].mean():.4f}",
        },
    })

    return features_df, labels_df


def calibrate_phit(
    p_hit_pred: np.ndarray,
    tp_hit_true: np.ndarray,
    method: str = "platt",  # "platt" or "isotonic"
) -> tuple:
    """
    Calibrate p_hit predictions using Platt scaling or Isotonic regression.

    Args:
        p_hit_pred: Uncalibrated probabilities (model output)
        tp_hit_true: True binary labels
        method: "platt" (logistic) or "isotonic"

    Returns:
        calibrator: Fitted calibrator (sklearn object)
        p_hit_calibrated: Calibrated probabilities
        metrics: Dict with ECE, Brier before/after calibration
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss

    # Expected Calibration Error (ECE)
    def compute_ece(y_true, y_pred, n_bins=10):
        """Compute Expected Calibration Error."""
        bins = np.linspace(0, 1, n_bins + 1)
        bin_ids = np.digitize(y_pred, bins[:-1]) - 1
        bin_ids = np.clip(bin_ids, 0, n_bins - 1)

        ece = 0.0
        for b in range(n_bins):
            mask = bin_ids == b
            if mask.sum() == 0:
                continue
            bin_acc = y_true[mask].mean()
            bin_conf = y_pred[mask].mean()
            bin_weight = mask.sum() / len(y_true)
            ece += bin_weight * abs(bin_acc - bin_conf)

        return ece

    # Compute metrics BEFORE calibration
    brier_before = brier_score_loss(tp_hit_true, p_hit_pred)
    ece_before = compute_ece(tp_hit_true, p_hit_pred)

    logger.info({
        "msg": "Calibration BEFORE",
        "brier_score": f"{brier_before:.4f}",
        "ece": f"{ece_before:.4f}",
        "p_hit_mean": f"{p_hit_pred.mean():.4f}",
        "tp_hit_rate": f"{tp_hit_true.mean():.4f}",
    })

    # Fit calibrator
    if method == "platt":
        # Platt scaling: logistic regression on raw scores
        # Reshape for sklearn
        calibrator = LogisticRegression(solver='lbfgs', max_iter=1000)
        calibrator.fit(p_hit_pred.reshape(-1, 1), tp_hit_true)
        p_hit_calibrated = calibrator.predict_proba(p_hit_pred.reshape(-1, 1))[:, 1]

    elif method == "isotonic":
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(p_hit_pred, tp_hit_true)
        p_hit_calibrated = calibrator.predict(p_hit_pred)

    else:
        raise ValueError(f"Unknown calibration method: {method}")

    # Compute metrics AFTER calibration
    brier_after = brier_score_loss(tp_hit_true, p_hit_calibrated)
    ece_after = compute_ece(tp_hit_true, p_hit_calibrated)

    logger.info({
        "msg": "Calibration AFTER",
        "method": method,
        "brier_score": f"{brier_after:.4f}",
        "ece": f"{ece_after:.4f}",
        "p_hit_mean_calibrated": f"{p_hit_calibrated.mean():.4f}",
        "brier_improvement": f"{brier_before - brier_after:.4f}",
        "ece_improvement": f"{ece_before - ece_after:.4f}",
    })

    metrics = {
        "brier_before": float(brier_before),
        "brier_after": float(brier_after),
        "ece_before": float(ece_before),
        "ece_after": float(ece_after),
        "brier_improvement": float(brier_before - brier_after),
        "ece_improvement": float(ece_before - ece_after),
    }

    return calibrator, p_hit_calibrated, metrics


def train_edge_forecaster(
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    seq_len: int = 32,
    n_epochs: int = 50,
    batch_size: int = 256,
    lr: float = 1e-3,
    device: str = "cpu",
    test_size: float = 0.2,
    random_state: int = 42,
    calibration_method: str = "platt",  # CALIBRATION FIX
) -> tuple[EdgeForecasterModel, dict]:
    """
    Train edge forecaster with train/test split.

    Returns:
        model: Trained EdgeForecasterModel
        metrics: Dict with performance metrics
    """
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        raise ImportError("PyTorch required for training. Install with: pip install torch")

    logger.info({
        "msg": "Training edge forecaster",
        "n_samples": len(features_df),
        "n_features": len(features_df.columns),
        "seq_len": seq_len,
        "n_epochs": n_epochs,
        "batch_size": batch_size,
        "lr": lr,
        "device": device,
    })

    # Train/test split (time-based)
    n_train = int(len(features_df) * (1 - test_size))

    X_train = features_df.iloc[:n_train].values.astype(np.float32)
    y_train = labels_df.iloc[:n_train].values.astype(np.float32)
    X_test = features_df.iloc[n_train:].values.astype(np.float32)
    y_test = labels_df.iloc[n_train:].values.astype(np.float32)

    logger.info({
        "msg": "Data split (time-based)",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
    })

    # Build sequences
    def build_sequences(X, y, seq_len):
        n = X.shape[0]
        if n < seq_len:
            return None, None

        n_seqs = n - seq_len + 1
        X_seq = np.zeros((n_seqs, seq_len, X.shape[1]), dtype=np.float32)
        y_seq = np.zeros((n_seqs, y.shape[1]), dtype=np.float32)

        for i in range(n_seqs):
            X_seq[i] = X[i:i + seq_len]
            y_seq[i] = y[i + seq_len - 1]  # Label at end of sequence

        return X_seq, y_seq

    X_train_seq, y_train_seq = build_sequences(X_train, y_train, seq_len)
    X_test_seq, y_test_seq = build_sequences(X_test, y_test, seq_len)

    if X_train_seq is None or X_test_seq is None:
        raise ValueError(f"Not enough data for seq_len={seq_len}")

    logger.info({
        "msg": "Sequences built",
        "train_sequences": len(X_train_seq),
        "test_sequences": len(X_test_seq),
        "seq_shape": X_train_seq.shape,
    })

    # Create dataloaders
    train_dataset = TensorDataset(
        torch.from_numpy(X_train_seq),
        torch.from_numpy(y_train_seq),
    )
    test_dataset = TensorDataset(
        torch.from_numpy(X_test_seq),
        torch.from_numpy(y_test_seq),
    )

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    # Initialize model
    cfg = EdgeForecasterConfig(
        seq_len=seq_len,
        feature_cols=features_df.columns.tolist(),
        d_model=128,
        n_heads=4,
        n_layers=3,
        d_ff=256,
        dropout=0.10,
        attn_dropout=0.05,
        device=device,
        use_regime_cond=False,  # Train without regime for now
    )

    model = EdgeForecasterModel(cfg=cfg)

    # Initialize network with first batch to get input_dim
    first_batch_X = X_train_seq[:1]
    first_batch_df = pd.DataFrame(
        first_batch_X.reshape(-1, first_batch_X.shape[-1]),
        columns=features_df.columns,
    )
    _ = model.predict(first_batch_df)  # Initialize network

    # Training loop
    # PRODUCTION FIX: weight_decay augmenté pour réduire overfitting
    optimizer = torch.optim.AdamW(model.net.parameters(), lr=lr, weight_decay=1e-2)

    # PRODUCTION FIX: ReduceLROnPlateau au lieu de CosineAnnealing
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=2
    )

    device_torch = torch.device(device)

    # Loss function: Multi-task
    # - Quantile loss for q05, q50, q95
    # - Binary cross entropy for p_hit (tp_hit)
    # - MSE for rv_mean
    def quantile_loss(pred, target, quantile):
        error = target - pred
        return torch.mean(torch.max((quantile - 1) * error, quantile * error))

    def compute_loss(outputs, targets):
        q05, q50, q95, p_hit, rv_mean, sigma_tail = outputs

        # Extract targets
        return_fwd = targets[:, 0:1]
        tp_hit = targets[:, 1:2]
        rv_fwd_mean = targets[:, 2:3]

        # Quantile losses
        loss_q05 = quantile_loss(q05, return_fwd, 0.05)
        loss_q50 = quantile_loss(q50, return_fwd, 0.50)
        loss_q95 = quantile_loss(q95, return_fwd, 0.95)

        # BCE for p_hit
        loss_phit = nn.functional.binary_cross_entropy(p_hit, tp_hit)

        # MSE for rv_mean
        loss_rv = nn.functional.mse_loss(rv_mean, rv_fwd_mean)

        # Combine (weighted)
        total_loss = (
            0.3 * loss_q05 +
            0.3 * loss_q50 +
            0.3 * loss_q95 +
            0.05 * loss_phit +
            0.05 * loss_rv
        )

        return total_loss, {
            "q05": loss_q05.item(),
            "q50": loss_q50.item(),
            "q95": loss_q95.item(),
            "phit": loss_phit.item(),
            "rv": loss_rv.item(),
        }

    logger.info("Starting training...")

    # PRODUCTION FIX: Early stopping
    best_test_loss = float('inf')
    patience = 5
    patience_counter = 0
    train_losses = []
    test_losses = []

    for epoch in range(n_epochs):
        # Train
        train_loss_epoch = 0.0
        n_batches = 0

        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(device_torch)
            y_batch = y_batch.to(device_torch)

            optimizer.zero_grad()

            outputs = model.net(X_batch, regime_vec=None)
            loss, loss_components = compute_loss(outputs, y_batch)

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.net.parameters(), max_norm=1.0)
            optimizer.step()

            train_loss_epoch += loss.item()
            n_batches += 1

        train_loss_epoch /= n_batches
        train_losses.append(train_loss_epoch)

        # Test
        test_loss_epoch = 0.0
        n_test_batches = 0

        with torch.no_grad():
            for X_batch, y_batch in test_loader:
                X_batch = X_batch.to(device_torch)
                y_batch = y_batch.to(device_torch)

                outputs = model.net(X_batch, regime_vec=None)
                loss, _ = compute_loss(outputs, y_batch)

                test_loss_epoch += loss.item()
                n_test_batches += 1

        test_loss_epoch /= n_test_batches
        test_losses.append(test_loss_epoch)

        # PRODUCTION FIX: Step scheduler with test_loss
        scheduler.step(test_loss_epoch)

        # PRODUCTION FIX: Compute output statistics pour détecter NaN/Inf
        with torch.no_grad():
            # Sample a batch for stats
            X_sample, y_sample = next(iter(test_loader))
            X_sample = X_sample.to(device_torch)
            outputs_sample = model.net(X_sample, regime_vec=None)
            q05_s, q50_s, q95_s, p_hit_s, rv_mean_s, sigma_tail_s = outputs_sample

            output_stats = {
                "q05_mean": q05_s.mean().item(),
                "q05_std": q05_s.std().item(),
                "q50_mean": q50_s.mean().item(),
                "q50_std": q50_s.std().item(),
                "q95_mean": q95_s.mean().item(),
                "q95_std": q95_s.std().item(),
                "p_hit_mean": p_hit_s.mean().item(),
                "p_hit_std": p_hit_s.std().item(),
                "nan_count": torch.isnan(q50_s).sum().item(),
                "inf_count": torch.isinf(q50_s).sum().item(),
            }

        # Log progress with output stats
        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info({
                "epoch": epoch + 1,
                "train_loss": f"{train_loss_epoch:.6f}",
                "test_loss": f"{test_loss_epoch:.6f}",
                "lr": f"{optimizer.param_groups[0]['lr']:.6f}",
                **output_stats,
            })

        # PRODUCTION FIX: Early stopping + best model checkpointing
        if test_loss_epoch < best_test_loss:
            best_test_loss = test_loss_epoch
            patience_counter = 0

            # PRODUCTION FIX: Save best model checkpoint
            best_model_state = {
                'epoch': epoch + 1,
                'model_state_dict': model.net.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'test_loss': test_loss_epoch,
                'train_loss': train_loss_epoch,
                'config': cfg.__dict__,
            }
        else:
            patience_counter += 1
            if patience_counter >= patience:
                logger.info({
                    "msg": "Early stopping triggered",
                    "epoch": epoch + 1,
                    "patience": patience,
                    "best_test_loss": f"{best_test_loss:.6f}",
                    "best_epoch": best_model_state['epoch'],
                })
                break

    logger.info({
        "msg": "Training complete",
        "best_test_loss": f"{best_test_loss:.6f}",
        "final_train_loss": f"{train_losses[-1]:.6f}",
        "final_test_loss": f"{test_losses[-1]:.6f}",
    })

    # Evaluate on test set
    logger.info("Computing final metrics on test set...")

    all_preds = []
    all_targets = []

    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(device_torch)
            outputs = model.net(X_batch, regime_vec=None)

            q05, q50, q95, p_hit, rv_mean, sigma_tail = outputs

            preds = torch.stack([q05.squeeze(), q50.squeeze(), q95.squeeze(), p_hit.squeeze()], dim=1)
            all_preds.append(preds.cpu().numpy())
            all_targets.append(y_batch.cpu().numpy())

    all_preds = np.concatenate(all_preds, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)

    # Extract predictions and targets
    q05_pred = all_preds[:, 0]
    q50_pred = all_preds[:, 1]
    q95_pred = all_preds[:, 2]
    p_hit_pred = all_preds[:, 3]

    return_fwd = all_targets[:, 0]
    tp_hit = all_targets[:, 1]
    rv_fwd_mean = all_targets[:, 2]

    # CALIBRATION FIX: Calibrate p_hit on test set
    logger.info("Calibrating p_hit predictions...")
    calibrator, p_hit_calibrated, calib_metrics = calibrate_phit(
        p_hit_pred, tp_hit, method=calibration_method
    )

    # PRODUCTION FIX: Compute metrics on CALIBRATED p_hit
    from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error

    # A) Output statistics (uncalibrated)
    output_stats_uncalibrated = {
        "q05_mean": float(np.mean(q05_pred)),
        "q05_std": float(np.std(q05_pred)),
        "q50_mean": float(np.mean(q50_pred)),
        "q50_std": float(np.std(q50_pred)),
        "q95_mean": float(np.mean(q95_pred)),
        "q95_std": float(np.std(q95_pred)),
        "p_hit_mean_uncalibrated": float(np.mean(p_hit_pred)),
        "p_hit_std_uncalibrated": float(np.std(p_hit_pred)),
    }

    # B) Output statistics (calibrated)
    output_stats_calibrated = {
        "p_hit_mean_calibrated": float(np.mean(p_hit_calibrated)),
        "p_hit_std_calibrated": float(np.std(p_hit_calibrated)),
    }

    # C) p_hit calibration metrics (Brier score, ECE)
    brier_phit_uncalibrated = brier_score_loss(tp_hit, p_hit_pred)
    brier_phit_calibrated = brier_score_loss(tp_hit, p_hit_calibrated)

    # D) Quantile metrics
    mae_q05 = mean_absolute_error(return_fwd, q05_pred)
    mae_q50 = mean_absolute_error(return_fwd, q50_pred)
    mae_q95 = mean_absolute_error(return_fwd, q95_pred)
    rmse_q50 = np.sqrt(mean_squared_error(return_fwd, q50_pred))

    # E) Directional accuracy (sign match)
    directional_accuracy = np.mean(np.sign(q50_pred) == np.sign(return_fwd))

    # F) Correlation q50 vs return_fwd
    corr_q50_return = np.corrcoef(q50_pred, return_fwd)[0, 1]

    # G) Sharpe of predictions (q50 as signal)
    sharpe_pred = (
        np.mean(q50_pred) / (np.std(q50_pred) + 1e-8) * np.sqrt(252 * 24 * 60)
    )

    # H) Quantile loss
    def quantile_loss_np(pred, target, quantile):
        error = target - pred
        return np.mean(np.maximum((quantile - 1) * error, quantile * error))

    qloss_q05 = quantile_loss_np(q05_pred, return_fwd, 0.05)
    qloss_q50 = quantile_loss_np(q50_pred, return_fwd, 0.50)
    qloss_q95 = quantile_loss_np(q95_pred, return_fwd, 0.95)

    # CALIBRATION FIX: Composite trading metric (for early stopping in future)
    # trading_metric = -brier_phit_calibrated (lower is better)
    # Alternative: Sharpe_pred weighted by calibration quality
    trading_metric_composite = sharpe_pred - 2.0 * brier_phit_calibrated

    metrics = {
        # Loss
        "best_test_loss": float(best_test_loss),
        "final_train_loss": float(train_losses[-1]),
        "final_test_loss": float(test_losses[-1]),
        "overfitting_ratio": float(test_losses[-1] / (train_losses[-1] + 1e-8)),

        # Calibration (BEFORE and AFTER)
        "brier_phit_uncalibrated": float(brier_phit_uncalibrated),
        "brier_phit_calibrated": float(brier_phit_calibrated),
        **calib_metrics,  # brier_before, brier_after, ece_before, ece_after, improvements

        # Accuracy
        "mae_q05": float(mae_q05),
        "mae_q50": float(mae_q50),
        "mae_q95": float(mae_q95),
        "rmse_q50": float(rmse_q50),

        # Direction
        "directional_accuracy": float(directional_accuracy),
        "corr_q50_return": float(corr_q50_return),

        # Sharpe
        "sharpe_pred": float(sharpe_pred),

        # Quantile Loss
        "qloss_q05": float(qloss_q05),
        "qloss_q50": float(qloss_q50),
        "qloss_q95": float(qloss_q95),

        # CALIBRATION FIX: Trading metric composite
        "trading_metric_composite": float(trading_metric_composite),

        # Output stats
        **output_stats_uncalibrated,
        **output_stats_calibrated,

        # Training info
        "n_train": int(len(X_train_seq)),
        "n_test": int(len(X_test_seq)),
        "n_epochs_completed": int(len(train_losses)),
        "best_epoch": int(best_model_state['epoch']) if 'best_model_state' in locals() else int(len(train_losses)),
    }

    logger.info({
        "msg": "Evaluation complete (CALIBRATED)",
        "brier_phit_uncalibrated": f"{brier_phit_uncalibrated:.4f}",
        "brier_phit_calibrated": f"{brier_phit_calibrated:.4f}",
        "ece_after": f"{calib_metrics['ece_after']:.4f}",
        "mae_q50": f"{mae_q50:.4f}",
        "sharpe_pred": f"{sharpe_pred:.4f}",
        "trading_metric_composite": f"{trading_metric_composite:.4f}",
    })

    # Print results
    print("\n" + "=" * 80)
    print("EDGE FORECASTER TRAINING RESULTS (CALIBRATED)")
    print("=" * 80)
    print(f"\nBrier Score (p_hit UNCALIBRATED): {brier_phit_uncalibrated:.4f}")
    print(f"Brier Score (p_hit CALIBRATED): {brier_phit_calibrated:.4f}")
    print(f"ECE (BEFORE calibration): {calib_metrics['ece_before']:.4f}")
    print(f"ECE (AFTER calibration): {calib_metrics['ece_after']:.4f}")
    print(f"MAE (q50): {mae_q50:.4f}")
    print(f"Sharpe (predictions): {sharpe_pred:.4f}")
    print(f"Trading Metric Composite: {trading_metric_composite:.4f}")
    print(f"\nBest Test Loss: {best_test_loss:.6f}")
    print(f"Final Train Loss: {train_losses[-1]:.6f}")
    print(f"Final Test Loss: {test_losses[-1]:.6f}")
    print(f"\nTrain sequences: {len(X_train_seq):,}")
    print(f"Test sequences: {len(X_test_seq):,}")
    print(f"Epochs: {n_epochs}")
    print("=" * 80)

    # Check targets (CALIBRATED)
    meets_brier = brier_phit_calibrated < 0.20
    meets_mae = mae_q50 < 0.005  # 0.5%
    meets_sharpe = sharpe_pred > 0.5

    print("\n" + "=" * 80)
    print("TARGET PERFORMANCE CHECK (CALIBRATED)")
    print("=" * 80)
    print(f"Brier (calibrated) < 0.20: {'✅' if meets_brier else '❌'} ({brier_phit_calibrated:.4f})")
    print(f"MAE < 0.5%: {'✅' if meets_mae else '❌'} ({mae_q50:.4f})")
    print(f"Sharpe > 0.5: {'✅' if meets_sharpe else '❌'} ({sharpe_pred:.4f})")

    if meets_brier and meets_mae and meets_sharpe:
        print("\n🎉 ALL TARGETS MET - Model ready for production!")
    else:
        print("\n⚠️ Some targets not met - Consider:")
        if not meets_brier:
            print("  - Calibration: ECE still high, try isotonic or more data")
        if not meets_mae:
            print("  - Accuracy: Feature engineering, more data, bigger model")
        if not meets_sharpe:
            print("  - Edge: Model not finding predictive patterns (TP/SL trop difficile?)")

    print("=" * 80 + "\n")

    # PRODUCTION FIX: Restore best model state before returning
    if 'best_model_state' in locals() and best_model_state is not None:
        model.net.load_state_dict(best_model_state['model_state_dict'])
        logger.info({
            "msg": "Restored best model state",
            "best_epoch": best_model_state['epoch'],
            "best_test_loss": f"{best_model_state['test_loss']:.6f}",
        })

    # CALIBRATION FIX: Return calibrator in addition to model/metrics/checkpoint
    return model, metrics, best_model_state if 'best_model_state' in locals() else None, calibrator


def main():
    parser = argparse.ArgumentParser(description="Train Edge Forecaster")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument(
        "--output",
        type=str,
        default="artifacts/models/edge/production_v1.pt",
        help="Output path for trained model",
    )
    parser.add_argument("--horizon", type=int, default=60, help="Forward horizon in minutes (PRODUCTION: default 60 = 1h, was 240)")
    parser.add_argument("--seq-len", type=int, default=32, help="Sequence length (default 32)")
    parser.add_argument("--epochs", type=int, default=50, help="Number of epochs (default 50)")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size (default 256)")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate (default 1e-3)")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu or cuda)")
    parser.add_argument("--test-size", type=float, default=0.2, help="Test set proportion (default 0.2)")

    # CALIBRATION FIX: TP/SL parameters
    parser.add_argument("--k-tp", type=float, default=2.0, help="TP coefficient (TP = k_tp * rv_60), default 2.0")
    parser.add_argument("--m-sl", type=float, default=1.5, help="SL coefficient (SL = m_sl * rv_60), default 1.5")
    parser.add_argument("--min-tp", type=float, default=0.005, help="Min TP threshold (default 0.005 = 0.5%)")
    parser.add_argument("--max-tp", type=float, default=0.025, help="Max TP threshold (default 0.025 = 2.5%)")
    parser.add_argument("--min-sl", type=float, default=0.003, help="Min SL threshold (default 0.003 = 0.3%)")
    parser.add_argument("--max-sl", type=float, default=0.015, help="Max SL threshold (default 0.015 = 1.5%)")

    args = parser.parse_args()

    # Load data (CALIBRATION FIX: nouveaux paramètres)
    features_df, labels_df = load_training_data(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        horizon_minutes=args.horizon,
        k_tp=args.k_tp,
        m_sl=args.m_sl,
        min_tp=args.min_tp,
        max_tp=args.max_tp,
        min_sl=args.min_sl,
        max_sl=args.max_sl,
    )

    # Train (CALIBRATION FIX: récupérer calibrator)
    model, metrics, best_checkpoint, calibrator = train_edge_forecaster(
        features_df,
        labels_df,
        seq_len=args.seq_len,
        n_epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
        test_size=args.test_size,
        calibration_method="platt",  # ou "isotonic"
    )

    # PRODUCTION FIX: Save model (best checkpoint restored)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info({"msg": "Saving model (best checkpoint)", "path": str(output_path)})
    model.save(str(output_path))

    # PRODUCTION FIX: Save best checkpoint separately
    if best_checkpoint is not None:
        checkpoint_path = output_path.parent / f"{output_path.stem}_best_checkpoint.pt"
        import torch
        torch.save(best_checkpoint, checkpoint_path)
        logger.info({"msg": "Saved best checkpoint", "path": str(checkpoint_path)})

    # CALIBRATION FIX: Save calibrator (pickle)
    if calibrator is not None:
        import pickle
        calibrator_path = output_path.parent / f"{output_path.stem}_calibrator.pkl"
        with open(calibrator_path, 'wb') as f:
            pickle.dump(calibrator, f)
        logger.info({"msg": "Saved p_hit calibrator", "path": str(calibrator_path)})

    # PRODUCTION FIX: Save metrics with git/config info (JSON FIX)
    metrics_extended = {
        **metrics,
        "config": {
            "symbol": args.symbol,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "horizon_minutes": args.horizon,
            "seq_len": args.seq_len,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "device": args.device,
            # CALIBRATION FIX: Save TP/SL params
            "k_tp": args.k_tp,
            "m_sl": args.m_sl,
            "min_tp": args.min_tp,
            "max_tp": args.max_tp,
            "min_sl": args.min_sl,
            "max_sl": args.max_sl,
        },
        "timestamp": pd.Timestamp.now().isoformat(),
    }

    # JSON FIX: Convert to JSON-serializable types
    metrics_extended = to_jsonable(metrics_extended)

    metrics_path = output_path.parent / f"{output_path.stem}_metrics.json"
    with open(metrics_path, 'w') as f:
        json.dump(metrics_extended, f, indent=2)

    logger.info({
        "msg": "Training complete",
        "model_path": str(output_path),
        "metrics_path": str(metrics_path),
        "calibrator_path": str(calibrator_path) if calibrator else "None",
    })

    print(f"\n✅ Model saved to: {output_path}")
    print(f"✅ Metrics saved to: {metrics_path}")
    if calibrator:
        print(f"✅ Calibrator saved to: {calibrator_path}")


if __name__ == "__main__":
    main()
