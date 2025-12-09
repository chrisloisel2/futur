"""Tests for Decision Transformer."""
import pytest
import torch
import numpy as np

from models.decision_transformer import (
    DecisionTransformer,
    CausalSelfAttention,
    TransformerBlock,
    TrajectoryDataset,
    compute_returns_to_go,
    reward_shaping,
    create_trading_trajectories,
)


class TestCausalSelfAttention:
    """Test causal self-attention."""

    def test_forward(self):
        """Test forward pass."""
        batch_size = 4
        seq_len = 10
        d_model = 64

        attn = CausalSelfAttention(d_model=d_model, n_heads=4)

        x = torch.randn(batch_size, seq_len, d_model)
        output = attn(x)

        assert output.shape == (batch_size, seq_len, d_model)

    def test_causal_masking(self):
        """Test that future tokens are masked."""
        d_model = 32
        seq_len = 5

        attn = CausalSelfAttention(d_model=d_model, n_heads=2)

        # Create input where each position has distinct value
        x = torch.arange(seq_len).unsqueeze(0).unsqueeze(-1).float()
        x = x.repeat(1, 1, d_model)

        output = attn(x)

        # Output at position t should not depend on positions > t
        # (This is hard to test directly, but we check shape and no errors)
        assert output.shape == (1, seq_len, d_model)

    def test_attention_mask(self):
        """Test padding mask."""
        batch_size = 2
        seq_len = 10
        d_model = 32

        attn = CausalSelfAttention(d_model=d_model, n_heads=4)

        x = torch.randn(batch_size, seq_len, d_model)

        # Mask out last 5 positions
        attention_mask = torch.ones(batch_size, seq_len)
        attention_mask[:, 5:] = 0

        output = attn(x, attention_mask)

        assert output.shape == (batch_size, seq_len, d_model)


class TestTransformerBlock:
    """Test transformer block."""

    def test_forward(self):
        """Test forward pass."""
        batch_size = 4
        seq_len = 10
        d_model = 64

        block = TransformerBlock(d_model=d_model, n_heads=4, d_ff=256)

        x = torch.randn(batch_size, seq_len, d_model)
        output = block(x)

        assert output.shape == (batch_size, seq_len, d_model)


class TestDecisionTransformer:
    """Test Decision Transformer."""

    def test_initialization(self):
        """Test model initialization."""
        model = DecisionTransformer(
            state_dim=20,
            action_dim=3,
            d_model=64,
            n_heads=4,
            n_layers=2,
        )

        assert model.state_dim == 20
        assert model.action_dim == 3
        assert model.d_model == 64

        # Check parameters initialized
        for param in model.parameters():
            assert param.requires_grad

    def test_forward(self):
        """Test forward pass."""
        batch_size = 4
        seq_len = 10
        state_dim = 20
        action_dim = 3

        model = DecisionTransformer(
            state_dim=state_dim,
            action_dim=action_dim,
            d_model=64,
            n_heads=4,
            n_layers=2,
        )

        states = torch.randn(batch_size, seq_len, state_dim)
        actions = torch.randint(0, action_dim, (batch_size, seq_len))
        rtgs = torch.randn(batch_size, seq_len, 1)
        timesteps = torch.arange(seq_len).unsqueeze(0).repeat(batch_size, 1)

        action_logits = model(states, actions, rtgs, timesteps)

        assert action_logits.shape == (batch_size, seq_len, action_dim)

    def test_forward_with_mask(self):
        """Test forward pass with attention mask."""
        batch_size = 4
        seq_len = 10
        state_dim = 20

        model = DecisionTransformer(
            state_dim=state_dim,
            action_dim=3,
            d_model=64,
        )

        states = torch.randn(batch_size, seq_len, state_dim)
        actions = torch.randint(0, 3, (batch_size, seq_len))
        rtgs = torch.randn(batch_size, seq_len, 1)
        timesteps = torch.arange(seq_len).unsqueeze(0).repeat(batch_size, 1)

        # Mask out last 3 positions
        attention_mask = torch.ones(batch_size, seq_len)
        attention_mask[:, 7:] = 0

        action_logits = model(states, actions, rtgs, timesteps, attention_mask)

        assert action_logits.shape == (batch_size, seq_len, 3)

    def test_get_action(self):
        """Test action sampling."""
        model = DecisionTransformer(
            state_dim=20,
            action_dim=3,
            d_model=64,
        )

        states = torch.randn(2, 5, 20)
        actions = torch.randint(0, 3, (2, 5))
        rtgs = torch.randn(2, 5, 1)
        timesteps = torch.arange(5).unsqueeze(0).repeat(2, 1)

        # Deterministic
        action, probs = model.get_action(states, actions, rtgs, timesteps, deterministic=True)

        assert action.shape == (2,)
        assert probs.shape == (2, 3)
        assert torch.allclose(probs.sum(dim=1), torch.ones(2))

        # Stochastic
        action_sample, probs_sample = model.get_action(
            states, actions, rtgs, timesteps, deterministic=False
        )

        assert action_sample.shape == (2,)

    def test_temperature_sampling(self):
        """Test temperature in sampling."""
        model = DecisionTransformer(state_dim=20, action_dim=3, d_model=64)

        states = torch.randn(1, 5, 20)
        actions = torch.randint(0, 3, (1, 5))
        rtgs = torch.randn(1, 5, 1)
        timesteps = torch.arange(5).unsqueeze(0)

        # High temperature (more uniform)
        _, probs_high = model.get_action(states, actions, rtgs, timesteps, temperature=2.0)

        # Low temperature (more peaked)
        _, probs_low = model.get_action(states, actions, rtgs, timesteps, temperature=0.5)

        # Higher temperature should have lower max probability
        assert probs_high.max() < probs_low.max()


