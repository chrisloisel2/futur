"""
Example usage of enhanced SSL model with multiple encoders and objectives.

Shows how to:
1. Train with different encoder types
2. Train with different SSL objectives
3. Use pretrained encoder for downstream tasks
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import yaml
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.optim import AdamW
from tqdm import tqdm

from SELF_SUPERVISED.model_ssl_enhanced import SSLModel, create_ssl_model
from SELF_SUPERVISED.dataloader_ssl import get_ssl_dataloaders
from SELF_SUPERVISED.contrastive import NTXentLoss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config_ssl_enhanced.yaml") -> dict:
    """Load configuration."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        logger.warning(f"Config file {config_path} not found, using defaults")
        return get_default_config()


def get_default_config() -> dict:
    """Get default configuration."""
    return {
        'data': {
            'source': 'mongodb',
            'mongo_uri': 'mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/',
            'db_name': 'trader2',
            'collection_name': 'historical_ohlcv',
            'symbols': ['BTC/USDT', 'ETH/USDT'],
            'sequence_length': 100,
            'train_ratio': 0.8,
        },
        'model': {
            'input_dim': 8,
            'd_model': 256,
            'n_heads': 8,
            'n_layers': 4,
            'projection_dim': 128,
            'mask_ratio': 0.3,
            'patch_len': 16,
            'dropout': 0.1,
        },
        'training': {
            'batch_size': 64,
            'epochs': 10,
            'lr': 0.001,
            'device': 'auto',
            'num_workers': 4,
        },
    }


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


def train_masked_modeling(
    encoder_type: str = "transformer",
    config: dict = None,
    epochs: int = 10,
):
    """
    Example: Train with Masked Modeling (MAE).

    Args:
        encoder_type: 'transformer', 'timesnet', or 'multimodal'
        config: Configuration dict
        epochs: Number of epochs
    """
    logger.info(f"=== Training Masked Modeling with {encoder_type} ===")

    if config is None:
        config = get_default_config()

    device = get_device(config['training']['device'])

    # Create model
    model = create_ssl_model(
        config=config['model'],
        encoder_type=encoder_type,
        ssl_objective="masked",
    ).to(device)

    logger.info(f"Model created on {device}")

    # Create dataloaders (single view for MAE)
    train_loader, val_loader = get_ssl_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        return_two_views=False,  # Single view for MAE
    )

    # Optimizer
    optimizer = AdamW(model.parameters(), lr=config['training']['lr'])

    # Training loop
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch_idx, x in enumerate(pbar):
            x = x.to(device)

            # Forward (model computes loss internally)
            outputs = model(x)
            loss = outputs['loss']

            # Backward
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

    logger.info("Training complete!")
    return model


def train_contrastive_learning(
    encoder_type: str = "transformer",
    config: dict = None,
    epochs: int = 10,
):
    """
    Example: Train with Contrastive Learning.

    Args:
        encoder_type: 'transformer', 'timesnet', or 'multimodal'
        config: Configuration dict
        epochs: Number of epochs
    """
    logger.info(f"=== Training Contrastive Learning with {encoder_type} ===")

    if config is None:
        config = get_default_config()

    device = get_device(config['training']['device'])

    # Create model
    model = create_ssl_model(
        config=config['model'],
        encoder_type=encoder_type,
        ssl_objective="contrastive",
    ).to(device)

    logger.info(f"Model created on {device}")

    # Create dataloaders (two views for contrastive)
    train_loader, val_loader = get_ssl_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        return_two_views=True,  # Two views for contrastive
    )

    # Loss
    criterion = NTXentLoss(temperature=0.5)

    # Optimizer
    optimizer = AdamW(model.parameters(), lr=config['training']['lr'])

    # Training loop
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch_idx, (x1, x2) in enumerate(pbar):
            x1 = x1.to(device)
            x2 = x2.to(device)

            # Forward
            outputs = model(x1, x_aug=x2)
            proj1 = outputs['proj1']
            proj2 = outputs['proj2']

            # Contrastive loss
            loss = criterion(proj1, proj2)

            # Backward
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

    logger.info("Training complete!")
    return model


