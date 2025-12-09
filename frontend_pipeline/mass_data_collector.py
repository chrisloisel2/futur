"""
Massive financial data collection script with web scraping and proxy rotation
Includes market, on-chain, sentiment, macro, and derivatives data
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
import requests
from fake_useragent import UserAgent


@dataclass
class APIConfig:
    """API tokens configuration for all data sources."""
    # Market data
    BINANCE_API_KEY: Optional[str] = "AQ8vw1nZw3kQ7dJLw55RAirkEsFti7outofj7rhd9cAtUOhqD3btYq4DlF1zbA6U"
    BINANCE_SECRET_KEY: Optional[str] = "tk9FiWlLLeSlTmzqclAyxpceyYypQ6fIUtw8cnwLj3qlMRe39SeFcN9aRIhE62t7"
    COINBASE_API_KEY: Optional[str] = "NHlsOf3gZN9/sCXehCc+46lJcQvnbKB8QQKao+YVz+cqnx3xpCmgVt2cuJvPIsp0QMGdhI/HKSPGAMvL4BPokA=="
    KRAKEN_API_KEY: Optional[str] = None

    # On-chain premium
    GLASSNODE_API_KEY: Optional[str] = None
    COINMETRICS_API_KEY: Optional[str] = None
    MESSARI_API_KEY: Optional[str] = None
    DUNE_API_KEY: Optional[str] = None

    # Alternative and sentiment
    TWITTER_BEARER_TOKEN: Optional[str] = None
    REDDIT_CLIENT_ID: Optional[str] = None
    REDDIT_CLIENT_SECRET: Optional[str] = None
    CRYPTOPANIC_API_KEY: Optional[str] = None
    COINGLASS_API_KEY: Optional[str] = None

    # Macroeconomic data
    ALPHA_VANTAGE_API_KEY: Optional[str] = "KN6W8M6IUSA4R727"
    FRED_API_KEY: Optional[str] = "b9e4f0a34004b7516928eee82f8d6978"
    QUANDL_API_KEY: Optional[str] = None


class ProxyManager:

    def __init__(self):
        self.proxy_sources = [
            "https://www.sslproxies.org/",
            "https://free-proxy-list.net/",
            "https://us-proxy.org/",
            "https://www.proxy-list.download/HTTP",
            "https://spys.one/en/free-proxy-list/",
        ]
        self.proxies = []
        self.last_refresh = None
        self.ua = UserAgent()

    async def refresh_proxies(self) -> List[str]:
        """Rafraîchir la liste des proxies depuis les sources publiques."""
        self.logger.info("Refreshing proxy list...")
        new_proxies = []

        for source in self.proxy_sources:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(source, timeout=10) as response:
                        content = await response.text()
                        proxies = self.extract_proxies_from_html(content)
                        new_proxies.extend(proxies)
            except Exception as e:
                self.logger.warning(f"Failed to fetch proxies from {source}: {e}")

        # Nettoyer et dédupliquer
        self.proxies = list(set([p for p in new_proxies if self.validate_proxy_format(p)]))
        self.last_refresh = datetime.utcnow()
        self.logger.info(f"Loaded {len(self.proxies)} fresh proxies")
        return self.proxies

    def extract_proxies_from_html(self, html: str) -> List[str]:
        """Extraire les proxies du HTML."""
        proxies = []

        # Pattern pour IP:Port
        ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
        port_pattern = r'<td>(\d{2,5})</td>'

        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text()

        # Trouver les IPs
        ips = re.findall(ip_pattern, text)
        ports = re.findall(port_pattern, html)

        # Combiner IPs et ports
        for i, ip in enumerate(ips[:len(ports)]):
            if i < len(ports):
                proxies.append(f"http://{ip}:{ports[i]}")

        return proxies[:100]  # Limiter pour éviter trop de proxies

    def validate_proxy_format(self, proxy: str) -> bool:
        """Valider le format du proxy."""
        pattern = r'http://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}'
        return bool(re.match(pattern, proxy))

    def get_random_proxy(self) -> Optional[str]:
        """Obtenir un proxy aléatoire."""
        if not self.proxies or (self.last_refresh and
                               (datetime.utcnow() - self.last_refresh) > timedelta(minutes=30)):
            asyncio.create_task(self.refresh_proxies())

        return random.choice(self.proxies) if self.proxies else None

    def get_random_headers(self) -> Dict[str, str]:
        """Générer des headers aléatoires pour éviter la détection."""
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }


class WebScraper:
    """Système de scraping avancé avec gestion de proxies."""

    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.logger = logging.getLogger(__name__)
        self.session = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Obtenir une session HTTP réutilisable."""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self.session

    async def scrape_with_retry(self, url: str, max_retries: int = 3) -> Optional[str]:
        """Scraper une URL avec retry et rotation de proxies."""
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_random_proxy()
            headers = self.proxy_manager.get_random_headers()

            try:
                session = await self.get_session()
                async with session.get(url, proxy=proxy, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        self.logger.warning(f"HTTP {response.status} for {url}")
            except Exception as e:
                self.logger.debug(f"Attempt {attempt + 1} failed for {url}: {e}")
                await asyncio.sleep(2 ** attempt)  # Backoff exponentiel

        self.logger.error(f"All scraping attempts failed for {url}")
        return None


class MassDataCollector:
    """
    Collector orchestrating multiple data sources including web scraping.
    """

    def __init__(self, config: APIConfig) -> None:
        self.config = config
        self.base_path = Path("datasets/trading")
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.proxy_manager = ProxyManager()
        self.scraper = WebScraper(self.proxy_manager)
        self.setup_logging()

    def setup_logging(self) -> None:
        """Set up file and console logging."""
        log_file = self.base_path / "collection.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(),
            ],
        )
        self.logger = logging.getLogger(__name__)

    async def collect_all_data(self, years: int = 5, symbols: Optional[List[str]] = None) -> Dict[str, Dict]:
        """
        Run a full multi-source data collection including web scraping.
        """
        self.logger.info("Starting massive data collection with web scraping")

        if symbols is None:
            symbols = self.get_top_cryptos(50)  # Réduit pour le scraping

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=years * 365)

        # Charger les proxies dès le début
        await self.proxy_manager.refresh_proxies()

        tasks = [
            self.collect_price_data(symbols, start_date, end_date),
            self.collect_onchain_data(start_date, end_date),
            self.collect_sentiment_data(symbols, start_date, end_date),
            self.collect_macro_data(start_date, end_date),
            self.collect_derivatives_data(symbols, start_date, end_date),
            self.collect_web_data(symbols, start_date, end_date),  # Nouveau: données web
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                self.logger.error("Collection subtask failed: %s", result)

        return self.compile_dataset([r for r in results if isinstance(r, dict)])

    # === NOUVELLES MÉTHODES DE SCRAPING ===

    async def collect_web_data(self, symbols: List[str], start_date: datetime, end_date: datetime) -> Dict[str, Dict]:
        """Collecte massive de données via web scraping."""
        self.logger.info("Starting web data collection")

        web_data = {
            "market_cap_rankings": await self.scrape_market_cap_rankings(),
            "exchange_volumes": await self.scrape_exchange_volumes(),
            "github_activity": await self.scrape_github_activity(symbols),
            "developer_activity": await self.scrape_developer_metrics(symbols),
            "whale_watching": await self.scrape_whale_transactions(),
            "mining_metrics": await self.scrape_mining_data(),
            "fear_greed_index": await self.scrape_fear_greed_index(),
            "social_mentions": await self.scrape_social_mentions(symbols),
        }

        return {"web_data": web_data}

    async def scrape_market_cap_rankings(self) -> pd.DataFrame:
        """Scraper les classements par market cap depuis CoinMarketCap/CoinGecko."""
        self.logger.info("Scraping market cap rankings")

        urls = [
            "https://coinmarketcap.com/",
            "https://www.coingecko.com/",
        ]

        all_data = []
        for url in urls:
            try:
                html = await self.scraper.scrape_with_retry(url)
                if html:
                    soup = BeautifulSoup(html, 'html.parser')

                    # Tentative d'extraction des données de classement
                    if "coinmarketcap" in url:
                        data = self.parse_coinmarketcap_ranking(soup)
                    else:
                        data = self.parse_coingecko_ranking(soup)

                    all_data.extend(data)

            except Exception as e:
                self.logger.warning(f"Failed to scrape {url}: {e}")

        return pd.DataFrame(all_data)

    def parse_coinmarketcap_ranking(self, soup: BeautifulSoup) -> List[Dict]:
        """Parser le HTML de CoinMarketCap."""
        data = []
        try:
            # Sélecteur pour les lignes du tableau (à adapter selon la structure actuelle)
            rows = soup.select('table tr')[1:11]  # Top 10

            for row in rows:
                cells = row.select('td')
                if len(cells) > 3:
                    data.append({
                        'rank': cells[1].get_text(strip=True),
                        'name': cells[2].get_text(strip=True),
                        'market_cap': cells[3].get_text(strip=True),
                        'timestamp': datetime.utcnow()
                    })
        except Exception as e:
            self.logger.warning(f"Error parsing CoinMarketCap: {e}")

        return data

    async def scrape_exchange_volumes(self) -> pd.DataFrame:
        """Scraper les volumes des exchanges."""
        self.logger.info("Scraping exchange volumes")

        url = "https://coinmarketcap.com/rankings/exchanges/"
        html = await self.scraper.scrape_with_retry(url)

        volumes = []
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            # Implémenter l'extraction des volumes d'exchanges
            # Cette structure dépend du site cible

        return pd.DataFrame(volumes)

    async def scrape_github_activity(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper l'activité GitHub des projets crypto."""
        self.logger.info("Scraping GitHub activity")

        # Mapping symbol -> repo GitHub
        github_repos = {
            "BTC/USDT": "bitcoin/bitcoin",
            "ETH/USDT": "ethereum/go-ethereum",
            "SOL/USDT": "solana-labs/solana",
            "DOT/USDT": "paritytech/polkadot",
        }

        activity_data = []
        for symbol, repo in github_repos.items():
            if symbol in symbols:
                try:
                    # API GitHub (gratuite avec limites)
                    url = f"https://api.github.com/repos/{repo}"
                    headers = {'User-Agent': 'Mozilla/5.0'}

                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers) as response:
                            if response.status == 200:
                                repo_data = await response.json()
                                activity_data.append({
                                    'symbol': symbol,
                                    'stars': repo_data.get('stargazers_count', 0),
                                    'forks': repo_data.get('forks_count', 0),
                                    'watchers': repo_data.get('watchers_count', 0),
                                    'last_update': repo_data.get('updated_at', ''),
                                    'timestamp': datetime.utcnow()
                                })

                            await asyncio.sleep(1)  # Respect rate limits

                except Exception as e:
                    self.logger.warning(f"Failed to scrape GitHub for {symbol}: {e}")

        return pd.DataFrame(activity_data)

    async def scrape_developer_metrics(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper les métriques de développement."""
        self.logger.info("Scraping developer metrics")

        # Utiliser des APIs publiques ou scraping de sites comme Santiment
        dev_data = []

        for symbol in symbols[:10]:  # Limiter pour éviter le rate limiting
            try:
                # Exemple: Santiment a des données développeurs gratuites
                # Cette partie nécessiterait une adaptation aux sources disponibles
                dev_metrics = {
                    'symbol': symbol,
                    'commit_activity': random.randint(0, 100),  # Placeholder
                    'developer_count': random.randint(5, 50),   # Placeholder
                    'timestamp': datetime.utcnow()
                }
                dev_data.append(dev_metrics)

            except Exception as e:
                self.logger.warning(f"Failed to get dev metrics for {symbol}: {e}")

        return pd.DataFrame(dev_data)

    async def scrape_whale_transactions(self) -> pd.DataFrame:
        """Scraper les transactions des whales."""
        self.logger.info("Scraping whale transactions")

        # Sources possibles: Whale Alert, blockchain explorers
        whale_data = []

        try:
            # Exemple avec Whale Alert (s'adapter à la disponibilité)
            url = "https://api.whale-alert.io/v1/transactions"
            # Note: nécessite généralement une clé API

            # Pour l'instant, placeholder avec données simulées
            whale_data = [{
                'symbol': 'BTC',
                'amount': random.uniform(100, 10000),
                'from_exchange': 'Binance',
                'to_exchange': 'Cold Wallet',
                'timestamp': datetime.utcnow()
            } for _ in range(10)]

        except Exception as e:
            self.logger.warning(f"Failed to scrape whale data: {e}")

        return pd.DataFrame(whale_data)

    async def scrape_mining_data(self) -> pd.DataFrame:
        """Scraper les données de minage."""
        self.logger.info("Scraping mining data")

        mining_data = []

        try:
            # Données Bitcoin mining
            btc_url = "https://blockchain.info/q/hashrate"
            async with aiohttp.ClientSession() as session:
                async with session.get(btc_url) as response:
                    if response.status == 200:
                        hashrate = await response.text()
                        mining_data.append({
                            'metric': 'bitcoin_hashrate',
                            'value': float(hashrate),
                            'timestamp': datetime.utcnow()
                        })

            # Difficulté de minage
            diff_url = "https://blockchain.info/q/getdifficulty"
            async with aiohttp.ClientSession() as session:
                async with session.get(diff_url) as response:
                    if response.status == 200:
                        difficulty = await response.text()
                        mining_data.append({
                            'metric': 'bitcoin_difficulty',
                            'value': float(difficulty),
                            'timestamp': datetime.utcnow()
                        })

        except Exception as e:
            self.logger.warning(f"Failed to scrape mining data: {e}")

        return pd.DataFrame(mining_data)

    async def scrape_fear_greed_index(self) -> pd.DataFrame:
        """Scraper le Fear & Greed Index."""
        self.logger.info("Scraping Fear & Greed Index")

        try:
            # Alternative Crypto Fear & Greed Index
            url = "https://api.alternative.me/fng/"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'data' in data and len(data['data']) > 0:
                            latest = data['data'][0]
                            return pd.DataFrame([{
                                'value': int(latest['value']),
                                'classification': latest['value_classification'],
                                'timestamp': datetime.utcnow()
                            }])

        except Exception as e:
            self.logger.warning(f"Failed to scrape Fear & Greed: {e}")

        return pd.DataFrame()

    async def scrape_social_mentions(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper les mentions sociales (approximation)."""
        self.logger.info("Scraping social mentions")

        social_data = []

        # Placeholder - en réalité, il faudrait utiliser des APIs sociales
        # ou des services comme LunarCrush, Santiment, etc.
        for symbol in symbols[:15]:
            social_data.append({
                'symbol': symbol,
                'twitter_mentions': random.randint(100, 10000),
                'reddit_posts': random.randint(10, 1000),
                'sentiment_score': random.uniform(-1, 1),
                'timestamp': datetime.utcnow()
            })

        return pd.DataFrame(social_data)

    # === MÉTHODES EXISTANTES (AUGMENTÉES) ===

    async def collect_price_data(
        self, symbols: List[str], start_date: datetime, end_date: datetime
    ) -> Dict[str, Dict[str, pd.DataFrame]]:
        """Collect high-frequency price data from multiple sources."""
        self.logger.info("Collecting price data for %d symbols", len(symbols))

        price_data = await super().collect_price_data(symbols, start_date, end_date)

        # Ajouter le scraping de prix en backup
        try:
            scraped_prices = await self.scrape_backup_prices(symbols[:10])  # Top 10 seulement
            price_data["scraped_prices"] = scraped_prices
        except Exception as e:
            self.logger.warning(f"Price scraping failed: {e}")

        return price_data

    async def scrape_backup_prices(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper les prix en backup si les APIs échouent."""
        price_data = []

        for symbol in symbols:
            try:
                # Exemple: scraping de Yahoo Finance pour les paires USD
                base = symbol.split('/')[0]
                if base in ['BTC', 'ETH', 'SOL']:
                    url = f"https://finance.yahoo.com/quote/{base}-USD"
                    html = await self.scraper.scrape_with_retry(url)

                    if html:
                        soup = BeautifulSoup(html, 'html.parser')
                        # Extraire le prix (sélecteur à adapter)
                        price_element = soup.find('fin-streamer', {'data-field': 'regularMarketPrice'})
                        if price_element:
                            price = float(price_element.get_text().replace(',', ''))
                            price_data.append({
                                'symbol': symbol,
                                'price': price,
                                'timestamp': datetime.utcnow()
                            })

            except Exception as e:
                self.logger.debug(f"Price scraping failed for {symbol}: {e}")

        return pd.DataFrame(price_data)

    async def collect_onchain_data(self, start_date: datetime, end_date: datetime) -> Dict[str, Dict]:
        """Collect on-chain data with web scraping fallback."""
        self.logger.info("Collecting on-chain data")

        onchain_data = await super().collect_onchain_data(start_date, end_date)

        # Données on-chain via scraping
        scraped_onchain = {
            "bitcoin_stats": await self.scrape_bitcoin_stats(),
            "ethereum_stats": await self.scrape_ethereum_stats(),
            "mempool_data": await self.scrape_mempool_data(),
        }

        onchain_data["scraped_onchain"] = scraped_onchain
        return onchain_data

    async def scrape_bitcoin_stats(self) -> pd.DataFrame:
        """Scraper les statistiques Bitcoin."""
        try:
            url = "https://blockchain.info/stats?format=json"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return pd.DataFrame([{
                            **data,
                            'timestamp': datetime.utcnow()
                        }])
        except Exception as e:
            self.logger.warning(f"Failed to scrape Bitcoin stats: {e}")

        return pd.DataFrame()

    async def scrape_ethereum_stats(self) -> pd.DataFrame:
        """Scraper les statistiques Ethereum."""
        try:
            # Utiliser Etherscan ou des APIs publiques
            url = "https://api.etherscan.io/api?module=stats&action=ethprice"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data['status'] == '1':
                            return pd.DataFrame([{
                                'ethusd': float(data['result']['ethusd']),
                                'timestamp': datetime.utcnow()
                            }])
        except Exception as e:
            self.logger.warning(f"Failed to scrape Ethereum stats: {e}")

        return pd.DataFrame()

    async def scrape_mempool_data(self) -> pd.DataFrame:
        """Scraper les données du mempool Bitcoin."""
        try:
            url = "https://mempool.space/api/mempool"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return pd.DataFrame([{
                            'mempool_transactions': data.get('count', 0),
                            'mempool_size': data.get('vsize', 0),
                            'timestamp': datetime.utcnow()
                        }])
        except Exception as e:
            self.logger.warning(f"Failed to scrape mempool data: {e}")

        return pd.DataFrame()

    async def collect_sentiment_data(
        self, symbols: List[str], start_date: datetime, end_date: datetime
    ) -> Dict[str, Dict]:
        """Collect sentiment data with enhanced web scraping."""
        self.logger.info("Collecting sentiment data")

        sentiment_data = await super().collect_sentiment_data(symbols, start_date, end_date)

        # Données de sentiment via scraping
        scraped_sentiment = {
            "reddit_trends": await self.scrape_reddit_trends(symbols),
            "news_sentiment": await self.scrape_news_sentiment(symbols),
        }

        sentiment_data["scraped_sentiment"] = scraped_sentiment
        return sentiment_data

    async def scrape_reddit_trends(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper les tendances Reddit."""
        reddit_data = []

        subreddits = ['CryptoCurrency', 'Bitcoin', 'ethereum']

        for subreddit in subreddits:
            try:
                url = f"https://www.reddit.com/r/{subreddit}/hot/.json?limit=10"
                headers = {'User-Agent': 'Mozilla/5.0'}

                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            posts = data['data']['children']

                            for post in posts:
                                post_data = post['data']
                                reddit_data.append({
                                    'subreddit': subreddit,
                                    'title': post_data.get('title', ''),
                                    'upvotes': post_data.get('ups', 0),
                                    'comments': post_data.get('num_comments', 0),
                                    'created_utc': post_data.get('created_utc', 0),
                                    'timestamp': datetime.utcnow()
                                })

                await asyncio.sleep(2)  # Respect rate limits

            except Exception as e:
                self.logger.warning(f"Failed to scrape Reddit r/{subreddit}: {e}")

        return pd.DataFrame(reddit_data)

    async def scrape_news_sentiment(self, symbols: List[str]) -> pd.DataFrame:
        """Scraper le sentiment des actualités."""
        news_data = []

        # Sources d'actualités crypto
        news_sources = [
            "https://cointelegraph.com/",
            "https://decrypt.co/",
        ]

        for source in news_sources:
            try:
                html = await self.scraper.scrape_with_retry(source)
                if html:
                    soup = BeautifulSoup(html, 'html.parser')

                    # Extraire les titres des articles
                    titles = soup.find_all(['h1', 'h2', 'h3'], limit=10)

                    for title in titles:
                        text = title.get_text(strip=True)
                        if text and any(symbol.split('/')[0] in text for symbol in symbols[:5]):
                            news_data.append({
                                'source': source,
                                'title': text,
                                'timestamp': datetime.utcnow(),
                                'contains_crypto': True
                            })

            except Exception as e:
                self.logger.warning(f"Failed to scrape news from {source}: {e}")

        return pd.DataFrame(news_data)

    # === MÉTHODES UTILITAIRES EXISTANTES ===

    def get_top_cryptos(self, limit: int = 100) -> List[str]:
        """Return a truncated list of top market cap cryptos."""
        top_cryptos = [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
            "ADA/USDT", "AVAX/USDT", "DOT/USDT", "DOGE/USDT", "MATIC/USDT",
            "LTC/USDT", "LINK/USDT", "ATOM/USDT", "ETC/USDT", "XLM/USDT",
        ]
        return top_cryptos[:limit]

    def compile_dataset(self, results: List[Dict]) -> Dict:
        """Merge collected data and persist to disk."""
        self.logger.info("Compiling final dataset")

        compiled_data: Dict = {}
        for result in results:
            compiled_data.update(result)

        self.save_dataset(compiled_data)
        return compiled_data

    def save_dataset(self, data: Dict) -> None:
        """Persist collected data as Parquet files plus metadata."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dataset_path = self.base_path / f"mass_dataset_{timestamp}"
        dataset_path.mkdir(parents=True, exist_ok=True)

        for data_type, data_content in data.items():
            if data_content is None:
                continue

            if isinstance(data_content, pd.DataFrame):
                data_content.to_parquet(dataset_path / f"{data_type}.parquet")
                continue

            if isinstance(data_content, dict):
                for key, sub_data in data_content.items():
                    if sub_data is None:
                        continue
                    sub_path = dataset_path / data_type / f"{key}.parquet"
                    sub_path.parent.mkdir(parents=True, exist_ok=True)
                    df = sub_data if isinstance(sub_data, pd.DataFrame) else pd.DataFrame(sub_data)
                    df.to_parquet(sub_path)
                continue

            df = pd.DataFrame(data_content if isinstance(data_content, list) else [data_content])
            df.to_parquet(dataset_path / f"{data_type}.parquet")

        metadata = {
            "collection_date": timestamp,
            "data_sources": list(data.keys()),
            "total_size": self.calculate_dataset_size(dataset_path),
            "web_scraping_used": True,
            "proxies_used": len(self.proxy_manager.proxies),
        }

        with open(dataset_path / "metadata.json", "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=2)

        self.logger.info("Dataset saved to %s", dataset_path)

    def calculate_dataset_size(self, path: Path) -> str:
        """Compute human readable size for a dataset directory."""
        total_size = 0
        for file_path in path.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size

        for unit in ["B", "KB", "MB", "GB"]:
            if total_size < 1024.0:
                return f"{total_size:.2f} {unit}"
            total_size /= 1024.0
        return f"{total_size:.2f} TB"

    async def close(self):
        """Fermer les sessions ouvertes."""
        if self.scraper.session:
            await self.scraper.session.close()

import os
import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

async def main() -> None:
    """Entry point for the collector script."""
    config = APIConfig(
        BINANCE_API_KEY=os.getenv("BINANCE_API_KEY"),
        BINANCE_SECRET_KEY=os.getenv("BINANCE_SECRET_KEY"),
        COINBASE_API_KEY=os.getenv("COINBASE_API_KEY"),
        KRAKEN_API_KEY=os.getenv("KRAKEN_API_KEY"),
        GLASSNODE_API_KEY=os.getenv("GLASSNODE_API_KEY"),
        COINMETRICS_API_KEY=os.getenv("COINMETRICS_API_KEY"),
        MESSARI_API_KEY=os.getenv("MESSARI_API_KEY"),
        DUNE_API_KEY=os.getenv("DUNE_API_KEY"),
        TWITTER_BEARER_TOKEN=os.getenv("TWITTER_BEARER_TOKEN"),
        REDDIT_CLIENT_ID=os.getenv("REDDIT_CLIENT_ID"),
        REDDIT_CLIENT_SECRET=os.getenv("REDDIT_CLIENT_SECRET"),
        CRYPTOPANIC_API_KEY=os.getenv("CRYPTOPANIC_API_KEY"),
        COINGLASS_API_KEY=os.getenv("COINGLASS_API_KEY"),
        ALPHA_VANTAGE_API_KEY=os.getenv("ALPHA_VANTAGE_API_KEY"),
        FRED_API_KEY=os.getenv("FRED_API_KEY"),
        QUANDL_API_KEY=os.getenv("QUANDL_API_KEY"),
    )

    available_tokens = {k: v for k, v in vars(config).items() if v}
    print(f"API tokens available: {len(available_tokens)}")

    collector = MassDataCollector(config)

    try:
        dataset = await collector.collect_all_data(years=1)  # Commencer avec 1 an pour les tests
        print(f"Collection finished with {len(dataset)} data groups")

        # Afficher un résumé des données collectées
        for data_type, content in dataset.items():
            if isinstance(content, dict):
                print(f"{data_type}: {len(content)} sub-categories")
            else:
                print(f"{data_type}: {len(content) if hasattr(content, '__len__') else 'N/A'} records")

    except Exception as exc:
        logging.error("Collection failed: %s", exc)
    finally:
        await collector.close()


if __name__ == "__main__":
    # Installation des dépendances nécessaires:
    # pip install aiohttp beautifulsoup4 pandas fake-useragent requests
    asyncio.run(main())
