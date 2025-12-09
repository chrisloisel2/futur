"""
FT-Transformer: Feature Tokenizer + Transformer for tabular data.

Based on "Revisiting Deep Learning Models for Tabular Data" (NeurIPS 2021)
Uses continuous feature embeddings and multi-head attention.
"""
import math
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F


class NumericalFeatureTokenizer(nn.Module):
    """
    Tokenize numerical features into embeddings.

    Each feature gets its own learnable embedding transformation.
    """

    def __init__(self, n_features: int, d_token: int, bias: bool = True):
        """
        Args:
            n_features: Number of numerical features
            d_token: Token dimension
            bias: Whether to use bias in linear layers
        """
        super().__init__()
        self.n_features = n_features
        self.d_token = d_token

        # Per-feature linear transformations
        self.weight = nn.Parameter(torch.randn(n_features, d_token))
        self.bias = nn.Parameter(torch.randn(n_features, d_token)) if bias else None

        self._reset_parameters()

    def _reset_parameters(self):
        """Initialize parameters."""
        d_sqrt_inv = 1 / math.sqrt(self.d_token)
        nn.init.uniform_(self.weight, -d_sqrt_inv, d_sqrt_inv)
        if self.bias is not None:
            nn.init.uniform_(self.bias, -d_sqrt_inv, d_sqrt_inv)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Tokenize features.

        Args:
            x: [batch, n_features]

        Returns:
            tokens: [batch, n_features, d_token]
        """
        # x: [batch, n_features]
        # weight: [n_features, d_token]

        # Element-wise multiplication + bias
        tokens = x.unsqueeze(-1) * self.weight.unsqueeze(0)  # [batch, n_features, d_token]

        if self.bias is not None:
            tokens = tokens + self.bias.unsqueeze(0)

        return tokens


class MultiHeadAttention(nn.Module):
    """Multi-head self-attention for feature tokens."""

    def __init__(
        self,
        d_token: int,
        n_heads: int,
        dropout: float = 0.0,
        bias: bool = True,
    ):
        """
        Args:
            d_token: Token dimension
            n_heads: Number of attention heads
            dropout: Dropout rate
            bias: Whether to use bias
        """
        super().__init__()
        assert d_token % n_heads == 0, "d_token must be divisible by n_heads"

        self.d_token = d_token
        self.n_heads = n_heads
        self.d_head = d_token // n_heads

        self.W_q = nn.Linear(d_token, d_token, bias=bias)
        self.W_k = nn.Linear(d_token, d_token, bias=bias)
        self.W_v = nn.Linear(d_token, d_token, bias=bias)
        self.W_out = nn.Linear(d_token, d_token, bias=bias)

        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Args:
            x: [batch, n_tokens, d_token]
            mask: Optional attention mask

        Returns:
            [batch, n_tokens, d_token]
        """
        batch_size, n_tokens, _ = x.shape

        # Linear projections
        Q = self.W_q(x)  # [batch, n_tokens, d_token]
        K = self.W_k(x)
        V = self.W_v(x)

        # Reshape for multi-head
        Q = Q.view(batch_size, n_tokens, self.n_heads, self.d_head).transpose(1, 2)
        K = K.view(batch_size, n_tokens, self.n_heads, self.d_head).transpose(1, 2)
        V = V.view(batch_size, n_tokens, self.n_heads, self.d_head).transpose(1, 2)

        # Attention scores
        scores = torch.matmul(Q, K.transpose(-2, -1)) / math.sqrt(self.d_head)

        if mask is not None:
            scores = scores.masked_fill(mask == 0, -1e9)

        attn = F.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        # Apply attention to values
        context = torch.matmul(attn, V)  # [batch, n_heads, n_tokens, d_head]

        # Concatenate heads
        context = context.transpose(1, 2).contiguous().view(
            batch_size, n_tokens, self.d_token
        )

        # Final linear
        output = self.W_out(context)

        return output


class FeedForward(nn.Module):
    """Position-wise feed-forward network."""

    def __init__(
        self,
        d_token: int,
        d_ff: int,
        dropout: float = 0.0,
        activation: str = "reglu",
    ):
        """
        Args:
            d_token: Token dimension
            d_ff: Feedforward dimension
            dropout: Dropout rate
            activation: Activation function ('relu', 'gelu', 'reglu')
        """
        super().__init__()
        self.activation = activation

        if activation == "reglu":
            # ReGLU: better than ReLU/GELU for tabular
            self.linear1 = nn.Linear(d_token, d_ff * 2)
            self.linear2 = nn.Linear(d_ff, d_token)
        else:
            self.linear1 = nn.Linear(d_token, d_ff)
            self.linear2 = nn.Linear(d_ff, d_token)

        self.dropout = nn.Dropout(dropout)

        if activation == "relu":
            self.act = nn.ReLU()
        elif activation == "gelu":
            self.act = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, n_tokens, d_token]

        Returns:
            [batch, n_tokens, d_token]
        """
        if self.activation == "reglu":
            # ReGLU activation
            x = self.linear1(x)
            x, gate = x.chunk(2, dim=-1)
            x = x * F.relu(gate)
            x = self.dropout(x)
            x = self.linear2(x)
        else:
            x = self.linear1(x)
            x = self.act(x)
            x = self.dropout(x)
            x = self.linear2(x)

        return x


class TransformerLayer(nn.Module):
    """Single Transformer layer with attention + FFN."""

    def __init__(
        self,
        d_token: int,
        n_heads: int,
        d_ff: int,
        dropout: float = 0.0,
        activation: str = "reglu",
    ):
        super().__init__()
        self.attention = MultiHeadAttention(d_token, n_heads, dropout)
        self.ffn = FeedForward(d_token, d_ff, dropout, activation)

        self.norm1 = nn.LayerNorm(d_token)
        self.norm2 = nn.LayerNorm(d_token)

        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: [batch, n_tokens, d_token]
            mask: Optional attention mask

        Returns:
            [batch, n_tokens, d_token]
        """
        # Pre-norm architecture
        # Attention + residual
        x = x + self.dropout(self.attention(self.norm1(x), mask))

        # FFN + residual
        x = x + self.dropout(self.ffn(self.norm2(x)))

        return x


