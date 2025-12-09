"""
Example usage of self-supervised learning models.

Shows how to:
1. Load data from MongoDB
2. Pretrain TS2Vec or MAE
3. Use pretrained encoder for downstream tasks
"""
import logging
import yaml
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from SELF_SUPERVISED import (
    pretrain_ts2vec,
    pretrain_mae,
    pretrain_simclr,
    get_ssl_dataloaders,
    create_mae_dataloaders,
    TS2VecModel,
    MAEModel,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config_ssl.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def example_ts2vec_pretraining():
    """Example: Pretrain TS2Vec model."""
    logger.info("=== TS2Vec Pretraining Example ===")

    # Load config
    config = load_config("config_ssl.yaml")

    # Create dataloaders (two views for contrastive learning)
    train_loader, val_loader = get_ssl_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        stride=config['data']['stride'],
        return_two_views=True,
    )

    logger.info(f"Train batches: {len(train_loader)}, Val batches: {len(val_loader)}")

    # Merge configs
    ts2vec_config = {
        **config['ts2vec'],
        **config['training'],
    }

    # Pretrain
    model = pretrain_ts2vec(
        config=ts2vec_config,
        train_loader=train_loader,
        val_loader=val_loader,
        checkpoint_dir=config['checkpoints']['ts2vec_dir'],
    )

    logger.info("TS2Vec pretraining complete!")

    return model


def example_mae_pretraining():
    """Example: Pretrain MAE model."""
    logger.info("=== MAE Pretraining Example ===")

    # Load config
    config = load_config("config_ssl.yaml")

    # Create dataloaders (single view for MAE)
    train_loader, val_loader = create_mae_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        stride=config['data']['stride'],
    )

    logger.info(f"Train batches: {len(train_loader)}, Val batches: {len(val_loader)}")

    # Merge configs
    mae_config = {
        **config['mae'],
        **config['training'],
    }

    # Pretrain
    model = pretrain_mae(
        config=mae_config,
        train_loader=train_loader,
        val_loader=val_loader,
        checkpoint_dir=config['checkpoints']['mae_dir'],
    )

    logger.info("MAE pretraining complete!")

    return model


def example_simclr_pretraining():
    """Example: Pretrain SimCLR model."""
    logger.info("=== SimCLR Pretraining Example ===")

    # Load config
    config = load_config("config_ssl.yaml")

    # Create dataloaders
    train_loader, val_loader = get_ssl_dataloaders(
        data_config=config['data'],
        batch_size=config['training']['batch_size'],
        num_workers=config['training']['num_workers'],
        train_ratio=config['data']['train_ratio'],
        sequence_length=config['data']['sequence_length'],
        stride=config['data']['stride'],
        return_two_views=True,
    )

    # Merge configs
    simclr_config = {
        **config['simclr'],
        **config['training'],
    }

    # Pretrain
    model = pretrain_simclr(
        config=simclr_config,
        train_loader=train_loader,
        val_loader=val_loader,
        checkpoint_dir=config['checkpoints']['simclr_dir'],
    )

    logger.info("SimCLR pretraining complete!")

    return model


def example_use_pretrained_encoder():
    """Example: Use pretrained encoder for downstream task."""
    logger.info("=== Using Pretrained Encoder ===")

    device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')

    # Load pretrained TS2Vec model
    checkpoint_path = "./checkpoints/ts2vec/ts2vec_final.pt"

    if not Path(checkpoint_path).exists():
        logger.error(f"Checkpoint not found: {checkpoint_path}")
        logger.info("Run pretraining first!")
        return

    model = TS2VecModel(
        input_dim=8,
        hidden_dim=64,
        output_dim=320,
        depth=10,
    ).to(device)

    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()

    logger.info("Pretrained model loaded!")

    # Example: Encode new data
    dummy_data = torch.randn(4, 100, 8).to(device)  # [batch, seq_len, input_dim]

    with torch.no_grad():
        # Get sequence-level embeddings
        embeddings = model.encode(dummy_data, return_all=False)  # [batch, output_dim]
        logger.info(f"Embeddings shape: {embeddings.shape}")

        # Get timestep-level embeddings
        embeddings_all = model.encode(dummy_data, return_all=True)  # [batch, seq_len, output_dim]
        logger.info(f"All timesteps embeddings shape: {embeddings_all.shape}")

    # Use embeddings for downstream task (e.g., classification, regression)
    # Example: Add a prediction head
    prediction_head = torch.nn.Linear(320, 1).to(device)
    predictions = prediction_head(embeddings)
    logger.info(f"Predictions shape: {predictions.shape}")


def example_transfer_learning():
    """Example: Transfer learning with pretrained encoder."""
    logger.info("=== Transfer Learning Example ===")

    device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')

    # Load pretrained encoder
    encoder = TS2VecModel(
        input_dim=8,
        hidden_dim=64,
        output_dim=320,
        depth=10,
    ).to(device)

    checkpoint_path = "./checkpoints/ts2vec/ts2vec_final.pt"

    if Path(checkpoint_path).exists():
        checkpoint = torch.load(checkpoint_path, map_location=device)
        encoder.load_state_dict(checkpoint['model_state_dict'])
        logger.info("Pretrained encoder loaded!")
    else:
        logger.warning("No checkpoint found, using random initialization")

    # Freeze encoder (optional)
    for param in encoder.parameters():
        param.requires_grad = False

    # Add downstream task head
    class DownstreamModel(torch.nn.Module):
        def __init__(self, encoder, output_dim=1):
            super().__init__()
            self.encoder = encoder
            self.head = torch.nn.Sequential(
                torch.nn.Linear(320, 128),
                torch.nn.ReLU(),
                torch.nn.Dropout(0.1),
                torch.nn.Linear(128, output_dim),
            )

        def forward(self, x):
            # Encode
            embeddings = self.encoder.encode(x, return_all=False)
            # Predict
            return self.head(embeddings)

    model = DownstreamModel(encoder).to(device)

    logger.info("Downstream model created!")

    # Train on supervised task...
    # (See ai/TRAIN/train.py for training loop)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Self-Supervised Learning Examples")
    parser.add_argument(
        '--mode',
        type=str,
        choices=['ts2vec', 'mae', 'simclr', 'use_pretrained', 'transfer'],
        default='ts2vec',
        help='Which example to run',
    )

    args = parser.parse_args()

    if args.mode == 'ts2vec':
        example_ts2vec_pretraining()
    elif args.mode == 'mae':
        example_mae_pretraining()
    elif args.mode == 'simclr':
        example_simclr_pretraining()
    elif args.mode == 'use_pretrained':
        example_use_pretrained_encoder()
    elif args.mode == 'transfer':
        example_transfer_learning()
