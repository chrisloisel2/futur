"""
DLinear model for time series forecasting.

Based on "Are Transformers Effective for Time Series Forecasting?" (AAAI 2023)
DLinear decomposes time series into trend and seasonal components using linear layers.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class MovingAvg(nn.Module):
    """Moving average block to highlight trend information."""

    def __init__(self, kernel_size: int, stride: int = 1):
        super().__init__()
        self.kernel_size = kernel_size
        self.avg = nn.AvgPool1d(kernel_size=kernel_size, stride=stride, padding=0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch, seq_len, features]

        Returns:
            Moving average: [batch, seq_len, features]
        """
        # Padding on both ends
        front = x[:, 0:1, :].repeat(1, (self.kernel_size - 1) // 2, 1)
        end = x[:, -1:, :].repeat(1, (self.kernel_size - 1) // 2, 1)
        x = torch.cat([front, x, end], dim=1)

        # [batch, features, seq_len]
        x = x.permute(0, 2, 1)
        x = self.avg(x)
        x = x.permute(0, 2, 1)

        return x


class SeriesDecomp(nn.Module):
    """Series decomposition block to separate trend and seasonal components."""

    def __init__(self, kernel_size: int):
        super().__init__()
        self.moving_avg = MovingAvg(kernel_size, stride=1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: Input time series [batch, seq_len, features]

        Returns:
            trend: Trend component [batch, seq_len, features]
            seasonal: Seasonal component [batch, seq_len, features]
        """
        trend = self.moving_avg(x)
        seasonal = x - trend
        return seasonal, trend


class DLinear(nn.Module):
    """
    DLinear model: Decomposition + Linear layers.

    Separates time series into trend and seasonal components,
    then applies separate linear layers to each.
    """

    def __init__(
        self,
        seq_len: int,
        pred_len: int,
        enc_in: int,
        individual: bool = False,
        kernel_size: int = 25,
    ):
        """
        Initialize DLinear model.

        Args:
            seq_len: Input sequence length
            pred_len: Prediction sequence length
            enc_in: Number of input features
            individual: Whether to use individual linear layers per feature
            kernel_size: Kernel size for moving average decomposition
        """
        super().__init__()
        self.seq_len = seq_len
        self.pred_len = pred_len
        self.enc_in = enc_in
        self.individual = individual

        # Decomposition
        self.decomposition = SeriesDecomp(kernel_size)

        if self.individual:
            # Individual linear layers for each feature
            self.linear_seasonal = nn.ModuleList([
                nn.Linear(self.seq_len, self.pred_len)
                for _ in range(self.enc_in)
            ])
            self.linear_trend = nn.ModuleList([
                nn.Linear(self.seq_len, self.pred_len)
                for _ in range(self.enc_in)
            ])
        else:
            # Shared linear layers
            self.linear_seasonal = nn.Linear(self.seq_len, self.pred_len)
            self.linear_trend = nn.Linear(self.seq_len, self.pred_len)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: Input tensor [batch, seq_len, enc_in]

        Returns:
            Predictions [batch, pred_len, enc_in]
        """
        # Decomposition
        seasonal, trend = self.decomposition(x)

        if self.individual:
            # Process each feature individually
            seasonal_output = torch.zeros(
                [x.size(0), self.pred_len, self.enc_in],
                dtype=x.dtype,
                device=x.device
            )
            trend_output = torch.zeros(
                [x.size(0), self.pred_len, self.enc_in],
                dtype=x.dtype,
                device=x.device
            )

            for i in range(self.enc_in):
                seasonal_output[:, :, i] = self.linear_seasonal[i](
                    seasonal[:, :, i]
                )
                trend_output[:, :, i] = self.linear_trend[i](
                    trend[:, :, i]
                )

            x = seasonal_output + trend_output
        else:
            # [batch, enc_in, seq_len] -> [batch, enc_in, pred_len]
            seasonal = self.linear_seasonal(seasonal.permute(0, 2, 1)).permute(0, 2, 1)
            trend = self.linear_trend(trend.permute(0, 2, 1)).permute(0, 2, 1)

            x = seasonal + trend

        return x
