"""
S3 Data Source - Stub implementation for API server
This provides a minimal implementation to allow the API server to start.
"""
import logging
from typing import List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class S3DataSource:
    """Minimal S3 data source implementation."""

    def __init__(self, bucket: str, prefix: str, cache_dir: str):
        """Initialize S3 data source.

        Args:
            bucket: S3 bucket name
            prefix: S3 prefix path
            cache_dir: Local cache directory
        """
        self.bucket = bucket
        self.prefix = prefix
        self.cache_dir = cache_dir
        logger.info(f"S3DataSource initialized: bucket={bucket}, prefix={prefix}")

    def list_available_years(self) -> List[int]:
        """List available years in S3.

        Returns:
            List of available years
        """
        logger.warning("S3DataSource.list_available_years() - stub implementation")
        return []

    def list_symbols(self, year: int) -> List[str]:
        """List available symbols for a given year.

        Args:
            year: Year to query

        Returns:
            List of symbol names
        """
        logger.warning(f"S3DataSource.list_symbols({year}) - stub implementation")
        return []

    def load_data(
        self,
        symbol: str,
        year: int,
        interval: str = "1h",
        quote: str = "USDT"
    ) -> Optional[pd.DataFrame]:
        """Load data for a symbol/year.

        Args:
            symbol: Trading symbol
            year: Year to load
            interval: Timeframe interval
            quote: Quote currency

        Returns:
            DataFrame with price data or None
        """
        logger.warning(
            f"S3DataSource.load_data(symbol={symbol}, year={year}, "
            f"interval={interval}, quote={quote}) - stub implementation"
        )
        return None
