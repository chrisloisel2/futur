"""
Example: Test different fusion strategies for combining time series and tabular embeddings.
"""
import torch
import torch.nn as nn
import pytorch_lightning as pl
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
import time

from models import TimeSeriesBackbone
from models.tabular import FTTransformer
from models.fusion import FusionStrategy, AdvancedFusionModule


def create_synthetic_multimodal_data(
    n_samples: int = 1000,
    seq_len: int = 96,
    n_ts_features: int = 7,
    n_tab_features: int = 20,
    n_classes: int = 2,
):
    """
    Create synthetic data for testing fusion.

    Returns:
        X_ts: [n_samples, seq_len, n_ts_features] time series
        X_tab: [n_samples, n_tab_features] tabular
        y: [n_samples] targets
    """
    print(f"\nGenerating synthetic multimodal data...")
    print(f"  Samples: {n_samples}")
    print(f"  Time series: [{seq_len}, {n_ts_features}]")
    print(f"  Tabular: {n_tab_features} features")
    print(f"  Classes: {n_classes}")

    # Time series: trend + seasonality + noise
    t = np.linspace(0, 10 * np.pi, n_samples + seq_len)

    X_ts = []
    for i in range(n_samples):
        sample = []
        for j in range(n_ts_features):
            # Different patterns per feature
            trend = np.linspace(0, 1, seq_len)
            seasonal = np.sin(t[i:i+seq_len] * (j + 1))
            noise = np.random.randn(seq_len) * 0.1

            series = trend + seasonal + noise
            sample.append(series)

        X_ts.append(np.array(sample).T)  # [seq_len, n_ts_features]

    X_ts = np.array(X_ts, dtype=np.float32)

    # Tabular: random features with some structure
    X_tab = np.random.randn(n_samples, n_tab_features).astype(np.float32)

    # Add structure: some features correlated with time series
    for i in range(min(5, n_tab_features)):
        X_tab[:, i] = X_ts[:, -1, i % n_ts_features] + np.random.randn(n_samples) * 0.1

    # Target: based on both modalities
    # Time series contribution: last value
    ts_signal = X_ts[:, -1, 0]

    # Tabular contribution: sum of first 5 features
    tab_signal = X_tab[:, :5].sum(axis=1)

    # Combined signal
    combined_signal = 0.6 * ts_signal + 0.4 * tab_signal

    # Binary classification
    y = (combined_signal > np.median(combined_signal)).astype(np.int64)

    print(f"  Class distribution: {np.bincount(y)}")

    return X_ts, X_tab, y


