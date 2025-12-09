"""
Example: Train Decision Transformer for crypto trading.
"""
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import numpy as np
import matplotlib.pyplot as plt

from models.decision_transformer import (
    DecisionTransformer,
    TrajectoryDataset,
    create_trading_trajectories,
    train_decision_transformer,
)


def create_synthetic_trading_data(
    n_timesteps: int = 10000,
    n_features: int = 20,
):
    """
    Create synthetic crypto trading data.

    Returns:
        prices: [n_timesteps] price series
        features: [n_timesteps, n_features] feature matrix
    """
    print(f"\nGenerating synthetic trading data...")
    print(f"  Timesteps: {n_timesteps}")
    print(f"  Features: {n_features}")

    # Generate price series (geometric Brownian motion)
    np.random.seed(42)

    dt = 1.0  # 1 hour
    mu = 0.0001  # drift
    sigma = 0.02  # volatility

    prices = np.zeros(n_timesteps)
    prices[0] = 100.0  # Initial price

    for t in range(1, n_timesteps):
        dW = np.random.randn() * np.sqrt(dt)
        prices[t] = prices[t-1] * np.exp((mu - 0.5 * sigma**2) * dt + sigma * dW)

    # Generate features (technical indicators + noise)
    features = np.zeros((n_timesteps, n_features))

    for i in range(n_features):
        # Mix of trend, seasonality, and noise
        if i < 5:
            # Price-based features (returns, moving averages, etc.)
            features[:, i] = (prices - np.mean(prices)) / np.std(prices)
            features[:, i] += np.random.randn(n_timesteps) * 0.1
        elif i < 10:
            # Momentum features
            features[:, i] = np.gradient(prices)
            features[:, i] = (features[:, i] - np.mean(features[:, i])) / np.std(features[:, i])
        else:
            # Random noise features
            features[:, i] = np.random.randn(n_timesteps)

    print(f"  Price range: [{prices.min():.2f}, {prices.max():.2f}]")
    print(f"  Features mean: {features.mean():.3f}, std: {features.std():.3f}")

    return prices, features