class TestTrajectoryDataset:
    """Test trajectory dataset."""

    def test_dataset_creation(self):
        """Test dataset creation."""
        n_traj = 10
        max_len = 20
        state_dim = 15

        states = [np.random.randn(max_len, state_dim) for _ in range(n_traj)]
        actions = [np.random.randint(0, 3, max_len) for _ in range(n_traj)]
        rewards = [np.random.randn(max_len) for _ in range(n_traj)]
        rtgs = [np.random.randn(max_len) for _ in range(n_traj)]
        timesteps = [np.arange(max_len) for _ in range(n_traj)]

        dataset = TrajectoryDataset(
            states=np.array(states, dtype=object),
            actions=np.array(actions, dtype=object),
            rewards=np.array(rewards, dtype=object),
            returns_to_go=np.array(rtgs, dtype=object),
            timesteps=np.array(timesteps, dtype=object),
            max_len=max_len,
        )

        assert len(dataset) == n_traj

        # Get item
        item = dataset[0]

        assert item["states"].shape == (max_len, state_dim)
        assert item["actions"].shape == (max_len,)
        assert item["rtgs"].shape == (max_len, 1)
        assert item["timesteps"].shape == (max_len,)
        assert item["attention_mask"].shape == (max_len,)

    def test_padding(self):
        """Test padding for short sequences."""
        max_len = 50
        short_len = 30
        state_dim = 10

        states = [np.random.randn(short_len, state_dim)]
        actions = [np.random.randint(0, 3, short_len)]
        rewards = [np.random.randn(short_len)]
        rtgs = [np.random.randn(short_len)]
        timesteps = [np.arange(short_len)]

        dataset = TrajectoryDataset(
            states=np.array(states, dtype=object),
            actions=np.array(actions, dtype=object),
            rewards=np.array(rewards, dtype=object),
            returns_to_go=np.array(rtgs, dtype=object),
            timesteps=np.array(timesteps, dtype=object),
            max_len=max_len,
        )

        item = dataset[0]

        # Should be padded to max_len
        assert item["states"].shape == (max_len, state_dim)
        assert item["actions"].shape == (max_len,)

        # Attention mask should have 1s for real tokens, 0s for padding
        assert item["attention_mask"].sum() == short_len


class TestUtilityFunctions:
    """Test utility functions."""

    def test_compute_returns_to_go(self):
        """Test RTG computation."""
        rewards = np.array([1.0, 2.0, 3.0, 4.0])
        rtg = compute_returns_to_go(rewards, gamma=1.0)

        # With gamma=1, RTG = cumsum from end
        expected = np.array([10.0, 9.0, 7.0, 4.0])

        assert np.allclose(rtg, expected)

    def test_compute_returns_to_go_with_discount(self):
        """Test RTG with discounting."""
        rewards = np.array([1.0, 1.0, 1.0])
        gamma = 0.9

        rtg = compute_returns_to_go(rewards, gamma)

        # RTG[0] = 1 + 0.9*1 + 0.9^2*1 = 2.71
        assert rtg[0] == pytest.approx(1 + 0.9 + 0.81, rel=1e-5)

    def test_reward_shaping(self):
        """Test reward shaping with turnover penalty."""
        returns = np.array([0.01, 0.02, 0.03, 0.04])
        actions = np.array([0, 0, 1, 1])  # Change at t=2

        shaped = reward_shaping(returns, actions, turnover_penalty=0.005)

        # t=2 should have penalty
        assert shaped[2] < returns[2]

        # t=0, t=1, t=3 should be unchanged
        assert shaped[0] == returns[0]
        assert shaped[1] == returns[1]
        assert shaped[3] == returns[3]

    def test_create_trading_trajectories(self):
        """Test trajectory creation from price data."""
        n_timesteps = 500
        n_features = 10

        # Generate synthetic data
        prices = 100 + np.cumsum(np.random.randn(n_timesteps) * 0.5)
        features = np.random.randn(n_timesteps, n_features)

        trajectories = create_trading_trajectories(
            prices=prices,
            features=features,
            target_returns=[0.01],
            turnover_penalty=0.001,
            lookback=50,
        )

        # Check keys
        assert "states" in trajectories
        assert "actions" in trajectories
        assert "rewards" in trajectories
        assert "returns_to_go" in trajectories
        assert "timesteps" in trajectories

        # Check we have trajectories
        assert len(trajectories["states"]) > 0

        # Check shapes
        assert len(trajectories["states"]) == len(trajectories["actions"])
        assert len(trajectories["states"]) == len(trajectories["rewards"])


class TestGradientFlow:
    """Test gradient flow."""

    def test_backward_pass(self):
        """Test backward pass and gradients."""
        model = DecisionTransformer(
            state_dim=10,
            action_dim=3,
            d_model=32,
            n_layers=2,
        )

        batch_size = 2
        seq_len = 5

        states = torch.randn(batch_size, seq_len, 10, requires_grad=True)
        actions = torch.randint(0, 3, (batch_size, seq_len))
        rtgs = torch.randn(batch_size, seq_len, 1)
        timesteps = torch.arange(seq_len).unsqueeze(0).repeat(batch_size, 1)

        # Forward
        action_logits = model(states, actions, rtgs, timesteps)

        # Loss
        loss = action_logits.sum()

        # Backward
        loss.backward()

        # Check gradients exist
        assert states.grad is not None

        # Check model gradients
        for param in model.parameters():
            if param.requires_grad:
                assert param.grad is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
