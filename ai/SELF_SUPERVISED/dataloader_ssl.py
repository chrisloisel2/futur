"""
DataLoader for self-supervised learning on time series.

Handles loading data from MongoDB, creating sequences, and
applying augmentations for contrastive learning.
"""
import logging
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from pymongo import MongoClient

logger = logging.getLogger(__name__)


class TimeSeriesSSLDataset(Dataset):
    """
    Dataset for self-supervised learning on time series.

    Loads OHLCV data from MongoDB and creates sequences for SSL.
    Supports multiple augmentations for contrastive learning.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        sequence_length: int = 100,
        stride: int = 1,
        features: Optional[List[str]] = None,
        augment: bool = True,
        return_two_views: bool = True,
    ):
        """
        Args:
            data: DataFrame with time series data
            sequence_length: Length of each sequence
            stride: Stride for creating sequences
            features: List of feature columns to use (None = all numeric)
            augment: Whether to apply augmentations
            return_two_views: If True, return two augmented views (for contrastive)
        """
        self.data = data
        self.sequence_length = sequence_length
        self.stride = stride
        self.augment = augment
        self.return_two_views = return_two_views

        # Select features
        if features is None:
            self.features = data.select_dtypes(include=[np.number]).columns.tolist()
        else:
            self.features = features

        # Extract sequences
        self.sequences = self._create_sequences()

        logger.info(
            f"Created SSL dataset: {len(self.sequences)} sequences of length {sequence_length}"
        )

    def _create_sequences(self) -> List[np.ndarray]:
        """Create overlapping sequences from data."""
        sequences = []

        data_values = self.data[self.features].values

        for i in range(0, len(data_values) - self.sequence_length + 1, self.stride):
            seq = data_values[i:i + self.sequence_length]
            sequences.append(seq)

        return sequences

    def _augment_sequence(self, seq: np.ndarray) -> np.ndarray:
        """Apply random augmentations to sequence."""
        seq = seq.copy()

        # Jitter: Add Gaussian noise
        if np.random.rand() > 0.5:
            noise = np.random.randn(*seq.shape) * 0.03
            seq = seq + noise

        # Scaling: Multiply by random factor
        if np.random.rand() > 0.5:
            factor = np.random.randn() * 0.1 + 1.0
            seq = seq * factor

        # Time shift: Random circular shift
        if np.random.rand() > 0.5:
            shift = np.random.randint(-self.sequence_length // 10, self.sequence_length // 10)
            seq = np.roll(seq, shift, axis=0)

        return seq

    def __len__(self) -> int:
        return len(self.sequences)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, ...]:
        """
        Get sequence(s).

        Returns:
            If return_two_views:
                (seq1, seq2) - two augmented views
            Else:
                seq - single sequence
        """
        seq = self.sequences[idx].astype(np.float32)

        if self.return_two_views:
            # Create two augmented views for contrastive learning
            if self.augment:
                seq1 = self._augment_sequence(seq)
                seq2 = self._augment_sequence(seq)
            else:
                seq1 = seq
                seq2 = seq.copy()

            return (
                torch.from_numpy(seq1),
                torch.from_numpy(seq2),
            )
        else:
            # Single sequence (for MAE)
            if self.augment:
                seq = self._augment_sequence(seq)

            return torch.from_numpy(seq)


def load_data_from_mongodb(
    mongo_uri: str,
    db_name: str,
    collection_name: str = "historical_ohlcv",
    symbols: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load time series data from MongoDB.

    Args:
        mongo_uri: MongoDB connection URI
        db_name: Database name
        collection_name: Collection name
        symbols: List of symbols to load (None = all)
        limit: Maximum number of documents to load

    Returns:
        DataFrame with time series data
    """
    logger.info(f"Loading data from MongoDB: {db_name}.{collection_name}")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    collection = db[collection_name]

    # Build query
    query = {}
    if symbols:
        query['coin'] = {'$in': symbols}

    # Load data
    cursor = collection.find(query).sort('timestamp', 1)

    if limit:
        cursor = cursor.limit(limit)

    data = list(cursor)
    client.close()

    if not data:
        raise ValueError("No data found in MongoDB")

    df = pd.DataFrame(data)

    # Convert timestamp to datetime
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.sort_values('timestamp')

    logger.info(f"Loaded {len(df)} records from MongoDB")

    return df


