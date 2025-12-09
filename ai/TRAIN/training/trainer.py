"""
Training loop with evaluation and gradient accumulation.
"""
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import torch
import torch.nn as nn
from torch.utils.data import DataLoader


class TradingTrainer:
    def __init__(
        self,
        model: nn.Module,
        config: Dict[str, Any],
        device: str,
        metrics_callback: Optional[Callable[[torch.Tensor, torch.Tensor], Dict[str, Any]]] = None,
    ) -> None:
        self.model = model
        self.device = device
        self.model.to(self.device)
        self.config = config
        self.metrics_callback = metrics_callback

        lr = float(config.get("learning_rate", 1e-3))
        optimizer_name = config.get("optimizer", "AdamW").lower()
        if optimizer_name == "adam":
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        else:
            self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr)

        self.criterion = nn.MSELoss()

        self.gradient_clip = float(config.get("gradient_clip", 0.0) or 0.0)
        self.fast_dev_run = bool(config.get("fast_dev_run", False))
        self.limit_batches = int(config.get("limit_batches", 0) or 0)
        self.epochs = int(config.get("epochs", 1))
        self.grad_accum = int(config.get("gradient_accumulation_steps", 1))

    def fit(self, train_loader: DataLoader, val_loader: Optional[DataLoader] = None) -> None:
        self.model.train()
        for epoch in range(self.epochs):
            for batch_idx, (x, y) in enumerate(train_loader):
                if self.limit_batches and batch_idx >= self.limit_batches:
                    break

                x = x.to(self.device)
                y = y.to(self.device)

                preds = self.model(x)
                loss = self.criterion(preds, y) / self.grad_accum
                loss.backward()

                if self.gradient_clip > 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip)

                if (batch_idx + 1) % self.grad_accum == 0:
                    self.optimizer.step()
                    self.optimizer.zero_grad()

                if self.fast_dev_run:
                    break

            if val_loader is not None:
                self.evaluate(val_loader)

            if self.fast_dev_run:
                break

    @torch.no_grad()
    def evaluate(self, loader: DataLoader) -> Dict[str, Any]:
        self.model.eval()
        all_preds = []
        all_targets = []
        losses = []

        for batch_idx, (x, y) in enumerate(loader):
            if self.limit_batches and batch_idx >= self.limit_batches:
                break

            x = x.to(self.device)
            y = y.to(self.device)

            preds = self.model(x)
            loss = self.criterion(preds, y)
            losses.append(loss.item())
            all_preds.append(preds.detach().cpu())
            all_targets.append(y.detach().cpu())

            if self.fast_dev_run:
                break

        metrics: Dict[str, Any] = {"loss": float(sum(losses) / max(len(losses), 1))}
        if self.metrics_callback and all_preds and all_targets:
            preds_cat = torch.cat(all_preds)
            targets_cat = torch.cat(all_targets)
            try:
                metrics.update(self.metrics_callback(targets_cat, preds_cat))
            except Exception:
                pass

        self.model.train()
        return metrics

    def save_checkpoint(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.model.state_dict(), path)
