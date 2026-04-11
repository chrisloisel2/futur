"""
Pipeline for saving scraped indicators to AWS S3 in Parquet format.
"""
import logging
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Dict, List
import pandas as pd
import boto3
from botocore.exceptions import ClientError
from scrapy.exceptions import DropItem

logger = logging.getLogger(__name__)


class S3IndicatorsPipeline:
    """
    Pipeline for batching and saving crypto indicators to S3.

    Features:
    - Batches items by symbol and time period
    - Saves to S3 in Parquet format for efficient storage
    - Automatic deduplication
    - Memory-efficient processing
    """

    def __init__(
        self,
        bucket: str,
        prefix: str,
        batch_size: int = 1000,
        region_name: str = 'us-east-1'
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip('/')
        self.batch_size = batch_size
        self.region_name = region_name

        # Buffer for batching items
        self.items_buffer: Dict[str, List[dict]] = {}

        # S3 client
        self.s3_client = None

        # Stats
        self.stats = {
            'items_processed': 0,
            'items_saved': 0,
            'batches_uploaded': 0,
            'errors': 0,
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize pipeline from crawler settings."""
        return cls(
            bucket=crawler.settings.get('S3_BUCKET', 'qbia'),
            prefix=crawler.settings.get('S3_INDICATORS_PREFIX', 'bourse/indicators'),
            batch_size=crawler.settings.getint('S3_BATCH_SIZE', 1000),
            region_name=crawler.settings.get('AWS_REGION', 'us-east-1'),
        )

    def open_spider(self, spider):
        """Called when spider is opened."""
        self.s3_client = boto3.client('s3', region_name=self.region_name)
        logger.info(f"S3IndicatorsPipeline opened: s3://{self.bucket}/{self.prefix}")

    def close_spider(self, spider):
        """Called when spider is closed - flush remaining items."""
        logger.info("Flushing remaining items to S3...")

        # Save all remaining batches
        for key in list(self.items_buffer.keys()):
            if self.items_buffer[key]:
                self._save_batch(key, self.items_buffer[key])

        logger.info(f"Pipeline stats: {self.stats}")

    def process_item(self, item, spider):
        """Process each scraped item."""
        try:
            # Create a key for batching (symbol + year + month)
            timestamp = item.get('timestamp')
            if not timestamp:
                raise DropItem("Item missing timestamp")

            symbol = item.get('symbol')
            if not symbol:
                raise DropItem("Item missing symbol")

            # Convert timestamp to datetime if it's not already
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp)

            # Create batch key: symbol_year_month
            batch_key = f"{symbol}_{timestamp.year}_{timestamp.month:02d}"

            # Initialize buffer for this key if needed
            if batch_key not in self.items_buffer:
                self.items_buffer[batch_key] = []

            # Convert item to dict and add to buffer
            item_dict = dict(item)
            self.items_buffer[batch_key].append(item_dict)

            self.stats['items_processed'] += 1

            # Check if batch is ready to save
            if len(self.items_buffer[batch_key]) >= self.batch_size:
                self._save_batch(batch_key, self.items_buffer[batch_key])
                self.items_buffer[batch_key] = []

            return item

        except Exception as e:
            logger.error(f"Error processing item: {e}")
            self.stats['errors'] += 1
            raise DropItem(f"Error processing item: {e}")

    def _save_batch(self, batch_key: str, items: List[dict]):
        """Save a batch of items to S3."""
        try:
            if not items:
                return

            logger.info(f"Saving batch {batch_key} with {len(items)} items")

            # Convert to DataFrame
            df = pd.DataFrame(items)

            # Ensure timestamp is datetime
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Sort by timestamp
            df = df.sort_values('timestamp')

            # Remove duplicates (keep last)
            df = df.drop_duplicates(subset=['symbol', 'timestamp', 'source'], keep='last')

            # Parse batch key to get symbol and date
            parts = batch_key.split('_')
            symbol = parts[0]
            year = parts[1]
            month = parts[2]

            # Create S3 key
            s3_key = f"{self.prefix}/indicators_1m_{year}/{symbol}_{year}_{month}_indicators.parquet"

            # Check if file already exists and merge if needed
            try:
                # Try to download existing file
                temp_file = Path(tempfile.mktemp(suffix='.parquet'))
                self.s3_client.download_file(self.bucket, s3_key, str(temp_file))

                # Read existing data
                existing_df = pd.read_parquet(temp_file)

                # Merge with new data
                df = pd.concat([existing_df, df], ignore_index=True)
                df = df.drop_duplicates(subset=['symbol', 'timestamp', 'source'], keep='last')
                df = df.sort_values('timestamp')

                logger.info(f"Merged with existing data: {len(existing_df)} + {len(items)} = {len(df)} rows")

                # Clean up temp file
                if temp_file.exists():
                    temp_file.unlink()

            except ClientError as e:
                # File doesn't exist yet, that's fine
                if e.response['Error']['Code'] != '404':
                    logger.warning(f"Error checking existing file: {e}")

            # Save to temporary file
            temp_output = Path(tempfile.mktemp(suffix='.parquet'))
            df.to_parquet(temp_output, index=False, compression='snappy')

            # Upload to S3
            self.s3_client.upload_file(str(temp_output), self.bucket, s3_key)

            logger.info(f"Uploaded to s3://{self.bucket}/{s3_key}")

            # Clean up
            if temp_output.exists():
                temp_output.unlink()

            self.stats['items_saved'] += len(items)
            self.stats['batches_uploaded'] += 1

        except Exception as e:
            logger.error(f"Error saving batch {batch_key}: {e}")
            self.stats['errors'] += 1


class CalculatedIndicatorsPipeline:
    """
    Pipeline for calculating technical indicators from OHLCV data.

    This pipeline calculates indicators that aren't provided by APIs.
    """

    def __init__(self):
        # Keep a rolling window of data for each symbol to calculate indicators
        self.symbol_data: Dict[str, List[dict]] = {}
        self.window_size = 100  # Keep last 100 candles for calculations

    def process_item(self, item, spider):
        """Calculate additional indicators."""
        try:
            symbol = item.get('symbol')
            if not symbol:
                return item

            # Initialize buffer for this symbol
            if symbol not in self.symbol_data:
                self.symbol_data[symbol] = []

            # Add current item to buffer
            self.symbol_data[symbol].append(dict(item))

            # Keep only last N items
            if len(self.symbol_data[symbol]) > self.window_size:
                self.symbol_data[symbol] = self.symbol_data[symbol][-self.window_size:]

            # Calculate indicators if we have enough data
            if len(self.symbol_data[symbol]) >= 20:
                self._calculate_indicators(item, self.symbol_data[symbol])

            return item

        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return item

    def _calculate_indicators(self, item, historical_data: List[dict]):
        """Calculate technical indicators from historical data."""
        try:
            # Convert to DataFrame for easier calculations
            df = pd.DataFrame(historical_data)

            if 'close' not in df.columns or df['close'].empty:
                return

            # Calculate SMAs
            if len(df) >= 7:
                item['sma_7'] = df['close'].tail(7).mean()
            if len(df) >= 25:
                item['sma_25'] = df['close'].tail(25).mean()
            if len(df) >= 99:
                item['sma_99'] = df['close'].tail(99).mean()

            # Calculate EMAs
            if len(df) >= 7:
                item['ema_7'] = df['close'].ewm(span=7, adjust=False).mean().iloc[-1]
            if len(df) >= 25:
                item['ema_25'] = df['close'].ewm(span=25, adjust=False).mean().iloc[-1]
            if len(df) >= 99:
                item['ema_99'] = df['close'].ewm(span=99, adjust=False).mean().iloc[-1]

            # Calculate RSI (14 periods)
            if len(df) >= 14 and 'rsi_14' not in item:
                delta = df['close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                item['rsi_14'] = rsi.iloc[-1]

            # Calculate volume SMA
            if 'volume' in df.columns and len(df) >= 20:
                item['volume_sma'] = df['volume'].tail(20).mean()

            # Calculate pivot points
            if 'high' in df.columns and 'low' in df.columns and 'close' in df.columns:
                high = df['high'].iloc[-1]
                low = df['low'].iloc[-1]
                close = df['close'].iloc[-1]

                pivot = (high + low + close) / 3
                item['pivot_point'] = pivot
                item['resistance_1'] = 2 * pivot - low
                item['support_1'] = 2 * pivot - high
                item['resistance_2'] = pivot + (high - low)
                item['support_2'] = pivot - (high - low)

        except Exception as e:
            logger.error(f"Error in indicator calculations: {e}")


class ValidationPipeline:
    """Pipeline for validating scraped data."""

    def process_item(self, item, spider):
        """Validate item data."""
        # Check required fields
        required_fields = ['symbol', 'timestamp', 'source']
        for field in required_fields:
            if not item.get(field):
                raise DropItem(f"Missing required field: {field}")

        # Validate numeric fields
        numeric_fields = ['open', 'high', 'low', 'close', 'volume']
        for field in numeric_fields:
            value = item.get(field)
            if value is not None:
                try:
                    float(value)
                except (ValueError, TypeError):
                    raise DropItem(f"Invalid numeric value for {field}: {value}")

        return item
