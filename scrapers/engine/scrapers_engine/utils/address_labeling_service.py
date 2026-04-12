"""
Service d'identification des adresses blockchain
Utilise des labels publics gratuits (Etherscan, bases locales, patterns)
"""

import requests
import logging
from typing import Optional, List, Dict
import time
import re


class AddressLabelingService:
    """Service pour identifier les propriétaires d'adresses blockchain"""

    def __init__(self, etherscan_api_key: Optional[str] = None):
        self.logger = logging.getLogger(__name__)
        self.etherscan_api_key = etherscan_api_key

        # Cache des labels
        self.label_cache = {}  # {address: {'owner': str, 'type': str, 'source': str}}

        # Rate limiting Etherscan (5 req/sec gratuit)
        self.last_etherscan_request = 0
        self.etherscan_interval = 0.2  # 200ms = 5 req/sec

        # Stats
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'api_calls': 0,
            'pattern_matches': 0,
            'unknown': 0
        }

        # Charger la base locale d'adresses connues
        self.known_addresses = self._load_known_addresses()

    def _load_known_addresses(self) -> Dict[str, Dict[str, str]]:
        """
        Charge la base locale d'adresses connues
        Format: {address: {'owner': str, 'type': str}}
        """
        return {
            # ==========================================
            # BITCOIN - Exchanges
            # ==========================================

            # Binance
            "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s": {"owner": "binance", "type": "exchange"},
            "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo": {"owner": "binance", "type": "exchange"},
            "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97": {"owner": "binance", "type": "exchange"},

            # Coinbase
            "3Nxwenay9Z8Lc9JBiywExpnEFiLp6Afp8v": {"owner": "coinbase", "type": "exchange"},
            "36n452uGq1x4mK7bfyZR8wgE47AnBb2pzi": {"owner": "coinbase", "type": "exchange"},

            # Kraken
            "3EJcqWvTTVjR4p8zAjF61d5gvXvV7VTNmx": {"owner": "kraken", "type": "exchange"},

            # Bitfinex
            "1Kr6QSydW9bFQG1mXiPNNu6WpJGmUa9i1g": {"owner": "bitfinex", "type": "exchange"},

            # Huobi
            "1HckjUpRGcrrRAtFaaCAUaGjsPx9oYmLaZ": {"owner": "huobi", "type": "exchange"},

            # Bitstamp
            "1DEP8i3QJCsomS4BSMY2RpU1upv62aGvhD": {"owner": "bitstamp", "type": "exchange"},

            # ==========================================
            # ETHEREUM - Exchanges
            # ==========================================

            # Binance
            "0x28C6c06298d514Db089934071355E5743bf21d60": {"owner": "binance", "type": "exchange"},
            "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549": {"owner": "binance", "type": "exchange"},
            "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d": {"owner": "binance", "type": "exchange"},
            "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F": {"owner": "binance", "type": "exchange"},
            "0x9696f59E4d72E237BE84fFD425DCaD154Bf96976": {"owner": "binance", "type": "exchange"},
            "0x4E9ce36E442e55EcD9025B9a6E0D88485d628A67": {"owner": "binance", "type": "exchange"},
            "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8": {"owner": "binance", "type": "exchange"},
            "0xF977814e90dA44bFA03b6295A0616a897441aceC": {"owner": "binance", "type": "exchange"},

            # Coinbase
            "0x503828976D22510aad0201ac7EC88293211D23Da": {"owner": "coinbase", "type": "exchange"},
            "0xddfAbCdc4D8FfC6d5beaf154f18B778f892A0740": {"owner": "coinbase", "type": "exchange"},
            "0x3cD751E6b0078Be393132286c442345e5DC49699": {"owner": "coinbase", "type": "exchange"},
            "0xb5d85CBf7cB3EE0D56b3bB207D5Fc4B82f43F511": {"owner": "coinbase", "type": "exchange"},
            "0xeB2629a2734e272Bcc07BDA959863f316F4bD4Cf": {"owner": "coinbase", "type": "exchange"},

            # Kraken
            "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2": {"owner": "kraken", "type": "exchange"},
            "0x0A869d79a7052C7f1b55a8EbAbbEa3420F0D1E13": {"owner": "kraken", "type": "exchange"},
            "0xE853c56864A2ebe4576a807D26Fdc4A0adA51919": {"owner": "kraken", "type": "exchange"},

            # Bitfinex
            "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb": {"owner": "bitfinex", "type": "exchange"},
            "0x876EabF441B2EE5B5b0554Fd502a8E0600950cFa": {"owner": "bitfinex", "type": "exchange"},

            # Huobi
            "0xab5C66752a9e8167967685F1450532fB96d5d24f": {"owner": "huobi", "type": "exchange"},
            "0x6748F50f686bfbcA6Fe8ad62b22228b87F31ff2b": {"owner": "huobi", "type": "exchange"},

            # FTX (historical)
            "0x2FAF487A4414Fe77e2327F0bf4AE2a264a776AD2": {"owner": "ftx", "type": "exchange"},

            # Crypto.com
            "0x6262998Ced04146fA42253a5C0AF90CA02dfd2A3": {"owner": "crypto.com", "type": "exchange"},
            "0x46340b20830761efd32832A74d7169B29FEB9758": {"owner": "crypto.com", "type": "exchange"},

            # OKX
            "0x236ab2830f8e4Cf8dD0c972dbcbE24d195Cd7aaE": {"owner": "okx", "type": "exchange"},
            "0x98ec059Dc3aDFBdd63429454aeB0C990FBA4A128": {"owner": "okx", "type": "exchange"},

            # Bybit
            "0xF89d7b9c864f589bbF53a82105107622B35EaA40": {"owner": "bybit", "type": "exchange"},

            # Gate.io
            "0x0D0707963952f2fBA59dD06f2b425ace40b492Fe": {"owner": "gate.io", "type": "exchange"},

            # ==========================================
            # ETHEREUM - Smart Contracts / DeFi
            # ==========================================

            # Tether (USDT)
            "0xdAC17F958D2ee523a2206206994597C13D831ec7": {"owner": "tether_usdt", "type": "contract"},

            # USD Coin (USDC)
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": {"owner": "usdc", "type": "contract"},

            # Wrapped Bitcoin (WBTC)
            "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599": {"owner": "wbtc", "type": "contract"},

            # Uniswap V2 Router
            "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D": {"owner": "uniswap_v2_router", "type": "contract"},

            # Uniswap V3 Router
            "0xE592427A0AEce92De3Edee1F18E0157C05861564": {"owner": "uniswap_v3_router", "type": "contract"},

            # SushiSwap Router
            "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F": {"owner": "sushiswap_router", "type": "contract"},

            # 1inch Router
            "0x1111111254fb6c44bAC0beD2854e76F90643097d": {"owner": "1inch_router", "type": "contract"},

            # Curve Finance
            "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7": {"owner": "curve_3pool", "type": "contract"},

            # Aave V2
            "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9": {"owner": "aave_v2_pool", "type": "contract"},

            # Compound
            "0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B": {"owner": "compound_comptroller", "type": "contract"},

            # MakerDAO
            "0x5ef30b9986345249bc32d8928B7ee64DE9435E39": {"owner": "makerdao_vault", "type": "contract"},

            # ==========================================
            # SOLANA - Exchanges
            # ==========================================

            # Binance
            "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": {"owner": "binance", "type": "exchange"},
            "FWHaYAL8S8XgNUSCqRsF7Bxnx4cCxVRDGbWU9yVyJ1bD": {"owner": "binance", "type": "exchange"},

            # FTX (historical)
            "2CAXheYmHe8FVF5gXv1UABU4BL8kkDv3z6JVe4yNHZEb": {"owner": "ftx", "type": "exchange"},

            # Kraken
            "39J1sWHCJsP9RTbC6UD38PKqijvNKcz3k2tcpTLzDEMN": {"owner": "kraken", "type": "exchange"},

            # Coinbase
            "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7mk1": {"owner": "coinbase", "type": "exchange"},
        }

    def _wait_for_etherscan_rate_limit(self):
        """Respecte le rate limit Etherscan (5 req/sec)"""
        elapsed = time.time() - self.last_etherscan_request
        if elapsed < self.etherscan_interval:
            time.sleep(self.etherscan_interval - elapsed)
        self.last_etherscan_request = time.time()

    def _check_known_addresses(self, address: str) -> Optional[Dict[str, str]]:
        """Vérifie si l'adresse est dans la base locale"""
        # Normaliser l'adresse (lowercase pour Ethereum, case-sensitive pour Bitcoin/Solana)
        if address.startswith('0x'):
            # Ethereum - lowercase
            address = address.lower()
        # Bitcoin/Solana - garder tel quel

        return self.known_addresses.get(address)

    def _check_pattern_matching(self, address: str, blockchain: str) -> Optional[Dict[str, str]]:
        """
        Identification par pattern matching
        Certaines adresses suivent des patterns identifiables
        """
        # Ethereum contract address patterns
        if blockchain.lower() == 'ethereum':
            # Smart contracts - souvent pattern 0x + 40 hex
            if re.match(r'^0x[a-fA-F0-9]{40}$', address):
                # Vérifier si c'est un contract connu par pattern
                # (Cette logique peut être étendue)
                pass

        # Bitcoin exchange patterns
        elif blockchain.lower() == 'bitcoin':
            # Les exchanges utilisent souvent des adresses multisig (commencent par 3)
            if address.startswith('3'):
                self.stats['pattern_matches'] += 1
                return {"owner": "unknown_multisig", "type": "likely_exchange"}

            # SegWit exchanges (bc1q...)
            if address.startswith('bc1q'):
                self.stats['pattern_matches'] += 1
                return {"owner": "unknown_segwit", "type": "likely_exchange"}

        return None

    def _query_etherscan_label(self, address: str) -> Optional[Dict[str, str]]:
        """
        Requête Etherscan pour obtenir le label d'une adresse
        Nécessite une clé API Etherscan (gratuite)
        """
        if not self.etherscan_api_key:
            return None

        try:
            self._wait_for_etherscan_rate_limit()

            # Vérifier si c'est un contract
            url = "https://api.etherscan.io/api"
            params = {
                'module': 'contract',
                'action': 'getsourcecode',
                'address': address,
                'apikey': self.etherscan_api_key
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            self.stats['api_calls'] += 1

            data = response.json()

            if data.get('status') == '1' and data.get('result'):
                result = data['result'][0]
                contract_name = result.get('ContractName')

                if contract_name:
                    self.logger.info(f"Etherscan label for {address}: {contract_name}")
                    return {
                        "owner": contract_name.lower().replace(' ', '_'),
                        "type": "contract"
                    }

        except Exception as e:
            self.logger.debug(f"Error querying Etherscan for {address}: {e}")

        return None

    def identify_address(self, address: str, blockchain: str = 'bitcoin') -> Dict[str, str]:
        """
        Identifie le propriétaire d'une adresse blockchain

        Args:
            address: Adresse blockchain
            blockchain: Type de blockchain (bitcoin, ethereum, solana)

        Returns:
            Dict avec {'owner': str, 'type': str, 'source': str}
        """
        # Vérifier le cache
        cache_key = f"{blockchain}:{address}"
        if cache_key in self.label_cache:
            self.stats['cache_hits'] += 1
            return self.label_cache[cache_key]

        self.stats['cache_misses'] += 1

        # 1. Vérifier la base locale
        label = self._check_known_addresses(address)
        if label:
            result = {**label, 'source': 'local_db'}
            self.label_cache[cache_key] = result
            return result

        # 2. Pattern matching
        label = self._check_pattern_matching(address, blockchain)
        if label:
            result = {**label, 'source': 'pattern'}
            self.label_cache[cache_key] = result
            return result

        # 3. Query Etherscan (si Ethereum et API key disponible)
        if blockchain.lower() == 'ethereum' and self.etherscan_api_key:
            label = self._query_etherscan_label(address)
            if label:
                result = {**label, 'source': 'etherscan'}
                self.label_cache[cache_key] = result
                return result

        # 4. Unknown
        self.stats['unknown'] += 1
        result = {
            'owner': 'unknown',
            'type': 'unknown',
            'source': 'none'
        }
        self.label_cache[cache_key] = result
        return result

    def classify_transaction_type(self, from_label: Dict[str, str],
                                  to_label: Dict[str, str]) -> str:
        """
        Classifie le type de transaction selon les labels des adresses

        Args:
            from_label: Label de l'adresse source
            to_label: Label de l'adresse destination

        Returns:
            Type de transaction (exchange_to_wallet, wallet_to_exchange, etc.)
        """
        from_type = from_label.get('type', 'unknown')
        to_type = to_label.get('type', 'unknown')

        # Mapping des types
        if from_type == 'exchange' and to_type in ['unknown', 'wallet']:
            return 'exchange_to_wallet'
        elif from_type in ['unknown', 'wallet'] and to_type == 'exchange':
            return 'wallet_to_exchange'
        elif from_type == 'exchange' and to_type == 'exchange':
            return 'exchange_to_exchange'
        elif from_type == 'contract' or to_type == 'contract':
            return 'contract_interaction'
        else:
            return 'wallet_to_wallet'

    def get_stats(self) -> Dict:
        """Retourne les statistiques d'utilisation"""
        total_lookups = self.stats['cache_hits'] + self.stats['cache_misses']
        cache_hit_rate = (self.stats['cache_hits'] / total_lookups * 100) if total_lookups > 0 else 0

        return {
            **self.stats,
            'cache_size': len(self.label_cache),
            'known_addresses_count': len(self.known_addresses),
            'cache_hit_rate': f"{cache_hit_rate:.1f}%"
        }


# Instance globale
_labeling_service = None


def get_labeling_service(etherscan_api_key: Optional[str] = None) -> AddressLabelingService:
    """Retourne l'instance globale du service de labeling (singleton)"""
    global _labeling_service
    if _labeling_service is None:
        _labeling_service = AddressLabelingService(etherscan_api_key)
    return _labeling_service
