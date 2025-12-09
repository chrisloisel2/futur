"""
Self-Supervised Learning module for time series representation learning.

This module provides state-of-the-art self-supervised learning techniques
for financial time series, enabling models to learn meaningful representations
from unlabeled data.

Supported methods:
- TS2Vec: Contrastive learning for time series
- MAE: Masked Autoencoder for time series
- SimCLR: Contrastive learning framework

Usage:
    from SELF_SUPERVISED import TS2VecModel, pretrain_ts2vec
    from SELF_SUPERVISED import MAEModel, pretrain_mae
"""

from .model_ssl import TS2VecModel, MAEModel, SimCLRModel
from .pretrain import pretrain_ts2vec, pretrain_mae, pretrain_simclr
from .dataloader_ssl import TimeSeriesSSLDataset, get_ssl_dataloaders
from .masking_strategies import RandomMasking, BlockMasking, GeometricMasking
from .contrastive import (
    TS2VecLoss,
    NTXentLoss,
    SupConLoss,
    create_augmentations,
    temporal_augmentation,
)
from .mae import MAEEncoder, MAEDecoder, masked_modeling_loss

__all__ = [
    # Models
    "TS2VecModel",
    "MAEModel",
    "SimCLRModel",
    # Pretraining
    "pretrain_ts2vec",
    "pretrain_mae",
    "pretrain_simclr",
    # Data
    "TimeSeriesSSLDataset",
    "get_ssl_dataloaders",
    # Masking
    "RandomMasking",
    "BlockMasking",
    "GeometricMasking",
    # Contrastive
    "TS2VecLoss",
    "NTXentLoss",
    "SupConLoss",
    "create_augmentations",
    "temporal_augmentation",
    # MAE
    "MAEEncoder",
    "MAEDecoder",
    "masked_modeling_loss",
]

__version__ = "1.0.0"
