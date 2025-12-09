"""
PyTorch Lightning training module with MASE loss and SAM optimizer.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl
from torch.optim import AdamW
from typing import Dict, Optional, Any

from .backbone import TimeSeriesBackbone


class MASELoss(nn.Module):
    """
    Mean Absolute Scaled Error (MASE) loss.

    MASE = MAE / MAE_naive
    where MAE_naive is the MAE of a naive seasonal forecast.
    """

    def __init__(self, seasonal_period: int = 1):
        """
        Args:
            seasonal_period: Period for naive seasonal forecast (1 for naive forecast)
        """
        super().__init__()
        self.seasonal_period = seasonal_period

    def forward(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
        y_train: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Compute MASE loss.

        Args:
            y_pred: Predictions [batch, pred_len, features]
            y_true: Ground truth [batch, pred_len, features]
            y_train: Training data for computing naive forecast MAE [batch, seq_len, features]

        Returns:
            MASE loss scalar
        """
        # MAE of predictions
        mae = F.l1_loss(y_pred, y_true)

        if y_train is None:
            # If no training data, just return MAE
            return mae

        # Compute naive forecast MAE on training data
        # Naive forecast: y_t = y_{t-seasonal_period}
        if y_train.size(1) <= self.seasonal_period:
            return mae

        naive_forecast = y_train[:, :-self.seasonal_period, :]
        naive_target = y_train[:, self.seasonal_period:, :]

        mae_naive = F.l1_loss(naive_forecast, naive_target)

        # MASE = MAE / MAE_naive (with small epsilon to avoid division by zero)
        mase = mae / (mae_naive + 1e-8)

        return mase


class SAM(torch.optim.Optimizer):
    """
    Sharpness-Aware Minimization (SAM) optimizer.

    Based on "Sharpness-Aware Minimization for Efficiently Improving Generalization" (ICLR 2021)
    """

    def __init__(self, params, base_optimizer, rho=0.05, adaptive=False, **kwargs):
        """
        Args:
            params: Model parameters
            base_optimizer: Base optimizer class (e.g., AdamW)
            rho: Neighborhood size
            adaptive: Whether to use adaptive SAM
            **kwargs: Arguments for base optimizer
        """
        assert rho >= 0.0, f"Invalid rho: {rho}"

        defaults = dict(rho=rho, adaptive=adaptive, **kwargs)
        super(SAM, self).__init__(params, defaults)

        self.base_optimizer = base_optimizer(self.param_groups, **kwargs)
        self.param_groups = self.base_optimizer.param_groups

    @torch.no_grad()
    def first_step(self, zero_grad=False):
        """
        First step: Compute gradient and move to worst-case point in neighborhood.
        """
        grad_norm = self._grad_norm()

        for group in self.param_groups:
            scale = group["rho"] / (grad_norm + 1e-12)

            for p in group["params"]:
                if p.grad is None:
                    continue

                # Save original parameters
                self.state[p]["old_p"] = p.data.clone()

                # Compute epsilon (perturbation)
                e_w = (
                    (torch.pow(p, 2) if group["adaptive"] else 1.0)
                    * p.grad
                    * scale.to(p)
                )

                # Move to worst-case point
                p.add_(e_w)

        if zero_grad:
            self.zero_grad()

    @torch.no_grad()
    def second_step(self, zero_grad=False):
        """
        Second step: Return to original point and update parameters.
        """
        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue

                # Return to original parameters
                p.data = self.state[p]["old_p"]

        # Update with base optimizer
        self.base_optimizer.step()

        if zero_grad:
            self.zero_grad()

    def step(self, closure=None):
        """Not used - call first_step() then second_step() explicitly."""
        raise NotImplementedError(
            "SAM requires two forward-backward passes. "
            "Use first_step() and second_step() instead."
        )

    def _grad_norm(self):
        """Compute gradient norm across all parameters."""
        shared_device = self.param_groups[0]["params"][0].device
        norm = torch.norm(
            torch.stack([
                ((torch.abs(p) if group["adaptive"] else 1.0) * p.grad).norm(p=2).to(shared_device)
                for group in self.param_groups
                for p in group["params"]
                if p.grad is not None
            ]),
            p=2,
        )
        return norm

    def load_state_dict(self, state_dict):
        super().load_state_dict(state_dict)
        self.base_optimizer.param_groups = self.param_groups


