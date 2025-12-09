"""
Pretraining scripts for self-supervised learning models.

Implements training loops for:
- TS2Vec (contrastive learning)
- MAE (masked autoencoding)
- SimCLR (contrastive learning)
"""
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Any

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from tqdm import tqdm

from .model_ssl import TS2VecModel, MAEModel, SimCLRModel
from .contrastive import TS2VecLoss, NTXentLoss, temporal_augmentation
from .masking_strategies import get_masking_strategy
from .dataloader_ssl import get_ssl_dataloaders, create_mae_dataloaders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_device(device_str: str = 'auto') -> torch.device:
    """Get torch device."""
    if device_str == 'auto':
        if torch.backends.mps.is_available():
            return torch.device('mps')
        elif torch.cuda.is_available():
            return torch.device('cuda')
        else:
            return torch.device('cpu')
    else:
        return torch.device(device_str)


def save_checkpoint(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    loss: float,
    checkpoint_dir: Path,
    filename: str = "checkpoint.pt",
):
    """Save training checkpoint."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'loss': loss,
    }

    filepath = checkpoint_dir / filename
    torch.save(checkpoint, filepath)
    logger.info(f"Checkpoint saved: {filepath}")


def load_checkpoint(
    model: nn.Module,
    optimizer: Optional[torch.optim.Optimizer],
    checkpoint_path: str,
    device: torch.device,
):
    """Load training checkpoint."""
    checkpoint = torch.load(checkpoint_path, map_location=device)

    model.load_state_dict(checkpoint['model_state_dict'])

    if optimizer:
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])

    epoch = checkpoint['epoch']
    loss = checkpoint['loss']

    logger.info(f"Checkpoint loaded: epoch {epoch}, loss {loss:.4f}")

    return epoch, loss


def pretrain_ts2vec(
    config: Dict[str, Any],
    train_loader: DataLoader,
    val_loader: Optional[DataLoader] = None,
    checkpoint_dir: str = "./checkpoints/ts2vec",
    resume_from: Optional[str] = None,
) -> TS2VecModel:
    """
    Pretrain TS2Vec model.

    Args:
        config: Configuration dict with model and training parameters
        train_loader: Training dataloader (returns two views)
        val_loader: Optional validation dataloader
        checkpoint_dir: Directory to save checkpoints
        resume_from: Optional checkpoint path to resume from

    Returns:
        Trained TS2VecModel
    """
    logger.info("Starting TS2Vec pretraining...")

    # Setup
    device = get_device(config.get('device', 'auto'))
    checkpoint_dir = Path(checkpoint_dir)

    # Model
    model = TS2VecModel(
        input_dim=config['input_dim'],
        hidden_dim=config.get('hidden_dim', 64),
        output_dim=config.get('output_dim', 320),
        depth=config.get('depth', 10),
    ).to(device)

    # Loss
    criterion = TS2VecLoss(
        temperature=config.get('temperature', 0.2),
        temporal_unit=config.get('temporal_unit', 0),
    )

    # Optimizer
    optimizer = AdamW(
        model.parameters(),
        lr=config.get('lr', 1e-3),
        weight_decay=config.get('weight_decay', 1e-5),
    )

    # Scheduler
    scheduler = CosineAnnealingLR(
        optimizer,
        T_max=config.get('epochs', 100),
    )

    # Resume from checkpoint
    start_epoch = 0
    if resume_from:
        start_epoch, _ = load_checkpoint(model, optimizer, resume_from, device)
        start_epoch += 1

    # Training loop
    epochs = config.get('epochs', 100)

    for epoch in range(start_epoch, epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch in pbar:
            x1, x2 = batch
            x1 = x1.to(device)
            x2 = x2.to(device)

            # Forward
            z1 = model(x1)
            z2 = model(x2)

            # Loss
            loss = criterion(z1, z2)

            # Backward
            optimizer.zero_grad()
            loss.backward()

            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        # Validation
        if val_loader:
            model.eval()
            val_loss = 0.0

            with torch.no_grad():
                for batch in val_loader:
                    x1, x2 = batch
                    x1 = x1.to(device)
                    x2 = x2.to(device)

                    z1 = model(x1)
                    z2 = model(x2)

                    loss = criterion(z1, z2)
                    val_loss += loss.item()

            val_loss /= len(val_loader)

            logger.info(
                f"Epoch {epoch+1}: train_loss={train_loss:.4f}, val_loss={val_loss:.4f}"
            )
        else:
            logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

        # Step scheduler
        scheduler.step()

        # Save checkpoint
        if (epoch + 1) % config.get('save_every', 10) == 0:
            save_checkpoint(
                model, optimizer, epoch, train_loss,
                checkpoint_dir, f"ts2vec_epoch_{epoch+1}.pt"
            )

    # Save final model
    save_checkpoint(
        model, optimizer, epochs - 1, train_loss,
        checkpoint_dir, "ts2vec_final.pt"
    )

    logger.info("TS2Vec pretraining complete!")

    return model


def pretrain_mae(
    config: Dict[str, Any],
    train_loader: DataLoader,
    val_loader: Optional[DataLoader] = None,
    checkpoint_dir: str = "./checkpoints/mae",
    resume_from: Optional[str] = None,
) -> MAEModel:
    """
    Pretrain MAE model.

    Args:
        config: Configuration dict
        train_loader: Training dataloader (single view)
        val_loader: Optional validation dataloader
        checkpoint_dir: Checkpoint directory
        resume_from: Optional checkpoint to resume from

    Returns:
        Trained MAEModel
    """
    logger.info("Starting MAE pretraining...")

    # Setup
    device = get_device(config.get('device', 'auto'))
    checkpoint_dir = Path(checkpoint_dir)

    # Model
    model = MAEModel(
        input_dim=config['input_dim'],
        d_model=config.get('d_model', 256),
        n_heads=config.get('n_heads', 8),
        n_layers=config.get('n_layers', 6),
        decoder_depth=config.get('decoder_depth', 2),
        dropout=config.get('dropout', 0.1),
        mask_ratio=config.get('mask_ratio', 0.75),
    ).to(device)

    # Masking strategy
    masking_strategy = get_masking_strategy(
        strategy=config.get('masking_strategy', 'random'),
        mask_ratio=config.get('mask_ratio', 0.75),
        **config.get('masking_kwargs', {})
    )

    # Optimizer
    optimizer = AdamW(
        model.parameters(),
        lr=config.get('lr', 1e-3),
        weight_decay=config.get('weight_decay', 1e-5),
    )

    # Scheduler
    scheduler = CosineAnnealingLR(
        optimizer,
        T_max=config.get('epochs', 100),
    )

    # Resume from checkpoint
    start_epoch = 0
    if resume_from:
        start_epoch, _ = load_checkpoint(model, optimizer, resume_from, device)
        start_epoch += 1

    # Training loop
    epochs = config.get('epochs', 100)

    for epoch in range(start_epoch, epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch in pbar:
            x = batch.to(device)
            batch_size, seq_len, _ = x.shape

            # Generate mask
            mask = masking_strategy(batch_size, seq_len, device)

            # Forward (model computes loss internally)
            reconstructed, mask, loss = model(x, mask)

            # Backward
            optimizer.zero_grad()
            loss.backward()

            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        # Validation
        if val_loader:
            model.eval()
            val_loss = 0.0

            with torch.no_grad():
                for batch in val_loader:
                    x = batch.to(device)
                    batch_size, seq_len, _ = x.shape

                    mask = masking_strategy(batch_size, seq_len, device)
                    reconstructed, mask, loss = model(x, mask)

                    val_loss += loss.item()

            val_loss /= len(val_loader)

            logger.info(
                f"Epoch {epoch+1}: train_loss={train_loss:.4f}, val_loss={val_loss:.4f}"
            )
        else:
            logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

        # Step scheduler
        scheduler.step()

        # Save checkpoint
        if (epoch + 1) % config.get('save_every', 10) == 0:
            save_checkpoint(
                model, optimizer, epoch, train_loss,
                checkpoint_dir, f"mae_epoch_{epoch+1}.pt"
            )

    # Save final model
    save_checkpoint(
        model, optimizer, epochs - 1, train_loss,
        checkpoint_dir, "mae_final.pt"
    )

    logger.info("MAE pretraining complete!")

    return model


def pretrain_simclr(
    config: Dict[str, Any],
    train_loader: DataLoader,
    val_loader: Optional[DataLoader] = None,
    checkpoint_dir: str = "./checkpoints/simclr",
    resume_from: Optional[str] = None,
) -> SimCLRModel:
    """
    Pretrain SimCLR model.

    Args:
        config: Configuration dict
        train_loader: Training dataloader (two views)
        val_loader: Optional validation dataloader
        checkpoint_dir: Checkpoint directory
        resume_from: Optional checkpoint to resume from

    Returns:
        Trained SimCLRModel
    """
    logger.info("Starting SimCLR pretraining...")

    # Setup
    device = get_device(config.get('device', 'auto'))
    checkpoint_dir = Path(checkpoint_dir)

    # Model
    model = SimCLRModel(
        input_dim=config['input_dim'],
        hidden_dim=config.get('hidden_dim', 256),
        projection_dim=config.get('projection_dim', 128),
        depth=config.get('depth', 6),
    ).to(device)

    # Loss
    criterion = NTXentLoss(temperature=config.get('temperature', 0.5))

    # Optimizer
    optimizer = AdamW(
        model.parameters(),
        lr=config.get('lr', 1e-3),
        weight_decay=config.get('weight_decay', 1e-5),
    )

    # Scheduler
    scheduler = CosineAnnealingLR(
        optimizer,
        T_max=config.get('epochs', 100),
    )

    # Resume from checkpoint
    start_epoch = 0
    if resume_from:
        start_epoch, _ = load_checkpoint(model, optimizer, resume_from, device)
        start_epoch += 1

    # Training loop
    epochs = config.get('epochs', 100)

    for epoch in range(start_epoch, epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch in pbar:
            x1, x2 = batch
            x1 = x1.to(device)
            x2 = x2.to(device)

            # Forward
            z1 = model(x1)
            z2 = model(x2)

            # Loss
            loss = criterion(z1, z2)

            # Backward
            optimizer.zero_grad()
            loss.backward()

            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        # Validation
        if val_loader:
            model.eval()
            val_loss = 0.0

            with torch.no_grad():
                for batch in val_loader:
                    x1, x2 = batch
                    x1 = x1.to(device)
                    x2 = x2.to(device)

                    z1 = model(x1)
                    z2 = model(x2)

                    loss = criterion(z1, z2)
                    val_loss += loss.item()

            val_loss /= len(val_loader)

            logger.info(
                f"Epoch {epoch+1}: train_loss={train_loss:.4f}, val_loss={val_loss:.4f}"
            )
        else:
            logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

        # Step scheduler
        scheduler.step()

        # Save checkpoint
        if (epoch + 1) % config.get('save_every', 10) == 0:
            save_checkpoint(
                model, optimizer, epoch, train_loss,
                checkpoint_dir, f"simclr_epoch_{epoch+1}.pt"
            )

    # Save final model
    save_checkpoint(
        model, optimizer, epochs - 1, train_loss,
        checkpoint_dir, "simclr_final.pt"
    )

    logger.info("SimCLR pretraining complete!")

    return model
