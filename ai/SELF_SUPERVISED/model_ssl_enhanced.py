"""
Enhanced Self-Supervised Learning Models with Multiple Encoders.

Implements SSL with 3 encoder choices:
1. Transformer Encoder
2. TimesNet
3. MultiModal existing model

And 3 SSL objectives:
A. Masked Modeling (MAE)
B. Contrastive Learning (TS2Vec)
C. Next Patch Prediction
"""
import math
from typing import Dict, Optional, Tuple, Literal

import torch
import torch.nn as nn
import torch.nn.functional as F


# ============================================================================
# FEATURE ENCODERS
# ============================================================================


class TransformerEncoder(nn.Module):
    """
    Transformer encoder for time series.

    Standard Transformer with positional encoding.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        dropout: float = 0.1,
        max_len: int = 5000,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            n_heads: Number of attention heads
            n_layers: Number of encoder layers
            dropout: Dropout rate
            max_len: Maximum sequence length
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model

        # Input projection
        self.input_proj = nn.Linear(input_dim, d_model)

        # Positional encoding
        self.pos_encoder = PositionalEncoding(d_model, dropout, max_len)

        # Transformer encoder layers
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

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional [batch, seq_len] mask (True = keep)

        Returns:
            embeddings: [batch, seq_len, d_model]
        """
        # Project to d_model
        x = self.input_proj(x)

        # Add positional encoding
        x = self.pos_encoder(x)

        # Create attention mask if provided
        src_key_padding_mask = None
        if mask is not None:
            # Invert mask: True = masked (padding), False = attend
            src_key_padding_mask = ~mask

        # Encode
        x = self.encoder(x, src_key_padding_mask=src_key_padding_mask)
        x = self.norm(x)

        return x


class TimesNetEncoder(nn.Module):
    """
    TimesNet encoder for time series.

    Applies 2D convolutions on temporal representations.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_layers: int = 4,
        kernel_size: int = 3,
        dropout: float = 0.1,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            n_layers: Number of TimesNet layers
            kernel_size: 2D conv kernel size
            dropout: Dropout rate
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model

        # Input projection
        self.input_proj = nn.Linear(input_dim, d_model)

        # TimesNet blocks
        self.blocks = nn.ModuleList([
            TimesNetBlock(d_model, kernel_size, dropout)
            for _ in range(n_layers)
        ])

        # Output norm
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional mask

        Returns:
            embeddings: [batch, seq_len, d_model]
        """
        # Project
        x = self.input_proj(x)

        # Apply TimesNet blocks
        for block in self.blocks:
            x = block(x)

        # Norm
        x = self.norm(x)

        # Apply mask if provided
        if mask is not None:
            x = x * mask.unsqueeze(-1)

        return x


