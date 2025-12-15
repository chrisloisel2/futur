"""
S3DataSource for loading historical klines data from AWS S3.
"""
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import boto3
import pandas as pd
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3DataSource:
    """
    Data source for loading klines (OHLCV) data from S3 bucket.

    Supports:
    - Multi-year data loading
    - Multi-symbol data loading
    - Local caching to avoid re-downloading
    - Automatic conversion from Binance klines format to standard OHLCV DataFrame
    """

    COLUMN_NAMES = [
        'open_time',
        'open',
        'high',
        'low',
        'close',
        'volume',
        'close_time',
        'quote_volume',
        'trades',
        'taker_buy_base',
        'taker_buy_quote',
        'ignore'
    ]

    def __init__(
        self,
        bucket: str,
        prefix: str = "bourse/mintrad",
        cache_dir: Optional[str] = None,
        region_name: str = "us-east-1"
    ):
        """
        Initialize S3 data source.

        Args:
            bucket: S3 bucket name (e.g., "qbia")
            prefix: S3 prefix path (e.g., "bourse/mintrad")
            cache_dir: Local directory for caching downloaded files (optional)
            region_name: AWS region name
        """
        self.bucket = bucket
        self.prefix = prefix.rstrip('/')
        self.cache_dir = Path(cache_dir) if cache_dir else None

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Using local cache directory: {self.cache_dir}")

        self.s3_client = boto3.client('s3', region_name=region_name)
        logger.info(f"Initialized S3DataSource: bucket={bucket}, prefix={prefix}")

    def list_available_years(self) -> List[int]:
        """
        List all available years in the S3 bucket.

        Returns:
            Sorted list of available years (e.g., [2017, 2018, ..., 2025])
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=self.prefix + '/',
                Delimiter='/'
            )

            years = []
            for prefix_obj in response.get('CommonPrefixes', []):
                folder_name = prefix_obj['Prefix'].rstrip('/').split('/')[-1]
                # Extract year from folder name like "klines_1m_TRADING_USDT_2024"
                match = re.search(r'(\d{4})$', folder_name)
                if match:
                    years.append(int(match.group(1)))

            years.sort()
            logger.info(f"Found {len(years)} years: {years}")
            return years

        except ClientError as e:
            logger.error(f"Error listing years from S3: {e}")
            return []

    def list_available_symbols(self, year: int) -> List[str]:
        """
        List all available trading symbols for a given year.

        Args:
            year: Year to list symbols for (e.g., 2024)

        Returns:
            List of symbol names (e.g., ["BTCUSDT", "ETHUSDT", ...])
        """
        folder_prefix = f"{self.prefix}/klines_1m_TRADING_USDT_{year}/"

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=folder_prefix
            )

            symbols = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                filename = key.split('/')[-1]
                # Extract symbol from filename like "BTCUSDT_2024_1m.parquet"
                match = re.match(r'([A-Z]+USDT)_\d{4}_1m\.parquet', filename)
                if match:
                    symbols.append(match.group(1))

            symbols.sort()
            logger.info(f"Found {len(symbols)} symbols for year {year}")
            return symbols

        except ClientError as e:
            logger.error(f"Error listing symbols for year {year}: {e}")
            return []

    def _get_s3_key(self, symbol: str, year: int) -> str:
        """
        Construct S3 key for a symbol/year combination.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            year: Year

        Returns:
            S3 key (e.g., "bourse/mintrad/klines_1m_TRADING_USDT_2024/BTCUSDT_2024_1m.parquet")
        """
        return f"{self.prefix}/klines_1m_TRADING_USDT_{year}/{symbol}_{year}_1m.parquet"

    def _get_cache_path(self, symbol: str, year: int) -> Optional[Path]:
        """
        Get local cache file path for a symbol/year.

        Args:
            symbol: Trading symbol
            year: Year

        Returns:
            Path to cache file, or None if caching is disabled
        """
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{symbol}_{year}_1m.parquet"

    def fetch_symbol_data(self, symbol: str, year: int) -> pd.DataFrame:
        """
        Fetch data for a single symbol and year.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            year: Year to fetch

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, symbol
        """
        cache_path = self._get_cache_path(symbol, year)

        # Check cache first
        if cache_path and cache_path.exists():
            logger.info(f"Loading {symbol} {year} from cache: {cache_path}")
            df = pd.read_parquet(cache_path)
            # Ensure timestamp is datetime
            if 'timestamp' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            return df

        # Download from S3
        s3_key = self._get_s3_key(symbol, year)
        logger.info(f"Downloading {symbol} {year} from S3: s3://{self.bucket}/{s3_key}")

        try:
            # Always download to temporary location first
            import tempfile
            temp_file = Path(tempfile.mktemp(suffix='.parquet'))

            self.s3_client.download_file(self.bucket, s3_key, str(temp_file))

            # Read raw parquet file
            df_raw = pd.read_parquet(temp_file)

            # Convert to standard format
            df = self._convert_to_standard_format(df_raw, symbol)

            # Save converted data to cache if enabled
            if cache_path:
                df.to_parquet(cache_path, index=False)
                logger.info(f"Cached {symbol} {year} to {cache_path}")

            # Clean up temp file
            if temp_file.exists():
                temp_file.unlink()

            logger.info(f"Loaded {symbol} {year}: {len(df)} rows")
            return df

        except ClientError as e:
            logger.error(f"Error downloading {symbol} {year} from S3: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error processing {symbol} {year}: {e}")
            return pd.DataFrame()

    def _convert_to_standard_format(self, df_raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Convert raw Binance klines format to standard OHLCV format.

        Args:
            df_raw: Raw DataFrame with numeric columns (0-11)
            symbol: Trading symbol to add

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, symbol
        """
        # Rename columns to standard names
        df_raw.columns = self.COLUMN_NAMES

        # Convert timestamp from milliseconds to datetime
        df_raw['timestamp'] = pd.to_datetime(df_raw['open_time'], unit='ms', utc=True)

        # Select and rename relevant columns
        df = df_raw[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()

        # Add symbol identifier
        df['symbol'] = symbol

        # Sort by timestamp
        df = df.sort_values('timestamp').reset_index(drop=True)

        return df

    def fetch_symbol_range(
        self,
        symbol: str,
        start_year: int,
        end_year: int
    ) -> pd.DataFrame:
        """
        Fetch data for a single symbol across multiple years.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            start_year: Start year (inclusive)
            end_year: End year (inclusive)

        Returns:
            Concatenated DataFrame with all years
        """
        logger.info(f"Fetching {symbol} from {start_year} to {end_year}")

        frames = []
        for year in range(start_year, end_year + 1):
            df = self.fetch_symbol_data(symbol, year)
            if not df.empty:
                frames.append(df)

        if not frames:
            logger.warning(f"No data found for {symbol} between {start_year}-{end_year}")
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values('timestamp').reset_index(drop=True)

        logger.info(f"Loaded {symbol}: {len(result)} total rows from {len(frames)} years")
        return result

    def fetch_all_symbols_range(
        self,
        symbols: List[str],
        start_year: int,
        end_year: int
    ) -> pd.DataFrame:
        """
        Fetch data for multiple symbols across multiple years.

        Args:
            symbols: List of trading symbols (e.g., ["BTCUSDT", "ETHUSDT"])
            start_year: Start year (inclusive)
            end_year: End year (inclusive)

        Returns:
            Concatenated DataFrame with all symbols and years
        """
        logger.info(f"Fetching {len(symbols)} symbols from {start_year} to {end_year}")
        logger.info(f"Symbols: {symbols}")

        frames = []
        for symbol in symbols:
            df = self.fetch_symbol_range(symbol, start_year, end_year)
            if not df.empty:
                frames.append(df)

        if not frames:
            logger.warning(f"No data found for any symbol between {start_year}-{end_year}")
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(['symbol', 'timestamp']).reset_index(drop=True)

        logger.info(f"Loaded {len(frames)} symbols: {len(result)} total rows")
        return result
