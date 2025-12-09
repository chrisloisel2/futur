"""
Non-stationary Transformer for time series.

Based on "Non-stationary Transformers: Exploring the Stationarity in Time Series Forecasting" (NeurIPS 2022)
Handles non-stationary time series by projecting queries/keys/values with de-stationary attention.
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class LearnablePositionalEncoding(nn.Module):
    """Learnable positional encoding instead of fixed sinusoidal."""

    def __init__(self, d_model: int, max_len: int = 5000):
        super().__init__()
        self.encoding = nn.Parameter(torch.randn(1, max_len, d_model))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, d_model]

        Returns:
            x + positional encoding
        """
        seq_len = x.size(1)
        return x + self.encoding[:, :seq_len, :]


class DeStationaryAttention(nn.Module):
    """
    De-stationary Attention mechanism.

    Projects queries and keys with learned scaling factors to handle non-stationarity.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        dropout: float = 0.1,
    ):
        super().__init__()
        assert d_model % n_heads == 0, "d_model must be divisible by n_heads"

        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads

        # Projections
        self.w_q = nn.Linear(d_model, d_model)
        self.w_k = nn.Linear(d_model, d_model)
        self.w_v = nn.Linear(d_model, d_model)
        self.fc = nn.Linear(d_model, d_model)

        # De-stationary projections
        self.tau_learner = nn.Parameter(torch.randn(1, 1, d_model))
        self.delta_learner = nn.Parameter(torch.randn(1, 1, d_model))

        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        mask: torch.Tensor = None,
    ) -> torch.Tensor:
        """
        Args:
            query: [batch, seq_len, d_model]
            key: [batch, seq_len, d_model]
            value: [batch, seq_len, d_model]
            mask: Optional attention mask

        Returns:
            [batch, seq_len, d_model]
        """
        batch_size = query.size(0)

        # De-stationary scaling
        tau = torch.sigmoid(self.tau_learner)
        delta = torch.sigmoid(self.delta_learner)

        # Apply de-stationary projection
        query = query * tau
        key = key * delta

        # Multi-head split
        Q = self.w_q(query).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)
        K = self.w_k(key).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)
        V = self.w_v(value).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)

        # Attention scores
        scores = torch.matmul(Q, K.transpose(-2, -1)) / math.sqrt(self.d_k)

        if mask is not None:
            scores = scores.masked_fill(mask == 0, -1e9)

        attn = F.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        # Apply attention to values
        context = torch.matmul(attn, V)

        # Concatenate heads
        context = context.transpose(1, 2).contiguous().view(
            batch_size, -1, self.d_model
        )

        # Final projection
        output = self.fc(context)

        return output


class FeedForward(nn.Module):
    """Position-wise feed-forward network."""

    def __init__(self, d_model: int, d_ff: int, dropout: float = 0.1):
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_ff)
        self.linear2 = nn.Linear(d_ff, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.linear2(self.dropout(F.gelu(self.linear1(x))))


class NonStationaryTransformerLayer(nn.Module):
    """Single layer of Non-stationary Transformer."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        d_ff: int,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.attention = DeStationaryAttention(d_model, n_heads, dropout)
        self.feed_forward = FeedForward(d_model, d_ff, dropout)

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)

        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, mask: torch.Tensor = None) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, d_model]
            mask: Optional attention mask

        Returns:
            [batch, seq_len, d_model]
        """
        # Self-attention with residual
        attn_out = self.attention(x, x, x, mask)
        x = self.norm1(x + self.dropout(attn_out))

        # Feed-forward with residual
        ff_out = self.feed_forward(x)
        x = self.norm2(x + self.dropout(ff_out))

        return x


class NonStationaryTransformer(nn.Module):
    """
    Non-stationary Transformer encoder.

    Stacks multiple transformer layers with de-stationary attention.
    """

    def __init__(
        self,
        seq_len: int,
        enc_in: int,
        d_model: int = 512,
        n_heads: int = 8,
        d_ff: int = 2048,
        n_layers: int = 3,
        dropout: float = 0.1,
    ):
        """
        Initialize Non-stationary Transformer.

        Args:
            seq_len: Input sequence length
            enc_in: Number of input features
            d_model: Model dimension
            n_heads: Number of attention heads
            d_ff: Feedforward dimension
            n_layers: Number of transformer layers
            dropout: Dropout rate
        """
        super().__init__()
        self.seq_len = seq_len
        self.d_model = d_model

        # Input embedding
        self.value_embedding = nn.Linear(enc_in, d_model)

        # Learnable positional encoding
        self.positional_encoding = LearnablePositionalEncoding(d_model, seq_len)

        # Transformer layers
        self.layers = nn.ModuleList([
            NonStationaryTransformerLayer(d_model, n_heads, d_ff, dropout)
            for _ in range(n_layers)
        ])

        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, mask: torch.Tensor = None) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, enc_in]
            mask: Optional attention mask

        Returns:
            [batch, seq_len, d_model]
        """
        # Embed input
        x = self.value_embedding(x)

        # Add positional encoding
        x = self.positional_encoding(x)
        x = self.dropout(x)

        # Pass through transformer layers
        for layer in self.layers:
            x = layer(x, mask)

        return x
