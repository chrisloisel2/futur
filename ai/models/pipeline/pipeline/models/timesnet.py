"""
TimesNet: Temporal 2D-Variation Modeling for General Time Series Analysis.

Based on "TimesNet: Temporal 2D-Variation Modeling for General Time Series Analysis" (ICLR 2023)
Transforms 1D time series into 2D tensors to capture multi-periodicity patterns.
"""
import torch
import torch.nn as nn
import torch.fft as fft


class Inception_Block_V1(nn.Module):
    """Inception block for processing 2D time series representations."""

    def __init__(self, in_channels: int, out_channels: int, num_kernels: int = 6):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.num_kernels = num_kernels

        kernels = []
        for i in range(self.num_kernels):
            kernels.append(
                nn.Conv2d(
                    in_channels, out_channels, kernel_size=2 * i + 1, padding=i
                )
            )
        self.kernels = nn.ModuleList(kernels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, in_channels, height, width]

        Returns:
            [batch, out_channels, height, width]
        """
        res = []
        for i in range(self.num_kernels):
            res.append(self.kernels[i](x))

        res = torch.stack(res, dim=-1).mean(-1)
        return res


class TimesBlock(nn.Module):
    """
    TimesBlock: Core building block that transforms 1D series to 2D representation.
    """

    def __init__(
        self,
        seq_len: int,
        pred_len: int,
        d_model: int,
        d_ff: int,
        num_kernels: int = 6,
        top_k: int = 5,
    ):
        """
        Initialize TimesBlock.

        Args:
            seq_len: Input sequence length
            pred_len: Prediction length
            d_model: Model dimension
            d_ff: Feedforward dimension
            num_kernels: Number of inception kernels
            top_k: Number of top frequencies to use
        """
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.k = top_k

        # Parameter-efficient design
        self.conv = nn.Sequential(
            Inception_Block_V1(d_model, d_ff, num_kernels=num_kernels),
            nn.GELU(),
            Inception_Block_V1(d_ff, d_model, num_kernels=num_kernels),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through TimesBlock.

        Args:
            x: [batch, seq_len, d_model]

        Returns:
            [batch, seq_len, d_model]
        """
        B, T, N = x.shape

        # FFT to find periodicities
        x_freq = fft.rfft(x, dim=1)

        # Find top-k frequencies
        frequency_list = abs(x_freq).mean(0).mean(-1)
        frequency_list[0] = 0  # Remove DC component
        _, top_list = torch.topk(frequency_list, self.k)
        top_list = top_list.detach().cpu().numpy()

        period_list = []
        for i in range(self.k):
            period = self.seq_len // top_list[i]
            period_list.append(period)

        res = []
        for i in range(self.k):
            period = period_list[i]

            # Padding
            if self.seq_len % period != 0:
                length = ((self.seq_len // period) + 1) * period
                padding = torch.zeros(
                    [x.shape[0], (length - self.seq_len), x.shape[2]],
                    device=x.device
                )
                out = torch.cat([x, padding], dim=1)
            else:
                length = self.seq_len
                out = x

            # Reshape to 2D: [B, period, num_periods, N]
            out = out.reshape(B, length // period, period, N)
            out = out.permute(0, 3, 1, 2).contiguous()  # [B, N, num_periods, period]

            # 2D conv
            out = self.conv(out)

            # Reshape back
            out = out.permute(0, 2, 3, 1).reshape(B, -1, N)
            out = out[:, :self.seq_len, :]

            res.append(out)

        res = torch.stack(res, dim=-1).mean(-1)
        return res


class TimesNet(nn.Module):
    """
    TimesNet model for time series forecasting.

    Stacks multiple TimesBlocks to capture multi-scale temporal patterns.
    """

    def __init__(
        self,
        seq_len: int,
        pred_len: int,
        enc_in: int,
        d_model: int = 64,
        d_ff: int = 128,
        num_kernels: int = 6,
        top_k: int = 5,
        e_layers: int = 2,
        dropout: float = 0.1,
    ):
        """
        Initialize TimesNet.

        Args:
            seq_len: Input sequence length
            pred_len: Prediction length
            enc_in: Number of input features
            d_model: Model dimension
            d_ff: Feedforward dimension
            num_kernels: Number of inception kernels
            top_k: Number of top frequencies
            e_layers: Number of encoder layers
            dropout: Dropout rate
        """
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.model = nn.ModuleList([
            TimesBlock(seq_len, pred_len, d_model, d_ff, num_kernels, top_k)
            for _ in range(e_layers)
        ])

        self.enc_embedding = nn.Linear(enc_in, d_model)
        self.layer_norm = nn.LayerNorm(d_model)
        self.projection = nn.Linear(d_model, enc_in)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, enc_in]

        Returns:
            [batch, pred_len, enc_in]
        """
        # Embedding
        x = self.enc_embedding(x)
        x = self.dropout(x)

        # TimesBlocks
        for layer in self.model:
            x = layer(x) + x
            x = self.layer_norm(x)

        # Projection to output
        x = self.projection(x)

        # Return last pred_len timesteps
        return x[:, -self.pred_len:, :]