def load_data_from_parquet(
    file_path: str,
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Load time series data from Parquet file.

    Args:
        file_path: Path to Parquet file
        columns: Columns to load (None = all)

    Returns:
        DataFrame with time series data
    """
    logger.info(f"Loading data from Parquet: {file_path}")

    df = pd.read_parquet(file_path, columns=columns)

    logger.info(f"Loaded {len(df)} records from Parquet")

    return df


def get_ssl_dataloaders(
    data_config: Dict[str, Any],
    batch_size: int = 32,
    num_workers: int = 4,
    train_ratio: float = 0.8,
    sequence_length: int = 100,
    stride: int = 1,
    return_two_views: bool = True,
) -> Tuple[DataLoader, DataLoader]:
    """
    Create train and validation dataloaders for SSL.

    Args:
        data_config: Configuration dict with data source info
        batch_size: Batch size
        num_workers: Number of worker processes
        train_ratio: Ratio of training data
        sequence_length: Sequence length
        stride: Stride for creating sequences
        return_two_views: Whether to return two views (for contrastive)

    Returns:
        train_loader, val_loader
    """
    # Load data
    data_source = data_config.get('source', 'mongodb')

    if data_source == 'mongodb':
        df = load_data_from_mongodb(
            mongo_uri=data_config['mongo_uri'],
            db_name=data_config['db_name'],
            collection_name=data_config.get('collection_name', 'historical_ohlcv'),
            symbols=data_config.get('symbols'),
            limit=data_config.get('limit'),
        )
    elif data_source == 'parquet':
        df = load_data_from_parquet(
            file_path=data_config['file_path'],
            columns=data_config.get('columns'),
        )
    else:
        raise ValueError(f"Unknown data source: {data_source}")

    # Normalize data (simple z-score)
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    df[numeric_cols] = (df[numeric_cols] - df[numeric_cols].mean()) / (df[numeric_cols].std() + 1e-8)

    # Split into train/val
    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx]
    val_df = df.iloc[split_idx:]

    logger.info(f"Train size: {len(train_df)}, Val size: {len(val_df)}")

    # Create datasets
    train_dataset = TimeSeriesSSLDataset(
        data=train_df,
        sequence_length=sequence_length,
        stride=stride,
        augment=True,
        return_two_views=return_two_views,
    )

    val_dataset = TimeSeriesSSLDataset(
        data=val_df,
        sequence_length=sequence_length,
        stride=stride,
        augment=False,  # No augmentation for validation
        return_two_views=return_two_views,
    )

    # Create dataloaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=True,
    )

    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True,
    )

    return train_loader, val_loader


def create_mae_dataloaders(
    data_config: Dict[str, Any],
    batch_size: int = 32,
    num_workers: int = 4,
    train_ratio: float = 0.8,
    sequence_length: int = 100,
    stride: int = 1,
) -> Tuple[DataLoader, DataLoader]:
    """
    Create dataloaders for MAE (single view).

    Args:
        data_config: Configuration dict
        batch_size: Batch size
        num_workers: Number of workers
        train_ratio: Training data ratio
        sequence_length: Sequence length
        stride: Stride

    Returns:
        train_loader, val_loader
    """
    return get_ssl_dataloaders(
        data_config=data_config,
        batch_size=batch_size,
        num_workers=num_workers,
        train_ratio=train_ratio,
        sequence_length=sequence_length,
        stride=stride,
        return_two_views=False,  # MAE uses single view
    )
