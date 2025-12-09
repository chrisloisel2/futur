"""
Decision Transformer for Crypto Trading.

Based on "Decision Transformer: Reinforcement Learning via Sequence Modeling" (NeurIPS 2021)

Features:
- Return-to-Go (RTG) conditioning
- Discrete action space: [-1, 0, 1] (Sell, Hold, Buy)
- Context length: 100 timesteps
- Reward shaping with turnover penalty
- Causal masking for autoregressive generation
- Variable sequence length support
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, Optional, Tuple, List
import numpy as np


class CausalSelfAttention(nn.Module):
    """
    Causal self-attention with masking for autoregressive generation.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int = 8,
        dropout: float = 0.1,
        max_seq_len: int = 300,  # 100 timesteps * 3 (state, action, rtg)
    ):
        super().__init__()

        assert d_model % n_heads == 0

        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads

        # Q, K, V projections
        self.W_q = nn.Linear(d_model, d_model)
        self.W_k = nn.Linear(d_model, d_model)
        self.W_v = nn.Linear(d_model, d_model)

        # Output projection
        self.W_o = nn.Linear(d_model, d_model)

        self.dropout = nn.Dropout(dropout)

        # Causal mask
        self.register_buffer(
            "causal_mask",
            torch.tril(torch.ones(max_seq_len, max_seq_len)).view(
                1, 1, max_seq_len, max_seq_len
            ),
        )

    def forward(
        self,
        x: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Forward pass with causal masking.

        Args:
            x: [batch, seq_len, d_model]
            attention_mask: [batch, seq_len] optional padding mask

        Returns:
            output: [batch, seq_len, d_model]
        """
        batch_size, seq_len, d_model = x.shape

        # Project to Q, K, V
        Q = self.W_q(x).view(batch_size, seq_len, self.n_heads, self.d_k).transpose(1, 2)
        K = self.W_k(x).view(batch_size, seq_len, self.n_heads, self.d_k).transpose(1, 2)
        V = self.W_v(x).view(batch_size, seq_len, self.n_heads, self.d_k).transpose(1, 2)

        # Scaled dot-product attention
        scores = torch.matmul(Q, K.transpose(-2, -1)) / np.sqrt(self.d_k)

        # Apply causal mask
        causal_mask = self.causal_mask[:, :, :seq_len, :seq_len]
        scores = scores.masked_fill(causal_mask == 0, float("-inf"))

        # Apply padding mask if provided
        if attention_mask is not None:
            # attention_mask: [batch, seq_len]
            # Expand to [batch, 1, 1, seq_len] for broadcasting
            attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
            scores = scores.masked_fill(attention_mask == 0, float("-inf"))

        # Softmax and dropout
        attn = F.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        # Apply attention to values
        output = torch.matmul(attn, V)

        # Reshape and project
        output = output.transpose(1, 2).contiguous().view(batch_size, seq_len, d_model)
        output = self.W_o(output)

        return output


class TransformerBlock(nn.Module):
    """
    Transformer block with causal self-attention.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int = 8,
        d_ff: int = 2048,
        dropout: float = 0.1,
    ):
        super().__init__()

        self.attention = CausalSelfAttention(d_model, n_heads, dropout)

        self.feed_forward = nn.Sequential(
            nn.Linear(d_model, d_ff),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d_model),
            nn.Dropout(dropout),
        )

        self.ln1 = nn.LayerNorm(d_model)
        self.ln2 = nn.LayerNorm(d_model)

    def forward(
        self,
        x: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, d_model]
            attention_mask: [batch, seq_len]

        Returns:
            output: [batch, seq_len, d_model]
        """
        # Self-attention with residual
        attn_out = self.attention(self.ln1(x), attention_mask)
        x = x + attn_out

        # Feed-forward with residual
        ff_out = self.feed_forward(self.ln2(x))
        x = x + ff_out

        return x


class DecisionTransformer(nn.Module):
    """
    Decision Transformer for crypto trading.

    Architecture:
    - Input: Interleaved (return-to-go, state, action) sequences
    - Transformer encoder with causal masking
    - Output: Action predictions

    Action Space: {-1: Sell, 0: Hold, 1: Buy}
    """

    def __init__(
        self,
        state_dim: int,
        action_dim: int = 3,  # Sell, Hold, Buy
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        d_ff: int = 1024,
        dropout: float = 0.1,
        max_context_len: int = 100,  # Max timesteps
        max_ep_len: int = 1000,
    ):
        """
        Initialize Decision Transformer.

        Args:
            state_dim: Dimension of state (features)
            action_dim: Number of actions (3 for Sell/Hold/Buy)
            d_model: Transformer hidden dimension
            n_heads: Number of attention heads
            n_layers: Number of transformer layers
            d_ff: Feed-forward dimension
            dropout: Dropout probability
            max_context_len: Maximum context length (timesteps)
            max_ep_len: Maximum episode length for positional encoding
        """
        super().__init__()

        self.state_dim = state_dim
        self.action_dim = action_dim
        self.d_model = d_model
        self.max_context_len = max_context_len

        # Embeddings for each modality
        # RTG (Return-To-Go) embedding
        self.rtg_embed = nn.Linear(1, d_model)

        # State embedding
        self.state_embed = nn.Linear(state_dim, d_model)

        # Action embedding (discrete actions)
        self.action_embed = nn.Embedding(action_dim, d_model)

        # Positional encoding (timestep embedding)
        self.timestep_embed = nn.Embedding(max_ep_len, d_model)

        # Embedding type (to distinguish rtg/state/action)
        self.embed_type = nn.Embedding(3, d_model)

        # Transformer blocks
        self.blocks = nn.ModuleList(
            [
                TransformerBlock(d_model, n_heads, d_ff, dropout)
                for _ in range(n_layers)
            ]
        )

        # Layer norm
        self.ln = nn.LayerNorm(d_model)

        # Action prediction head
        self.action_head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, action_dim),
        )

        # Initialize weights
        self.apply(self._init_weights)

    def _init_weights(self, module):
        """Initialize weights."""
        if isinstance(module, (nn.Linear, nn.Embedding)):
            module.weight.data.normal_(mean=0.0, std=0.02)
            if isinstance(module, nn.Linear) and module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.LayerNorm):
            module.bias.data.zero_()
            module.weight.data.fill_(1.0)

    def forward(
        self,
        states: torch.Tensor,
        actions: torch.Tensor,
        rtgs: torch.Tensor,
        timesteps: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Forward pass.

        Args:
            states: [batch, seq_len, state_dim]
            actions: [batch, seq_len] discrete actions (0, 1, 2)
            rtgs: [batch, seq_len, 1] return-to-go values
            timesteps: [batch, seq_len] timestep indices
            attention_mask: [batch, seq_len] optional padding mask

        Returns:
            action_logits: [batch, seq_len, action_dim]
        """
        batch_size, seq_len = states.shape[0], states.shape[1]

        # Embed each modality
        rtg_embeddings = self.rtg_embed(rtgs)  # [batch, seq_len, d_model]
        state_embeddings = self.state_embed(states)  # [batch, seq_len, d_model]
        action_embeddings = self.action_embed(actions)  # [batch, seq_len, d_model]

        # Add timestep embeddings
        timestep_embeddings = self.timestep_embed(timesteps)  # [batch, seq_len, d_model]

        rtg_embeddings = rtg_embeddings + timestep_embeddings
        state_embeddings = state_embeddings + timestep_embeddings
        action_embeddings = action_embeddings + timestep_embeddings

        # Add type embeddings (0=rtg, 1=state, 2=action)
        type_rtg = self.embed_type(torch.zeros(batch_size, seq_len, dtype=torch.long, device=states.device))
        type_state = self.embed_type(torch.ones(batch_size, seq_len, dtype=torch.long, device=states.device))
        type_action = self.embed_type(torch.ones(batch_size, seq_len, dtype=torch.long, device=states.device) * 2)

        rtg_embeddings = rtg_embeddings + type_rtg
        state_embeddings = state_embeddings + type_state
        action_embeddings = action_embeddings + type_action

        # Interleave: (rtg_1, state_1, action_1, rtg_2, state_2, action_2, ...)
        # Stack along new dimension then reshape
        stacked = torch.stack(
            [rtg_embeddings, state_embeddings, action_embeddings],
            dim=2
        )  # [batch, seq_len, 3, d_model]

        # Reshape to [batch, seq_len * 3, d_model]
        sequence = stacked.reshape(batch_size, seq_len * 3, self.d_model)

        # Expand attention mask if provided
        if attention_mask is not None:
            # attention_mask: [batch, seq_len]
            # Expand to [batch, seq_len * 3] (repeat each mask value 3 times)
            attention_mask = attention_mask.unsqueeze(2).repeat(1, 1, 3).reshape(batch_size, seq_len * 3)

        # Pass through transformer blocks
        for block in self.blocks:
            sequence = block(sequence, attention_mask)

        # Layer norm
        sequence = self.ln(sequence)

        # Extract action predictions
        # We predict action from state embedding (position 1, 4, 7, ...)
        # Indices: 1, 4, 7, ... = 1 + 3*i for i=0,1,2,...
        action_positions = torch.arange(1, seq_len * 3, 3, device=sequence.device)
        action_features = sequence[:, action_positions, :]  # [batch, seq_len, d_model]

        # Predict action logits
        action_logits = self.action_head(action_features)  # [batch, seq_len, action_dim]

        return action_logits

    def get_action(
        self,
        states: torch.Tensor,
        actions: torch.Tensor,
        rtgs: torch.Tensor,
        timesteps: torch.Tensor,
        temperature: float = 1.0,
        deterministic: bool = False,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Get action for current timestep.

        Args:
            states: [batch, seq_len, state_dim]
            actions: [batch, seq_len] previous actions
            rtgs: [batch, seq_len, 1] return-to-go
            timesteps: [batch, seq_len] timesteps
            temperature: Sampling temperature
            deterministic: If True, take argmax; else sample

        Returns:
            action: [batch] predicted action
            action_probs: [batch, action_dim] action probabilities
        """
        # Forward pass
        action_logits = self.forward(states, actions, rtgs, timesteps)

        # Get last timestep logits
        last_logits = action_logits[:, -1, :] / temperature  # [batch, action_dim]

        # Convert to probabilities
        action_probs = F.softmax(last_logits, dim=-1)

        if deterministic:
            # Take argmax
            action = torch.argmax(action_probs, dim=-1)
        else:
            # Sample from distribution
            action = torch.multinomial(action_probs, num_samples=1).squeeze(-1)

        return action, action_probs


class TrajectoryDataset(torch.utils.data.Dataset):
    """
    Dataset for trajectory sequences.

    Stores trajectories of (state, action, reward, return-to-go).
    """

    def __init__(
        self,
        states: np.ndarray,
        actions: np.ndarray,
        rewards: np.ndarray,
        returns_to_go: np.ndarray,
        timesteps: np.ndarray,
        max_len: int = 100,
    ):
        """
        Initialize dataset.

        Args:
            states: [n_trajectories, max_timesteps, state_dim]
            actions: [n_trajectories, max_timesteps]
            rewards: [n_trajectories, max_timesteps]
            returns_to_go: [n_trajectories, max_timesteps]
            timesteps: [n_trajectories, max_timesteps]
            max_len: Maximum context length
        """
        self.states = states
        self.actions = actions
        self.rewards = rewards
        self.returns_to_go = returns_to_go
        self.timesteps = timesteps
        self.max_len = max_len

    def __len__(self):
        return len(self.states)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        """Get trajectory."""
        traj_len = len(self.states[idx])

        # Sample random subsequence if trajectory is longer than max_len
        if traj_len > self.max_len:
            start_idx = np.random.randint(0, traj_len - self.max_len + 1)
            end_idx = start_idx + self.max_len
        else:
            start_idx = 0
            end_idx = traj_len

        # Extract subsequence
        states = self.states[idx][start_idx:end_idx]
        actions = self.actions[idx][start_idx:end_idx]
        rtgs = self.returns_to_go[idx][start_idx:end_idx]
        timesteps = self.timesteps[idx][start_idx:end_idx]

        # Pad if necessary
        if len(states) < self.max_len:
            pad_len = self.max_len - len(states)

            states = np.concatenate([states, np.zeros((pad_len, states.shape[1]))], axis=0)
            actions = np.concatenate([actions, np.zeros(pad_len)], axis=0)
            rtgs = np.concatenate([rtgs, np.zeros(pad_len)], axis=0)
            timesteps = np.concatenate([timesteps, np.zeros(pad_len)], axis=0)

            # Attention mask (1 for real tokens, 0 for padding)
            attention_mask = np.concatenate([
                np.ones(end_idx - start_idx),
                np.zeros(pad_len)
            ])
        else:
            attention_mask = np.ones(self.max_len)

        return {
            "states": torch.FloatTensor(states),
            "actions": torch.LongTensor(actions.astype(np.int64)),
            "rtgs": torch.FloatTensor(rtgs).unsqueeze(-1),
            "timesteps": torch.LongTensor(timesteps.astype(np.int64)),
            "attention_mask": torch.FloatTensor(attention_mask),
        }


def compute_returns_to_go(rewards: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    """
    Compute return-to-go (cumulative future rewards).

    Args:
        rewards: [n_timesteps] or [n_trajectories, n_timesteps]
        gamma: Discount factor

    Returns:
        rtg: [n_timesteps] or [n_trajectories, n_timesteps]
    """
    if rewards.ndim == 1:
        rtg = np.zeros_like(rewards)
        cumsum = 0.0
        for t in reversed(range(len(rewards))):
            cumsum = rewards[t] + gamma * cumsum
            rtg[t] = cumsum
        return rtg
    else:
        # Batch processing
        rtg = np.zeros_like(rewards)
        for i in range(len(rewards)):
            rtg[i] = compute_returns_to_go(rewards[i], gamma)
        return rtg


def reward_shaping(
    returns: np.ndarray,
    actions: np.ndarray,
    turnover_penalty: float = 0.001,
) -> np.ndarray:
    """
    Shape rewards with turnover penalty.

    Args:
        returns: [n_timesteps] raw returns
        actions: [n_timesteps] actions taken
        turnover_penalty: Penalty for changing position

    Returns:
        shaped_rewards: [n_timesteps] shaped rewards
    """
    shaped_rewards = returns.copy()

    # Penalize turnover (changing position)
    for t in range(1, len(actions)):
        if actions[t] != actions[t-1]:
            shaped_rewards[t] -= turnover_penalty

    return shaped_rewards


def create_trading_trajectories(
    prices: np.ndarray,
    features: np.ndarray,
    target_returns: List[float] = [0.01, 0.03, 0.05],
    turnover_penalty: float = 0.001,
    lookback: int = 100,
) -> Dict[str, np.ndarray]:
    """
    Create training trajectories from historical data.

    Args:
        prices: [n_timesteps] price series
        features: [n_timesteps, n_features] feature matrix
        target_returns: Target returns for conditioning (1%, 3%, 5%)
        turnover_penalty: Penalty for changing positions
        lookback: Lookback window

    Returns:
        Dict with trajectories for each target return
    """
    n_timesteps = len(prices)
    n_features = features.shape[1]

    trajectories = {
        "states": [],
        "actions": [],
        "rewards": [],
        "returns_to_go": [],
        "timesteps": [],
    }

    # Compute price returns
    price_returns = np.diff(prices) / prices[:-1]

    # For each target return, create optimal trajectory
    for target_return in target_returns:
        # Simple strategy: buy if future return > target, sell if < -target, else hold
        # This is supervised learning from optimal actions

        states_traj = []
        actions_traj = []
        rewards_traj = []

        for t in range(lookback, n_timesteps - 1):
            # State: features at time t
            state = features[t]

            # Optimal action based on future return
            future_return = price_returns[t]

            if future_return > target_return:
                action = 2  # Buy (maps to +1)
                reward = future_return  # Long position profit
            elif future_return < -target_return:
                action = 0  # Sell (maps to -1)
                reward = -future_return  # Short position profit
            else:
                action = 1  # Hold (maps to 0)
                reward = 0.0  # No profit

            states_traj.append(state)
            actions_traj.append(action)
            rewards_traj.append(reward)

        # Convert to arrays
        states_traj = np.array(states_traj)
        actions_traj = np.array(actions_traj)
        rewards_traj = np.array(rewards_traj)

        # Apply reward shaping (turnover penalty)
        rewards_traj = reward_shaping(rewards_traj, actions_traj, turnover_penalty)

        # Compute return-to-go
        rtg_traj = compute_returns_to_go(rewards_traj, gamma=1.0)

        # Timesteps
        timesteps_traj = np.arange(len(states_traj))

        # Split into episodes of max lookback length
        for start_idx in range(0, len(states_traj), lookback):
            end_idx = min(start_idx + lookback, len(states_traj))

            if end_idx - start_idx < 10:  # Skip very short sequences
                continue

            trajectories["states"].append(states_traj[start_idx:end_idx])
            trajectories["actions"].append(actions_traj[start_idx:end_idx])
            trajectories["rewards"].append(rewards_traj[start_idx:end_idx])
            trajectories["returns_to_go"].append(rtg_traj[start_idx:end_idx])
            trajectories["timesteps"].append(timesteps_traj[start_idx:end_idx])

    # Convert to arrays (ragged arrays)
    return trajectories


def train_decision_transformer(
    model: DecisionTransformer,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    n_epochs: int = 100,
    lr: float = 1e-4,
    device: str = "cpu",
) -> Dict[str, List[float]]:
    """
    Train Decision Transformer.

    Args:
        model: DecisionTransformer model
        train_loader: Training dataloader
        val_loader: Validation dataloader
        n_epochs: Number of epochs
        lr: Learning rate
        device: Device

    Returns:
        history: Training history
    """
    model = model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    criterion = nn.CrossEntropyLoss()

    history = {"train_loss": [], "val_loss": [], "val_accuracy": []}

    for epoch in range(n_epochs):
        # Train
        model.train()
        train_loss = 0.0

        for batch in train_loader:
            states = batch["states"].to(device)
            actions = batch["actions"].to(device)
            rtgs = batch["rtgs"].to(device)
            timesteps = batch["timesteps"].to(device)
            attention_mask = batch["attention_mask"].to(device)

            # Forward
            action_logits = model(states, actions, rtgs, timesteps, attention_mask)

            # Loss (predict actions)
            # Only compute loss on non-padded tokens
            mask = attention_mask.bool()
            loss = criterion(
                action_logits[mask].reshape(-1, model.action_dim),
                actions[mask].reshape(-1)
            )

            # Backward
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item()

        train_loss /= len(train_loader)

        # Validate
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0

        with torch.no_grad():
            for batch in val_loader:
                states = batch["states"].to(device)
                actions = batch["actions"].to(device)
                rtgs = batch["rtgs"].to(device)
                timesteps = batch["timesteps"].to(device)
                attention_mask = batch["attention_mask"].to(device)

                action_logits = model(states, actions, rtgs, timesteps, attention_mask)

                mask = attention_mask.bool()
                loss = criterion(
                    action_logits[mask].reshape(-1, model.action_dim),
                    actions[mask].reshape(-1)
                )

                val_loss += loss.item()

                # Accuracy
                preds = torch.argmax(action_logits[mask], dim=-1)
                val_correct += (preds == actions[mask]).sum().item()
                val_total += mask.sum().item()

        val_loss /= len(val_loader)
        val_accuracy = val_correct / val_total

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_accuracy"].append(val_accuracy)

        if (epoch + 1) % 10 == 0:
            print(f"Epoch {epoch+1}/{n_epochs} - "
                  f"Train Loss: {train_loss:.4f}, "
                  f"Val Loss: {val_loss:.4f}, "
                  f"Val Acc: {val_accuracy:.4f}")

    return history