def main():
    """
    Train Decision Transformer on synthetic trading data.
    """
    print("=" * 80)
    print("DECISION TRANSFORMER FOR CRYPTO TRADING")
    print("=" * 80)

    # Configuration
    n_timesteps = 10000
    n_features = 20
    state_dim = n_features
    action_dim = 3  # Sell, Hold, Buy
    context_len = 100
    target_returns = [0.01, 0.03, 0.05]  # 1%, 3%, 5%
    turnover_penalty = 0.001

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\nDevice: {device}")

    # 1. Generate data
    prices, features = create_synthetic_trading_data(n_timesteps, n_features)

    # 2. Create trajectories
    print("\n" + "="*80)
    print("CREATING TRAJECTORIES")
    print("="*80)

    trajectories = create_trading_trajectories(
        prices=prices,
        features=features,
        target_returns=target_returns,
        turnover_penalty=turnover_penalty,
        lookback=context_len,
    )

    print(f"\nCreated {len(trajectories['states'])} trajectory segments")
    print(f"Target returns: {target_returns}")
    print(f"Turnover penalty: {turnover_penalty}")

    # Statistics
    all_actions = np.concatenate(trajectories["actions"])
    action_dist = np.bincount(all_actions.astype(int), minlength=3)
    print(f"\nAction distribution:")
    print(f"  Sell (0): {action_dist[0]} ({action_dist[0]/len(all_actions)*100:.1f}%)")
    print(f"  Hold (1): {action_dist[1]} ({action_dist[1]/len(all_actions)*100:.1f}%)")
    print(f"  Buy (2): {action_dist[2]} ({action_dist[2]/len(all_actions)*100:.1f}%)")

    # 3. Create datasets
    print("\n" + "="*80)
    print("CREATING DATASETS")
    print("="*80)

    # Split train/val
    n_train = int(0.8 * len(trajectories["states"]))

    train_dataset = TrajectoryDataset(
        states=np.array(trajectories["states"][:n_train], dtype=object),
        actions=np.array(trajectories["actions"][:n_train], dtype=object),
        rewards=np.array(trajectories["rewards"][:n_train], dtype=object),
        returns_to_go=np.array(trajectories["returns_to_go"][:n_train], dtype=object),
        timesteps=np.array(trajectories["timesteps"][:n_train], dtype=object),
        max_len=context_len,
    )

    val_dataset = TrajectoryDataset(
        states=np.array(trajectories["states"][n_train:], dtype=object),
        actions=np.array(trajectories["actions"][n_train:], dtype=object),
        rewards=np.array(trajectories["rewards"][n_train:], dtype=object),
        returns_to_go=np.array(trajectories["returns_to_go"][n_train:], dtype=object),
        timesteps=np.array(trajectories["timesteps"][n_train:], dtype=object),
        max_len=context_len,
    )

    train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=64, shuffle=False, num_workers=0)

    print(f"\nTrain set: {len(train_dataset)} trajectories")
    print(f"Val set: {len(val_dataset)} trajectories")
    print(f"Batch size: 64")

    # 4. Initialize model
    print("\n" + "="*80)
    print("INITIALIZING MODEL")
    print("="*80)

    model = DecisionTransformer(
        state_dim=state_dim,
        action_dim=action_dim,
        d_model=128,
        n_heads=4,
        n_layers=3,
        d_ff=512,
        dropout=0.1,
        max_context_len=context_len,
    )

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"\nModel: DecisionTransformer")
    print(f"  State dim: {state_dim}")
    print(f"  Action dim: {action_dim}")
    print(f"  d_model: 128")
    print(f"  n_layers: 3")
    print(f"  n_heads: 4")
    print(f"  Context length: {context_len}")
    print(f"  Parameters: {n_params:,}")

    # 5. Train
    print("\n" + "="*80)
    print("TRAINING")
    print("="*80)

    history = train_decision_transformer(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        n_epochs=50,
        lr=1e-4,
        device=device,
    )

    # 6. Evaluate
    print("\n" + "="*80)
    print("EVALUATION")
    print("="*80)

    final_train_loss = history["train_loss"][-1]
    final_val_loss = history["val_loss"][-1]
    final_val_acc = history["val_accuracy"][-1]

    print(f"\nFinal Results:")
    print(f"  Train Loss: {final_train_loss:.4f}")
    print(f"  Val Loss: {final_val_loss:.4f}")
    print(f"  Val Accuracy: {final_val_acc:.4f}")

    # 7. Test inference
    print("\n" + "="*80)
    print("INFERENCE TEST")
    print("="*80)

    model.eval()

    # Get a test batch
    test_batch = next(iter(val_loader))

    with torch.no_grad():
        states = test_batch["states"].to(device)
        actions = test_batch["actions"].to(device)
        rtgs = test_batch["rtgs"].to(device)
        timesteps = test_batch["timesteps"].to(device)

        # Predict actions
        action_preds, action_probs = model.get_action(
            states[:, :-1, :],  # All but last
            actions[:, :-1],
            rtgs[:, :-1, :],
            timesteps[:, :-1],
            deterministic=True,
        )

        # Compare with actual
        actual_actions = actions[:, -1]

        correct = (action_preds == actual_actions).sum().item()
        total = len(action_preds)

        print(f"\nInference on test batch:")
        print(f"  Batch size: {total}")
        print(f"  Correct predictions: {correct}/{total} ({correct/total*100:.1f}%)")

        # Show some examples
        print(f"\nExample predictions (first 5):")
        for i in range(min(5, total)):
            pred = action_preds[i].item()
            actual = actual_actions[i].item()
            rtg = rtgs[i, -1, 0].item()

            action_names = {0: "Sell", 1: "Hold", 2: "Buy"}

            match = "✓" if pred == actual else "✗"
            print(f"  {match} RTG: {rtg:+.4f}, Pred: {action_names[pred]}, Actual: {action_names[actual]}")

    # 8. Test different RTG conditions
    print("\n" + "="*80)
    print("RTG CONDITIONING TEST")
    print("="*80)

    with torch.no_grad():
        # Use first trajectory from val set
        test_state = states[0:1, :50, :]  # First 50 timesteps
        test_actions = actions[0:1, :50]
        test_timesteps = timesteps[0:1, :50]

        # Test with different RTG values
        test_rtgs = [0.01, 0.03, 0.05]  # 1%, 3%, 5% target returns

        print(f"\nTesting with different return-to-go targets:")
        for target_rtg in test_rtgs:
            # Create constant RTG
            rtg_tensor = torch.full((1, 50, 1), target_rtg, device=device)

            # Get action
            action, probs = model.get_action(
                test_state,
                test_actions,
                rtg_tensor,
                test_timesteps,
                deterministic=True,
            )

            action_names = {0: "Sell", 1: "Hold", 2: "Buy"}
            print(f"  RTG={target_rtg:.2%}: Action={action_names[action.item()]}, "
                  f"Probs={probs[0].cpu().numpy()}")

    # 9. Plot training curves
    print("\n" + "="*80)
    print("PLOTTING RESULTS")
    print("="*80)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    # Loss curves
    axes[0].plot(history["train_loss"], label="Train Loss")
    axes[0].plot(history["val_loss"], label="Val Loss")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].set_title("Training Loss")
    axes[0].legend()
    axes[0].grid(True)

    # Accuracy curve
    axes[1].plot(history["val_accuracy"], label="Val Accuracy")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Accuracy")
    axes[1].set_title("Validation Accuracy")
    axes[1].legend()
    axes[1].grid(True)

    plt.tight_layout()
    plt.savefig("decision_transformer_training.png", dpi=150, bbox_inches="tight")
    print("\nSaved training curves to: decision_transformer_training.png")

    # 10. Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    print(f"\n✅ Decision Transformer trained successfully!")
    print(f"\nKey Results:")
    print(f"  - Context length: {context_len} timesteps")
    print(f"  - Action space: 3 (Sell, Hold, Buy)")
    print(f"  - Target returns: {target_returns}")
    print(f"  - Final validation accuracy: {final_val_acc:.1%}")
    print(f"  - Model parameters: {n_params:,}")

    print(f"\nThe model learns to:")
    print(f"  1. Condition on return-to-go targets")
    print(f"  2. Predict optimal trading actions")
    print(f"  3. Handle variable sequence lengths")
    print(f"  4. Apply turnover penalty (less trading)")

    print(f"\nNext steps:")
    print(f"  - Test on real crypto data")
    print(f"  - Backtest trading strategy")
    print(f"  - Fine-tune hyperparameters")
    print(f"  - Add risk management")

    return model, history


if __name__ == "__main__":
    model, history = main()
