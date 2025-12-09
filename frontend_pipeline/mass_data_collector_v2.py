"""
MASS DATA COLLECTOR V3 - Production Ready avec Proxies Rotatifs & MongoDB
================================================================================
Collecteur de données massif pour signaux alpha en trading crypto.
Intègre 5 catégories de données : Marché, On-Chain, Sentiment, Macro, Dérivés.

Architecture optimisée avec proxies rotatifs, MongoDB et gestion anti-bannissement.
"""
import asyncio
import json
import logging
import os
import time
import random
import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Awaitable
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
import requests
from fake_useragent import UserAgent
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


# Configuration MongoDB
DEFAULT_URI = os.getenv("MONGO_URI", "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/")
DEFAULT_DB = os.getenv("MONGO_DB", "trader2")
COLLECTION = "historical_ohlcv"

@dataclass
class APIConfig:
    """Configuration centralisée des clés API."""

    BINANCE_API_KEY: Optional[str] = "AQ8vw1nZw3kQ7dJLw55RAirkEsFti7outofj7rhd9cAtUOhqD3btYq4DlF1zbA6U"
    BINANCE_SECRET_KEY: Optional[str] = "tk9FiWlLLeSlTmzqclAyxpceyYypQ6fIUtw8cnwLj3qlMRe39SeFcN9aRIhE62t7"
    COINBASE_API_KEY: Optional[str] = "NHlsOf3gZN9/sCXehCc+46lJcQvnbKB8QQKao+YVz+cqnx3xpCmgVt2cuJvPIsp0QMGdhI/HKSPGAMvL4BPokA=="
    KRAKEN_API_KEY: Optional[str] = "JoziUTQ6tE8X6ZDCvLE2C5N6j0TNmnEuvl6hK+Fr6ysT8hifDUKEXM3DNp0paaZwpwSZOtauk6TFh71ipjjeOQ=="
    # Macroeconomic data
    ALPHA_VANTAGE_API_KEY: Optional[str] = "KN6W8M6IUSA4R727"
    FRED_API_KEY: Optional[str] = "43bc7aaee164345478f76e9defe95313"
    QUANDL_API_KEY: Optional[str] = "x1zQYEnaHFuiG7Lwy1U7"
    # Market Data


    # On-Chain Premium
    GLASSNODE_API_KEY: str = field(default_factory=lambda: os.getenv("GLASSNODE_API_KEY", ""))
    COINMETRICS_API_KEY: str = field(default_factory=lambda: os.getenv("COINMETRICS_API_KEY", ""))
    MESSARI_API_KEY: str = field(default_factory=lambda: os.getenv("MESSARI_API_KEY", ""))
    DUNE_API_KEY: str = field(default_factory=lambda: os.getenv("DUNE_API_KEY", ""))

    # Sentiment & Social
    TWITTER_BEARER_TOKEN: str = field(default_factory=lambda: os.getenv("TWITTER_BEARER_TOKEN", ""))
    REDDIT_CLIENT_ID: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    REDDIT_CLIENT_SECRET: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    CRYPTOPANIC_API_KEY: str = field(default_factory=lambda: os.getenv("CRYPTOPANIC_API_KEY", ""))
    LUNARCRUSH_API_KEY: str = field(default_factory=lambda: os.getenv("LUNARCRUSH_API_KEY", ""))

    # Derivatives
    COINGLASS_API_KEY: str = field(default_factory=lambda: os.getenv("COINGLASS_API_KEY", ""))

    # Macro
    QUANDL_API_KEY: Optional[str] = field(default_factory=lambda: os.getenv("QUANDL_API_KEY", ""))