class FusedModel(nn.Module):
    """
    Model combining time series and tabular branches with fusion.
    """

    def __init__(
        self,
        # Time series config
        seq_len: int,
        n_ts_features: int,
        timeseries_dim: int = 256,
        # Tabular config
        n_tab_features: int,
        tabular_dim: int = 128,
        # Fusion config
        fusion_strategy: str = "adaptive",
        fusion_dim: int = 384,
        n_classes: int = 2,
        **fusion_kwargs,
    ):
        """
        Initialize fused model.
        """
        super().__init__()

        self.seq_len = seq_len
        self.fusion_strategy = fusion_strategy

        # Time series branch
        self.timeseries_branch = TimeSeriesBackbone(
            seq_len=seq_len,
            pred_len=24,  # Not used for embedding extraction
            enc_in=n_ts_features,
            embedding_dim=timeseries_dim,
            dlinear_individual=False,
            timesnet_d_model=64,
            timesnet_layers=2,
            timesnet_top_k=3,
            transformer_d_model=128,
            transformer_n_heads=4,
            transformer_n_layers=2,
            dropout=0.1,
        )

        # Tabular branch
        self.tabular_branch = FTTransformer(
            n_features=n_tab_features,
            n_classes=None,  # Just embeddings
            d_token=32,
            n_blocks=2,
            attention_n_heads=4,
            embedding_dim=tabular_dim,
            dropout=0.1,
        )

        # Fusion module
        self.fusion = FusionStrategy(
            strategy=fusion_strategy,
            timeseries_dim=timeseries_dim,
            tabular_dim=tabular_dim,
            fusion_dim=fusion_dim,
            seq_len=seq_len,
            **fusion_kwargs,
        )

        # Classification head
        self.classifier = nn.Sequential(
            nn.Linear(fusion_dim, fusion_dim // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(fusion_dim // 2, n_classes),
        )

    def forward(self, x_ts: torch.Tensor, x_tab: torch.Tensor) -> dict:
        """
        Forward pass.

        Args:
            x_ts: [batch, seq_len, n_ts_features]
            x_tab: [batch, n_tab_features]

        Returns:
            Dict with logits and fusion info
        """
        # Get embeddings from each branch
        ts_embedding = self.timeseries_branch(x_ts)  # [batch, timeseries_dim]
        tab_embedding = self.tabular_branch(x_tab, return_embedding=True)  # [batch, tabular_dim]

        # Fuse embeddings
        fusion_output = self.fusion(
            ts_embedding,
            tab_embedding,
            timeseries_input=x_ts if self.fusion_strategy == "adaptive" else None,
        )

        fused_embedding = fusion_output["fused_embedding"]

        # Classification
        logits = self.classifier(fused_embedding)

        return {
            "logits": logits,
            "ts_embedding": ts_embedding,
            "tab_embedding": tab_embedding,
            "fused_embedding": fused_embedding,
            **fusion_output,
        }


def test_fusion_strategy(
    strategy: str,
    X_ts_train: np.ndarray,
    X_tab_train: np.ndarray,
    y_train: np.ndarray,
    X_ts_test: np.ndarray,
    X_tab_test: np.ndarray,
    y_test: np.ndarray,
    n_epochs: int = 20,
    **fusion_kwargs,
):
    """
    Test a fusion strategy.

    Returns:
        metrics: Dict with test accuracy and training time
    """
    print(f"\n{'='*80}")
    print(f"Testing Fusion Strategy: {strategy.upper()}")
    print(f"{'='*80}")

    seq_len = X_ts_train.shape[1]
    n_ts_features = X_ts_train.shape[2]
    n_tab_features = X_tab_train.shape[1]
    n_classes = len(np.unique(y_train))

    # Model
    model = FusedModel(
        seq_len=seq_len,
        n_ts_features=n_ts_features,
        n_tab_features=n_tab_features,
        fusion_strategy=strategy,
        n_classes=n_classes,
        **fusion_kwargs,
    )

    # Count parameters
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Model parameters: {n_params:,}")

    # Data
    train_dataset = TensorDataset(
        torch.FloatTensor(X_ts_train),
        torch.FloatTensor(X_tab_train),
        torch.LongTensor(y_train),
    )

    test_dataset = TensorDataset(
        torch.FloatTensor(X_ts_test),
        torch.FloatTensor(X_tab_test),
        torch.LongTensor(y_test),
    )

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    # Training
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-5)
    criterion = nn.CrossEntropyLoss()

    start_time = time.time()

    for epoch in range(n_epochs):
        model.train()
        train_loss = 0
        train_correct = 0
        train_total = 0

        for x_ts, x_tab, y in train_loader:
            optimizer.zero_grad()

            outputs = model(x_ts, x_tab)
            logits = outputs["logits"]

            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            _, predicted = torch.max(logits, 1)
            train_total += y.size(0)
            train_correct += (predicted == y).sum().item()

        train_acc = train_correct / train_total

        if (epoch + 1) % 5 == 0:
            print(f"Epoch {epoch+1}/{n_epochs} - Loss: {train_loss/len(train_loader):.4f}, Acc: {train_acc:.4f}")

    training_time = time.time() - start_time

    # Test
    model.eval()
    test_correct = 0
    test_total = 0

    all_regime_probs = []
    all_gating_weights = []

    with torch.no_grad():
        for x_ts, x_tab, y in test_loader:
            outputs = model(x_ts, x_tab)
            logits = outputs["logits"]

            _, predicted = torch.max(logits, 1)
            test_total += y.size(0)
            test_correct += (predicted == y).sum().item()

            # Collect fusion info if available
            if "regime_probs" in outputs:
                all_regime_probs.append(outputs["regime_probs"].cpu().numpy())
            if "gating_weights" in outputs:
                all_gating_weights.append(outputs["gating_weights"].cpu().numpy())

    test_acc = test_correct / test_total

    print(f"\nResults:")
    print(f"  Test Accuracy: {test_acc:.4f}")
    print(f"  Training Time: {training_time:.2f}s")
    print(f"  Parameters: {n_params:,}")

    # Additional analysis for adaptive strategy
    if strategy == "adaptive" and all_regime_probs:
        regime_probs = np.concatenate(all_regime_probs, axis=0)
        avg_regime_probs = regime_probs.mean(axis=0)

        print(f"\nAverage Regime Probabilities:")
        regime_names = ["Trending", "Mean-Reverting", "Volatile", "Stable"]
        for i, (name, prob) in enumerate(zip(regime_names, avg_regime_probs)):
            print(f"  {name}: {prob:.3f}")

        if all_gating_weights:
            gating_weights = np.concatenate(all_gating_weights, axis=0)
            avg_gating = gating_weights.mean(axis=0)

            print(f"\nAverage Gating Weights:")
            print(f"  Time Series: {avg_gating[0]:.3f}")
            print(f"  Tabular: {avg_gating[1]:.3f}")

    return {
        "test_accuracy": test_acc,
        "training_time": training_time,
        "n_params": n_params,
    }


