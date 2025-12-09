"""
Self-supervised learning models for time series.

Implements:
- TS2Vec: Contrastive learning with hierarchical contrasting
- MAE: Masked Autoencoder for time series
- SimCLR: Simple contrastive learning framework
"""
import math
from typing import Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


class DilatedConvEncoder(nn.Module):
    """
    Dilated convolutional encoder for time series.

    Uses exponentially increasing dilation rates to capture
    multi-scale temporal patterns.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        output_dim: int = 320,
        depth: int = 10,
        kernel_size: int = 3,
    ):
        """
        Args:
            input_dim: Input feature dimension
            hidden_dim: Hidden dimension for conv layers
            output_dim: Output embedding dimension
            depth: Number of dilated conv layers
            kernel_size: Kernel size for convolutions
        """
        super().__init__()

        self.input_dim = input_dim
        self.output_dim = output_dim

        # Input projection
        self.input_fc = nn.Linear(input_dim, hidden_dim)

        # Dilated convolutions with exponentially increasing dilation
        self.convs = nn.ModuleList()
        for i in range(depth):
            dilation = 2 ** i
            padding = (kernel_size - 1) * dilation // 2

            conv = nn.Conv1d(
                hidden_dim,
                hidden_dim,
                kernel_size=kernel_size,
                dilation=dilation,
                padding=padding,
            )
            self.convs.append(conv)

        # Output projection
        self.output_fc = nn.Linear(hidden_dim, output_dim)

        # Layer normalization
        self.layer_norms = nn.ModuleList([
            nn.LayerNorm(hidden_dim) for _ in range(depth)
        ])

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional [batch, seq_len] mask (1 = keep, 0 = mask)

        Returns:
            embeddings: [batch, seq_len, output_dim]
        """
        # Input projection
        h = self.input_fc(x)  # [batch, seq_len, hidden_dim]

        # Transpose for Conv1d: [batch, hidden_dim, seq_len]
        h = h.transpose(1, 2)

        # Apply mask if provided
        if mask is not None:
            mask_expanded = mask.unsqueeze(1)  # [batch, 1, seq_len]
            h = h * mask_expanded

        # Dilated convolutions with residual connections
        for conv, ln in zip(self.convs, self.layer_norms):
            residual = h
            h = conv(h)
            h = h.transpose(1, 2)  # [batch, seq_len, hidden_dim]
            h = ln(h)
            h = F.relu(h)
            h = h.transpose(1, 2)  # [batch, hidden_dim, seq_len]
            h = h + residual  # Residual connection

        # Transpose back: [batch, seq_len, hidden_dim]
        h = h.transpose(1, 2)

        # Output projection
        out = self.output_fc(h)  # [batch, seq_len, output_dim]

        return out


class TS2VecModel(nn.Module):
    """
    TS2Vec: Universal time series representation learning.

    Uses hierarchical contrasting with temporal and instance-wise contrasting.

    Reference: "TS2Vec: Towards Universal Representation of Time Series"
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        output_dim: int = 320,
        depth: int = 10,
        kernel_size: int = 3,
    ):
        """
        Args:
            input_dim: Input feature dimension
            hidden_dim: Hidden dimension
            output_dim: Output embedding dimension
            depth: Number of encoder layers
            kernel_size: Convolution kernel size
        """
        super().__init__()

        self.encoder = DilatedConvEncoder(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            output_dim=output_dim,
            depth=depth,
            kernel_size=kernel_size,
        )

        self.output_dim = output_dim

    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Encode time series.

        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional mask

        Returns:
            embeddings: [batch, seq_len, output_dim]
        """
        return self.encoder(x, mask)

    def encode(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
        return_all: bool = False,
    ) -> torch.Tensor:
        """
        Encode and optionally pool embeddings.

        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional mask
            return_all: If True, return all timesteps; else return pooled

        Returns:
            If return_all: [batch, seq_len, output_dim]
            Else: [batch, output_dim] (max pooling over time)
        """
        embeddings = self.forward(x, mask)

        if return_all:
            return embeddings
        else:
            # Max pooling over time dimension
            pooled = embeddings.max(dim=1)[0]  # [batch, output_dim]
            return pooled


