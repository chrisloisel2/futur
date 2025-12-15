"""
Spider for scraping Google Trends, macro-economic data, and on-chain metrics.
"""
import json
import logging
from datetime import datetime, timedelta
import scrapy
from scrapy.http import Request

from ..items_alternative import TrendDataItem, MacroEconomicDataItem, OnChainDataItem

logger = logging.getLogger(__name__)


class TrendsMacroSpider(scrapy.Spider):
    """
    Spider for trends, macro data, and on-chain metrics.

    Sources:
    - Google Trends (search interest)
    - Alternative.me (Fear & Greed)
    - Glassnode/Santiment (on-chain)
    - CoinGecko (market data)
    - FRED API (macro economics)
    """

    name = 'trends_macro'
    allowed_domains = [
        'trends.google.com',
        'api.alternative.me',
        'api.coingecko.com',
        'api.glassnode.com',
        'api.santiment.net',
        'api.stlouisfed.org',
    ]

    def __init__(
        self,
        symbols=None,
        glassnode_api_key=None,
        fred_api_key=None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.symbols = self._parse_symbols(symbols) if symbols else ['BTC', 'ETH']
        self.glassnode_api_key = glassnode_api_key
        self.fred_api_key = fred_api_key

    def _parse_symbols(self, symbols_arg):
        """Parse symbols."""
        if isinstance(symbols_arg, str):
            return [s.strip().replace('USDT', '').upper() for s in symbols_arg.split(',')]
        return symbols_arg

    def start_requests(self):
        """Generate initial requests."""
        for symbol in self.symbols:
            # 1. CoinGecko - Market trends
            yield from self._coingecko_requests(symbol)

            # 2. Glassnode - On-chain metrics
            if self.glassnode_api_key:
                yield from self._glassnode_requests(symbol)

        # 3. FRED - Macro economic data (global, not per symbol)
        if self.fred_api_key:
            yield from self._fred_requests()

        # 4. Fear & Greed Index
        yield from self._feargreed_requests()

    def _coingecko_requests(self, symbol: str):
        """Generate CoinGecko requests."""
        # Map symbols to CoinGecko IDs
        coin_map = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'BNB': 'binancecoin',
            'ADA': 'cardano',
            'SOL': 'solana',
            'XRP': 'ripple',
            'DOGE': 'dogecoin',
        }

        coin_id = coin_map.get(symbol, symbol.lower())

        # Get trending status and market data
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        yield Request(
            url=url,
            callback=self.parse_coingecko,
            meta={'symbol': symbol},
            errback=self.handle_error,
        )

        # Get trending coins (global)
        if symbol == 'BTC':
            trending_url = "https://api.coingecko.com/api/v3/search/trending"
            yield Request(
                url=trending_url,
                callback=self.parse_trending,
                errback=self.handle_error,
            )

    def _glassnode_requests(self, symbol: str):
        """Generate Glassnode on-chain requests."""
        # Glassnode uses different asset codes
        asset_map = {'BTC': 'BTC', 'ETH': 'ETH'}
        asset = asset_map.get(symbol)

        if not asset:
            return

        # Various on-chain metrics
        metrics = [
            'addresses/active_count',
            'addresses/new_non_zero_count',
            'transactions/count',
            'transactions/transfers_volume_sum',
            'distribution/balance_exchanges',
            'market/mvrv',
        ]

        for metric in metrics:
            url = (
                f"https://api.glassnode.com/v1/metrics/{metric}"
                f"?a={asset}&api_key={self.glassnode_api_key}"
            )

            yield Request(
                url=url,
                callback=self.parse_glassnode,
                meta={'symbol': symbol, 'metric': metric},
                errback=self.handle_error,
            )

    def _fred_requests(self):
        """Generate FRED economic data requests."""
        # Key economic indicators
        series_ids = {
            'DFF': 'fed_rate',  # Federal Funds Rate
            'CPIAUCSL': 'inflation_rate',  # CPI
            'UNRATE': 'unemployment_rate',
            'GDP': 'gdp_growth',
            'M2SL': 'm2_money_supply',
            'DEXUSEU': 'dollar_index',
        }

        for series_id, field_name in series_ids.items():
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}"
                f"&api_key={self.fred_api_key}"
                f"&file_type=json"
                f"&limit=1"
                f"&sort_order=desc"
            )

            yield Request(
                url=url,
                callback=self.parse_fred,
                meta={'field_name': field_name},
                errback=self.handle_error,
            )

    def _feargreed_requests(self):
        """Generate Fear & Greed requests."""
        url = "https://api.alternative.me/fng/?limit=30"
        yield Request(
            url=url,
            callback=self.parse_feargreed_history,
            errback=self.handle_error,
        )

    def parse_coingecko(self, response):
        """Parse CoinGecko market data."""
        try:
            data = json.loads(response.text)
            symbol = response.meta['symbol']

            # Trend data
            trend_item = TrendDataItem()
            trend_item['symbol'] = symbol + 'USDT'
            trend_item['timestamp'] = datetime.now()
            trend_item['source'] = 'coingecko'

            # Market data as proxy for trends
            market_data = data.get('market_data', {})

            trend_item['search_volume'] = data.get('coingecko_rank', 0)
            trend_item['search_volume_change'] = market_data.get('price_change_percentage_24h', 0)

            trend_item['scraped_at'] = datetime.now()

            yield trend_item

            # Macro data
            macro_item = MacroEconomicDataItem()
            macro_item['timestamp'] = datetime.now()
            macro_item['source'] = 'coingecko'

            # Market cap and dominance
            macro_item['total_market_cap'] = market_data.get('market_cap', {}).get('usd', 0)

            # BTC dominance (if BTC)
            if symbol == 'BTC':
                macro_item['btc_dominance'] = market_data.get('market_cap_percentage', {}).get('btc', 0)

            macro_item['scraped_at'] = datetime.now()

            yield macro_item

        except Exception as e:
            logger.error(f"Error parsing CoinGecko data: {e}")

    def parse_trending(self, response):
        """Parse trending coins."""
        try:
            data = json.loads(response.text)

            if data.get('coins'):
                for coin_data in data['coins']:
                    coin = coin_data.get('item', {})

                    trend_item = TrendDataItem()
                    trend_item['symbol'] = coin.get('symbol', '').upper() + 'USDT'
                    trend_item['timestamp'] = datetime.now()
                    trend_item['source'] = 'coingecko_trending'

                    trend_item['search_volume'] = coin.get('market_cap_rank', 0)
                    trend_item['scraped_at'] = datetime.now()

                    yield trend_item

        except Exception as e:
            logger.error(f"Error parsing trending data: {e}")

    def parse_glassnode(self, response):
        """Parse Glassnode on-chain data."""
        try:
            data = json.loads(response.text)
            symbol = response.meta['symbol']
            metric = response.meta['metric']

            if data and len(data) > 0:
                latest = data[-1]

                item = OnChainDataItem()
                item['symbol'] = symbol + 'USDT'
                item['timestamp'] = datetime.fromtimestamp(latest['t'])
                item['source'] = 'glassnode'

                # Map metric to field
                value = latest['v']
                if 'active_count' in metric:
                    item['active_addresses'] = value
                elif 'new_non_zero' in metric:
                    item['new_addresses'] = value
                elif 'transactions/count' in metric:
                    item['transaction_count'] = value
                elif 'volume_sum' in metric:
                    item['transaction_volume'] = value
                elif 'balance_exchanges' in metric:
                    item['supply_on_exchanges'] = value
                elif 'mvrv' in metric:
                    item['mvrv_ratio'] = value

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing Glassnode data: {e}")

    def parse_fred(self, response):
        """Parse FRED economic data."""
        try:
            data = json.loads(response.text)
            field_name = response.meta['field_name']

            if data.get('observations'):
                latest = data['observations'][-1]

                item = MacroEconomicDataItem()
                item['timestamp'] = datetime.strptime(latest['date'], '%Y-%m-%d')
                item['source'] = 'fred'

                # Set the appropriate field
                value = float(latest['value'])
                setattr(item, field_name, value)

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing FRED data: {e}")

    def parse_feargreed_history(self, response):
        """Parse Fear & Greed historical data."""
        try:
            data = json.loads(response.text)

            if data.get('data'):
                for entry in data['data']:
                    item = MacroEconomicDataItem()
                    item['timestamp'] = datetime.fromtimestamp(int(entry['timestamp']))
                    item['source'] = 'feargreed'

                    # Add fear & greed to macro data
                    # (Could also create separate item type)
                    fg_value = int(entry['value'])
                    item['vix_index'] = fg_value  # Using VIX field as proxy

                    item['scraped_at'] = datetime.now()

                    yield item

        except Exception as e:
            logger.error(f"Error parsing Fear & Greed history: {e}")

    def handle_error(self, failure):
        """Handle request failures."""
        logger.error(f"Request failed: {failure.request.url}")
        logger.error(f"Error: {failure.value}")