def main():
    """
    Compare different fusion strategies.
    """
    print("=" * 80)
    print("FUSION STRATEGY COMPARISON")
    print("=" * 80)

    # Set seed
    np.random.seed(42)
    torch.manual_seed(42)

    # Generate data
    X_ts, X_tab, y = create_synthetic_multimodal_data(
        n_samples=1000,
        seq_len=96,
        n_ts_features=7,
        n_tab_features=20,
        n_classes=2,
    )

    # Split
    train_size = int(0.8 * len(X_ts))

    X_ts_train, X_ts_test = X_ts[:train_size], X_ts[train_size:]
    X_tab_train, X_tab_test = X_tab[:train_size], X_tab[train_size:]
    y_train, y_test = y[:train_size], y[train_size:]

    print(f"\nData split:")
    print(f"  Train: {len(X_ts_train)} samples")
    print(f"  Test: {len(X_ts_test)} samples")

    # Test different strategies
    strategies = {
        "concat": {},
        "weighted": {},
        "adaptive": {
            "n_heads": 4,
            "n_regimes": 4,
            "meta_window": 24,
        },
    }

    results = {}

    for strategy, kwargs in strategies.items():
        try:
            metrics = test_fusion_strategy(
                strategy,
                X_ts_train,
                X_tab_train,
                y_train,
                X_ts_test,
                X_tab_test,
                y_test,
                n_epochs=20,
                **kwargs,
            )
            results[strategy] = metrics
        except Exception as e:
            print(f"\nError testing {strategy}: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)

    print(f"\n{'Strategy':<15} {'Accuracy':<12} {'Train Time':<15} {'Parameters':<15}")
    print("-" * 80)

    for strategy, metrics in results.items():
        print(
            f"{strategy:<15} "
            f"{metrics['test_accuracy']:<12.4f} "
            f"{metrics['training_time']:<15.2f} "
            f"{metrics['n_params']:<15,}"
        )

    # Best strategy
    if results:
        best_strategy = max(results.items(), key=lambda x: x[1]["test_accuracy"])
        print(f"\n{'='*80}")
        print(f"Best Strategy: {best_strategy[0]} (Accuracy: {best_strategy[1]['test_accuracy']:.4f})")
        print(f"{'='*80}")

    return results


if __name__ == "__main__":
    results = main()
