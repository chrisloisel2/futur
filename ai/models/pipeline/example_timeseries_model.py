"""Example usage of time series deep learning models."""
import torch
import pytorch_lightning as pl
from torch.utils.data import DataLoader, TensorDataset
import numpy as np

from models import TimeSeriesLightningModule


def create_synthetic_data(n_samples=1000, seq_len=96, pred_len=24, n_features=7):
    """
    Create synthetic time series data for demonstration.

    Args:
        n_samples: Number of samples
        seq_len: Input sequence length
        pred_len: Prediction length
        n_features: Number of features

    Returns:
        train_loader, val_loader, test_loader
    """
    print(f"\nGenerating synthetic data...")
    print(f"  Samples: {n_samples}")
    print(f"  Sequence length: {seq_len}")
    print(f"  Prediction length: {pred_len}")
    print(f"  Features: {n_features}")

    # Generate random time series with trend + seasonality
    t = np.linspace(0, 10 * np.pi, n_samples + seq_len + pred_len)

    data = []
    for i in range(n_features):
        # Trend
        trend = np.linspace(0, 10, len(t))

        # Seasonality (multiple periods)
        seasonal = (
            np.sin(t * (i + 1)) +
            0.5 * np.sin(t * (i + 1) * 2) +
            0.25 * np.sin(t * (i + 1) * 4)
        )

        # Noise
        noise = np.random.randn(len(t)) * 0.1

        series = trend + seasonal + noise
        data.append(series)

    data = np.stack(data, axis=1)  # [time, features]

    # Create sliding windows
    X, y = [], []
    for i in range(n_samples):
        X.append(data[i:i + seq_len])
        y.append(data[i + seq_len:i + seq_len + pred_len])

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)

    print(f"  X shape: {X.shape}")
    print(f"  y shape: {y.shape}")

    # Split: 70% train, 15% val, 15% test
    train_size = int(0.7 * n_samples)
    val_size = int(0.15 * n_samples)

    X_train, y_train = X[:train_size], y[:train_size]
    X_val, y_val = X[train_size:train_size + val_size], y[train_size:train_size + val_size]
    X_test, y_test = X[train_size + val_size:], y[train_size + val_size:]

    # Create datasets
    train_dataset = TensorDataset(
        torch.from_numpy(X_train),
        torch.from_numpy(y_train),
    )
    val_dataset = TensorDataset(
        torch.from_numpy(X_val),
        torch.from_numpy(y_val),
    )
    test_dataset = TensorDataset(
        torch.from_numpy(X_test),
        torch.from_numpy(y_test),
    )

    # Create dataloaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=32,
        shuffle=True,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=32,
        shuffle=False,
        num_workers=0,
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=32,
        shuffle=False,
        num_workers=0,
    )

    print(f"  Train batches: {len(train_loader)}")
    print(f"  Val batches: {len(val_loader)}")
    print(f"  Test batches: {len(test_loader)}")

    return train_loader, val_loader, test_loader


def collate_fn(batch):
    """Custom collate function for dataloader."""
    x, y = zip(*batch)
    return {
        "x": torch.stack(x),
        "y": torch.stack(y),
    }


