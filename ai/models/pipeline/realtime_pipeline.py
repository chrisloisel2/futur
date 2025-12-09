"""
Real-Time Trading Pipeline - Connecte les collecteurs, processeurs, modèles et API
"""
import asyncio
import sys
from pathlib import Path

# Ajouter le dossier parent au path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
import logging
from collections import defaultdict

# Imports locaux
from pipeline.collectors.alpaca_collector import AlpacaCollector, AlpacaCryptoCollector
from pipeline.collectors.finnhub_collector import FinnhubCollector
from pipeline.collectors.fmp_collector import FMPCollector
from pipeline.collectors.polygon_collector import PolygonCollector
from pipeline.collectors.yahoo_finance_collector import YahooFinanceCollector
from pipeline.collectors.generic_public_collector import GenericPublicCollector
from pipeline.processors.feature_processor import FeatureProcessor

# Storage
import pandas as pd
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RealTimePipeline:
    """Pipeline temps réel pour le trading."""

    def __init__(self, config: Dict):
        """
        Args:
            config: Configuration de la pipeline
        """
        self.config = config
        self.feature_processor = FeatureProcessor(
            window_size=config.get('window_size', 100),
            buffer_size=config.get('buffer_size', 1000)
        )

        # Collectors
        self.collectors = []
        self.active_symbols = set()

        # Predictions cache
        self.predictions = defaultdict(lambda: {
            'signal': 'HOLD',
            'confidence': 0.0,
            'price': 0.0,
            'timestamp': 0
        })

        # Storage
        self.storage_path = Path(config.get('storage_path', 'datasets/realtime'))
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Buffers pour agrégation
        self.trade_buffers = defaultdict(list)
        self.last_save_time = datetime.now()

        # Collector heartbeat/status
        self.collector_status = {}

    def _mark_status(self, name: str, status: str, symbols: int = 0, error: str = None):
        """Mettre à jour le statut d'un collecteur."""
        self.collector_status[name] = {
            'status': status,
            'symbols': symbols,
            'last_message': None,
            'messages': 0
        }
        if error:
            self.collector_status[name]['error'] = error

        # Statistiques
        self.stats = {
            'trades_processed': 0,
            'predictions_made': 0,
            'symbols_tracked': 0,
            'start_time': datetime.now()
        }

    async def initialize_collectors(self):
        """Initialiser tous les collectors configurés."""
        collectors_config = self.config.get('collectors', {})

        # Pré-marquer les collecteurs désactivés pour les rendre visibles côté UI
        for name, cfg in collectors_config.items():
            if not cfg.get('enabled', False):
                symbols = len(cfg.get('symbols', [])) if isinstance(cfg, dict) else 0
                self._mark_status(name, 'disabled', symbols=symbols)

        # Alpaca
        if 'alpaca' in collectors_config and collectors_config['alpaca'].get('enabled'):
            alpaca_config = collectors_config['alpaca']
            if not alpaca_config.get('api_key') or not alpaca_config.get('api_secret') or 'YOUR_' in alpaca_config.get('api_key', ''):
                self._mark_status('alpaca', 'error', error='API key/secret manquants', symbols=len(alpaca_config.get('symbols', [])))
                logger.warning("⚠️ Alpaca non initialisé: API key/secret manquants")
            else:
                collector = AlpacaCollector(
                    api_key=alpaca_config['api_key'],
                    api_secret=alpaca_config['api_secret'],
                    feed=alpaca_config.get('feed', 'iex')
                )

                if await collector.connect():
                    symbols = alpaca_config.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols, ["trades", "quotes"])
                        self.active_symbols.update(symbols)
                    self.collectors.append(('alpaca', collector))
                    self._mark_status('alpaca', 'connected', symbols=len(symbols))
                logger.info(f"✅ Alpaca collector initialized with {len(symbols)} symbols")

        # Alpaca Crypto
        if 'alpaca_crypto' in collectors_config and collectors_config['alpaca_crypto'].get('enabled'):
            crypto_config = collectors_config['alpaca_crypto']
            if not crypto_config.get('api_key') or not crypto_config.get('api_secret') or 'YOUR_' in crypto_config.get('api_key', ''):
                self._mark_status('alpaca_crypto', 'error', error='API key/secret manquants', symbols=len(crypto_config.get('symbols', [])))
                logger.warning("⚠️ Alpaca Crypto non initialisé: API key/secret manquants")
            else:
                collector = AlpacaCryptoCollector(
                    api_key=crypto_config['api_key'],
                    api_secret=crypto_config['api_secret']
                )

                if await collector.connect():
                    symbols = crypto_config.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols)
                        self.active_symbols.update(symbols)
                    self.collectors.append(('alpaca_crypto', collector))
                    self._mark_status('alpaca_crypto', 'connected', symbols=len(symbols))
                logger.info(f"✅ Alpaca Crypto collector initialized with {len(symbols)} symbols")

        # Finnhub
        if 'finnhub' in collectors_config and collectors_config['finnhub'].get('enabled'):
            finnhub_config = collectors_config['finnhub']
            api_key = finnhub_config.get('api_key')
            if not api_key or 'YOUR_' in api_key or 'PUT_' in api_key:
                self._mark_status('finnhub', 'error', error='API key manquante', symbols=len(finnhub_config.get('stocks', [])))
                logger.warning("⚠️ Finnhub non initialisé: API key manquante")
            else:
                collector = FinnhubCollector(api_key=api_key)

                if await collector.connect():
                    # Stocks
                    if 'stocks' in finnhub_config:
                        stocks = finnhub_config['stocks']
                        await collector.subscribe_stocks(stocks)
                        self.active_symbols.update(stocks)

                    # Crypto
                    if 'crypto' in finnhub_config:
                        crypto = finnhub_config['crypto']
                        exchange = finnhub_config.get('crypto_exchange', 'BINANCE')
                        await collector.subscribe_crypto(crypto, exchange=exchange)
                        self.active_symbols.update([f"{exchange}:{s}" for s in crypto])

                    # Forex
                    if 'forex' in finnhub_config:
                        forex = finnhub_config['forex']
                        await collector.subscribe_forex(forex)
                        self.active_symbols.update(forex)

                    self.collectors.append(('finnhub', collector))
                    self._mark_status('finnhub', 'connected', symbols=len(self.active_symbols))
                    logger.info(f"✅ Finnhub collector initialized")

        # FMP
        if 'fmp' in collectors_config and collectors_config['fmp'].get('enabled'):
            fmp_config = collectors_config['fmp']
            api_key = fmp_config.get('api_key', '')
            if not api_key or 'PUT_' in api_key or 'YOUR_' in api_key:
                self._mark_status('fmp', 'error', error='API key manquante', symbols=len(fmp_config.get('symbols', [])))
                logger.warning("⚠️ FMP non initialisé: API key manquante")
            else:
                collector = FMPCollector(
                    api_key=api_key,
                    ws_url=fmp_config.get('ws_url', 'wss://ws.fmpcloud.io')
                )

                if await collector.connect():
                    symbols = fmp_config.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols)
                        self.active_symbols.update(symbols)
                    self.collectors.append(('fmp', collector))
                    self._mark_status('fmp', 'connected', symbols=len(symbols))
                    logger.info(f"✅ FMP collector initialized with {len(symbols)} symbols")

        # Polygon
        if 'polygon' in collectors_config and collectors_config['polygon'].get('enabled'):
            poly_config = collectors_config['polygon']
            api_key = poly_config.get('api_key', '')
            if not api_key or 'PUT_' in api_key or 'YOUR_' in api_key:
                self._mark_status('polygon', 'error', error='API key manquante', symbols=len(poly_config.get('symbols', [])))
                logger.warning("⚠️ Polygon non initialisé: API key manquante")
            else:
                collector = PolygonCollector(
                    api_key=api_key,
                    market=poly_config.get('market', 'stocks'),
                    channels=poly_config.get('channels', ['T'])
                )

                if await collector.connect():
                    symbols = poly_config.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols, channels=poly_config.get('channels'))
                        self.active_symbols.update(symbols)
                    self.collectors.append(('polygon', collector))
                    self._mark_status('polygon', 'connected', symbols=len(symbols))
                    logger.info(f"✅ Polygon collector initialized with {len(symbols)} symbols")

        # Yahoo Finance (non-officiel)
        if 'yahoo' in collectors_config and collectors_config['yahoo'].get('enabled'):
            yahoo_config = collectors_config['yahoo']
            collector = YahooFinanceCollector(
                ws_url=yahoo_config.get('ws_url', 'wss://streamer.finance.yahoo.com/?version=2')
            )

            if await collector.connect():
                symbols = yahoo_config.get('symbols', [])
                if symbols:
                    await collector.subscribe(symbols)
                    self.active_symbols.update(symbols)
                self.collectors.append(('yahoo_finance', collector))
                self._mark_status('yahoo_finance', 'connected', symbols=len(symbols))
                logger.info(f"✅ Yahoo Finance collector initialized with {len(symbols)} symbols")

        # Massive / autres flux publics configurés
        if 'massive' in collectors_config and collectors_config['massive'].get('enabled'):
            massive_config = collectors_config['massive']
            ws_url = massive_config.get('ws_url', '')
            if not ws_url:
                self._mark_status('massive', 'error', error='ws_url manquant', symbols=len(massive_config.get('symbols', [])))
                logger.warning("⚠️ Massive non initialisé: ws_url manquant")
            else:
                collector = GenericPublicCollector(
                    name="Massive",
                    ws_url=ws_url,
                    api_key=massive_config.get('api_key'),
                    headers=massive_config.get('headers'),
                    auth_message=massive_config.get('auth_message'),
                    subscribe_template=massive_config.get('subscribe_message'),
                    symbol_field=massive_config.get('symbol_field', 'symbol'),
                    price_field=massive_config.get('price_field', 'price'),
                    volume_field=massive_config.get('volume_field', 'volume'),
                    timestamp_field=massive_config.get('timestamp_field', 'timestamp')
                )

                if await collector.connect():
                    symbols = massive_config.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols)
                        self.active_symbols.update(symbols)
                    self.collectors.append(('massive', collector))
                    self._mark_status('massive', 'connected', symbols=len(symbols))
                    logger.info(f"✅ Massive collector initialized with {len(symbols)} symbols")

        # Community / open-source feeds (liste de flux génériques)
        if 'community' in collectors_config:
            for feed in collectors_config['community'].get('feeds', []):
                if not feed.get('enabled'):
                    continue

                if not feed.get('ws_url'):
                    self._mark_status(feed.get('name', 'community'), 'error', error='ws_url manquant', symbols=len(feed.get('symbols', [])))
                    logger.warning(f"⚠️ Community feed {feed.get('name', 'community')} non initialisé: ws_url manquant")
                    continue

                collector = GenericPublicCollector(
                    name=feed.get('name', 'community'),
                    ws_url=feed.get('ws_url', ''),
                    api_key=feed.get('api_key'),
                    headers=feed.get('headers'),
                    auth_message=feed.get('auth_message'),
                    subscribe_template=feed.get('subscribe_message'),
                    symbol_field=feed.get('symbol_field', 'symbol'),
                    price_field=feed.get('price_field', 'price'),
                    volume_field=feed.get('volume_field', 'volume'),
                    timestamp_field=feed.get('timestamp_field', 'timestamp')
                )

                if await collector.connect():
                    symbols = feed.get('symbols', [])
                    if symbols:
                        await collector.subscribe(symbols)
                        self.active_symbols.update(symbols)
                    self.collectors.append((feed.get('name', 'community'), collector))
                    self._mark_status(feed.get('name', 'community'), 'connected', symbols=len(symbols))
                    logger.info(f"✅ Community feed '{feed.get('name', 'community')}' initialized with {len(symbols)} symbols")

        self.stats['symbols_tracked'] = len(self.active_symbols)
        logger.info(f"🚀 Pipeline initialized with {len(self.collectors)} collectors tracking {len(self.active_symbols)} symbols")

    async def process_trade_data(self, data: Dict):
        """Traiter les données de trade."""
        try:
            symbol = data.get('symbol')
            price = data.get('price')
            volume = data.get('volume', data.get('size', 0))
            timestamp = data.get('timestamp')

            if not all([symbol, price, timestamp]):
                return

            # Ajouter au feature processor
            self.feature_processor.add_trade(symbol, price, volume, timestamp)

            # Ajouter au buffer de storage
            self.trade_buffers[symbol].append({
                'timestamp': timestamp,
                'price': price,
                'volume': volume,
                'type': data.get('type', 'trade')
            })

            # Calculer les features
            features = self.feature_processor.calculate_features(symbol)

            if features:
                # Générer une prédiction
                prediction = await self.generate_prediction(symbol, features)

                if prediction:
                    self.predictions[symbol] = prediction
                    self.stats['predictions_made'] += 1

                    # Log des signaux intéressants
                    if prediction['signal'] != 'HOLD' and prediction['confidence'] > 0.7:
                        logger.info(
                            f"🎯 SIGNAL: {symbol} - {prediction['signal']} "
                            f"(Conf: {prediction['confidence']:.2%}, Price: ${price:.2f})"
                        )

            self.stats['trades_processed'] += 1

            # Sauvegarder périodiquement
            if (datetime.now() - self.last_save_time).seconds > 300:  # Toutes les 5 min
                await self.save_data()

        except Exception as e:
            logger.error(f"❌ Error processing trade data: {e}")

    async def _run_collector_stream(self, name: str, collector):
        """Wrapper pour suivre les heartbeats des collectors."""
        # S'assurer que le collector est référencé
        self.collector_status.setdefault(name, {
            'status': 'connected',
            'symbols': 0,
            'last_message': None,
            'messages': 0
        })

        try:
            async def tracked_callback(message: Dict):
                status = self.collector_status.get(name, {})
                status['last_message'] = datetime.utcnow().isoformat()
                status['messages'] = status.get('messages', 0) + 1
                self.collector_status[name] = status
                await self.process_trade_data(message)

            await collector.stream(tracked_callback)

            # Stream terminé
            if name in self.collector_status:
                self.collector_status[name]['status'] = 'stopped'
        except Exception as e:
            logger.error(f"❌ Collector {name} stream error: {e}")
            if name in self.collector_status:
                self.collector_status[name]['status'] = 'error'
                self.collector_status[name]['error'] = str(e)

    async def generate_prediction(self, symbol: str, features: Dict) -> Optional[Dict]:
        """
        Générer une prédiction de trading.

        Pour l'instant, utilise une stratégie simple. À remplacer par un modèle ML.
        """
        try:
            # Stratégie simple basée sur les indicateurs techniques
            rsi = features.get('rsi', 50)
            macd = features.get('macd', 0)
            bb_position = features.get('bb_position', 0.5)
            volume_ratio = features.get('volume_ratio', 1)

            # Signaux
            signal = 'HOLD'
            confidence = 0.0

            # Signal BULLISH
            if rsi < 30 and macd > 0 and bb_position < 0.2 and volume_ratio > 1.2:
                signal = 'BUY'
                confidence = 0.8
            elif rsi < 40 and macd > 0 and bb_position < 0.3:
                signal = 'BUY'
                confidence = 0.6

            # Signal BEARISH
            elif rsi > 70 and macd < 0 and bb_position > 0.8 and volume_ratio > 1.2:
                signal = 'SELL'
                confidence = 0.8
            elif rsi > 60 and macd < 0 and bb_position > 0.7:
                signal = 'SELL'
                confidence = 0.6

            return {
                'symbol': symbol,
                'signal': signal,
                'confidence': confidence,
                'price': features.get('price', 0),
                'timestamp': features.get('timestamp', 0),
                'indicators': {
                    'rsi': rsi,
                    'macd': macd,
                    'bb_position': bb_position,
                    'volume_ratio': volume_ratio
                }
            }

        except Exception as e:
            logger.error(f"❌ Error generating prediction: {e}")
            return None

    async def save_data(self):
        """Sauvegarder les données collectées."""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

            for symbol, trades in self.trade_buffers.items():
                if not trades:
                    continue

                df = pd.DataFrame(trades)
                filename = self.storage_path / f"{symbol.replace('/', '_')}_{timestamp}.parquet"
                df.to_parquet(filename)

                logger.info(f"💾 Saved {len(trades)} trades for {symbol}")

            # Vider les buffers
            self.trade_buffers.clear()
            self.last_save_time = datetime.now()

        except Exception as e:
            logger.error(f"❌ Error saving data: {e}")

    async def run(self):
        """Lancer la pipeline."""
        logger.info("🚀 Starting Real-Time Trading Pipeline...")

        # Initialiser les collectors
        await self.initialize_collectors()

        if not self.collectors:
            logger.error("❌ No collectors initialized. Check your configuration.")
            return

        # Créer les tasks pour chaque collector
        tasks = []
        for name, collector in self.collectors:
            task = asyncio.create_task(
                self._run_collector_stream(name, collector)
            )
            tasks.append(task)
            logger.info(f"📡 Streaming from {name}...")

        # Task pour afficher les stats
        stats_task = asyncio.create_task(self.print_stats())
        tasks.append(stats_task)

        try:
            # Attendre que toutes les tasks se terminent
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("\n⏹️  Stopping pipeline...")
            await self.save_data()
            await self.cleanup()
        except Exception as e:
            logger.error(f"❌ Pipeline error: {e}")
            await self.cleanup()

    async def print_stats(self):
        """Afficher les statistiques périodiquement."""
        while True:
            await asyncio.sleep(60)  # Toutes les 60 secondes

            runtime = (datetime.now() - self.stats['start_time']).seconds
            trades_per_sec = self.stats['trades_processed'] / runtime if runtime > 0 else 0

            logger.info(
                f"\n📊 PIPELINE STATS:\n"
                f"  ⏱️  Runtime: {runtime // 60}m {runtime % 60}s\n"
                f"  📈 Trades processed: {self.stats['trades_processed']} ({trades_per_sec:.2f}/s)\n"
                f"  🎯 Predictions made: {self.stats['predictions_made']}\n"
                f"  📡 Symbols tracked: {self.stats['symbols_tracked']}\n"
            )

    async def cleanup(self):
        """Nettoyer les ressources."""
        logger.info("🧹 Cleaning up...")

        for name, collector in self.collectors:
            await collector.close()
            if name in self.collector_status:
                self.collector_status[name]['status'] = 'closed'

        logger.info("✅ Cleanup complete")

    def get_predictions(self) -> Dict:
        """Obtenir les prédictions actuelles."""
        return dict(self.predictions)

    def get_stats(self) -> Dict:
        """Obtenir les statistiques."""
        return self.stats.copy()

    def get_collector_status(self) -> Dict:
        """Obtenir l'état des collecteurs."""
        return self.collector_status.copy()