class TimesNetBlock(nn.Module):
    """Single TimesNet block with 2D convolution."""

    def __init__(self, d_model: int, kernel_size: int = 3, dropout: float = 0.1):
        super().__init__()

        # FFT parameters
        self.d_model = d_model

        # 2D Conv layers
        padding = (kernel_size - 1) // 2
        self.conv1 = nn.Conv2d(
            d_model, d_model,
            kernel_size=(kernel_size, kernel_size),
            padding=(padding, padding),
        )
        self.conv2 = nn.Conv2d(
            d_model, d_model,
            kernel_size=(kernel_size, kernel_size),
            padding=(padding, padding),
        )

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, d_model]

        Returns:
            output: [batch, seq_len, d_model]
        """
        residual = x

        # FFT to frequency domain
        x_freq = torch.fft.rfft(x, dim=1)
        x_freq = torch.stack([x_freq.real, x_freq.imag], dim=-1)  # [B, freq, d_model, 2]

        # Reshape for 2D conv: [B, d_model, freq, 2]
        batch_size, freq_len, d_model, _ = x_freq.shape
        x_freq = x_freq.permute(0, 2, 1, 3).contiguous()

        # 2D convolution in frequency domain
        x_freq = self.conv1(x_freq)
        x_freq = F.relu(x_freq)
        x_freq = self.dropout(x_freq)
        x_freq = self.conv2(x_freq)

        # Reshape back: [B, freq, d_model, 2]
        x_freq = x_freq.permute(0, 2, 1, 3).contiguous()

        # IFFT back to time domain
        x_freq_complex = torch.complex(x_freq[..., 0], x_freq[..., 1])
        x = torch.fft.irfft(x_freq_complex, n=residual.shape[1], dim=1)

        # Residual connection
        x = x + residual
        x = self.norm1(x)

        # FFN
        ffn_out = F.relu(x)
        ffn_out = self.dropout(ffn_out)
        x = x + ffn_out
        x = self.norm2(x)

        return x


class MultiModalEncoder(nn.Module):
    """
    Wrapper around existing MultiModalTradingModel.

    Integrates with your existing model from TRAIN.
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 4,
        dropout: float = 0.1,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            n_heads: Number of attention heads
            n_layers: Number of layers
            dropout: Dropout rate
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model

        # Input projection
        self.input_proj = nn.Linear(input_dim, d_model)

        # Positional encoding
        self.pos_encoder = PositionalEncoding(d_model, dropout)

        # Transformer encoder (similar to MultiModalTradingModel)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, input_dim]
            mask: Optional mask

        Returns:
            embeddings: [batch, seq_len, d_model]
        """
        # Project and encode
        x = self.input_proj(x)
        x = self.pos_encoder(x)

        # Apply mask if provided
        src_key_padding_mask = None
        if mask is not None:
            src_key_padding_mask = ~mask

        x = self.encoder(x, src_key_padding_mask=src_key_padding_mask)
        x = self.norm(x)

        return x


# ============================================================================
# PROJECTION HEAD
# ============================================================================


class ProjectionHead(nn.Module):
    """
    MLP projection head for contrastive learning.

    Projects from d_model to projection_dim (typically 128).
    """

    def __init__(
        self,
        d_model: int,
        projection_dim: int = 128,
        hidden_dim: Optional[int] = None,
    ):
        """
        Args:
            d_model: Input dimension
            projection_dim: Output projection dimension
            hidden_dim: Hidden dimension (default: d_model)
        """
        super().__init__()

        if hidden_dim is None:
            hidden_dim = d_model

        self.projection = nn.Sequential(
            nn.Linear(d_model, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, projection_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, d_model] or [batch, seq_len, d_model]

        Returns:
            projections: [batch, projection_dim] or [batch, seq_len, projection_dim]
        """
        return self.projection(x)


# ============================================================================
# SSL MODELS
# ============================================================================


