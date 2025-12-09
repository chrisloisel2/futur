"""
Metrics utilities for trading models.
"""
from typing import Any, Dict

import torch


def _max_drawdown(equity: torch.Tensor) -> float:
    peak = torch.cummax(equity, dim=0)[0]
    dd = (equity - peak) / peak
    return float(dd.min().abs().item())


class ModelMetrics:
    @staticmethod
    def track_all(targets: torch.Tensor, preds: torch.Tensor) -> Dict[str, Any]:
        with torch.no_grad():
            returns = preds
            mse = torch.mean((targets - preds) ** 2).item()
            mae = torch.mean(torch.abs(targets - preds)).item()
            std = torch.std(returns)
            sharpe = (torch.mean(returns) / (std + 1e-8) * (252 ** 0.5)).item()

            # Equity curve assuming unit capital
            equity = (1 + returns).cumprod()
            max_dd = _max_drawdown(equity)
        return {"mse": mse, "mae": mae, "sharpe": sharpe, "max_drawdown": max_dd}
