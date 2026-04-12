"""
Pipeline for alternative data (sentiment, geopolitical, trends, macro).
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


class AlternativeDataPipeline:
    """
    Pipeline for saving alternative data to S3.

    Handles:
    - Sentiment data
    - Geopolitical events
    - Trend data
    - Macro-economic data
    - On-chain metrics
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = 'bourse/alternative_data',
        batch_size: int = 100,
        region_name: str = 'us-east-1'
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip('/')
        self.batch_size = batch_size
        self.region_name = region_name

        # Buffers by data type
        self.buffers = {
            'sentiment': [],
            'geopolitical': [],
            'trends': [],
            'macro': [],
            'onchain': [],
        }

        self.s3_client = None

        self.stats = {
            'items_processed': 0,
            'items_saved': 0,
            'batches_uploaded': 0,
            'errors': 0,
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize from crawler settings."""
        return cls(
            bucket=crawler.settings.get('S3_BUCKET', 'qbia'),
            prefix=crawler.settings.get('S3_ALTERNATIVE_PREFIX', 'bourse/alternative_data'),
            batch_size=crawler.settings.getint('S3_ALT_BATCH_SIZE', 100),
            region_name=crawler.settings.get('AWS_REGION', 'us-east-1'),
        )

    def open_spider(self, spider):
        """Called when spider opens."""
        self.s3_client = boto3.client('s3', region_name=self.region_name)
        logger.info(f"AlternativeDataPipeline opened: s3://{self.bucket}/{self.prefix}")

    def close_spider(self, spider):
        """Called when spider closes - flush all buffers."""
        logger.info("Flushing alternative data buffers...")

        for data_type, items in self.buffers.items():
            if items:
                self._save_batch(data_type, items)

        logger.info(f"Alternative data pipeline stats: {self.stats}")

    def process_item(self, item, spider):
        """Process each item."""
        try:
            # Determine item type
            item_class = item.__class__.__name__

            data_type = self._get_data_type(item_class)
            if not data_type:
                return item

            # Convert to dict and add to buffer
            item_dict = dict(item)
            self.buffers[data_type].append(item_dict)

            self.stats['items_processed'] += 1

            # Check if buffer is ready to save
            if len(self.buffers[data_type]) >= self.batch_size:
                self._save_batch(data_type, self.buffers[data_type])
                self.buffers[data_type] = []

            return item

        except Exception as e:
            logger.error(f"Error processing alternative data item: {e}")
            self.stats['errors'] += 1
            raise DropItem(f"Error: {e}")

    def _get_data_type(self, item_class: str) -> str:
        """Map item class to data type."""
        mapping = {
            'SentimentDataItem': 'sentiment',
            'GeopoliticalEventItem': 'geopolitical',
            'TrendDataItem': 'trends',
            'MacroEconomicDataItem': 'macro',
            'OnChainDataItem': 'onchain',
            'InfluencerDataItem': 'sentiment',  # Group with sentiment
            'DeFiMetricsItem': 'onchain',  # Group with on-chain
            'WhaleAlertItem': 'onchain',
        }
        return mapping.get(item_class)

    def _save_batch(self, data_type: str, items: List[dict]):
        """Save a batch to S3."""
        try:
            if not items:
                return

            logger.info(f"Saving {data_type} batch with {len(items)} items")

            # Convert to DataFrame
            df = pd.DataFrame(items)

            # Ensure timestamp
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Sort by timestamp
            df = df.sort_values('timestamp')

            # Remove duplicates
            df = df.drop_duplicates(keep='last')

            # Determine S3 key based on data type and date
            now = datetime.now()
            year = now.year
            month = now.month

            # Different paths for different data types
            if data_type == 'sentiment':
                s3_key = f"{self.prefix}/sentiment/{year}/{year}_{month:02d}_sentiment.parquet"
            elif data_type == 'geopolitical':
                s3_key = f"{self.prefix}/geopolitical/{year}/{year}_{month:02d}_events.parquet"
            elif data_type == 'trends':
                s3_key = f"{self.prefix}/trends/{year}/{year}_{month:02d}_trends.parquet"
            elif data_type == 'macro':
                s3_key = f"{self.prefix}/macro/{year}/{year}_{month:02d}_macro.parquet"
            elif data_type == 'onchain':
                s3_key = f"{self.prefix}/onchain/{year}/{year}_{month:02d}_onchain.parquet"
            else:
                s3_key = f"{self.prefix}/other/{year}/{year}_{month:02d}_{data_type}.parquet"

            # Try to merge with existing data
            try:
                temp_file = Path(tempfile.mktemp(suffix='.parquet'))
                self.s3_client.download_file(self.bucket, s3_key, str(temp_file))

                existing_df = pd.read_parquet(temp_file)
                df = pd.concat([existing_df, df], ignore_index=True)
                df = df.drop_duplicates(keep='last')
                df = df.sort_values('timestamp')

                logger.info(f"Merged with existing data: {len(existing_df)} + {len(items)} = {len(df)} rows")

                if temp_file.exists():
                    temp_file.unlink()

            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    logger.warning(f"Error checking existing file: {e}")

            # Save to temp file
            temp_output = Path(tempfile.mktemp(suffix='.parquet'))
            df.to_parquet(temp_output, index=False, compression='snappy')

            # Upload to S3
            self.s3_client.upload_file(str(temp_output), self.bucket, s3_key)

            logger.info(f"Uploaded to s3://{self.bucket}/{s3_key}")

            # Cleanup
            if temp_output.exists():
                temp_output.unlink()

            self.stats['items_saved'] += len(items)
            self.stats['batches_uploaded'] += 1

        except Exception as e:
            logger.error(f"Error saving {data_type} batch: {e}")
            self.stats['errors'] += 1


class SentimentAggregationPipeline:
    """
    Pipeline for aggregating sentiment data and calculating composite scores.
    """

    def __init__(self):
        self.sentiment_buffer = {}  # {symbol: [items]}
        self.window_size = 100

    def process_item(self, item, spider):
        """Aggregate sentiment data."""
        try:
            # Only process sentiment items
            if item.__class__.__name__ != 'SentimentDataItem':
                return item

            symbol = item.get('symbol')
            if not symbol:
                return item

            # Initialize buffer
            if symbol not in self.sentiment_buffer:
                self.sentiment_buffer[symbol] = []

            # Add to buffer
            self.sentiment_buffer[symbol].append(dict(item))

            # Keep only recent items
            if len(self.sentiment_buffer[symbol]) > self.window_size:
                self.sentiment_buffer[symbol] = self.sentiment_buffer[symbol][-self.window_size:]

            # Calculate composite sentiment
            if len(self.sentiment_buffer[symbol]) >= 10:
                self._calculate_composite_sentiment(item, self.sentiment_buffer[symbol])

            return item

        except Exception as e:
            logger.error(f"Error in sentiment aggregation: {e}")
            return item

    def _calculate_composite_sentiment(self, item, historical_data: List[dict]):
        """Calculate composite sentiment score."""
        try:
            df = pd.DataFrame(historical_data)

            # Composite sentiment (weighted average of sources)
            if 'sentiment_score' in df.columns:
                recent_sentiment = df['sentiment_score'].dropna().tail(20).mean()
                item['composite_sentiment'] = recent_sentiment

            # Social volume trend
            if 'tweet_volume' in df.columns:
                volumes = df['tweet_volume'].dropna()
                if len(volumes) >= 2:
                    volume_change = (volumes.iloc[-1] - volumes.iloc[-10]) / volumes.iloc[-10] if volumes.iloc[-10] > 0 else 0
                    item['social_volume_trend'] = volume_change

        except Exception as e:
            logger.error(f"Error calculating composite sentiment: {e}")
