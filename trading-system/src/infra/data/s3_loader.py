"""
S3 Data Loader for Trading System
Loads processed market data from S3 for backtesting
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import s3fs
from common.logging.setup import get_logger

logger = get_logger(__name__)


class S3MarketDataLoader:
    """
    Load processed market data from S3.

    Data location: s3://qbia/bourse/processed/market/
    Structure: interval=1m/quote=USDT/symbol={SYMBOL}/year={YEAR}/*.parquet

    Processed data includes:
    - OHLCV (Open, High, Low, Close, Volume)
    - Technical indicators (EMA, RSI, ATR, etc.)
    - Volatility metrics (RV, VaR, CVaR)
    - Labels (label_policy, label_tradeable)
    """

    def __init__(
        self,
        bucket: str = "qbia",
        base_path: str = "bourse/processed/market",
        interval: str = "1m",
        quote: str = "USDT",
    ):
        self.bucket = bucket
        self.base_path = base_path
        self.interval = interval
        self.quote = quote
        self.fs = s3fs.S3FileSystem()

    def load(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        Load data for a symbol between start_date and end_date.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            columns: Optional list of columns to load (None = all)

        Returns:
            DataFrame with market data and features
        """
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)

        # Get years to load
        years = list(range(start.year, end.year + 1))

        logger.info({
            "msg": "Loading market data from S3",
            "symbol": symbol,
            "start": start_date,
            "end": end_date,
            "years": years,
        })

        dfs = []
        for year in years:
            try:
                year_df = self._load_year(symbol, year, columns)
                if year_df is not None and not year_df.empty:
                    dfs.append(year_df)
            except Exception as e:
                logger.warning({
                    "msg": "Failed to load year",
                    "symbol": symbol,
                    "year": year,
                    "error": str(e),
                })

        if not dfs:
            logger.error({
                "msg": "No data loaded",
                "symbol": symbol,
                "years": years,
            })
            return pd.DataFrame()

        # Concatenate all years
        df = pd.concat(dfs, ignore_index=True)

        # Ensure datetime column
        if 'datetime' not in df.columns and 'Open_Time' in df.columns:
            df['datetime'] = pd.to_datetime(df['Open_Time'], unit='ms')
        elif 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])

        # Filter by date range
        if 'datetime' in df.columns:
            # Ensure timezone compatibility
            if df['datetime'].dt.tz is not None:
                # Data is timezone-aware, make comparison tz-aware too
                start = start.tz_localize('UTC')
                end = end.tz_localize('UTC')

            mask = (df['datetime'] >= start) & (df['datetime'] <= end)
            df = df[mask].copy()

        # Sort by time
        if 'datetime' in df.columns:
            df = df.sort_values('datetime').reset_index(drop=True)

        logger.info({
            "msg": "Data loaded successfully",
            "symbol": symbol,
            "rows": len(df),
            "start": df['datetime'].min() if 'datetime' in df.columns else None,
            "end": df['datetime'].max() if 'datetime' in df.columns else None,
            "columns": len(df.columns),
        })

        return df

    def _load_year(
        self,
        symbol: str,
        year: int,
        columns: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        """Load data for a specific year."""
        # Path pattern
        pattern = (
            f"{self.bucket}/{self.base_path}/"
            f"interval={self.interval}/"
            f"quote={self.quote}/"
            f"symbol={symbol}/"
            f"year={year}/*.parquet"
        )

        # Find files
        files = self.fs.glob(pattern)
        if not files:
            logger.warning({
                "msg": "No files found",
                "pattern": pattern,
            })
            return None

        # Read all parquet files for this year
        s3_files = [f"s3://{f}" for f in files]

        logger.debug({
            "msg": "Loading files",
            "symbol": symbol,
            "year": year,
            "files": len(s3_files),
        })

        dfs = []
        for s3_file in s3_files:
            try:
                df = pd.read_parquet(
                    s3_file,
                    filesystem=self.fs,
                    columns=columns,
                )
                dfs.append(df)
            except Exception as e:
                logger.warning({
                    "msg": "Failed to read file",
                    "file": s3_file,
                    "error": str(e),
                })

        if not dfs:
            return None

        return pd.concat(dfs, ignore_index=True)

    def get_available_symbols(self) -> List[str]:
        """Get list of available symbols."""
        pattern = f"{self.bucket}/{self.base_path}/interval={self.interval}/quote={self.quote}/symbol=*"

        try:
            paths = self.fs.glob(pattern)
            symbols = set()
            for path in paths:
                # Extract symbol from path
                parts = path.split('/')
                for part in parts:
                    if part.startswith('symbol='):
                        symbol = part.split('=')[1]
                        symbols.add(symbol)

            return sorted(list(symbols))
        except Exception as e:
            logger.error({
                "msg": "Failed to get symbols",
                "error": str(e),
            })
            return []

    def get_date_range(self, symbol: str) -> tuple[Optional[str], Optional[str]]:
        """Get earliest and latest available dates for a symbol."""
        try:
            # Load first and last year
            pattern = f"{self.bucket}/{self.base_path}/interval={self.interval}/quote={self.quote}/symbol={symbol}/year=*"
            paths = self.fs.glob(pattern)

            if not paths:
                return None, None

            years = []
            for path in paths:
                parts = path.split('/')
                for part in parts:
                    if part.startswith('year='):
                        year = int(part.split('=')[1])
                        years.append(year)

            if not years:
                return None, None

            min_year = min(years)
            max_year = max(years)

            # Load min/max year to get exact dates
            df_min = self._load_year(symbol, min_year, columns=['datetime', 'Open_Time'])
            df_max = self._load_year(symbol, max_year, columns=['datetime', 'Open_Time'])

            if df_min is None or df_max is None:
                return None, None

            # Get datetime
            if 'datetime' in df_min.columns:
                min_date = pd.to_datetime(df_min['datetime']).min()
            elif 'Open_Time' in df_min.columns:
                min_date = pd.to_datetime(df_min['Open_Time'], unit='ms').min()
            else:
                min_date = None

            if 'datetime' in df_max.columns:
                max_date = pd.to_datetime(df_max['datetime']).max()
            elif 'Open_Time' in df_max.columns:
                max_date = pd.to_datetime(df_max['Open_Time'], unit='ms').max()
            else:
                max_date = None

            return (
                min_date.strftime('%Y-%m-%d') if min_date else None,
                max_date.strftime('%Y-%m-%d') if max_date else None,
            )

        except Exception as e:
            logger.error({
                "msg": "Failed to get date range",
                "symbol": symbol,
                "error": str(e),
            })
            return None, None


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize column names to match trading system expectations.

    Processed data uses capitalized names (Open, High, Low, Close)
    Trading system expects lowercase (open, high, low, close)
    """
    rename_map = {
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume',
        'Open_Time': 'open_time',
        'close_time': 'close_time',
        'Quote_Volume': 'quote_volume',
        'Trades': 'trades',
        'Taker_Buy_Base': 'taker_buy_base',
        'Taker_Buy_Quote': 'taker_buy_quote',
    }

    return df.rename(columns=rename_map)
