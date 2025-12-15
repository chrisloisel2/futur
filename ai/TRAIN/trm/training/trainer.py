"""
Training loop for TRM with temporal validation.

Key features:
- Early stopping on validation Sharpe ratio
- Learning rate scheduling
- Gradient clipping for stability
- Comprehensive logging
- Checkpoint saving
"""
import logging
import time
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader

logger = logging.getLogger(__name__)


class TRMTrainer:
    """
    Trainer for Tiny Recursive Model.
    """

    def __init__(
        self,
        model: nn.Module,
        loss_fn: nn.Module,
        train_loader: DataLoader,
        val_loader: DataLoader,
        learning_rate: float = 1e-4,
        weight_decay: float = 1e-5,
        max_epochs: int = 100,
        patience: int = 20,
        grad_clip_norm: float = 1.0,
        device: str = 'cuda' if torch.cuda.is_available() else 'cpu',
        checkpoint_dir: Optional[str] = None,
        use_amp: bool = False  # DISABLED: AMP causes numerical instability
    ):
        """
        Args:
            model: TRM model to train
            loss_fn: Loss function (CompositeTradingLoss or similar)
            train_loader: Training data loader
            val_loader: Validation data loader
            learning_rate: Initial learning rate
            weight_decay: L2 regularization strength
            max_epochs: Maximum number of epochs
            patience: Early stopping patience (epochs)
            grad_clip_norm: Gradient clipping max norm
            device: Device to train on
            checkpoint_dir: Directory to save checkpoints
            use_amp: Whether to use automatic mixed precision
        """
        self.model = model.to(device)
        self.loss_fn = loss_fn.to(device)
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.device = device
        self.max_epochs = max_epochs
        self.patience = patience
        self.grad_clip_norm = grad_clip_norm
        # FORCE AMP TO FALSE - numerical instability
        self.use_amp = False
        self.learning_rate = learning_rate
        self.warmup_epochs = 2  # WARMUP: start with 10% LR for 2 epochs

        # Optimizer (AdamW with weight decay)
        # START with 10% of target LR for warmup
        self.optimizer = optim.AdamW(
            model.parameters(),
            lr=learning_rate * 0.1,  # Warmup LR
            weight_decay=weight_decay
        )

        # Learning rate scheduler (cosine annealing)
        self.scheduler = CosineAnnealingLR(
            self.optimizer,
            T_max=max_epochs,
            eta_min=learning_rate * 0.01
        )

        # AMP scaler REMOVED - causes gradient instability

        # Checkpoint directory
        if checkpoint_dir:
            self.checkpoint_dir = Path(checkpoint_dir)
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.checkpoint_dir = None

        # Training state
        self.current_epoch = 0
        self.best_val_loss = float('inf')
        self.best_val_sharpe = float('-inf')
        self.patience_counter = 0
        self.train_history = []
        self.val_history = []

        logger.info(
            f"Initialized TRMTrainer: "
            f"lr={learning_rate}, wd={weight_decay}, "
            f"epochs={max_epochs}, patience={patience}, "
            f"device={device}, amp={self.use_amp}"
        )

    def train_epoch(self) -> dict:
        """
        Train for one epoch.

        Returns:
            Metrics dict
        """
        self.model.train()
        epoch_loss = 0.0
        epoch_components = {}
        num_batches = 0

        # DEBUG: Track gradient stats
        total_grad_norm = 0.0
        total_param_norm = 0.0

        start_time = time.time()

        for batch_idx, (X, y) in enumerate(self.train_loader):
            X, y = X.to(self.device), y.to(self.device)

            # Forward pass (NO AMP - pure FP32)
            pred = self.model(X)
            loss, components = self.loss_fn(pred, y)

            # DEBUG: MANDATORY stats logging
            if batch_idx == 0:
                logger.info(f"[DEBUG Epoch {self.current_epoch}] First batch:")
                logger.info(f"  X stats: mean={X.mean().item():.6f}, std={X.std().item():.6f}, min={X.min().item():.6f}, max={X.max().item():.6f}")
                logger.info(f"  y stats: mean={y.mean().item():.6f}, std={y.std().item():.6f}, min={y.min().item():.6f}, max={y.max().item():.6f}")
                logger.info(f"  pred stats: mean={pred.mean().item():.6f}, std={pred.std().item():.6f}, min={pred.min().item():.6f}, max={pred.max().item():.6f}")
                logger.info(f"  pred[0:5]: {pred[:5].detach().cpu().numpy()}")
                logger.info(f"  y[0:5]: {y[:5].detach().cpu().numpy()}")
                logger.info(f"  loss: {loss.item():.6f}")

            # Backward pass
            self.optimizer.zero_grad()
            loss.backward()

            # CRITICAL: Check for NaN/Inf BEFORE clipping
            has_nan_or_inf = False
            total_grad_before_clip = 0.0

            for name, param in self.model.named_parameters():
                if param.grad is not None:
                    if torch.isnan(param.grad).any() or torch.isinf(param.grad).any():
                        logger.error(f"[GRADIENT EXPLOSION] NaN/Inf detected in {name}")
                        logger.error(f"  Grad: {param.grad}")
                        logger.error(f"  Param: {param.data}")
                        has_nan_or_inf = True

                    grad_val = param.grad.norm().item()
                    total_grad_before_clip += grad_val

                    # Log detailed gradient info in first epoch
                    if batch_idx == 0 and self.current_epoch <= 2:
                        logger.info(f"    {name}: grad_norm={grad_val:.6f}")

            # Log total gradient norm
            if batch_idx == 0:
                logger.info(f"  Total gradient norm (before clip): {total_grad_before_clip:.6f}")

            # STOP training if NaN/Inf detected
            if has_nan_or_inf:
                logger.error("STOPPING TRAINING - NaN/Inf gradients detected!")
                raise RuntimeError("NaN/Inf gradients - training stopped")

            # STOP if gradient norm explodes
            if total_grad_before_clip > 10.0:
                logger.error(f"GRADIENT EXPLOSION: norm={total_grad_before_clip:.2f} > 10.0")
                logger.error("STOPPING TRAINING - gradient explosion detected!")
                raise RuntimeError(f"Gradient explosion: norm={total_grad_before_clip:.2f}")

            # Gradient clipping
            grad_norm = torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)

            # AGGRESSIVE LR reduction if gradients are large
            if grad_norm > 0.5:
                current_lr = self.optimizer.param_groups[0]['lr']
                # Reduce by 50% each time gradients exceed threshold
                new_lr = max(current_lr * 0.5, 1e-6)  # Floor at 1e-6
                for param_group in self.optimizer.param_groups:
                    param_group['lr'] = new_lr
                logger.warning(f"[LR REDUCTION] grad_norm={grad_norm:.4f} > 0.5 → LR: {current_lr:.2e} → {new_lr:.2e}")

            self.optimizer.step()

            # DEBUG: Track gradient and parameter norms
            total_grad_norm += grad_norm.item()
            param_norm = sum(p.data.norm().item() for p in self.model.parameters())
            total_param_norm += param_norm

            # Accumulate metrics
            epoch_loss += loss.item()
            for key, val in components.items():
                if key not in epoch_components:
                    epoch_components[key] = 0.0
                epoch_components[key] += val

            num_batches += 1

        # Average metrics
        epoch_loss /= num_batches
        for key in epoch_components:
            epoch_components[key] /= num_batches

        # DEBUG: Log gradient stats
        avg_grad_norm = total_grad_norm / num_batches
        avg_param_norm = total_param_norm / num_batches
        if self.current_epoch <= 3:
            logger.info(f"[DEBUG Epoch {self.current_epoch}] Avg gradient norm: {avg_grad_norm:.6f}")
            logger.info(f"[DEBUG Epoch {self.current_epoch}] Avg parameter norm: {avg_param_norm:.6f}")

        elapsed_time = time.time() - start_time

        metrics = {
            'epoch': self.current_epoch,
            'loss': epoch_loss,
            'time': elapsed_time,
            'grad_norm': avg_grad_norm,
            'param_norm': avg_param_norm,
            **epoch_components
        }

        return metrics

    @torch.no_grad()
    def validate_epoch(self) -> dict:
        """
        Validate for one epoch.

        Returns:
            Metrics dict
        """
        self.model.eval()
        epoch_loss = 0.0
        epoch_components = {}
        num_batches = 0

        # Track statistics for Sharpe without storing full tensors
        sharpe_sum = 0.0
        sharpe_sum_sq = 0.0
        sharpe_count = 0

        # DEBUG: Track validation predictions
        all_preds = []
        all_targets = []

        start_time = time.time()

        for batch_idx, (X, y) in enumerate(self.val_loader):
            X, y = X.to(self.device), y.to(self.device)

            # Forward pass (NO AMP)
            pred = self.model(X)
            loss, components = self.loss_fn(pred, y)

            # DEBUG: Store predictions for first few batches
            if batch_idx < 5 and self.current_epoch <= 2:
                all_preds.append(pred.cpu().numpy())
                all_targets.append(y.cpu().numpy())

            # Accumulate metrics
            epoch_loss += loss.item()
            for key, val in components.items():
                if key not in epoch_components:
                    epoch_components[key] = 0.0
                epoch_components[key] += val

            # Update Sharpe statistics (pred * true_return)
            # CORRECTED: Use pred * y instead of sign(pred) * y
            realized = pred * y
            sharpe_sum += realized.sum().item()
            sharpe_sum_sq += (realized ** 2).sum().item()
            sharpe_count += realized.numel()

            num_batches += 1

        # Average metrics
        epoch_loss /= num_batches
        for key in epoch_components:
            epoch_components[key] /= num_batches

        sharpe = self._compute_sharpe_from_stats(sharpe_sum, sharpe_sum_sq, sharpe_count)

        # DEBUG: Log validation prediction stats
        if self.current_epoch <= 2 and len(all_preds) > 0:
            import numpy as np
            preds_concat = np.concatenate(all_preds)
            targets_concat = np.concatenate(all_targets)
            logger.info(f"[DEBUG Epoch {self.current_epoch}] Val predictions (first 5 batches):")
            logger.info(f"  pred mean={preds_concat.mean():.6f}, std={preds_concat.std():.6f}")
            logger.info(f"  pred min={preds_concat.min():.6f}, max={preds_concat.max():.6f}")
            logger.info(f"  target mean={targets_concat.mean():.6f}, std={targets_concat.std():.6f}")
            logger.info(f"  Sharpe components: sum={sharpe_sum:.6f}, sum_sq={sharpe_sum_sq:.6f}, count={sharpe_count}")

        elapsed_time = time.time() - start_time

        metrics = {
            'epoch': self.current_epoch,
            'loss': epoch_loss,
            'sharpe': sharpe,
            'time': elapsed_time,
            **epoch_components
        }

        return metrics

    def _compute_sharpe_from_stats(self, sum_returns: float, sum_squares: float, count: int) -> float:
        """
        Compute the Sharpe ratio from aggregated statistics with ANOMALY DETECTION.

        CRITICAL: Sharpe > 3.0 in validation is flagged as numerical anomaly.
        """
        if count == 0:
            return 0.0

        mean_return = sum_returns / count
        mean_square = sum_squares / count
        variance = max(mean_square - mean_return ** 2, 1e-12)
        std_return = variance ** 0.5

        sharpe = mean_return / std_return

        # Annualize (assume 1-minute bars, 252 trading days, 6.5 hours/day)
        annualization_factor = (252 * 6.5 * 60) ** 0.5
        sharpe *= annualization_factor

        # CRITICAL: Flag unrealistic Sharpe as anomaly
        if abs(sharpe) > 3.0:
            logger.warning(f"[ANOMALY] Unrealistic Sharpe={sharpe:.2f} detected (predictions may be saturating)")
            logger.warning(f"  Mean return: {mean_return:.8f}, Std return: {std_return:.8f}")

        return sharpe

    def save_checkpoint(self, is_best: bool = False):
        """
        Save model checkpoint.

        Args:
            is_best: Whether this is the best model so far
        """
        if self.checkpoint_dir is None:
            return

        checkpoint = {
            'epoch': self.current_epoch,
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'scheduler_state_dict': self.scheduler.state_dict(),
            'best_val_loss': self.best_val_loss,
            'best_val_sharpe': self.best_val_sharpe,
            'train_history': self.train_history,
            'val_history': self.val_history
        }

        # Save latest checkpoint
        checkpoint_path = self.checkpoint_dir / 'checkpoint_latest.pt'
        torch.save(checkpoint, checkpoint_path)

        # Save best checkpoint
        if is_best:
            best_path = self.checkpoint_dir / 'checkpoint_best.pt'
            torch.save(checkpoint, best_path)
            logger.info(f"Saved best checkpoint: {best_path}")

    def load_checkpoint(self, checkpoint_path: str):
        """
        Load model checkpoint.

        Args:
            checkpoint_path: Path to checkpoint file
        """
        checkpoint = torch.load(checkpoint_path, map_location=self.device)

        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
        self.current_epoch = checkpoint['epoch']
        self.best_val_loss = checkpoint['best_val_loss']
        self.best_val_sharpe = checkpoint.get('best_val_sharpe', float('-inf'))
        self.train_history = checkpoint.get('train_history', [])
        self.val_history = checkpoint.get('val_history', [])

        logger.info(f"Loaded checkpoint from epoch {self.current_epoch}")

    def train(self) -> dict:
        """
        Full training loop with early stopping.

        Returns:
            Training history dict
        """
        logger.info("Starting training...")

        for epoch in range(self.max_epochs):
            self.current_epoch = epoch + 1

            # Train epoch
            train_metrics = self.train_epoch()
            self.train_history.append(train_metrics)

            # Validate epoch
            val_metrics = self.validate_epoch()
            self.val_history.append(val_metrics)

            # WARMUP: Gradually increase LR for first epochs
            if self.current_epoch <= self.warmup_epochs:
                warmup_factor = self.current_epoch / self.warmup_epochs
                target_lr = self.learning_rate * warmup_factor
                for param_group in self.optimizer.param_groups:
                    param_group['lr'] = target_lr
                current_lr = target_lr
                logger.info(f"[WARMUP] Epoch {self.current_epoch}/{self.warmup_epochs}: LR={current_lr:.2e}")
            else:
                # Learning rate step (after warmup)
                self.scheduler.step()
                current_lr = self.scheduler.get_last_lr()[0]

            # Log metrics
            logger.info(
                f"Epoch {self.current_epoch}/{self.max_epochs} | "
                f"Train Loss: {train_metrics['loss']:.6f} | "
                f"Val Loss: {val_metrics['loss']:.6f} | "
                f"Val Sharpe: {val_metrics['sharpe']:.4f} | "
                f"LR: {current_lr:.2e}"
            )

            # Check for improvement (use Sharpe as primary metric)
            is_best = False
            if val_metrics['sharpe'] > self.best_val_sharpe:
                self.best_val_sharpe = val_metrics['sharpe']
                self.best_val_loss = val_metrics['loss']
                self.patience_counter = 0
                is_best = True
                logger.info(f"New best validation Sharpe: {self.best_val_sharpe:.4f}")
            else:
                self.patience_counter += 1

            # Save checkpoint
            self.save_checkpoint(is_best=is_best)

            # Early stopping
            if self.patience_counter >= self.patience:
                logger.info(
                    f"Early stopping triggered after {self.patience} epochs "
                    f"without improvement"
                )
                break

        logger.info(
            f"Training complete. Best validation Sharpe: {self.best_val_sharpe:.4f}"
        )

        return {
            'train_history': self.train_history,
            'val_history': self.val_history,
            'best_val_sharpe': self.best_val_sharpe,
            'best_val_loss': self.best_val_loss,
            'final_epoch': self.current_epoch
        }


if __name__ == "__main__":
    # Test trainer with dummy data
    logging.basicConfig(level=logging.INFO)

    from torch.utils.data import TensorDataset

    # Create dummy data
    n_train = 1000
    n_val = 200
    seq_len = 60
    num_features = 10
    batch_size = 32

    X_train = torch.randn(n_train, seq_len, num_features)
    y_train = torch.randn(n_train)
    X_val = torch.randn(n_val, seq_len, num_features)
    y_val = torch.randn(n_val)

    train_dataset = TensorDataset(X_train, y_train)
    val_dataset = TensorDataset(X_val, y_val)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=False)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    # Create model and loss
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from model.trm import TinyRecursiveModel
    from model.loss import CompositeTradingLoss

    model = TinyRecursiveModel(num_features=num_features)
    loss_fn = CompositeTradingLoss()

    # Create trainer
    trainer = TRMTrainer(
        model=model,
        loss_fn=loss_fn,
        train_loader=train_loader,
        val_loader=val_loader,
        max_epochs=5,
        patience=3,
        device='cpu'
    )

    # Train
    history = trainer.train()

    print("\nTraining complete!")
    print(f"Best validation Sharpe: {history['best_val_sharpe']:.4f}")
