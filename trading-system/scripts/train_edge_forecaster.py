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


def generate_forward_labels(
    df: pd.DataFrame,
    horizon_minutes: int = 240,  # 4 hours
    tp_threshold: float = 0.01,  # 1% take profit
) -> pd.DataFrame:
    """
    Generate forward-looking labels for training.

    Creates:
        - return_fwd: Future return at horizon
        - tp_hit: Binary flag if TP was hit
        - rv_fwd_mean: Forward realized volatility

    Args:
        df: DataFrame with OHLCV data
        horizon_minutes: Forward horizon in minutes (default 240 = 4h)
        tp_threshold: Take profit threshold (default 0.01 = 1%)

    Returns:
        DataFrame with original data + forward labels
    """
    df = df.copy()

    logger.info({
        "msg": "Generating forward labels",
        "horizon_minutes": horizon_minutes,
        "tp_threshold": tp_threshold,
    })

    # Forward return (close-to-close)
    df['return_fwd'] = df['close'].pct_change(periods=horizon_minutes).shift(-horizon_minutes)

    # TP hit (binary): did price reach tp_threshold at any point in horizon?
    # Approximation: max return in next horizon periods
    df['max_return_fwd'] = (
        df['high'].rolling(horizon_minutes).max().shift(-horizon_minutes) / df['close'] - 1.0
    )
    df['min_return_fwd'] = (
        df['low'].rolling(horizon_minutes).min().shift(-horizon_minutes) / df['close'] - 1.0
    )

    df['tp_hit'] = (
        (df['max_return_fwd'] >= tp_threshold) |  # Long TP hit
        (df['min_return_fwd'] <= -tp_threshold)   # Short TP hit
    ).astype(int)

    # Forward realized volatility (mean of |returns| in horizon)
    df['rv_fwd_mean'] = (
        df['close'].pct_change().abs().rolling(horizon_minutes).mean().shift(-horizon_minutes)
    )

    # Drop intermediate columns
    df = df.drop(columns=['max_return_fwd', 'min_return_fwd'])

    # Count valid labels
    n_valid = df[['return_fwd', 'tp_hit', 'rv_fwd_mean']].notna().all(axis=1).sum()

    logger.info({
        "msg": "Forward labels generated",
        "total_rows": len(df),
        "valid_labels": n_valid,
        "coverage": f"{n_valid / len(df):.2%}",
        "tp_hit_rate": f"{df['tp_hit'].mean():.2%}",
    })

    return df


