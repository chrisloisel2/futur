"""
Masked Autoencoder (MAE) components for time series.

Implements encoder, decoder, and loss functions for MAE-style
self-supervised learning on time series.
"""
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import math


class MAEEncoder(nn.Module):
    """
    Transformer encoder for MAE.

    Encodes visible (non-masked) patches.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        dropout: float = 0.1,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            n_heads: Number of attention heads
            n_layers: Number of encoder layers
            dropout: Dropout rate
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model

        # Patch embedding
        self.patch_embed = nn.Linear(input_dim, d_model)

        # Positional encoding
        self.pos_encoder = PositionalEncoding(d_model, dropout)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Layer norm
        self.norm = nn.LayerNorm(d_model)

    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Encode visible patches.

        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional [batch, seq_len] binary mask (1=keep, 0=mask)

        Returns:
            encoded: [batch, visible_len, d_model]
        """
        # Embed patches
        x = self.patch_embed(x)  # [batch, seq_len, d_model]

        # Apply positional encoding
        x = self.pos_encoder(x)

        # If mask provided, keep only visible patches
        if mask is not None:
            # Keep only visible patches
            visible_x = []
            for b in range(x.shape[0]):
                visible_x.append(x[b, mask[b]])

            # Pad to max length in batch
            max_visible = max(v.shape[0] for v in visible_x)
            padded_x = torch.zeros(
                len(visible_x), max_visible, self.d_model,
                device=x.device,
            )
            padding_mask = torch.ones(
                len(visible_x), max_visible,
                device=x.device,
                dtype=torch.bool,
            )

            for b, v in enumerate(visible_x):
                padded_x[b, :v.shape[0]] = v
                padding_mask[b, :v.shape[0]] = False

            x = padded_x
        else:
            padding_mask = None

        # Encode
        encoded = self.encoder(x, src_key_padding_mask=padding_mask)
        encoded = self.norm(encoded)

        return encoded


class MAEDecoder(nn.Module):
    """
    Transformer decoder for MAE.

    Reconstructs masked patches from encoded visible patches.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 2,
        dropout: float = 0.1,
    ):
        """
        Args:
            input_dim: Output feature dimension (to reconstruct)
            d_model: Model dimension
            n_heads: Number of attention heads
            n_layers: Number of decoder layers
            dropout: Dropout rate
        """
        super().__init__()

        self.d_model = d_model

        # Mask token
        self.mask_token = nn.Parameter(torch.randn(1, 1, d_model))

        # Positional encoding
        self.pos_encoder = PositionalEncoding(d_model, dropout)

        # Transformer decoder
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)

        # Output projection
        self.output_proj = nn.Linear(d_model, input_dim)

        # Layer norm
        self.norm = nn.LayerNorm(d_model)

    def forward(
        self,
        encoded: torch.Tensor,
        mask: torch.Tensor,
        target_len: int,
    ) -> torch.Tensor:
        """
        Decode and reconstruct full sequence.

        Args:
            encoded: [batch, visible_len, d_model] encoded visible patches
            mask: [batch, seq_len] binary mask (1=visible, 0=masked)
            target_len: Original sequence length

        Returns:
            reconstructed: [batch, seq_len, input_dim]
        """
        batch_size = encoded.shape[0]

        # Create full sequence with mask tokens
        full_seq = self.mask_token.expand(batch_size, target_len, -1)

        # Fill in visible patches
        for b in range(batch_size):
            visible_indices = mask[b].nonzero(as_tuple=True)[0]
            if len(visible_indices) > 0:
                full_seq[b, visible_indices] = encoded[b, :len(visible_indices)]

        # Apply positional encoding
        full_seq = self.pos_encoder(full_seq)

        # Decode
        decoded = self.decoder(full_seq, encoded)
        decoded = self.norm(decoded)

        # Project to input dimension
        reconstructed = self.output_proj(decoded)

        return reconstructed


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


def masked_modeling_loss(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    mask: torch.Tensor,
    loss_type: str = 'mse',
) -> torch.Tensor:
    """
    Compute masked modeling loss.

    Args:
        predictions: [batch, seq_len, dim] predicted values
        targets: [batch, seq_len, dim] ground truth values
        mask: [batch, seq_len] binary mask (1=visible, 0=masked)
        loss_type: 'mse' or 'l1'

    Returns:
        loss: Scalar loss (only on masked tokens)
    """
    # Invert mask: we want loss on masked tokens (0 -> 1, 1 -> 0)
    masked_tokens = ~mask.bool()

    if loss_type == 'mse':
        loss = F.mse_loss(
            predictions[masked_tokens],
            targets[masked_tokens],
            reduction='mean',
        )
    elif loss_type == 'l1':
        loss = F.l1_loss(
            predictions[masked_tokens],
            targets[masked_tokens],
            reduction='mean',
        )
    else:
        raise ValueError(f"Unknown loss type: {loss_type}")

    return loss


def patchify(x: torch.Tensor, patch_size: int) -> torch.Tensor:
    """
    Convert time series to patches.

    Args:
        x: [batch, seq_len, dim] time series
        patch_size: Size of each patch

    Returns:
        patches: [batch, n_patches, patch_size * dim]
    """
    batch_size, seq_len, dim = x.shape

    # Pad if necessary
    pad_len = (patch_size - seq_len % patch_size) % patch_size
    if pad_len > 0:
        x = F.pad(x, (0, 0, 0, pad_len))
        seq_len = x.shape[1]

    n_patches = seq_len // patch_size

    # Reshape to patches
    patches = x.reshape(batch_size, n_patches, patch_size, dim)
    patches = patches.reshape(batch_size, n_patches, -1)

    return patches


def unpatchify(
    patches: torch.Tensor,
    patch_size: int,
    dim: int,
    target_len: int,
) -> torch.Tensor:
    """
    Convert patches back to time series.

    Args:
        patches: [batch, n_patches, patch_size * dim] patches
        patch_size: Size of each patch
        dim: Feature dimension
        target_len: Target sequence length (for trimming padding)

    Returns:
        x: [batch, seq_len, dim] time series
    """
    batch_size, n_patches, _ = patches.shape

    # Reshape to time series
    x = patches.reshape(batch_size, n_patches, patch_size, dim)
    x = x.reshape(batch_size, -1, dim)

    # Trim to target length
    x = x[:, :target_len, :]

    return x
