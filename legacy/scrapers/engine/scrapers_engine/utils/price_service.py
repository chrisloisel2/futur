"""
Service de récupération des prix crypto via CoinGecko API (gratuit)
Permet de calculer la valeur USD des transactions historiques
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
import time
import hashlib


class PriceService:
    """Service pour récupérer les prix des cryptomonnaies"""

    # Mapping des symboles vers IDs CoinGecko
    COINGECKO_IDS = {
        'BTC': 'bitcoin',
        'ETH': 'ethereum',
        'SOL': 'solana',
        'USDT': 'tether',
        'USDC': 'usd-coin',
        'BNB': 'binancecoin',
        'XRP': 'ripple',
        'ADA': 'cardano',
        'DOGE': 'dogecoin',
        'MATIC': 'matic-network',
    }

    def __init__(self, cache_duration_hours=24):
        self.logger = logging.getLogger(__name__)
        self.base_url = 'https://api.coingecko.com/api/v3'

        # Cache des prix
        self.price_cache = {}  # {cache_key: {'price': float, 'timestamp': datetime}}
        self.cache_duration = timedelta(hours=cache_duration_hours)

        # Rate limiting (CoinGecko gratuit: 50 req/min)
        self.last_request_time = 0
        self.min_request_interval = 1.2  # 1.2 sec entre requêtes = 50 req/min max

        # Stats
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'api_calls': 0,
            'errors': 0
        }

    def _get_cache_key(self, symbol: str, date: Optional[datetime] = None) -> str:
        """Génère une clé de cache unique"""
        if date:
            date_str = date.strftime('%Y-%m-%d')
            return f"{symbol}_{date_str}"
        return f"{symbol}_current"

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Vérifie si le cache est encore valide"""
        if cache_key not in self.price_cache:
            return False

        cached_data = self.price_cache[cache_key]
        age = datetime.now() - cached_data['timestamp']
        return age < self.cache_duration

    def _wait_for_rate_limit(self):
        """Respecte le rate limit de CoinGecko"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            self.logger.debug(f"Rate limit: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Récupère le prix actuel d'une crypto

        Args:
            symbol: Symbole de la crypto (BTC, ETH, SOL, etc.)

        Returns:
            Prix en USD ou None si erreur
        """
        cache_key = self._get_cache_key(symbol)

        # Vérifier le cache
        if self._is_cache_valid(cache_key):
            self.stats['cache_hits'] += 1
            return self.price_cache[cache_key]['price']

        self.stats['cache_misses'] += 1

        # Obtenir l'ID CoinGecko
        coin_id = self.COINGECKO_IDS.get(symbol.upper())
        if not coin_id:
            self.logger.warning(f"Symbol {symbol} not found in CoinGecko mapping")
            return None

        try:
            self._wait_for_rate_limit()

            # Requête API
            url = f"{self.base_url}/simple/price"
            params = {
                'ids': coin_id,
                'vs_currencies': 'usd'
            }

            self.logger.debug(f"Fetching current price for {symbol}")
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            self.stats['api_calls'] += 1

            data = response.json()
            price = data.get(coin_id, {}).get('usd')

            if price:
                # Mettre en cache
                self.price_cache[cache_key] = {
                    'price': price,
                    'timestamp': datetime.now()
                }
                self.logger.info(f"Current price {symbol}: ${price:,.2f}")
                return price
            else:
                self.logger.error(f"Price not found in response for {symbol}")
                return None

        except requests.exceptions.RequestException as e:
            self.stats['errors'] += 1
            self.logger.error(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_historical_price(self, symbol: str, date: datetime) -> Optional[float]:
        """
        Récupère le prix historique d'une crypto à une date donnée

        Args:
            symbol: Symbole de la crypto (BTC, ETH, SOL, etc.)
            date: Date pour laquelle récupérer le prix

        Returns:
            Prix en USD ou None si erreur
        """
        cache_key = self._get_cache_key(symbol, date)

        # Vérifier le cache (cache permanent pour données historiques)
        if cache_key in self.price_cache:
            self.stats['cache_hits'] += 1
            return self.price_cache[cache_key]['price']

        self.stats['cache_misses'] += 1

        # Obtenir l'ID CoinGecko
        coin_id = self.COINGECKO_IDS.get(symbol.upper())
        if not coin_id:
            self.logger.warning(f"Symbol {symbol} not found in CoinGecko mapping")
            return None

        try:
            self._wait_for_rate_limit()

            # Format de date requis par CoinGecko: DD-MM-YYYY
            date_str = date.strftime('%d-%m-%Y')

            # Requête API
            url = f"{self.base_url}/coins/{coin_id}/history"
            params = {
                'date': date_str,
                'localization': 'false'
            }

            self.logger.debug(f"Fetching historical price for {symbol} on {date_str}")
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            self.stats['api_calls'] += 1

            data = response.json()
            price = data.get('market_data', {}).get('current_price', {}).get('usd')

            if price:
                # Mettre en cache (permanent pour historique)
                self.price_cache[cache_key] = {
                    'price': price,
                    'timestamp': datetime.now()
                }
                self.logger.info(f"Historical price {symbol} on {date_str}: ${price:,.2f}")
                return price
            else:
                self.logger.error(f"Price not found in response for {symbol} on {date_str}")
                return None

        except requests.exceptions.RequestException as e:
            self.stats['errors'] += 1
            self.logger.error(f"Error fetching historical price for {symbol} on {date_str}: {e}")
            return None

    def get_price(self, symbol: str, timestamp: Optional[datetime] = None) -> Optional[float]:
        """
        Récupère le prix d'une crypto (actuel ou historique)

        Args:
            symbol: Symbole de la crypto
            timestamp: Date/heure de la transaction (None = prix actuel)

        Returns:
            Prix en USD ou None si erreur
        """
        if timestamp is None or (datetime.now() - timestamp) < timedelta(hours=1):
            # Prix actuel si pas de timestamp ou timestamp très récent
            return self.get_current_price(symbol)
        else:
            # Prix historique
            return self.get_historical_price(symbol, timestamp)

    def get_multiple_current_prices(self, symbols: list) -> Dict[str, float]:
        """
        Récupère les prix actuels de plusieurs cryptos en une seule requête
        Plus efficace que des requêtes individuelles

        Args:
            symbols: Liste de symboles (BTC, ETH, SOL, etc.)

        Returns:
            Dict {symbol: price}
        """
        # Filtrer les symboles déjà en cache
        symbols_to_fetch = []
        result = {}

        for symbol in symbols:
            cache_key = self._get_cache_key(symbol)
            if self._is_cache_valid(cache_key):
                result[symbol] = self.price_cache[cache_key]['price']
                self.stats['cache_hits'] += 1
            else:
                symbols_to_fetch.append(symbol)

        if not symbols_to_fetch:
            return result

        # Mapper vers IDs CoinGecko
        coin_ids = []
        symbol_to_id = {}
        for symbol in symbols_to_fetch:
            coin_id = self.COINGECKO_IDS.get(symbol.upper())
            if coin_id:
                coin_ids.append(coin_id)
                symbol_to_id[coin_id] = symbol.upper()

        if not coin_ids:
            return result

        try:
            self._wait_for_rate_limit()

            # Requête batch
            url = f"{self.base_url}/simple/price"
            params = {
                'ids': ','.join(coin_ids),
                'vs_currencies': 'usd'
            }

            self.logger.debug(f"Fetching batch prices for {len(coin_ids)} coins")
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            self.stats['api_calls'] += 1

            data = response.json()

            # Parser les résultats
            for coin_id, price_data in data.items():
                symbol = symbol_to_id[coin_id]
                price = price_data.get('usd')

                if price:
                    result[symbol] = price

                    # Mettre en cache
                    cache_key = self._get_cache_key(symbol)
                    self.price_cache[cache_key] = {
                        'price': price,
                        'timestamp': datetime.now()
                    }

            self.logger.info(f"Fetched {len(result)} prices successfully")
            return result

        except requests.exceptions.RequestException as e:
            self.stats['errors'] += 1
            self.logger.error(f"Error fetching batch prices: {e}")
            return result

    def calculate_usd_value(self, amount: float, symbol: str,
                           timestamp: Optional[datetime] = None) -> Optional[float]:
        """
        Calcule la valeur USD d'un montant de crypto

        Args:
            amount: Montant de crypto
            symbol: Symbole de la crypto
            timestamp: Date/heure de la transaction (None = prix actuel)

        Returns:
            Valeur en USD ou None si erreur
        """
        price = self.get_price(symbol, timestamp)
        if price is None:
            return None

        return amount * price

    def get_stats(self) -> Dict:
        """Retourne les statistiques d'utilisation"""
        total_requests = self.stats['cache_hits'] + self.stats['cache_misses']
        cache_hit_rate = (self.stats['cache_hits'] / total_requests * 100) if total_requests > 0 else 0

        return {
            **self.stats,
            'cache_size': len(self.price_cache),
            'cache_hit_rate': f"{cache_hit_rate:.1f}%"
        }

    def clear_cache(self):
        """Vide le cache des prix"""
        self.price_cache.clear()
        self.logger.info("Price cache cleared")


# Instance globale
_price_service = None


def get_price_service() -> PriceService:
    """Retourne l'instance globale du service de prix (singleton)"""
    global _price_service
    if _price_service is None:
        _price_service = PriceService()
    return _price_service