def load_training_data(
    symbol: str,
    start_date: str,
    end_date: str,
    horizon_minutes: int = 240,
    tp_threshold: float = 0.01,
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

    # Generate forward labels
    df = generate_forward_labels(df, horizon_minutes, tp_threshold)

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
    optimizer = torch.optim.AdamW(model.net.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=n_epochs)

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

    best_test_loss = float('inf')
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

        scheduler.step()

        # Log progress
        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info({
                "epoch": epoch + 1,
                "train_loss": f"{train_loss_epoch:.6f}",
                "test_loss": f"{test_loss_epoch:.6f}",
                "lr": f"{scheduler.get_last_lr()[0]:.6f}",
            })

        # Save best model
        if test_loss_epoch < best_test_loss:
            best_test_loss = test_loss_epoch

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

    # Metrics
    from sklearn.metrics import brier_score_loss, mean_absolute_error

    # p_hit calibration (Brier score)
    brier_phit = brier_score_loss(all_targets[:, 1], all_preds[:, 3])

    # q50 accuracy (MAE)
    mae_q50 = mean_absolute_error(all_targets[:, 0], all_preds[:, 1])

    # Sharpe of predictions (q50 as signal)
    predicted_returns = all_preds[:, 1]
    sharpe_pred = (
        np.mean(predicted_returns) / (np.std(predicted_returns) + 1e-8) * np.sqrt(252 * 24 * 60)
    )

    metrics = {
        "brier_phit": brier_phit,
        "mae_q50": mae_q50,
        "sharpe_pred": sharpe_pred,
        "best_test_loss": best_test_loss,
        "final_train_loss": train_losses[-1],
        "final_test_loss": test_losses[-1],
        "n_train": len(X_train_seq),
        "n_test": len(X_test_seq),
        "n_epochs": n_epochs,
    }

    logger.info({
        "msg": "Evaluation complete",
        "brier_phit": f"{brier_phit:.4f}",
        "mae_q50": f"{mae_q50:.4f}",
        "sharpe_pred": f"{sharpe_pred:.4f}",
    })

    # Print results
    print("\n" + "=" * 80)
    print("EDGE FORECASTER TRAINING RESULTS")
    print("=" * 80)
    print(f"\nBrier Score (p_hit): {brier_phit:.4f}")
    print(f"MAE (q50): {mae_q50:.4f}")
    print(f"Sharpe (predictions): {sharpe_pred:.4f}")
    print(f"\nBest Test Loss: {best_test_loss:.6f}")
    print(f"Final Train Loss: {train_losses[-1]:.6f}")
    print(f"Final Test Loss: {test_losses[-1]:.6f}")
    print(f"\nTrain sequences: {len(X_train_seq):,}")
    print(f"Test sequences: {len(X_test_seq):,}")
    print(f"Epochs: {n_epochs}")
    print("=" * 80)

    # Check targets
    meets_brier = brier_phit < 0.20
    meets_mae = mae_q50 < 0.005  # 0.5%
    meets_sharpe = sharpe_pred > 0.5

    print("\n" + "=" * 80)
    print("TARGET PERFORMANCE CHECK")
    print("=" * 80)
    print(f"Brier < 0.20: {'✅' if meets_brier else '❌'} ({brier_phit:.4f})")
    print(f"MAE < 0.5%: {'✅' if meets_mae else '❌'} ({mae_q50:.4f})")
    print(f"Sharpe > 0.5: {'✅' if meets_sharpe else '❌'} ({sharpe_pred:.4f})")

    if meets_brier and meets_mae and meets_sharpe:
        print("\n🎉 ALL TARGETS MET - Model ready for production!")
    else:
        print("\n⚠️ Some targets not met - Consider:")
        if not meets_brier:
            print("  - Calibration: Train longer or add calibration layer")
        if not meets_mae:
            print("  - Accuracy: Feature engineering, more data, bigger model")
        if not meets_sharpe:
            print("  - Edge: Model not finding predictive patterns")

    print("=" * 80 + "\n")

    return model, metrics


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
    parser.add_argument("--horizon", type=int, default=240, help="Forward horizon in minutes (default 240 = 4h)")
    parser.add_argument("--tp-threshold", type=float, default=0.01, help="Take profit threshold (default 0.01 = 1%)")
    parser.add_argument("--seq-len", type=int, default=32, help="Sequence length (default 32)")
    parser.add_argument("--epochs", type=int, default=50, help="Number of epochs (default 50)")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size (default 256)")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate (default 1e-3)")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu or cuda)")
    parser.add_argument("--test-size", type=float, default=0.2, help="Test set proportion (default 0.2)")

    args = parser.parse_args()

    # Load data
    features_df, labels_df = load_training_data(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        horizon_minutes=args.horizon,
        tp_threshold=args.tp_threshold,
    )

    # Train
    model, metrics = train_edge_forecaster(
        features_df,
        labels_df,
        seq_len=args.seq_len,
        n_epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
        test_size=args.test_size,
    )

    # Save model
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info({"msg": "Saving model", "path": str(output_path)})
    model.save(str(output_path))

    # Save metrics
    metrics_path = output_path.parent / f"{output_path.stem}_metrics.json"
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, indent=2)

    logger.info({
        "msg": "Training complete",
        "model_path": str(output_path),
        "metrics_path": str(metrics_path),
    })

    print(f"\n✅ Model saved to: {output_path}")
    print(f"✅ Metrics saved to: {metrics_path}")


if __name__ == "__main__":
    main()
