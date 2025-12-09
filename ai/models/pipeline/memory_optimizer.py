"""Memory optimization utilities for large datasets."""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def optimize_dtypes(df: pd.DataFrame, aggressive: bool = False) -> pd.DataFrame:
    """
    Optimize DataFrame memory usage by downcasting numeric types.

    Args:
        df: Input DataFrame
        aggressive: If True, use float32 instead of float64
    """
    df_optimized = df.copy()
    initial_memory = df.memory_usage(deep=True).sum() / 1024**2

    for col in df_optimized.columns:
        col_type = df_optimized[col].dtype

        if col_type != object:
            if str(col_type).startswith("int"):
                df_optimized[col] = pd.to_numeric(df_optimized[col], downcast="integer")
            elif str(col_type).startswith("float"):
                if aggressive:
                    df_optimized[col] = df_optimized[col].astype(np.float32)
                else:
                    df_optimized[col] = pd.to_numeric(df_optimized[col], downcast="float")

        # Convert object columns to category if cardinality is low
        elif df_optimized[col].nunique() / len(df_optimized) < 0.5:
            df_optimized[col] = df_optimized[col].astype("category")

    final_memory = df_optimized.memory_usage(deep=True).sum() / 1024**2
    reduction = 100 * (initial_memory - final_memory) / initial_memory

    logger.info(
        f"Memory optimized: {initial_memory:.2f}MB -> {final_memory:.2f}MB "
        f"({reduction:.1f}% reduction)"
    )

    return df_optimized


def downsample_old_data(
    df: pd.DataFrame,
    timestamp_col: str = "timestamp",
    recent_periods: int = 1000,
    downsample_freq: str = "1D",
) -> pd.DataFrame:
    """
    Downsample older data to reduce memory usage.

    Args:
        df: Input DataFrame with timestamp index or column
        timestamp_col: Name of timestamp column
        recent_periods: Number of recent periods to keep at full resolution
        downsample_freq: Frequency for downsampling old data (e.g., '1D', '4H')
    """
    df_sorted = df.sort_values(timestamp_col)

    # Split into recent and old
    recent = df_sorted.iloc[-recent_periods:]
    old = df_sorted.iloc[:-recent_periods]

    if len(old) == 0:
        return df

    # Downsample old data
    old_downsampled = (
        old.set_index(timestamp_col)
        .resample(downsample_freq)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                **{col: "mean" for col in old.columns if col not in ["open", "high", "low", "close", "volume", timestamp_col]},
            }
        )
        .reset_index()
    )

    combined = pd.concat([old_downsampled, recent], ignore_index=True).sort_values(timestamp_col)

    logger.info(
        f"Downsampled old data: {len(df)} -> {len(combined)} rows "
        f"({100 * (len(df) - len(combined)) / len(df):.1f}% reduction)"
    )

    return combined


def chunked_loader(
    fetch_func,
    chunk_size: int = 5000,
    max_rows: Optional[int] = None,
    **kwargs,
):
    """
    Load data in chunks to avoid memory overflow.

    Args:
        fetch_func: Function that returns data (e.g., CcxtDataSource.fetch_historical_range)
        chunk_size: Size of each chunk
        max_rows: Maximum total rows to load
        **kwargs: Arguments to pass to fetch_func
    """
    all_data = []
    offset = 0
    total_loaded = 0

    while True:
        chunk = fetch_func(limit_per_call=chunk_size, **kwargs)

        if not chunk or len(chunk) == 0:
            break

        all_data.extend(chunk)
        total_loaded += len(chunk)

        logger.info(f"Loaded chunk: {len(chunk)} rows (total: {total_loaded})")

        if max_rows and total_loaded >= max_rows:
            all_data = all_data[:max_rows]
            break

        if len(chunk) < chunk_size:
            break

        offset += chunk_size

    return all_data
