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
        use_amp: bool = True  # Automatic Mixed Precision
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
        self.use_amp = use_amp and device == 'cuda'

        # Optimizer (AdamW with weight decay)
        self.optimizer = optim.AdamW(
            model.parameters(),
            lr=learning_rate,
            weight_decay=weight_decay
        )

        # Learning rate scheduler (cosine annealing)
        self.scheduler = CosineAnnealingLR(
            self.optimizer,
            T_max=max_epochs,
            eta_min=learning_rate * 0.01
        )

        # AMP scaler (if using mixed precision)
        self.scaler = torch.cuda.amp.GradScaler() if self.use_amp else None

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

        start_time = time.time()

        for batch_idx, (X, y) in enumerate(self.train_loader):
            X, y = X.to(self.device), y.to(self.device)

            # Forward pass (with mixed precision if enabled)
            if self.use_amp:
                with torch.cuda.amp.autocast():
                    pred = self.model(X)
                    loss, components = self.loss_fn(pred, y)
            else:
                pred = self.model(X)
                loss, components = self.loss_fn(pred, y)

            # Backward pass
            self.optimizer.zero_grad()

            if self.use_amp:
                self.scaler.scale(loss).backward()
                # Gradient clipping
                self.scaler.unscale_(self.optimizer)
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)
                self.optimizer.step()

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

        elapsed_time = time.time() - start_time

        metrics = {
            'epoch': self.current_epoch,
            'loss': epoch_loss,
            'time': elapsed_time,
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

        # Collect all predictions and targets for Sharpe calculation
        all_preds = []
        all_targets = []

        start_time = time.time()

        for X, y in self.val_loader:
            X, y = X.to(self.device), y.to(self.device)

            # Forward pass
            if self.use_amp:
                with torch.cuda.amp.autocast():
                    pred = self.model(X)
                    loss, components = self.loss_fn(pred, y)
            else:
                pred = self.model(X)
                loss, components = self.loss_fn(pred, y)

            # Accumulate metrics
            epoch_loss += loss.item()
            for key, val in components.items():
                if key not in epoch_components:
                    epoch_components[key] = 0.0
                epoch_components[key] += val

            # Store predictions and targets
            all_preds.append(pred.cpu())
            all_targets.append(y.cpu())

            num_batches += 1

        # Average metrics
        epoch_loss /= num_batches
        for key in epoch_components:
            epoch_components[key] /= num_batches

        # Compute validation Sharpe ratio
        all_preds = torch.cat(all_preds)
        all_targets = torch.cat(all_targets)
        sharpe = self._compute_sharpe(all_preds, all_targets)

        elapsed_time = time.time() - start_time

        metrics = {
            'epoch': self.current_epoch,
            'loss': epoch_loss,
            'sharpe': sharpe,
            'time': elapsed_time,
            **epoch_components
        }

        return metrics

    def _compute_sharpe(
        self,
        pred_returns: torch.Tensor,
        true_returns: torch.Tensor
    ) -> float:
        """
        Compute Sharpe ratio from predictions.

        Args:
            pred_returns: Predicted returns
            true_returns: Actual returns

        Returns:
            Sharpe ratio
        """
        # Positions from predictions
        positions = torch.sign(pred_returns)

        # Realized returns
        realized_returns = positions * true_returns

        # Sharpe ratio
        mean_return = realized_returns.mean().item()
        std_return = realized_returns.std().item() + 1e-8

        sharpe = mean_return / std_return

        # Annualize (assume 1-minute bars, 252 trading days, 6.5 hours/day)
        annualization_factor = (252 * 6.5 * 60) ** 0.5
        sharpe *= annualization_factor

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

            # Learning rate step
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