def main():
    """Run time series model training example."""
    print("=" * 80)
    print("TIME SERIES DEEP LEARNING - TRAINING EXAMPLE")
    print("=" * 80)

    # Configuration
    seq_len = 96  # 96 hours input
    pred_len = 24  # 24 hours prediction
    n_features = 7
    embedding_dim = 256

    # Create data
    train_loader, val_loader, test_loader = create_synthetic_data(
        n_samples=1000,
        seq_len=seq_len,
        pred_len=pred_len,
        n_features=n_features,
    )

    # Wrap with custom collate
    train_loader.collate_fn = collate_fn
    val_loader.collate_fn = collate_fn
    test_loader.collate_fn = collate_fn

    # Initialize model
    print("\n" + "=" * 80)
    print("INITIALIZING MODEL")
    print("=" * 80)

    model = TimeSeriesLightningModule(
        seq_len=seq_len,
        pred_len=pred_len,
        enc_in=n_features,
        embedding_dim=embedding_dim,
        # Training config
        learning_rate=1e-3,
        weight_decay=1e-5,
        use_sam=True,  # Use Sharpness-Aware Minimization
        sam_rho=0.05,
        seasonal_period=24,  # 24 hours seasonality
        # Model architecture
        dlinear_individual=False,
        timesnet_d_model=64,
        timesnet_layers=2,
        timesnet_top_k=5,
        transformer_d_model=256,
        transformer_n_heads=8,
        transformer_n_layers=3,
        dropout=0.1,
    )

    print(f"\nModel configuration:")
    print(f"  Input: [{seq_len}, {n_features}]")
    print(f"  Output: [{pred_len}, {n_features}]")
    print(f"  Embedding dimension: {embedding_dim}")
    print(f"  Using SAM optimizer: {model.use_sam}")

    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    print(f"\nModel parameters:")
    print(f"  Total: {total_params:,}")
    print(f"  Trainable: {trainable_params:,}")

    # Callbacks
    callbacks = [
        pl.callbacks.ModelCheckpoint(
            monitor="val/loss",
            mode="min",
            save_top_k=3,
            filename="timeseries-{epoch:02d}-{val_loss:.4f}",
        ),
        pl.callbacks.EarlyStopping(
            monitor="val/loss",
            patience=10,
            mode="min",
        ),
        pl.callbacks.LearningRateMonitor(logging_interval="epoch"),
    ]

    # Trainer
    print("\n" + "=" * 80)
    print("TRAINING")
    print("=" * 80)

    trainer = pl.Trainer(
        max_epochs=50,
        accelerator="auto",  # Use GPU if available
        devices=1,
        callbacks=callbacks,
        logger=pl.loggers.CSVLogger("logs/"),
        log_every_n_steps=10,
        gradient_clip_val=1.0,
        deterministic=False,
    )

    # Train
    trainer.fit(model, train_loader, val_loader)

    # Test
    print("\n" + "=" * 80)
    print("TESTING")
    print("=" * 80)

    trainer.test(model, test_loader)

    # Predictions
    print("\n" + "=" * 80)
    print("PREDICTION EXAMPLE")
    print("=" * 80)

    model.eval()
    with torch.no_grad():
        # Get one batch
        batch = next(iter(test_loader))
        x = batch["x"]
        y_true = batch["y"]

        # Predict
        outputs = model.predict_step({"x": x}, 0)

        embeddings = outputs["embeddings"]
        predictions = outputs["predictions"]

        print(f"\nInput shape: {x.shape}")
        print(f"True output shape: {y_true.shape}")
        print(f"Predicted shape: {predictions.shape}")
        print(f"Embeddings shape: {embeddings.shape}")

        # Compute metrics
        mae = torch.mean(torch.abs(predictions - y_true))
        mse = torch.mean((predictions - y_true) ** 2)
        rmse = torch.sqrt(mse)

        print(f"\nMetrics on test batch:")
        print(f"  MAE: {mae:.4f}")
        print(f"  MSE: {mse:.4f}")
        print(f"  RMSE: {rmse:.4f}")

        # Show branch predictions
        print(f"\nBranch predictions:")
        print(f"  DLinear: {outputs['dlinear_pred'].shape}")
        print(f"  TimesNet: {outputs['timesnet_pred'].shape}")

    print("\n" + "=" * 80)
    print("EXAMPLE COMPLETE")
    print("=" * 80)

    print("\nModel saved to: lightning_logs/")
    print("You can now use the trained model for:")
    print("  1. Forecasting future values")
    print("  2. Extracting 256D embeddings for downstream tasks")
    print("  3. Transfer learning on new datasets")

    return model, trainer


if __name__ == "__main__":
    # Set seed for reproducibility
    pl.seed_everything(42)

    model, trainer = main()