def train_next_patch_prediction(
    encoder_type: str = "transformer",
    config: dict = None,
    epochs: int = 10,
):
    """
    Example: Train with Next Patch Prediction.

    Args:
        encoder_type: 'transformer', 'timesnet', or 'multimodal'
        config: Configuration dict
        epochs: Number of epochs
    """
    logger.info(f"=== Training Next Patch Prediction with {encoder_type} ===")

    if config is None:
        config = get_default_config()

    device = get_device(config['training']['device'])

    # Create model
    model = create_ssl_model(
        config=config['model'],
        encoder_type=encoder_type,
        ssl_objective="next_patch",
    ).to(device)

    logger.info(f"Model created on {device}")

    # Create dataloaders (single view)
    train_loader, val_loader = get_ssl_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        return_two_views=False,
    )

    # Optimizer
    optimizer = AdamW(model.parameters(), lr=config['training']['lr'])

    # Training loop
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")

        for batch_idx, x in enumerate(pbar):
            x = x.to(device)

            # Forward (model computes loss internally)
            outputs = model(x)
            loss = outputs['loss']

            # Backward
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            pbar.set_postfix({'loss': loss.item()})

        train_loss /= len(train_loader)

        logger.info(f"Epoch {epoch+1}: train_loss={train_loss:.4f}")

    logger.info("Training complete!")
    return model


def use_pretrained_encoder():
    """
    Example: Use pretrained encoder for downstream task.
    """
    logger.info("=== Using Pretrained Encoder ===")

    device = get_device('auto')

    # Create and load pretrained model
    config = get_default_config()
    model = create_ssl_model(
        config=config['model'],
        encoder_type="transformer",
        ssl_objective="contrastive",
    ).to(device)

    logger.info("Pretrained model loaded")

    # Freeze encoder
    for param in model.encoder.parameters():
        param.requires_grad = False

    logger.info("Encoder frozen")

    # Add downstream task head
    class DownstreamModel(nn.Module):
        def __init__(self, ssl_model, num_classes=3):
            super().__init__()
            self.ssl_model = ssl_model
            self.head = nn.Sequential(
                nn.Linear(256, 128),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(128, num_classes),
            )

        def forward(self, x):
            # Encode
            embeddings = self.ssl_model.encode(x, return_all=False)
            # Predict
            return self.head(embeddings)

    downstream_model = DownstreamModel(model).to(device)

    logger.info("Downstream model created")

    # Example forward pass
    dummy_x = torch.randn(4, 100, 8).to(device)
    predictions = downstream_model(dummy_x)

    logger.info(f"Predictions shape: {predictions.shape}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enhanced SSL Training Examples")
    parser.add_argument(
        '--objective',
        type=str,
        choices=['masked', 'contrastive', 'next_patch'],
        default='contrastive',
        help='SSL objective',
    )
    parser.add_argument(
        '--encoder',
        type=str,
        choices=['transformer', 'timesnet', 'multimodal'],
        default='transformer',
        help='Encoder type',
    )
    parser.add_argument(
        '--epochs',
        type=int,
        default=10,
        help='Number of epochs',
    )

    args = parser.parse_args()

    # Load config
    config = load_config()

    # Train based on objective
    if args.objective == 'masked':
        model = train_masked_modeling(
            encoder_type=args.encoder,
            config=config,
            epochs=args.epochs,
        )
    elif args.objective == 'contrastive':
        model = train_contrastive_learning(
            encoder_type=args.encoder,
            config=config,
            epochs=args.epochs,
        )
    elif args.objective == 'next_patch':
        model = train_next_patch_prediction(
            encoder_type=args.encoder,
            config=config,
            epochs=args.epochs,
        )

    # Save model
    torch.save(
        model.state_dict(),
        f"checkpoints/ssl_{args.objective}_{args.encoder}.pt"
    )

    logger.info("Model saved!")