class TimeSeriesLightningModule(pl.LightningModule):
    """
    PyTorch Lightning module for time series forecasting.

    Features:
    - TimeSeriesBackbone model
    - MASE loss
    - SAM optimizer
    - Automatic logging and checkpointing
    """

    def __init__(
        self,
        # Model config
        seq_len: int,
        pred_len: int,
        enc_in: int,
        embedding_dim: int = 256,
        # Training config
        learning_rate: float = 1e-4,
        weight_decay: float = 1e-5,
        use_sam: bool = True,
        sam_rho: float = 0.05,
        seasonal_period: int = 1,
        # Model architecture config
        **model_kwargs,
    ):
        """
        Initialize Lightning module.

        Args:
            seq_len: Input sequence length
            pred_len: Prediction length
            enc_in: Number of input features
            embedding_dim: Output embedding dimension
            learning_rate: Learning rate
            weight_decay: Weight decay
            use_sam: Whether to use SAM optimizer
            sam_rho: SAM neighborhood size
            seasonal_period: Seasonal period for MASE loss
            **model_kwargs: Additional arguments for TimeSeriesBackbone
        """
        super().__init__()
        self.save_hyperparameters()

        self.seq_len = seq_len
        self.pred_len = pred_len
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.use_sam = use_sam
        self.sam_rho = sam_rho

        # Model
        self.model = TimeSeriesBackbone(
            seq_len=seq_len,
            pred_len=pred_len,
            enc_in=enc_in,
            embedding_dim=embedding_dim,
            **model_kwargs,
        )

        # Loss
        self.criterion = MASELoss(seasonal_period=seasonal_period)

        # Prediction head
        self.prediction_head = nn.Linear(embedding_dim, pred_len * enc_in)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, enc_in]

        Returns:
            predictions: [batch, pred_len, enc_in]
        """
        # Get embeddings
        embeddings = self.model(x)  # [batch, embedding_dim]

        # Project to predictions
        pred = self.prediction_head(embeddings)  # [batch, pred_len * enc_in]

        # Reshape
        batch_size = x.size(0)
        pred = pred.view(batch_size, self.pred_len, -1)  # [batch, pred_len, enc_in]

        return pred

    def training_step(self, batch: Dict[str, torch.Tensor], batch_idx: int) -> torch.Tensor:
        """
        Training step with SAM optimizer.

        Args:
            batch: Dict with 'x' [batch, seq_len, enc_in] and 'y' [batch, pred_len, enc_in]
            batch_idx: Batch index

        Returns:
            loss: Training loss
        """
        x, y = batch["x"], batch["y"]

        if self.use_sam:
            # SAM: First forward-backward pass
            y_pred = self(x)
            loss = self.criterion(y_pred, y, x)

            # Manual optimization with SAM
            opt = self.optimizers()
            opt.zero_grad()
            self.manual_backward(loss)
            opt.first_step(zero_grad=True)

            # Second forward-backward pass
            y_pred = self(x)
            loss_sam = self.criterion(y_pred, y, x)
            self.manual_backward(loss_sam)
            opt.second_step(zero_grad=True)

            # Log both losses
            self.log("train/loss_first", loss, prog_bar=True)
            self.log("train/loss_sam", loss_sam, prog_bar=True)

            return loss_sam
        else:
            # Standard training
            y_pred = self(x)
            loss = self.criterion(y_pred, y, x)

            self.log("train/loss", loss, prog_bar=True)

            return loss

    def validation_step(self, batch: Dict[str, torch.Tensor], batch_idx: int) -> torch.Tensor:
        """Validation step."""
        x, y = batch["x"], batch["y"]

        y_pred = self(x)
        loss = self.criterion(y_pred, y, x)

        # Additional metrics
        mae = F.l1_loss(y_pred, y)
        mse = F.mse_loss(y_pred, y)

        self.log("val/loss", loss, prog_bar=True)
        self.log("val/mae", mae)
        self.log("val/mse", mse)

        return loss

    def test_step(self, batch: Dict[str, torch.Tensor], batch_idx: int) -> torch.Tensor:
        """Test step."""
        x, y = batch["x"], batch["y"]

        y_pred = self(x)
        loss = self.criterion(y_pred, y, x)

        mae = F.l1_loss(y_pred, y)
        mse = F.mse_loss(y_pred, y)
        rmse = torch.sqrt(mse)

        self.log("test/loss", loss)
        self.log("test/mae", mae)
        self.log("test/mse", mse)
        self.log("test/rmse", rmse)

        return loss

    def configure_optimizers(self):
        """Configure optimizer."""
        if self.use_sam:
            # SAM with AdamW base
            optimizer = SAM(
                self.parameters(),
                AdamW,
                rho=self.sam_rho,
                lr=self.learning_rate,
                weight_decay=self.weight_decay,
            )
        else:
            optimizer = AdamW(
                self.parameters(),
                lr=self.learning_rate,
                weight_decay=self.weight_decay,
            )

        # Learning rate scheduler
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer.base_optimizer if self.use_sam else optimizer,
            T_max=self.trainer.max_epochs,
            eta_min=self.learning_rate * 0.01,
        )

        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "monitor": "val/loss",
            },
        }

    def predict_step(
        self, batch: Any, batch_idx: int, dataloader_idx: int = 0
    ) -> Dict[str, torch.Tensor]:
        """Prediction step returning embeddings and predictions."""
        x = batch["x"] if isinstance(batch, dict) else batch

        # Get embeddings and predictions
        embeddings, branch_predictions = self.model.forward_with_predictions(x)

        # Final predictions
        pred = self.prediction_head(embeddings)
        pred = pred.view(x.size(0), self.pred_len, -1)

        return {
            "embeddings": embeddings,
            "predictions": pred,
            "dlinear_pred": branch_predictions["dlinear"],
            "timesnet_pred": branch_predictions["timesnet"],
        }

    @property
    def automatic_optimization(self) -> bool:
        """Disable automatic optimization when using SAM."""
        return not self.use_sam