# Configuration exemple
DEFAULT_CONFIG = {
    'window_size': 100,
    'buffer_size': 1000,
    'storage_path': 'datasets/realtime',
    'collectors': {
        'finnhub': {
            'enabled': True,
            'api_key': 'YOUR_FINNHUB_API_KEY',  # Gratuit sur finnhub.io
            'stocks': ['AAPL', 'TSLA', 'MSFT', 'GOOGL', 'AMZN'],
            'crypto': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
            'crypto_exchange': 'BINANCE',
            'forex': ['OANDA:EUR_USD', 'OANDA:GBP_USD']
        },
        'alpaca': {
            'enabled': False,  # Nécessite compte Alpaca
            'api_key': 'YOUR_ALPACA_API_KEY',
            'api_secret': 'YOUR_ALPACA_API_SECRET',
            'feed': 'iex',
            'symbols': ['AAPL', 'TSLA', 'MSFT']
        },
        'alpaca_crypto': {
            'enabled': False,  # Nécessite compte Alpaca
            'api_key': 'YOUR_ALPACA_API_KEY',
            'api_secret': 'YOUR_ALPACA_API_SECRET',
            'symbols': ['BTC/USD', 'ETH/USD']
        },
        'fmp': {
            'enabled': False,
            'api_key': 'YOUR_FMP_API_KEY',
            'ws_url': 'wss://ws.fmpcloud.io',
            'symbols': ['AAPL', 'MSFT']
        },
        'polygon': {
            'enabled': False,
            'api_key': 'YOUR_POLYGON_API_KEY',
            'market': 'stocks',
            'channels': ['T'],
            'symbols': ['AAPL', 'TSLA']
        },
        'yahoo': {
            'enabled': False,
            'ws_url': 'wss://streamer.finance.yahoo.com/?version=2',
            'symbols': ['AAPL', 'TSLA', 'MSFT']
        },
        'massive': {
            'enabled': False,
            'ws_url': 'wss://your-massive-ws-endpoint',
            'api_key': 'YOUR_MASSIVE_API_KEY',
            'symbols': ['AAPL', 'TSLA'],
            'subscribe_message': {'action': 'subscribe', 'symbols': []}
        },
        'community': {
            'feeds': [
                {
                    'name': 'open_source_finance',
                    'enabled': False,
                    'ws_url': 'wss://streamer.finance.yahoo.com/?version=2',
                    'symbols': ['AAPL', 'MSFT'],
                    'subscribe_message': {'subscribe': []}
                }
            ]
        }
    }
}


async def main():
    """Point d'entrée principal."""
    # Charger la configuration
    config = DEFAULT_CONFIG

    # Créer et lancer la pipeline
    pipeline = RealTimePipeline(config)
    await pipeline.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Bye!")