class MAEModel(nn.Module):
    """
    Masked Autoencoder (MAE) for time series.

    Masks random portions of the input and reconstructs them.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        decoder_depth: int = 2,
        dropout: float = 0.1,
        mask_ratio: float = 0.75,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            n_heads: Number of attention heads
            n_layers: Number of encoder layers
            decoder_depth: Number of decoder layers
            dropout: Dropout rate
            mask_ratio: Ratio of masked tokens
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model
        self.mask_ratio = mask_ratio

        # Input embedding
        self.input_embed = nn.Linear(input_dim, d_model)

        # Positional encoding
        self.pos_encoder = PositionalEncoding(d_model, dropout, max_len=5000)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Mask token
        self.mask_token = nn.Parameter(torch.randn(1, 1, d_model))

        # Decoder
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=decoder_depth)

        # Output projection
        self.output_proj = nn.Linear(d_model, input_dim)

    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Forward pass with masking.

        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional [batch, seq_len] binary mask (1=keep, 0=mask)
                  If None, random masking is applied

        Returns:
            reconstructed: [batch, seq_len, input_dim]
            mask: [batch, seq_len] applied mask
            loss: Reconstruction loss (only on masked tokens)
        """
        batch_size, seq_len, _ = x.shape

        # Generate random mask if not provided
        if mask is None:
            mask = torch.rand(batch_size, seq_len, device=x.device) > self.mask_ratio

        # Embed input
        x_embed = self.input_embed(x)  # [batch, seq_len, d_model]
        x_embed = self.pos_encoder(x_embed)

        # Replace masked positions with mask token
        mask_expanded = mask.unsqueeze(-1)  # [batch, seq_len, 1]
        x_masked = x_embed * mask_expanded + self.mask_token * (~mask_expanded)

        # Encode
        memory = self.encoder(x_masked)  # [batch, seq_len, d_model]

        # Decode
        decoded = self.decoder(x_masked, memory)  # [batch, seq_len, d_model]

        # Project to input dimension
        reconstructed = self.output_proj(decoded)  # [batch, seq_len, input_dim]

        # Compute loss only on masked tokens
        loss = F.mse_loss(
            reconstructed[~mask],
            x[~mask],
            reduction='mean'
        )

        return reconstructed, mask, loss

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """
        Encode without masking (for downstream tasks).

        Args:
            x: [batch, seq_len, input_dim]

        Returns:
            embeddings: [batch, seq_len, d_model]
        """
        x_embed = self.input_embed(x)
        x_embed = self.pos_encoder(x_embed)
        embeddings = self.encoder(x_embed)
        return embeddings


class SimCLRModel(nn.Module):
    """
    SimCLR-style contrastive learning for time series.

    Uses data augmentation and NT-Xent loss.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 256,
        projection_dim: int = 128,
        depth: int = 6,
    ):
        """
        Args:
            input_dim: Input feature dimension
            hidden_dim: Hidden dimension
            projection_dim: Projection head output dimension
            depth: Encoder depth
        """
        super().__init__()

        # Encoder
        self.encoder = DilatedConvEncoder(
            input_dim=input_dim,
            hidden_dim=hidden_dim // 2,
            output_dim=hidden_dim,
            depth=depth,
        )

        # Projection head (MLP)
        self.projection = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, projection_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, input_dim]

        Returns:
            projections: [batch, projection_dim]
        """
        # Encode
        embeddings = self.encoder(x)  # [batch, seq_len, hidden_dim]

        # Pool over time
        pooled = embeddings.mean(dim=1)  # [batch, hidden_dim]

        # Project
        projections = self.projection(pooled)  # [batch, projection_dim]

        return projections

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """
        Encode without projection (for downstream tasks).

        Args:
            x: [batch, seq_len, input_dim]

        Returns:
            embeddings: [batch, hidden_dim]
        """
        embeddings = self.encoder(x)
        pooled = embeddings.mean(dim=1)
        return pooled


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding."""

    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 5000):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        position = torch.arange(max_len).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2) * (-math.log(10000.0) / d_model)
        )
        pe = torch.zeros(1, max_len, d_model)
        pe[0, :, 0::2] = torch.sin(position * div_term)
        pe[0, :, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, d_model]
        """
        x = x + self.pe[:, :x.size(1)]
        return self.dropout(x)