class SSLModel(nn.Module):
    """
    Unified Self-Supervised Learning model.

    Supports 3 encoders:
    - Transformer
    - TimesNet
    - MultiModal

    And 3 SSL objectives:
    - Masked Modeling (MAE)
    - Contrastive Learning (TS2Vec)
    - Next Patch Prediction
    """

    def __init__(
        self,
        input_dim: int,
        d_model: int = 256,
        encoder_type: Literal["transformer", "timesnet", "multimodal"] = "transformer",
        ssl_objective: Literal["masked", "contrastive", "next_patch"] = "contrastive",
        projection_dim: int = 128,
        mask_ratio: float = 0.3,
        patch_len: int = 16,
        **encoder_kwargs,
    ):
        """
        Args:
            input_dim: Input feature dimension
            d_model: Model dimension
            encoder_type: Choice of encoder
            ssl_objective: Choice of SSL objective
            projection_dim: Projection head output dimension
            mask_ratio: Masking ratio for MAE (0.2-0.4)
            patch_len: Patch length for next patch prediction
            **encoder_kwargs: Additional encoder-specific arguments
        """
        super().__init__()

        self.input_dim = input_dim
        self.d_model = d_model
        self.encoder_type = encoder_type
        self.ssl_objective = ssl_objective
        self.mask_ratio = mask_ratio
        self.patch_len = patch_len

        # Create encoder
        if encoder_type == "transformer":
            self.encoder = TransformerEncoder(
                input_dim=input_dim,
                d_model=d_model,
                **encoder_kwargs,
            )
        elif encoder_type == "timesnet":
            self.encoder = TimesNetEncoder(
                input_dim=input_dim,
                d_model=d_model,
                **encoder_kwargs,
            )
        elif encoder_type == "multimodal":
            self.encoder = MultiModalEncoder(
                input_dim=input_dim,
                d_model=d_model,
                **encoder_kwargs,
            )
        else:
            raise ValueError(f"Unknown encoder type: {encoder_type}")

        # Create projection head (for contrastive learning)
        self.projection_head = ProjectionHead(d_model, projection_dim)

        # Decoder (for masked modeling)
        if ssl_objective == "masked":
            self.decoder = nn.TransformerDecoder(
                nn.TransformerDecoderLayer(
                    d_model=d_model,
                    nhead=encoder_kwargs.get('n_heads', 8),
                    batch_first=True,
                ),
                num_layers=2,
            )
            self.output_proj = nn.Linear(d_model, input_dim)
            self.mask_token = nn.Parameter(torch.randn(1, 1, d_model))

        # Prediction head (for next patch)
        if ssl_objective == "next_patch":
            self.patch_predictor = nn.Sequential(
                nn.Linear(d_model, d_model),
                nn.ReLU(),
                nn.Linear(d_model, input_dim * patch_len),
            )

    def forward(
        self,
        x: torch.Tensor,
        x_aug: Optional[torch.Tensor] = None,
        mask: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """
        Forward pass with SSL objective.

        Args:
            x: [batch, seq_len, input_dim] input time series
            x_aug: [batch, seq_len, input_dim] augmented view (for contrastive)
            mask: [batch, seq_len] binary mask (for masked modeling)

        Returns:
            Dictionary with outputs depending on SSL objective:
            - masked: {'reconstructed', 'mask', 'loss'}
            - contrastive: {'z1', 'z2', 'proj1', 'proj2'}
            - next_patch: {'predictions', 'targets', 'loss'}
        """
        if self.ssl_objective == "masked":
            return self._forward_masked(x, mask)
        elif self.ssl_objective == "contrastive":
            return self._forward_contrastive(x, x_aug)
        elif self.ssl_objective == "next_patch":
            return self._forward_next_patch(x)
        else:
            raise ValueError(f"Unknown SSL objective: {self.ssl_objective}")

    def _forward_masked(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """
        A. Masked Modeling (MAE)

        Mask 20-40% of timesteps and reconstruct.
        """
        batch_size, seq_len, input_dim = x.shape

        # Generate mask if not provided
        if mask is None:
            mask = torch.rand(batch_size, seq_len, device=x.device) > self.mask_ratio

        # Encode with masking
        x_encoded = self.encoder(x, mask=mask)

        # Prepare decoder input (replace masked with mask token)
        x_decoder = x_encoded.clone()
        mask_expanded = mask.unsqueeze(-1)
        x_decoder = x_decoder * mask_expanded + self.mask_token * (~mask_expanded)

        # Decode
        x_decoded = self.decoder(x_decoder, x_encoded)

        # Project to input dimension
        reconstructed = self.output_proj(x_decoded)

        # Compute loss on masked tokens
        masked_tokens = ~mask
        loss = F.mse_loss(
            reconstructed[masked_tokens],
            x[masked_tokens],
            reduction='mean',
        )

        return {
            'reconstructed': reconstructed,
            'mask': mask,
            'loss': loss,
        }

    def _forward_contrastive(
        self,
        x: torch.Tensor,
        x_aug: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """
        B. Contrastive Learning (TS2Vec)

        Encode two augmented views and compute contrastive loss.
        """
        # Encode first view
        z1 = self.encoder(x)  # [batch, seq_len, d_model]

        # Encode second view (augmented)
        if x_aug is None:
            raise ValueError("x_aug required for contrastive learning")
        z2 = self.encoder(x_aug)

        # Pool over time dimension
        z1_pooled = z1.mean(dim=1)  # [batch, d_model]
        z2_pooled = z2.mean(dim=1)

        # Project for contrastive loss
        proj1 = self.projection_head(z1_pooled)  # [batch, projection_dim]
        proj2 = self.projection_head(z2_pooled)

        return {
            'z1': z1,
            'z2': z2,
            'proj1': proj1,
            'proj2': proj2,
        }

    def _forward_next_patch(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        """
        C. Next Patch Prediction

        Predict the next patch from current context.
        """
        batch_size, seq_len, input_dim = x.shape

        # Split into patches
        n_patches = seq_len // self.patch_len
        if seq_len % self.patch_len != 0:
            # Pad to make divisible
            pad_len = self.patch_len - (seq_len % self.patch_len)
            x = F.pad(x, (0, 0, 0, pad_len))
            seq_len = x.shape[1]
            n_patches = seq_len // self.patch_len

        # Reshape to patches: [batch, n_patches, patch_len, input_dim]
        x_patches = x.reshape(batch_size, n_patches, self.patch_len, input_dim)

        # Context patches (all except last)
        x_context = x_patches[:, :-1]  # [batch, n_patches-1, patch_len, input_dim]

        # Target patch (last patch)
        x_target = x_patches[:, -1]  # [batch, patch_len, input_dim]

        # Flatten context patches for encoding
        x_context_flat = x_context.reshape(batch_size, -1, input_dim)

        # Encode context
        z_context = self.encoder(x_context_flat)  # [batch, context_len, d_model]

        # Pool context
        z_pooled = z_context.mean(dim=1)  # [batch, d_model]

        # Predict next patch
        pred_next_patch = self.patch_predictor(z_pooled)  # [batch, input_dim * patch_len]
        pred_next_patch = pred_next_patch.reshape(batch_size, self.patch_len, input_dim)

        # Loss
        loss = F.mse_loss(pred_next_patch, x_target, reduction='mean')

        return {
            'predictions': pred_next_patch,
            'targets': x_target,
            'loss': loss,
        }

    def encode(self, x: torch.Tensor, return_all: bool = False) -> torch.Tensor:
        """
        Encode time series to embeddings (for downstream tasks).

        Args:
            x: [batch, seq_len, input_dim]
            return_all: If True, return all timesteps; else pool

        Returns:
            If return_all: [batch, seq_len, d_model]
            Else: [batch, d_model]
        """
        embeddings = self.encoder(x)

        if return_all:
            return embeddings
        else:
            # Pool over time
            return embeddings.mean(dim=1)


# ============================================================================
# UTILITIES
# ============================================================================


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


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================


def create_ssl_model(
    config: Dict,
    encoder_type: str = "transformer",
    ssl_objective: str = "contrastive",
) -> SSLModel:
    """
    Factory function to create SSL model from config.

    Args:
        config: Configuration dictionary
        encoder_type: 'transformer', 'timesnet', or 'multimodal'
        ssl_objective: 'masked', 'contrastive', or 'next_patch'

    Returns:
        SSLModel instance
    """
    return SSLModel(
        input_dim=config['input_dim'],
        d_model=config.get('d_model', 256),
        encoder_type=encoder_type,
        ssl_objective=ssl_objective,
        projection_dim=config.get('projection_dim', 128),
        mask_ratio=config.get('mask_ratio', 0.3),
        patch_len=config.get('patch_len', 16),
        n_heads=config.get('n_heads', 8),
        n_layers=config.get('n_layers', 6),
        dropout=config.get('dropout', 0.1),
    )
