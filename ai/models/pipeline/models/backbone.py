"""
Time Series Backbone combining DLinear, TimesNet, and Non-stationary Transformer.

Outputs 256D embeddings capturing multi-scale temporal patterns.
"""
import torch
import torch.nn as nn

from .dlinear import DLinear
from .timesnet import TimesNet
from .transformer import NonStationaryTransformer


class TimeSeriesBackbone(nn.Module):
    """
    Hybrid backbone combining multiple architectures.

    Architecture:
        1. DLinear branch: Captures trend + seasonal decomposition
        2. TimesNet branch: Captures multi-periodicity patterns
        3. Transformer branch: Captures long-range dependencies
        4. Fusion: Combines all branches
        5. Output: 256D embedding
    """

    def __init__(
        self,
        seq_len: int,
        pred_len: int,
        enc_in: int,
        # DLinear config
        dlinear_individual: bool = False,
        # TimesNet config
        timesnet_d_model: int = 64,
        timesnet_d_ff: int = 128,
        timesnet_layers: int = 2,
        timesnet_top_k: int = 5,
        # Transformer config
        transformer_d_model: int = 256,
        transformer_n_heads: int = 8,
        transformer_d_ff: int = 1024,
        transformer_n_layers: int = 3,
        # Output config
        embedding_dim: int = 256,
        dropout: float = 0.1,
    ):
        """
        Initialize TimeSeriesBackbone.

        Args:
            seq_len: Input sequence length
            pred_len: Prediction length
            enc_in: Number of input features
            dlinear_individual: Use individual DLinear per feature
            timesnet_d_model: TimesNet model dimension
            timesnet_d_ff: TimesNet feedforward dimension
            timesnet_layers: Number of TimesNet layers
            timesnet_top_k: Number of top frequencies in TimesNet
            transformer_d_model: Transformer model dimension
            transformer_n_heads: Number of attention heads
            transformer_d_ff: Transformer feedforward dimension
            transformer_n_layers: Number of transformer layers
            embedding_dim: Output embedding dimension
            dropout: Dropout rate
        """
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.enc_in = enc_in
        self.embedding_dim = embedding_dim

        # Branch 1: DLinear
        self.dlinear = DLinear(
            seq_len=seq_len,
            pred_len=pred_len,
            enc_in=enc_in,
            individual=dlinear_individual,
        )

        # Branch 2: TimesNet
        self.timesnet = TimesNet(
            seq_len=seq_len,
            pred_len=pred_len,
            enc_in=enc_in,
            d_model=timesnet_d_model,
            d_ff=timesnet_d_ff,
            e_layers=timesnet_layers,
            top_k=timesnet_top_k,
            dropout=dropout,
        )

        # Branch 3: Non-stationary Transformer
        self.transformer = NonStationaryTransformer(
            seq_len=seq_len,
            enc_in=enc_in,
            d_model=transformer_d_model,
            n_heads=transformer_n_heads,
            d_ff=transformer_d_ff,
            n_layers=transformer_n_layers,
            dropout=dropout,
        )

        # Fusion layer
        # DLinear output: [batch, pred_len, enc_in]
        # TimesNet output: [batch, pred_len, enc_in]
        # Transformer output: [batch, seq_len, transformer_d_model]

        self.dlinear_proj = nn.Linear(pred_len * enc_in, embedding_dim // 3)
        self.timesnet_proj = nn.Linear(pred_len * enc_in, embedding_dim // 3)
        self.transformer_proj = nn.Linear(
            seq_len * transformer_d_model, embedding_dim // 3
        )

        # Final projection
        self.final_proj = nn.Sequential(
            nn.Linear(embedding_dim, embedding_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embedding_dim, embedding_dim),
        )

        self.layer_norm = nn.LayerNorm(embedding_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass producing embeddings.

        Args:
            x: Input tensor [batch, seq_len, enc_in]

        Returns:
            Embeddings [batch, embedding_dim]
        """
        batch_size = x.size(0)

        # Branch 1: DLinear
        dlinear_out = self.dlinear(x)  # [batch, pred_len, enc_in]
        dlinear_embed = self.dlinear_proj(
            dlinear_out.reshape(batch_size, -1)
        )  # [batch, embedding_dim // 3]

        # Branch 2: TimesNet
        timesnet_out = self.timesnet(x)  # [batch, pred_len, enc_in]
        timesnet_embed = self.timesnet_proj(
            timesnet_out.reshape(batch_size, -1)
        )  # [batch, embedding_dim // 3]

        # Branch 3: Transformer
        transformer_out = self.transformer(x)  # [batch, seq_len, d_model]
        transformer_embed = self.transformer_proj(
            transformer_out.reshape(batch_size, -1)
        )  # [batch, embedding_dim // 3]

        # Concatenate embeddings
        combined = torch.cat(
            [dlinear_embed, timesnet_embed, transformer_embed], dim=1
        )  # [batch, embedding_dim]

        # Final projection with residual
        out = self.final_proj(combined)
        out = self.layer_norm(out + combined)
        out = self.dropout(out)

        return out

    def forward_with_predictions(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, dict]:
        """
        Forward pass returning both embeddings and branch predictions.

        Args:
            x: Input tensor [batch, seq_len, enc_in]

        Returns:
            embeddings: [batch, embedding_dim]
            predictions: Dict with predictions from each branch
        """
        batch_size = x.size(0)

        # Get predictions from each branch
        dlinear_pred = self.dlinear(x)
        timesnet_pred = self.timesnet(x)
        transformer_out = self.transformer(x)

        # Embeddings
        dlinear_embed = self.dlinear_proj(dlinear_pred.reshape(batch_size, -1))
        timesnet_embed = self.timesnet_proj(timesnet_pred.reshape(batch_size, -1))
        transformer_embed = self.transformer_proj(transformer_out.reshape(batch_size, -1))

        combined = torch.cat([dlinear_embed, timesnet_embed, transformer_embed], dim=1)
        embeddings = self.layer_norm(self.final_proj(combined) + combined)
        embeddings = self.dropout(embeddings)

        predictions = {
            "dlinear": dlinear_pred,
            "timesnet": timesnet_pred,
            "transformer": transformer_out,
        }

        return embeddings, predictions