class FTTransformer(nn.Module):
    """
    FT-Transformer: Feature Tokenizer + Transformer for tabular data.

    Architecture:
        1. Numerical feature tokenization
        2. CLS token prepending
        3. Transformer layers
        4. CLS token as output embedding
    """

    def __init__(
        self,
        n_features: int,
        d_token: int = 192,
        n_blocks: int = 3,
        n_heads: int = 8,
        d_ff_factor: float = 4 / 3,
        dropout: float = 0.1,
        activation: str = "reglu",
        embedding_dim: int = 128,
        n_classes: Optional[int] = None,
    ):
        """
        Initialize FT-Transformer.

        Args:
            n_features: Number of input features
            d_token: Token dimension
            n_blocks: Number of transformer blocks
            n_heads: Number of attention heads
            d_ff_factor: FFN dimension factor (d_ff = d_token * d_ff_factor)
            dropout: Dropout rate
            activation: Activation function
            embedding_dim: Output embedding dimension
            n_classes: Number of classes for classification (None for regression)
        """
        super().__init__()
        self.n_features = n_features
        self.d_token = d_token
        self.n_blocks = n_blocks
        self.embedding_dim = embedding_dim
        self.n_classes = n_classes

        d_ff = int(d_token * d_ff_factor)

        # Feature tokenizer
        self.feature_tokenizer = NumericalFeatureTokenizer(n_features, d_token)

        # CLS token (for aggregation)
        self.cls_token = nn.Parameter(torch.randn(1, 1, d_token))

        # Transformer blocks
        self.blocks = nn.ModuleList([
            TransformerLayer(d_token, n_heads, d_ff, dropout, activation)
            for _ in range(n_blocks)
        ])

        # Head
        self.head = nn.Sequential(
            nn.LayerNorm(d_token),
            nn.Linear(d_token, embedding_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        # Task-specific heads
        if n_classes is not None:
            # Classification
            self.task_head = nn.Linear(embedding_dim, n_classes)
        else:
            # Regression
            self.task_head = nn.Linear(embedding_dim, 1)

        self._reset_parameters()

    def _reset_parameters(self):
        """Initialize parameters."""
        nn.init.normal_(self.cls_token, std=0.02)

    def forward(
        self,
        x: torch.Tensor,
        return_embedding: bool = False,
    ) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: Input features [batch, n_features]
            return_embedding: If True, return embedding instead of predictions

        Returns:
            predictions [batch, n_classes] or [batch, 1]
            or embeddings [batch, embedding_dim] if return_embedding=True
        """
        batch_size = x.size(0)

        # Tokenize features: [batch, n_features, d_token]
        tokens = self.feature_tokenizer(x)

        # Prepend CLS token
        cls_tokens = self.cls_token.expand(batch_size, -1, -1)
        tokens = torch.cat([cls_tokens, tokens], dim=1)  # [batch, 1 + n_features, d_token]

        # Transformer blocks
        for block in self.blocks:
            tokens = block(tokens)

        # Extract CLS token
        cls_output = tokens[:, 0]  # [batch, d_token]

        # Embedding
        embedding = self.head(cls_output)  # [batch, embedding_dim]

        if return_embedding:
            return embedding

        # Task prediction
        output = self.task_head(embedding)

        return output

    def get_attention_weights(self, x: torch.Tensor) -> list:
        """
        Get attention weights from all layers.

        Args:
            x: Input features [batch, n_features]

        Returns:
            List of attention weights from each layer
        """
        batch_size = x.size(0)

        # Tokenize
        tokens = self.feature_tokenizer(x)
        cls_tokens = self.cls_token.expand(batch_size, -1, -1)
        tokens = torch.cat([cls_tokens, tokens], dim=1)

        attention_weights = []

        # Forward through blocks and collect attention
        for block in self.blocks:
            # Get attention from this block
            # Note: This requires modifying TransformerLayer to return attention
            tokens = block(tokens)
            # Placeholder - would need to modify MultiHeadAttention to return weights
            attention_weights.append(None)

        return attention_weights
