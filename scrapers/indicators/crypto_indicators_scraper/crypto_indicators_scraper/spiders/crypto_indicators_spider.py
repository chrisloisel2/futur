"""
Main spider for scraping crypto market indicators from multiple sources.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
import scrapy
from scrapy.http import Request
import boto3
from botocore.exceptions import ClientError

from ..items import CryptoIndicatorItem

logger = logging.getLogger(__name__)


class CryptoIndicatorsSpider(scrapy.Spider):
    """
    Spider for scraping crypto market indicators minute-by-minute.

    Features:
    - Fetches technical indicators from multiple APIs
    - Supports all crypto symbols from S3 database
    - Historical and real-time data collection
    - Automatic chunking by year and symbol
    """

    name = 'crypto_indicators'
    allowed_domains = [
        'api.binance.com',
        'min-api.cryptocompare.com',
        'api.coingecko.com',
        'api.taapi.io',
        'api.twelvedata.com',
    ]

    custom_settings = {
        'CONCURRENT_REQUESTS': 16,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 4,
        'DOWNLOAD_DELAY': 0.5,
        'RANDOMIZE_DOWNLOAD_DELAY': True,
    }

    def __init__(
        self,
        bucket='qbia',
        prefix='bourse/mintrad',
        symbols=None,
        start_year=None,
        end_year=None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.bucket = bucket
        self.prefix = prefix
        self.symbols = self._parse_symbols(symbols)
        self.start_year = int(start_year) if start_year else 2017
        self.end_year = int(end_year) if end_year else datetime.now().year

        # Initialize S3 client to get symbols and years
        self.s3_client = boto3.client('s3', region_name='us-east-1')

        # API keys (can be configured via settings or environment)
        self.cryptocompare_api_key = kwargs.get('cryptocompare_api_key', '')
        self.taapi_api_key = kwargs.get('taapi_api_key', '')
        self.twelvedata_api_key = kwargs.get('twelvedata_api_key', '')

        logger.info(f"Initialized spider for symbols: {self.symbols}")
        logger.info(f"Year range: {self.start_year} - {self.end_year}")

    def _parse_symbols(self, symbols_arg) -> List[str]:
        """Parse symbols from command line argument."""
        if not symbols_arg:
            return []

        if isinstance(symbols_arg, str):
            if ',' in symbols_arg:
                return [s.strip().upper() for s in symbols_arg.split(',')]
            return [symbols_arg.strip().upper()]

        return symbols_arg

    def start_requests(self):
        """Generate initial requests for all symbols and time periods."""
        # If no symbols specified, fetch from S3
        if not self.symbols:
            logger.info("No symbols specified, loading from S3...")
            self.symbols = self._load_symbols_from_s3()

        logger.info(f"Starting scrape for {len(self.symbols)} symbols")

        # Generate requests for each symbol and year
        for symbol in self.symbols:
            for year in range(self.start_year, self.end_year + 1):
                # We'll scrape indicators for each day of the year
                # to get minute-by-minute data
                yield from self._generate_requests_for_symbol_year(symbol, year)

    def _load_symbols_from_s3(self) -> List[str]:
        """Load available symbols from S3 bucket."""
        symbols = set()

        for year in range(self.start_year, self.end_year + 1):
            folder_prefix = f"{self.prefix}/klines_1m_TRADING_USDT_{year}/"

            try:
                response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket,
                    Prefix=folder_prefix,
                    MaxKeys=1000
                )

                for obj in response.get('Contents', []):
                    key = obj['Key']
                    filename = key.split('/')[-1]
                    # Extract symbol from filename like "BTCUSDT_2024_1m.parquet"
                    if filename.endswith('_1m.parquet'):
                        symbol = filename.split('_')[0]
                        symbols.add(symbol)

            except ClientError as e:
                logger.error(f"Error loading symbols for year {year}: {e}")

        symbols_list = sorted(list(symbols))
        logger.info(f"Loaded {len(symbols_list)} symbols from S3")
        return symbols_list

    def _generate_requests_for_symbol_year(self, symbol: str, year: int):
        """Generate requests for a symbol/year combination."""
        # For minute-by-minute data, we'll chunk by month to avoid overloading APIs
        start_date = datetime(year, 1, 1)
        end_date = datetime(year, 12, 31, 23, 59, 59)

        # If current year, only go up to today
        if year == datetime.now().year:
            end_date = datetime.now()

        # Generate monthly chunks
        current_date = start_date
        while current_date <= end_date:
            # Get end of month
            if current_date.month == 12:
                month_end = datetime(current_date.year + 1, 1, 1) - timedelta(seconds=1)
            else:
                month_end = datetime(current_date.year, current_date.month + 1, 1) - timedelta(seconds=1)

            if month_end > end_date:
                month_end = end_date

            # Generate request for this month
            yield from self._generate_api_requests(symbol, current_date, month_end)

            # Move to next month
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)

    def _generate_api_requests(self, symbol: str, start_date: datetime, end_date: datetime):
        """Generate API requests to multiple sources for indicators."""

        # Convert timestamps
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

        meta = {
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date,
        }

        # 1. Binance API for OHLCV + basic indicators
        binance_url = (
            f"https://api.binance.com/api/v3/klines?"
            f"symbol={symbol}&interval=1m&startTime={start_ts}&endTime={end_ts}&limit=1000"
        )
        yield Request(
            url=binance_url,
            callback=self.parse_binance,
            meta=meta,
            errback=self.handle_error,
        )

        # 2. CryptoCompare API for additional indicators
        if self.cryptocompare_api_key:
            cc_symbol = symbol.replace('USDT', '')
            cc_url = (
                f"https://min-api.cryptocompare.com/data/v2/histominute?"
                f"fsym={cc_symbol}&tsym=USDT&limit=2000"
                f"&toTs={int(end_date.timestamp())}"
                f"&api_key={self.cryptocompare_api_key}"
            )
            yield Request(
                url=cc_url,
                callback=self.parse_cryptocompare,
                meta=meta,
                errback=self.handle_error,
            )

        # 3. TaaPI (Technical Analysis API) for advanced indicators
        if self.taapi_api_key:
            # We'll request multiple indicators in sequence
            indicators = ['rsi', 'macd', 'bbands', 'stoch', 'adx', 'cci', 'atr']
            for indicator in indicators:
                taapi_url = (
                    f"https://api.taapi.io/{indicator}?"
                    f"secret={self.taapi_api_key}&exchange=binance"
                    f"&symbol={symbol}&interval=1m"
                )
                yield Request(
                    url=taapi_url,
                    callback=self.parse_taapi,
                    meta={**meta, 'indicator': indicator},
                    errback=self.handle_error,
                )

    def parse_binance(self, response):
        """Parse Binance klines data."""
        try:
            data = json.loads(response.text)

            symbol = response.meta['symbol']
            start_date = response.meta['start_date']

            for kline in data:
                item = CryptoIndicatorItem()

                # Basic info
                item['symbol'] = symbol
                item['timestamp'] = datetime.fromtimestamp(kline[0] / 1000)
                item['timeframe'] = '1m'
                item['source'] = 'binance'

                # OHLCV
                item['open'] = float(kline[1])
                item['high'] = float(kline[2])
                item['low'] = float(kline[3])
                item['close'] = float(kline[4])
                item['volume'] = float(kline[5])

                # Calculate basic indicators
                # (More complex indicators will be added from other sources)
                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing Binance data: {e}")

    def parse_cryptocompare(self, response):
        """Parse CryptoCompare data."""
        try:
            data = json.loads(response.text)

            if data.get('Response') != 'Success':
                logger.warning(f"CryptoCompare API error: {data}")
                return

            symbol = response.meta['symbol']
            history_data = data.get('Data', {}).get('Data', [])

            for point in history_data:
                item = CryptoIndicatorItem()

                item['symbol'] = symbol
                item['timestamp'] = datetime.fromtimestamp(point['time'])
                item['timeframe'] = '1m'
                item['source'] = 'cryptocompare'

                item['open'] = float(point.get('open', 0))
                item['high'] = float(point.get('high', 0))
                item['low'] = float(point.get('low', 0))
                item['close'] = float(point.get('close', 0))
                item['volume'] = float(point.get('volumefrom', 0))

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing CryptoCompare data: {e}")

    def parse_taapi(self, response):
        """Parse TaaPI technical indicators."""
        try:
            data = json.loads(response.text)

            if 'error' in data:
                logger.warning(f"TaaPI error: {data['error']}")
                return

            symbol = response.meta['symbol']
            indicator = response.meta['indicator']

            item = CryptoIndicatorItem()
            item['symbol'] = symbol
            item['timestamp'] = datetime.now()
            item['timeframe'] = '1m'
            item['source'] = 'taapi'

            # Map indicator data to item fields
            if indicator == 'rsi':
                item['rsi'] = data.get('value')
            elif indicator == 'macd':
                item['macd'] = data.get('valueMACD')
                item['macd_signal'] = data.get('valueMACDSignal')
                item['macd_histogram'] = data.get('valueMACDHist')
            elif indicator == 'bbands':
                item['bollinger_upper'] = data.get('valueUpperBand')
                item['bollinger_middle'] = data.get('valueMiddleBand')
                item['bollinger_lower'] = data.get('valueLowerBand')
            elif indicator == 'stoch':
                item['stoch_k'] = data.get('valueK')
                item['stoch_d'] = data.get('valueD')
            elif indicator == 'adx':
                item['adx'] = data.get('value')
            elif indicator == 'cci':
                item['cci'] = data.get('value')
            elif indicator == 'atr':
                item['atr'] = data.get('value')

            item['scraped_at'] = datetime.now()

            yield item

        except Exception as e:
            logger.error(f"Error parsing TaaPI data: {e}")

    def handle_error(self, failure):
        """Handle request failures."""
        logger.error(f"Request failed: {failure.request.url}")
        logger.error(f"Error: {failure.value}")
