"""
Simple multimodal trading model using a Transformer encoder over feature sequences.
Inputs are expected as tensors of shape (batch, seq_len, feature_dim).
"""
from typing import Any, Dict

import torch
import torch.nn as nn


class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 5000) -> None:
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        # Build positional encodings for batch-first inputs (1, seq_len, d_model)
        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-torch.log(torch.tensor(10000.0)) / d_model))
        pe = torch.zeros(1, max_len, d_model)
        pe[0, :, 0::2] = torch.sin(position * div_term)
        pe[0, :, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.pe[:, : x.size(1), :]
        return self.dropout(x)


class MultiModalTradingModel(nn.Module):
    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        params = config.get("params", {})
        d_model = int(params.get("d_model", 128))
        n_heads = int(params.get("n_heads", 4))
        num_layers = int(params.get("num_layers", 2))
        dropout = float(params.get("dropout", 0.1))
        feature_dim = int(params.get("feature_dim", d_model))

        self.input_proj = nn.Linear(feature_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=n_heads, dropout=dropout, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.positional_encoding = PositionalEncoding(d_model, dropout=dropout)

        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Tensor of shape (batch, seq_len, feature_dim)
        Returns:
            Tensor of shape (batch,) representing predicted next return/logit
        """
        h = self.input_proj(x)
        h = self.positional_encoding(h)
        h = self.transformer(h)
        pooled = h.mean(dim=1)
        return self.head(pooled).squeeze(-1)