class FreeProxyScraper:
    """Scraper pour récupérer des proxies gratuits depuis plusieurs sources."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        ]

    def _get_headers(self):
        return {'User-Agent': random.choice(self.user_agents)}

    async def fetch_freeproxylist_net(self) -> List[str]:
        """Récupérer les proxies de FreeProxyList.net."""
        try:
            url = "https://free-proxy-list.net/"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')

            proxies = []
            table = soup.find('table', {'class': 'table table-striped table-bordered'})
            if table:
                for row in table.find('tbody').find_all('tr')[:30]:
                    cols = row.find_all('td')
                    if len(cols) >= 7:
                        ip = cols[0].text.strip()
                        port = cols[1].text.strip()
                        https = cols[6].text.strip()
                        if https == 'yes':
                            proxies.append(f"https://{ip}:{port}")
                        else:
                            proxies.append(f"http://{ip}:{port}")

            self.logger.info(f"FreeProxyList.net: {len(proxies)} proxies found")
            return proxies
        except Exception as e:
            self.logger.error(f"FreeProxyList.net error: {e}")
            return []

    async def fetch_proxynova_com(self) -> List[str]:
        """Récupérer les proxies de ProxyNova.com."""
        try:
            url = "https://www.proxynova.com/proxy-server-list/"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')

            proxies = []
            table = soup.find('table', {'id': 'tbl_proxy_list'})
            if table:
                for row in table.find('tbody').find_all('tr')[:30]:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        ip = cols[0].text.strip()
                        port = cols[1].text.strip()
                        if ip and port:
                            proxies.append(f"http://{ip}:{port}")

            self.logger.info(f"ProxyNova.com: {len(proxies)} proxies found")
            return proxies
        except Exception as e:
            self.logger.error(f"ProxyNova.com error: {e}")
            return []

    async def fetch_geonode_proxies(self) -> List[str]:
        """Récupérer les proxies depuis l'API GeoNode."""
        try:
            url = "https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&sort_type=desc"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            data = response.json()

            proxies = []
            if 'data' in data:
                for proxy in data['data'][:30]:
                    ip = proxy.get('ip')
                    port = proxy.get('port')
                    protocols = proxy.get('protocols', [])
                    if ip and port and protocols:
                        protocol = 'https' if 'https' in protocols else 'http'
                        proxies.append(f"{protocol}://{ip}:{port}")

            self.logger.info(f"GeoNode: {len(proxies)} proxies found")
            return proxies
        except Exception as e:
            self.logger.error(f"GeoNode error: {e}")
            return []

    async def fetch_proxy_list_download(self) -> List[str]:
        """Récupérer les proxies de proxy-list.download."""
        try:
            urls = [
                "https://www.proxy-list.download/api/v1/get?type=http",
                "https://www.proxy-list.download/api/v1/get?type=https",
            ]

            proxies = []
            for url in urls:
                response = requests.get(url, headers=self._get_headers(), timeout=10)
                proxy_list = response.text.strip().split('\r\n')
                for proxy in proxy_list[:15]:
                    if proxy:
                        protocol = 'https' if 'https' in url else 'http'
                        proxies.append(f"{protocol}://{proxy}")

            self.logger.info(f"Proxy-list.download: {len(proxies)} proxies found")
            return proxies
        except Exception as e:
            self.logger.error(f"Proxy-list.download error: {e}")
            return []

    async def fetch_pubproxy_com(self) -> List[str]:
        """Récupérer les proxies de PubProxy.com."""
        try:
            url = "http://pubproxy.com/api/proxy?limit=20&format=json&type=http"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            data = response.json()

            proxies = []
            if 'data' in data:
                for proxy in data['data']:
                    ip = proxy.get('ip')
                    port = proxy.get('port')
                    protocol = proxy.get('type', 'http')
                    if ip and port:
                        proxies.append(f"{protocol}://{ip}:{port}")

            self.logger.info(f"PubProxy.com: {len(proxies)} proxies found")
            return proxies
        except Exception as e:
            self.logger.error(f"PubProxy.com error: {e}")
            return []

    async def fetch_all_proxies(self) -> List[str]:
        """Récupérer tous les proxies depuis toutes les sources."""
        self.logger.info("Starting proxy collection from all sources...")

        all_proxies = []

        # Lancer toutes les tâches en parallèle
        tasks = [
            self.fetch_freeproxylist_net(),
            self.fetch_proxynova_com(),
            self.fetch_geonode_proxies(),
            self.fetch_proxy_list_download(),
            self.fetch_pubproxy_com(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_proxies.extend(result)

        # Dédupliquer
        unique_proxies = list(set(all_proxies))
        self.logger.info(f"Total unique proxies collected: {len(unique_proxies)}")

        return unique_proxies

    async def test_proxy(self, proxy: str, test_url: str = "http://httpbin.org/ip", timeout: int = 5) -> bool:
        """Tester si un proxy fonctionne."""
        try:
            proxies_dict = {"http": proxy, "https": proxy}
            response = requests.get(test_url, proxies=proxies_dict, timeout=timeout)
            return response.status_code == 200
        except:
            return False

    async def get_working_proxies(self, max_workers: int = 20) -> List[str]:
        """Récupérer et tester les proxies pour ne garder que ceux qui fonctionnent."""
        all_proxies = await self.fetch_all_proxies()

        if not all_proxies:
            self.logger.warning("No proxies found")
            return []

        self.logger.info(f"Testing {len(all_proxies)} proxies...")

        working_proxies = []

        # Tester les proxies en parallèle
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            loop = asyncio.get_event_loop()
            futures = [
                loop.run_in_executor(executor, self._test_proxy_sync, proxy)
                for proxy in all_proxies[:100]  # Limiter à 100 pour ne pas surcharger
            ]

            results = await asyncio.gather(*futures, return_exceptions=True)

            for proxy, is_working in zip(all_proxies[:100], results):
                if is_working:
                    working_proxies.append(proxy)

        self.logger.info(f"Working proxies: {len(working_proxies)}/{len(all_proxies[:100])}")
        return working_proxies

    def _test_proxy_sync(self, proxy: str) -> bool:
        """Version synchrone du test de proxy."""
        try:
            proxies_dict = {"http": proxy, "https": proxy}
            response = requests.get("http://httpbin.org/ip", proxies=proxies_dict, timeout=5)
            return response.status_code == 200
        except:
            return False


class ProxyRotator:
    """Gestionnaire de proxies rotatifs pour éviter le bannissement IP."""

    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self.lock = threading.Lock()
        self.failed_proxies = set()
        self.scraper = FreeProxyScraper()
        self.last_refresh = None
        self.refresh_interval = 3600  # Rafraîchir toutes les heures

    async def initialize_proxies(self):
        """Initialiser les proxies en récupérant les proxies gratuits."""
        logging.info("Initializing free proxies...")
        free_proxies = await self.scraper.get_working_proxies()

        with self.lock:
            self.proxies = free_proxies
            self.last_refresh = time.time()

        logging.info(f"Initialized with {len(self.proxies)} working proxies")

    async def refresh_proxies_if_needed(self):
        """Rafraîchir les proxies si nécessaire."""
        if self.last_refresh is None or (time.time() - self.last_refresh) > self.refresh_interval:
            await self.initialize_proxies()

    def get_proxy(self):
        """Retourne le prochain proxy de la liste."""
        with self.lock:
            if not self.proxies:
                return None

            # Essayer de trouver un proxy valide
            attempts = 0
            max_attempts = len(self.proxies)

            while attempts < max_attempts:
                proxy = self.proxies[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.proxies)

                # Éviter les proxies qui ont échoué récemment
                if proxy not in self.failed_proxies:
                    return {'http': proxy, 'https': proxy}

                attempts += 1

            # Si tous les proxies ont échoué, réinitialiser les échecs
            self.failed_proxies.clear()
            return None

    def mark_proxy_failed(self, proxy):
        """Marquer un proxy comme défaillant."""
        with self.lock:
            if proxy and 'http' in proxy:
                self.failed_proxies.add(proxy['http'])

    def add_proxy(self, proxy):
        """Ajouter un proxy à la liste."""
        with self.lock:
            if proxy not in self.proxies:
                self.proxies.append(proxy)

    def get_stats(self):
        """Obtenir les statistiques des proxies."""
        with self.lock:
            return {
                'total_proxies': len(self.proxies),
                'failed_proxies': len(self.failed_proxies),
                'working_proxies': len(self.proxies) - len(self.failed_proxies)
            }


class MongoDBClient:
    """Client MongoDB pour la sauvegarde des données en temps réel."""

    def __init__(self, uri=DEFAULT_URI, db_name=DEFAULT_DB):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self.collection = self.db[COLLECTION]
        self.logger = logging.getLogger(__name__)
        self._create_indexes()

        # Collections séparées pour différents types de données
        self.collections = {
            'ohlcv': self.db['historical_ohlcv'],
            'orderbook': self.db['orderbook_data'],
            'sentiment': self.db['sentiment_data'],
            'onchain': self.db['onchain_data'],
            'macro': self.db['macro_data'],
            'derivatives': self.db['derivatives_data']
        }

        # Buffer pour ingestion par batch (optimisation)
        self.buffer = {key: [] for key in self.collections.keys()}
        self.buffer_size = 100  # Nombre de documents avant flush
        self.buffer_lock = threading.Lock()

    def _log_realtime_preview(self, data_type: str, data_list: List[Dict[str, Any]], limit: int = 2):
        """Afficher un aperçu des données reçues directement dans le terminal."""
        if not data_list:
            return

        try:
            preview_count = min(limit, len(data_list))
            preview_payload = []
            for item in data_list[:preview_count]:
                sanitized = {}
                for key, value in item.items():
                    if isinstance(value, datetime):
                        sanitized[key] = value.isoformat()
                    elif hasattr(value, "isoformat"):
                        sanitized[key] = value.isoformat()
                    else:
                        sanitized[key] = value
                preview_payload.append(sanitized)

            self.logger.info(
                f"Realtime data preview [{data_type}] showing {preview_count}/{len(data_list)}: "
                f"{json.dumps(preview_payload, default=str)}"
            )
        except Exception as exc:
            self.logger.debug(f"Failed to log realtime preview for {data_type}: {exc}")

    def _create_indexes(self):
        """Crée les index pour optimiser les performances."""
        try:
            # Index pour OHLCV
            self.collections['ohlcv'].create_index([("coin", 1), ("timestamp", 1)], unique=True)
            self.collections['ohlcv'].create_index([("timestamp", 1)])
            self.collections['ohlcv'].create_index([("coin", 1)])
            self.collections['ohlcv'].create_index([("timeframe", 1)])

            # Index pour orderbook
            self.collections['orderbook'].create_index([("symbol", 1), ("timestamp", 1)])

            # Index pour sentiment
            self.collections['sentiment'].create_index([("timestamp", 1)])
            self.collections['sentiment'].create_index([("source", 1)])

            # Index pour onchain
            self.collections['onchain'].create_index([("asset", 1), ("timestamp", 1)])
            self.collections['onchain'].create_index([("metric", 1)])

            # Index pour macro
            self.collections['macro'].create_index([("series", 1), ("date", 1)])

            # Index pour derivatives
            self.collections['derivatives'].create_index([("symbol", 1), ("timestamp", 1)])

            self.logger.info("MongoDB indexes created successfully")
        except Exception as e:
            self.logger.error(f"Error creating MongoDB indexes: {e}")

    async def insert_realtime(self, data_type: str, data: Dict[str, Any]):
        """Insère une donnée unique en temps réel dans MongoDB."""
        if not data:
            return False

        try:
            collection = self.collections.get(data_type, self.collections['ohlcv'])

            # Ajouter timestamp si absent
            if 'inserted_at' not in data:
                data['inserted_at'] = datetime.now()

            self._log_realtime_preview(data_type, [data])

            # Insertion immédiate
            result = collection.insert_one(data)
            self.logger.debug(f"Realtime insert: {data_type} - {result.inserted_id}")
            return True

        except DuplicateKeyError:
            # Mise à jour si doublon
            try:
                filter_key = self._get_filter_key(data_type, data)
                collection.update_one(filter_key, {'$set': data})
                self.logger.debug(f"Realtime update: {data_type}")
                return True
            except Exception as e:
                self.logger.error(f"Realtime update error: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Realtime insert error for {data_type}: {e}")
            return False

    async def insert_realtime_batch(self, data_type: str, data_list: List[Dict[str, Any]]):
        """Insère un batch de données en temps réel."""
        if not data_list:
            return 0

        try:
            collection = self.collections.get(data_type, self.collections['ohlcv'])

            # Ajouter timestamp à chaque document
            for data in data_list:
                if 'inserted_at' not in data:
                    data['inserted_at'] = datetime.now()

            # Préparation des opérations upsert
            operations = []
            for data in data_list:
                filter_key = self._get_filter_key(data_type, data)
                operations.append(
                    UpdateOne(filter_key, {'$set': data}, upsert=True)
                )

            # Exécution bulk
            if operations:
                self._log_realtime_preview(data_type, data_list)
                result = collection.bulk_write(operations, ordered=False)
                inserted_count = result.upserted_count + result.modified_count
                self.logger.info(f"MongoDB realtime batch: {data_type} - {inserted_count} documents")
                return inserted_count

        except BulkWriteError as e:
            inserted_count = e.details.get('nInserted', 0) + e.details.get('nUpserted', 0)
            self.logger.warning(f"MongoDB batch partial success: {inserted_count} documents")
            return inserted_count

        except Exception as e:
            self.logger.error(f"MongoDB batch insert error for {data_type}: {e}")
            return 0

    def _get_filter_key(self, data_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Génère la clé de filtre unique pour chaque type de données."""
        if data_type == 'ohlcv':
            return {
                'coin': data.get('coin', data.get('symbol')),
                'timestamp': data['timestamp'],
                'timeframe': data.get('timeframe', '1m')
            }
        elif data_type == 'orderbook':
            return {
                'symbol': data['symbol'],
                'timestamp': data['timestamp']
            }
        elif data_type == 'sentiment':
            return {
                'source': data.get('source', 'unknown'),
                'timestamp': data['timestamp']
            }
        elif data_type == 'onchain':
            return {
                'asset': data['asset'],
                'metric': data.get('metric', 'general'),
                'timestamp': data['timestamp']
            }
        elif data_type == 'macro':
            return {
                'series': data['series'],
                'date': data.get('date', data.get('timestamp'))
            }
        elif data_type == 'derivatives':
            return {
                'symbol': data['symbol'],
                'timestamp': data['timestamp']
            }
        else:
            return {'timestamp': data.get('timestamp', datetime.now())}

    async def add_to_buffer(self, data_type: str, data: Dict[str, Any]):
        """Ajoute des données au buffer pour insertion par batch."""
        with self.buffer_lock:
            self.buffer[data_type].append(data)

            # Flush automatique si buffer plein
            if len(self.buffer[data_type]) >= self.buffer_size:
                await self.flush_buffer(data_type)

    async def flush_buffer(self, data_type: str = None):
        """Vide le buffer et insère les données dans MongoDB."""
        if data_type:
            # Flush un type spécifique
            with self.buffer_lock:
                if self.buffer[data_type]:
                    data_to_insert = self.buffer[data_type].copy()
                    self.buffer[data_type].clear()

                    await self.insert_realtime_batch(data_type, data_to_insert)
        else:
            # Flush tous les types
            for dt in self.buffer.keys():
                await self.flush_buffer(dt)

    async def insert_ohlcv_data(self, coin_data):
        """Insère les données OHLCV dans MongoDB (compatibilité)."""
        if not coin_data:
            return 0

        # Convertir en liste si dict unique
        if isinstance(coin_data, dict):
            coin_data = [coin_data]

        return await self.insert_realtime_batch('ohlcv', coin_data)

    def get_stats(self) -> Dict[str, int]:
        """Obtenir les statistiques de la base de données."""
        stats = {}
        for name, collection in self.collections.items():
            stats[name] = collection.count_documents({})
        return stats

    async def close(self):
        """Fermer la connexion MongoDB."""
        try:
            await self.flush_buffer()
        except Exception as exc:
            self.logger.error(f"Error flushing MongoDB buffer before close: {exc}")

        self.client.close()
        self.logger.info("MongoDB connection closed")


class RateLimiter:
    """Gestionnaire intelligent de rate limiting."""

    def __init__(self, max_requests: int = 10, time_window: int = 1):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: List[float] = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Attendre si nécessaire avant d'autoriser une requête."""
        async with self.lock:
            now = time.time()
            # Retirer les requêtes anciennes
            self.requests = [req for req in self.requests if now - req < self.time_window]

            if len(self.requests) >= self.max_requests:
                sleep_time = self.time_window - (now - self.requests[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                self.requests = self.requests[1:]

            self.requests.append(now)


class DataCache:
    """Cache intelligent pour éviter les requêtes redondantes."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.memory_cache: Dict[str, tuple[Any, float]] = {}
        self.ttl = 3600  # 1 heure par défaut

    def _get_cache_key(self, url: str, params: Optional[Dict] = None) -> str:
        """Générer une clé unique pour le cache."""
        key_str = f"{url}_{json.dumps(params, sort_keys=True) if params else ''}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, url: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Récupérer depuis le cache."""
        key = self._get_cache_key(url, params)

        # Vérifier le cache mémoire
        if key in self.memory_cache:
            data, timestamp = self.memory_cache[key]
            if time.time() - timestamp < self.ttl:
                return data

        # Vérifier le cache disque
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached = json.load(f)
                if time.time() - cached['timestamp'] < self.ttl:
                    return cached['data']
            except Exception:
                pass

        return None

    def set(self, url: str, data: Any, params: Optional[Dict] = None):
        """Stocker dans le cache."""
        key = self._get_cache_key(url, params)
        timestamp = time.time()

        # Cache mémoire
        self.memory_cache[key] = (data, timestamp)

        # Cache disque
        try:
            cache_file = self.cache_dir / f"{key}.json"
            with open(cache_file, 'w') as f:
                json.dump({'data': data, 'timestamp': timestamp}, f)
        except Exception:
            pass


class BaseCollector:
    """Classe de base pour tous les collecteurs avec gestion des proxies."""

    def __init__(self, config: APIConfig, cache: DataCache, proxy_rotator: ProxyRotator, mongo_client: MongoDBClient):
        self.config = config
        self.cache = cache
        self.proxy_rotator = proxy_rotator
        self.mongo_client = mongo_client
        self.logger = logging.getLogger(self.__class__.__name__)
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter = RateLimiter(max_requests=8, time_window=1)  # Plus conservateur
        self.request_count = 0
        self.successful_requests = 0

        # Rotation des User-Agents
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36'
        ]

    def get_headers(self):
        """Générer des headers avec User-Agent aléatoire."""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
        }

    async def get_session(self) -> aiohttp.ClientSession:
        """Obtenir ou créer une session HTTP."""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            connector = aiohttp.TCPConnector(limit=50, limit_per_host=5)  # Plus conservateur
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self.session

    async def fetch_json(self, url: str, params: Optional[Dict] = None,
                        headers: Optional[Dict] = None, use_cache: bool = True,
                        max_retries: int = 3, use_proxies: bool = False) -> Optional[Dict]:
        """Requête HTTP avec cache, retry, rate limiting et proxies rotatifs."""

        # Vérifier le cache
        if use_cache:
            cached = self.cache.get(url, params)
            if cached is not None:
                return cached

        # Rate limiting
        await self.rate_limiter.acquire()

        # Headers par défaut
        if headers is None:
            headers = self.get_headers()

        # Retry logic avec proxies rotatifs
        for attempt in range(max_retries):
            proxies = None
            try:
                session = await self.get_session()

                # Configuration de la requête avec proxy
                request_kwargs = {
                    'url': url,
                    'params': params,
                    'headers': headers,
                    'timeout': 30
                }

                if use_proxies:
                    proxies = self.proxy_rotator.get_proxy()
                    if proxies:
                        data = await self._fetch_with_requests(url, params, headers, proxies)
                        if data is not None:
                            if use_cache:
                                self.cache.set(url, data, params)
                            return data
                        else:
                            self.proxy_rotator.mark_proxy_failed(proxies)

                async with session.get(**request_kwargs) as response:
                    self.request_count += 1

                    if response.status == 200:
                        data = await response.json()
                        self.successful_requests += 1
                        if use_cache:
                            self.cache.set(url, data, params)
                        return data
                    elif response.status == 429:  # Rate limit
                        self.logger.warning(f"Rate limit hit on attempt {attempt + 1}. Waiting...")
                        wait_time = 2 ** attempt + random.uniform(1, 3)
                        await asyncio.sleep(wait_time)
                    else:
                        self.logger.warning(f"HTTP {response.status} for {url}")
                        if proxies:
                            self.proxy_rotator.mark_proxy_failed(proxies)
                        await asyncio.sleep(5)

            except Exception as e:
                self.logger.debug(f"Attempt {attempt + 1} failed for {url}: {e}")
                if proxies:
                    self.proxy_rotator.mark_proxy_failed(proxies)
                wait_time = 2 ** attempt + random.uniform(1, 3)
                await asyncio.sleep(wait_time)

        self.logger.error(f"All {max_retries} attempts failed for {url}")
        return None

    async def _fetch_with_requests(self, url: str, params: Optional[Dict] = None,
                                 headers: Optional[Dict] = None, proxies: Optional[Dict] = None) -> Optional[Dict]:
        """Utiliser requests pour les requêtes avec proxy (compatibilité)."""
        if "binance.com" in url:
            return None
        try:
            # Pause aléatoire supplémentaire pour les requêtes avec proxy
            await asyncio.sleep(random.uniform(1, 3))

            response = requests.get(
                url,
                params=params,
                headers=headers or self.get_headers(),
                proxies=proxies,
                timeout=30
            )

            self.request_count += 1

            if response.status_code == 200:
                self.successful_requests += 1
                return response.json()
            else:
                self.logger.warning(f"HTTP {response.status_code} with proxy")
                return None

        except Exception as e:
            self.logger.debug(f"Proxy request failed: {e}")
            self.proxy_rotator.mark_proxy_failed(proxies)
            return None

    async def close(self):
        """Fermer les ressources."""
        if self.session and not self.session.closed:
            await self.session.close()


class MarketDataCollector(BaseCollector):
    """Collecteur de données de marché (OHLCV) multi-exchanges avec données minutieres."""

    async def get_binance_server_time(self) -> datetime:
        """Récupérer l'heure serveur Binance pour éviter un horodatage local incorrect."""
        url = "https://api.binance.com/api/v3/time"
        data = await self.fetch_json(url, use_cache=False, max_retries=1, use_proxies=False)
        if data and isinstance(data, dict) and 'serverTime' in data:
            server_dt = datetime.fromtimestamp(data['serverTime'] / 1000)
            self.logger.info(f"Binance server time: {server_dt.isoformat()}")
            return server_dt

        self.logger.warning("Failed to fetch Binance server time, falling back to local clock")
        return datetime.utcnow()

    async def collect_binance_ohlcv_minutely(self, symbols: List[str], start_date: datetime,
                                           end_date: datetime, interval: str = '1m') -> pd.DataFrame:
        """Collecter les données Binance minute par minute avec ingestion temps réel."""
        self.logger.info(f"Collecting Binance minutely data for {len(symbols)} symbols")
        all_data = []

        for symbol in symbols:
            symbol_binance = symbol.replace('/', '')  # BTC/USDT -> BTCUSDT
            max_empty_segments = 5
            empty_segments = 0

            # Segmentation pour éviter les limites
            current_start = start_date
            segment_days = 3  # 3 jours pour les données minute

            while current_start < end_date:
                current_end = min(current_start + timedelta(days=segment_days), end_date)

                self.logger.info(
                    f"[Binance] Segment {symbol} - {current_start.isoformat()} -> {current_end.isoformat()}"
                )

                url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': symbol_binance,
                    'interval': interval,
                    'startTime': int(current_start.timestamp() * 1000),
                    'endTime': int(current_end.timestamp() * 1000),
                    'limit': 1440
                }

                data = await self.fetch_json(url, params, use_proxies=False, use_cache=False)
                if not data:
                    empty_segments += 1
                    if empty_segments >= max_empty_segments:
                        self.logger.error(f"[Binance] Too many empty segments for {symbol}, stopping.")
                        break
                    continue
                else:
                    empty_segments = 0
                    self.logger.info(f"[Binance] {symbol} segment size: {len(data)}")
                    batch_entries = []
                    for entry in data:
                        df_entry = {
                            'timestamp': entry[0],
                            'datetime': pd.to_datetime(entry[0], unit='ms'),
                            'open': float(entry[1]),
                            'high': float(entry[2]),
                            'low': float(entry[3]),
                            'close': float(entry[4]),
                            'volume': float(entry[5]),
                            'coin': symbol,
                            'symbol': symbol,
                            'exchange': 'binance',
                            'timeframe': '1m'
                        }
                        all_data.append(df_entry)
                        batch_entries.append(df_entry)

                    # Ingestion MongoDB en temps réel pour ce batch
                    if batch_entries:
                        await self.mongo_client.insert_realtime_batch('ohlcv', batch_entries)
                        self.logger.info(f"Inserted {len(batch_entries)} records for {symbol} in realtime")
                    else:
                        self.logger.warning(f"[Binance] {symbol} segment empty after parsing")

                # Pause importante entre les segments
                await asyncio.sleep(random.uniform(2, 5))
                current_start = current_end

        df = pd.DataFrame(all_data)
        return df

    async def collect_coingecko_minutely(self, coins: List[str], days: int = 365,
                                         reference_time: Optional[datetime] = None) -> pd.DataFrame:
        """Collecter les données CoinGecko minute par minute avec ingestion temps réel."""
        self.logger.info(f"Collecting CoinGecko minutely data for {len(coins)} coins")
        all_data = []

        for coin in coins:
            self.logger.info(f"Processing {coin}...")
            max_empty_segments = 5
            empty_segments = 0

            end_date = reference_time or datetime.utcnow()
            start_date = end_date - timedelta(days=days)

            # Segmentation par mois
            segment_days = 60
            current_start = start_date

            while current_start < end_date:
                current_end = min(current_start + timedelta(days=segment_days), end_date)

                self.logger.info(
                    f"[CoinGecko] Segment {coin} - {current_start.date()} -> {current_end.date()}"
                )

                url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart/range"
                params = {
                    'vs_currency': 'usd',
                    'from': int(current_start.timestamp()),
                    'to': int(current_end.timestamp()),
                    'precision': 'full'
                }

                data = await self.fetch_json(url, params, use_proxies=False, use_cache=False)

                if not data or 'prices' not in data:
                    empty_segments += 1
                    if empty_segments >= max_empty_segments:
                        self.logger.error(f"[CoinGecko] Too many empty segments for {coin}, stopping.")
                        break
                    continue
                else:
                    empty_segments = 0
                    self.logger.info(f"[CoinGecko] {coin} segment size: {len(data['prices'])}")
                    batch_records = []
                    for price_point in data['prices']:
                        record = {
                            'coin': coin,
                            'timestamp': price_point[0],
                            'datetime': pd.to_datetime(price_point[0], unit='ms'),
                            'open': price_point[1],
                            'high': price_point[1],
                            'low': price_point[1],
                            'close': price_point[1],
                            'volume': 0,
                            'exchange': 'coingecko',
                            'timeframe': '1m'
                        }
                        all_data.append(record)
                        batch_records.append(record)

                    # Ingestion MongoDB en temps réel pour ce batch
                    if batch_records:
                        await self.mongo_client.insert_realtime_batch('ohlcv', batch_records)
                        self.logger.info(f"Inserted {len(batch_records)} records for {coin} in realtime")
                    else:
                        self.logger.warning(f"[CoinGecko] {coin} segment empty after parsing")

                # Pause importante entre les segments
                await asyncio.sleep(random.uniform(10, 15))
                current_start = current_end

        df = pd.DataFrame(all_data)
        return df

    async def collect_spot_orderbook(self, symbols: List[str]) -> pd.DataFrame:
        """Collecter les orderbooks (profondeur de marché)."""
        self.logger.info(f"Collecting orderbook data for {len(symbols)} symbols")
        orderbooks = []

        for symbol in symbols[:20]:  # Limiter pour éviter surcharge
            symbol_binance = symbol.replace('/', '')
            url = "https://api.binance.com/api/v3/depth"
            params = {'symbol': symbol_binance, 'limit': 100}

            data = await self.fetch_json(url, params, use_cache=False, use_proxies=False)
            if data and data.get('bids') and data.get('asks'):
                bids = pd.DataFrame(data['bids'], columns=['price', 'quantity'])
                asks = pd.DataFrame(data['asks'], columns=['price', 'quantity'])

                if bids.empty or asks.empty:
                    continue

                try:
                    best_bid = float(bids.iloc[0]['price'])
                    best_ask = float(asks.iloc[0]['price'])
                except Exception:
                    continue

                orderbooks.append({
                    'symbol': symbol,
                    'timestamp': datetime.now(),
                    'bid_depth': bids['quantity'].astype(float).sum(),
                    'ask_depth': asks['quantity'].astype(float).sum(),
                    'spread': best_ask - best_bid,
                    'best_bid': best_bid,
                    'best_ask': best_ask
                })
            else:
                self.logger.warning(f"[Orderbook] No data returned for {symbol}")

            await asyncio.sleep(0.5)  # Rate limiting courtois

        return pd.DataFrame(orderbooks)


class OnChainDataCollector(BaseCollector):
    """Collecteur de données on-chain (activité réseau)."""

    async def collect_glassnode_metrics(self, assets: List[str] = ['BTC', 'ETH']) -> pd.DataFrame:
        """Collecter les métriques Glassnode (premium)."""
        self.logger.info("Collecting Glassnode metrics")

        if not self.config.GLASSNODE_API_KEY:
            self.logger.warning("Glassnode API key missing - using public alternatives")
            return await self.collect_public_onchain_data(assets)

        all_metrics = []
        metrics_to_fetch = [
            'active_addresses',
            'transaction_count',
            'nvt',  # Network Value to Transactions
            'sopr',  # Spent Output Profit Ratio
        ]

        for asset in assets:
            for metric in metrics_to_fetch:
                url = f"https://api.glassnode.com/v1/metrics/addresses/{metric}"
                params = {
                    'a': asset,
                    'api_key': self.config.GLASSNODE_API_KEY,
                    'f': 'json'
                }

                data = await self.fetch_json(url, params, use_proxies=False)
                if data:
                    df = pd.DataFrame(data)
                    df['asset'] = asset
                    df['metric'] = metric
                    all_metrics.append(df)

                await asyncio.sleep(1)

        return pd.concat(all_metrics, ignore_index=True) if all_metrics else pd.DataFrame()

    async def collect_public_onchain_data(self, assets: List[str]) -> pd.DataFrame:
        """Collecter des données on-chain depuis sources publiques."""
        self.logger.info("Collecting public on-chain data")
        onchain_data = []

        # Bitcoin via blockchain.info
        if 'BTC' in assets:
            btc_stats = await self.fetch_json("https://blockchain.info/stats?format=json", use_proxies=False)
            if btc_stats:
                onchain_data.append({
                    'asset': 'BTC',
                    'timestamp': datetime.now(),
                    'difficulty': btc_stats.get('difficulty'),
                    'hashrate': btc_stats.get('hash_rate'),
                    'total_transactions': btc_stats.get('n_tx'),
                    'market_cap': btc_stats.get('market_price_usd', 0) * btc_stats.get('totalbc', 0) / 1e8
                })

        # Mempool.space pour Bitcoin
        mempool_data = await self.fetch_json("https://mempool.space/api/mempool", use_proxies=False)
        if mempool_data:
            onchain_data.append({
                'asset': 'BTC',
                'metric': 'mempool',
                'timestamp': datetime.now(),
                'mempool_tx_count': mempool_data.get('count', 0),
                'mempool_size': mempool_data.get('vsize', 0),
            })

        return pd.DataFrame(onchain_data)


class SentimentDataCollector(BaseCollector):
    """Collecteur de données de sentiment (social media, news)."""

    async def collect_reddit_sentiment(self, subreddits: List[str] = None) -> pd.DataFrame:
        """Collecter le sentiment Reddit."""
        if subreddits is None:
            subreddits = ['CryptoCurrency', 'Bitcoin', 'ethereum', 'CryptoMarkets']

        self.logger.info(f"Collecting Reddit data from {len(subreddits)} subreddits")
        reddit_data = []

        for subreddit in subreddits:
            url = f"https://www.reddit.com/r/{subreddit}/hot/.json"
            params = {'limit': 25}
            headers = {'User-Agent': random.choice(self.user_agents)}

            data = await self.fetch_json(url, params, headers, use_cache=False, use_proxies=False)
            if data and 'data' in data:
                posts = data['data'].get('children', [])

                for post in posts:
                    post_data = post.get('data', {})
                    reddit_data.append({
                        'subreddit': subreddit,
                        'title': post_data.get('title', ''),
                        'score': post_data.get('score', 0),
                        'upvote_ratio': post_data.get('upvote_ratio', 0),
                        'num_comments': post_data.get('num_comments', 0),
                        'created_utc': post_data.get('created_utc', 0),
                        'author': post_data.get('author', ''),
                        'timestamp': datetime.now()
                    })

            await asyncio.sleep(2)  # Respect Reddit rate limits

        return pd.DataFrame(reddit_data)

    async def collect_fear_greed_index(self) -> pd.DataFrame:
        """Collecter le Fear & Greed Index."""
        self.logger.info("Collecting Fear & Greed Index")

        url = "https://api.alternative.me/fng/"
        params = {'limit': 30}

        data = await self.fetch_json(url, params, use_cache=False, use_proxies=False)
        if data and 'data' in data:
            df = pd.DataFrame(data['data'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            df['value'] = df['value'].astype(int)
            return df

        return pd.DataFrame()


class MacroDataCollector(BaseCollector):
    """Collecteur de données macroéconomiques."""

    async def collect_fred_data(self, series: List[str] = None) -> pd.DataFrame:
        """Collecter les données FRED (Federal Reserve Economic Data)."""
        if series is None:
            series = [
                'DFF',      # Federal Funds Rate
                'T10Y2Y',   # 10-Year minus 2-Year Treasury Spread
                'CPIAUCSL', # CPI (Inflation)
                'UNRATE',   # Unemployment Rate
            ]

        self.logger.info(f"Collecting FRED data for {len(series)} series")

        if not self.config.FRED_API_KEY:
            self.logger.warning("FRED API key missing")
            return pd.DataFrame()

        all_data = []

        for serie in series:
            url = f"https://api.stlouisfed.org/fred/series/observations"
            params = {
                'series_id': serie,
                'api_key': self.config.FRED_API_KEY,
                'file_type': 'json',
                'limit': 1000
            }

            data = await self.fetch_json(url, params, use_proxies=False)
            if data and 'observations' in data:
                df = pd.DataFrame(data['observations'])
                df['series'] = serie
                all_data.append(df)

            await asyncio.sleep(0.5)

        return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()


class DerivativesDataCollector(BaseCollector):
    """Collecteur de données dérivés (futures, options, funding rates)."""

    async def collect_funding_rates(self, symbols: List[str]) -> pd.DataFrame:
        """Collecter les funding rates des futures perpétuels."""
        self.logger.info(f"Collecting funding rates for {len(symbols)} symbols")
        funding_data = []

        for symbol in symbols:
            symbol_binance = symbol.replace('/', '')
            url = "https://fapi.binance.com/fapi/v1/fundingRate"
            params = {
                'symbol': symbol_binance,
                'limit': 100
            }

            data = await self.fetch_json(url, params, use_cache=False, use_proxies=False)
            if data:
                for entry in data:
                    funding_data.append({
                        'symbol': symbol,
                        'funding_rate': float(entry['fundingRate']),
                        'funding_time': pd.to_datetime(entry['fundingTime'], unit='ms'),
                        'exchange': 'binance'
                    })

        return pd.DataFrame(funding_data)

    async def collect_open_interest(self, symbols: List[str]) -> pd.DataFrame:
        """Collecter l'open interest (positions ouvertes)."""
        self.logger.info(f"Collecting open interest for {len(symbols)} symbols")
        oi_data = []

        for symbol in symbols:
            symbol_binance = symbol.replace('/', '')
            url = "https://fapi.binance.com/fapi/v1/openInterest"
            params = {'symbol': symbol_binance}

            data = await self.fetch_json(url, params, use_cache=False, use_proxies=False)
            if data:
                oi_data.append({
                    'symbol': symbol,
                    'open_interest': float(data['openInterest']),
                    'timestamp': datetime.now(),
                    'exchange': 'binance'
                })

        return pd.DataFrame(oi_data)


class MassDataCollector:
    """Orchestrateur principal de collecte de données avec proxies et MongoDB."""

    def __init__(self, config: APIConfig):
        self.config = config
        self.base_path = Path("datasets/alpha_trading")
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Initialiser les composants partagés
        self.cache = DataCache(self.base_path / "cache")
        self.proxy_rotator = ProxyRotator()
        self.mongo_client = MongoDBClient()
        self.console = Console()

        # Initialiser les collecteurs spécialisés
        self.market_collector = MarketDataCollector(config, self.cache, self.proxy_rotator, self.mongo_client)
        self.onchain_collector = OnChainDataCollector(config, self.cache, self.proxy_rotator, self.mongo_client)
        self.sentiment_collector = SentimentDataCollector(config, self.cache, self.proxy_rotator, self.mongo_client)
        self.macro_collector = MacroDataCollector(config, self.cache, self.proxy_rotator, self.mongo_client)
        self.derivatives_collector = DerivativesDataCollector(config, self.cache, self.proxy_rotator, self.mongo_client)

        self.setup_logging()
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'start_time': None,
            'end_time': None
        }

    def setup_logging(self):
        """Configurer le logging."""
        log_file = self.base_path / "collection.log"

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file)]
        )
        self.logger = logging.getLogger(__name__)

    async def _run_tasks_with_progress(self, tasks: Dict[str, Awaitable[Any]]) -> Dict[str, Any]:
        """Afficher l'état des tâches asynchrones dans le terminal."""
        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TextColumn("{task.fields[status]}", justify="left"),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
            refresh_per_second=8,
        )

        task_ids = {name: None for name in tasks}
        results: Dict[str, Any] = {}

        with progress:
            for name in tasks:
                task_ids[name] = progress.add_task(f"{name}", status="en attente", total=1)

            async def runner(name: str, coro: Awaitable[Any]):
                progress.update(task_ids[name], status="en cours")
                try:
                    result = await coro
                    progress.update(task_ids[name], status="termine")
                    return name, result
                except Exception as exc:
                    progress.update(task_ids[name], status="erreur")
                    return name, exc
                finally:
                    progress.advance(task_ids[name], 1)

            coros = [runner(name, task) for name, task in tasks.items()]
            for coro in asyncio.as_completed(coros):
                name, result = await coro
                results[name] = result

        return results

    def get_top_coins(self, limit: int = 30) -> List[str]:
        """Obtenir les top cryptos pour CoinGecko."""
        return [
            'bitcoin', 'ethereum', 'ripple', 'bitcoin-cash', 'cardano',
            'litecoin', 'eos', 'stellar', 'monero', 'dash',
            'ethereum-classic', 'nem', 'zcash', 'qtum', 'bitcoin-gold',
            'lisk', 'tron', 'steem', 'dogecoin', 'vechain',
            'bitshares', 'golem', 'siacoin', 'decred', 'ontology',
            'icon', 'ae', 'ethos', 'ark', 'nano'
        ][:limit]

    def get_top_symbols(self, limit: int = 20) -> List[str]:
        """Obtenir les top cryptos par market cap pour Binance."""
        return [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
            "ADA/USDT", "AVAX/USDT", "DOT/USDT", "DOGE/USDT", "MATIC/USDT",
            "LTC/USDT", "LINK/USDT", "ATOM/USDT", "UNI/USDT", "XLM/USDT",
            "NEAR/USDT", "ALGO/USDT", "VET/USDT", "ICP/USDT", "FIL/USDT"
        ][:limit]

    async def collect_minutely_data(self, days: int = 365) -> Dict[str, pd.DataFrame]:
        """Collecter les données minuteires sur une année complète avec proxies gratuits."""
        self.logger.info(f"Starting minutely data collection for {days} days")
        self.stats['start_time'] = datetime.utcnow()

        # Initialiser les proxies gratuits
        self.logger.info("Initializing free proxies...")
        await self.proxy_rotator.initialize_proxies()
        proxy_stats = self.proxy_rotator.get_stats()
        self.logger.info(f"Proxies initialized: {proxy_stats}")

        # Heure de référence côté exchange pour éviter les dates futures dues à l'horloge locale
        try:
            reference_now = await self.market_collector.get_binance_server_time()
        except Exception:
            reference_now = datetime.utcnow()
            self.logger.warning("Using local UTC time as reference (Binance server time unavailable)")

        coins = self.get_top_coins(30)
        symbols = self.get_top_symbols(20)

        # Lancer les collectes en parallèle
        tasks = {
            'coingecko_minutely': self.market_collector.collect_coingecko_minutely(coins, days, reference_now),
            'binance_minutely': self.market_collector.collect_binance_ohlcv_minutely(symbols,
                reference_now - timedelta(days=min(days, 30)),  # Binance limite historique
                reference_now, '1m'),
            'orderbook_depth': self.market_collector.collect_spot_orderbook(symbols),
            'onchain_metrics': self.onchain_collector.collect_public_onchain_data(['BTC', 'ETH']),
            'reddit_sentiment': self.sentiment_collector.collect_reddit_sentiment(),
            'fear_greed_index': self.sentiment_collector.collect_fear_greed_index(),
            'fred_economic': self.macro_collector.collect_fred_data(),
            'funding_rates': self.derivatives_collector.collect_funding_rates(symbols),
            'open_interest': self.derivatives_collector.collect_open_interest(symbols),
        }

        self.logger.info(f"Launching {len(tasks)} parallel data collection tasks")
        results = await self._run_tasks_with_progress(tasks)

        # Compiler les résultats
        dataset = {}
        for name, result in results.items():
            if isinstance(result, Exception):
                self.logger.error(f"Task {name} failed: {result}")
            elif isinstance(result, pd.DataFrame):
                dataset[name] = result
                self.logger.info(f"{name}: {len(result)} records collected")
            else:
                self.logger.warning(f"{name}: unexpected result type")

        # Statistiques
        self.stats['end_time'] = datetime.now()
        self.stats['total_requests'] = (
            self.market_collector.request_count +
            self.onchain_collector.request_count +
            self.sentiment_collector.request_count +
            self.macro_collector.request_count +
            self.derivatives_collector.request_count
        )
        self.stats['successful_requests'] = (
            self.market_collector.successful_requests +
            self.onchain_collector.successful_requests +
            self.sentiment_collector.successful_requests +
            self.macro_collector.successful_requests +
            self.derivatives_collector.successful_requests
        )

        # Flush tous les buffers MongoDB avant de sauvegarder
        await self.mongo_client.flush_buffer()

        # Obtenir les stats MongoDB
        mongo_stats = self.mongo_client.get_stats()
        self.logger.info(f"MongoDB stats: {mongo_stats}")

        # Sauvegarder le dataset local
        self.save_dataset(dataset)

        return dataset

    def save_dataset(self, dataset: Dict[str, pd.DataFrame]):
        """Sauvegarder le dataset avec métadonnées."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_path = self.base_path / f"dataset_{timestamp}"
        dataset_path.mkdir(parents=True, exist_ok=True)

        total_records = 0

        for name, df in dataset.items():
            if df is not None and not df.empty:
                file_path = dataset_path / f"{name}.parquet"
                df.to_parquet(file_path, compression='gzip')
                total_records += len(df)
                self.logger.info(f"Saved {name}: {len(df)} records")

        # Métadonnées avec statistiques
        metadata = {
            'collection_date': timestamp,
            'total_records': total_records,
            'data_sources': list(dataset.keys()),
            'collection_duration': str(self.stats['end_time'] - self.stats['start_time']),
            'request_stats': {
                'total_requests': self.stats['total_requests'],
                'successful_requests': self.stats['successful_requests'],
                'success_rate': (self.stats['successful_requests'] / self.stats['total_requests'] * 100) if self.stats['total_requests'] > 0 else 0
            },
            'proxies_used': len(self.proxy_rotator.proxies),
            'mongo_db': DEFAULT_DB,
            'mongo_collection': COLLECTION
        }

        with open(dataset_path / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

        self.logger.info(f"Dataset saved to {dataset_path}")
        self.logger.info(f"Total records: {total_records}")
        self.logger.info(f"Request success rate: {metadata['request_stats']['success_rate']:.2f}%")

        # Créer un fichier de résumé
        self.create_summary_report(dataset, dataset_path)

    def create_summary_report(self, dataset: Dict[str, pd.DataFrame], path: Path):
        """Créer un rapport de résumé du dataset."""
        report = []
        report.append("=" * 80)
        report.append("ALPHA TRADING DATA COLLECTION REPORT - MINUTELY DATA")
        report.append("=" * 80)
        report.append(f"\nCollection Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Duration: {self.stats['end_time'] - self.stats['start_time']}")
        total_requests = self.stats.get('total_requests', 0) or 0
        successful_requests = self.stats.get('successful_requests', 0) or 0
        success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
        report.append(f"Successful Requests: {successful_requests}/{total_requests} ({success_rate:.2f}%)")
        report.append(f"Proxies Used: {len(self.proxy_rotator.proxies)}")
        report.append(f"MongoDB: {DEFAULT_DB}.{COLLECTION}")
        report.append(f"\nData Sources: {len(dataset)}")
        report.append("\n" + "-" * 80)

        for name, df in dataset.items():
            if df is not None and not df.empty:
                report.append(f"\n{name.upper()}")
                report.append(f"  Records: {len(df):,}")
                report.append(f"  Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}" if 'timestamp' in df.columns else "  No timestamp")
                report.append(f"  Columns: {', '.join(df.columns.tolist()[:8])}")
                if len(df.columns) > 8:
                    report.append(f"  ... and {len(df.columns) - 8} more columns")

        report.append("\n" + "=" * 80)

        with open(path / "summary.txt", 'w') as f:
            f.write('\n'.join(report))

        self.logger.info("Summary report created")

    async def close(self):
        """Fermer toutes les sessions et connexions."""
        await asyncio.gather(
            self.market_collector.close(),
            self.onchain_collector.close(),
            self.sentiment_collector.close(),
            self.macro_collector.close(),
            self.derivatives_collector.close(),
            return_exceptions=True
        )
        await self.mongo_client.close()


async def main():
    """Point d'entrée principal."""

    # Configuration des API keys
    config = APIConfig(
        BINANCE_API_KEY=os.getenv("BINANCE_API_KEY", ""),
        BINANCE_SECRET_KEY=os.getenv("BINANCE_SECRET_KEY", ""),
        GLASSNODE_API_KEY=os.getenv("GLASSNODE_API_KEY", ""),
        FRED_API_KEY=os.getenv("FRED_API_KEY", ""),
        ALPHA_VANTAGE_API_KEY=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
        COINGLASS_API_KEY=os.getenv("COINGLASS_API_KEY", ""),
        CRYPTOPANIC_API_KEY=os.getenv("CRYPTOPANIC_API_KEY", ""),
        LUNARCRUSH_API_KEY=os.getenv("LUNARCRUSH_API_KEY", ""),
    )

    collector = MassDataCollector(config)

    try:
        print("\n" + "=" * 80)
        print("MASS DATA COLLECTOR V3 - Starting Minutely Data Collection")
        print("=" * 80 + "\n")

        # Collecter les données minuteires sur 1 an
        dataset = await collector.collect_minutely_data(days=365)

        print("\n" + "=" * 80)
        print("COLLECTION COMPLETE!")
        print("=" * 80)
        print(f"\nTotal data sources: {len(dataset)}")

        total_records = sum(len(df) for df in dataset.values() if df is not None)
        print(f"Total records collected: {total_records:,}")

        success_rate = (collector.stats['successful_requests'] / collector.stats['total_requests'] * 100) if collector.stats['total_requests'] > 0 else 0
        print(f"Request success rate: {success_rate:.2f}%")

        print("\nData breakdown:")
        for name, df in dataset.items():
            if df is not None and not df.empty:
                print(f"  - {name}: {len(df):,} records")

        print(f"\nData saved to MongoDB: {DEFAULT_DB}.{COLLECTION}")
        print("\n" + "=" * 80)

    except Exception as e:
        logging.error(f"Collection failed: {e}", exc_info=True)
    finally:
        await collector.close()


if __name__ == "__main__":
    # Installation des dépendances:
    # pip install aiohttp pandas numpy beautifulsoup4 fake-useragent pyarrow pymongo motor requests

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted")
