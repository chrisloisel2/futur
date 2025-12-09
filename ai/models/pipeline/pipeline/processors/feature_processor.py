"""
Feature Processor - Transforme les données brutes en features pour l'IA
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
from collections import deque

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FeatureProcessor:
    """Processeur de features temps réel."""

    def __init__(self, window_size: int = 100, buffer_size: int = 1000):
        """
        Args:
            window_size: Taille de la fenêtre pour les calculs techniques
            buffer_size: Taille max du buffer de données
        """
        self.window_size = window_size
        self.buffer_size = buffer_size

        # Buffers pour chaque symbole
        self.price_buffers = {}  # {symbol: deque([prices])}
        self.volume_buffers = {}
        self.timestamp_buffers = {}
        self.trade_count_buffers = {}

    def add_trade(self, symbol: str, price: float, volume: float, timestamp: int):
        """Ajouter un trade au buffer."""
        # Initialiser les buffers si nécessaire
        if symbol not in self.price_buffers:
            self.price_buffers[symbol] = deque(maxlen=self.buffer_size)
            self.volume_buffers[symbol] = deque(maxlen=self.buffer_size)
            self.timestamp_buffers[symbol] = deque(maxlen=self.buffer_size)
            self.trade_count_buffers[symbol] = deque(maxlen=self.buffer_size)

        # Ajouter les données
        self.price_buffers[symbol].append(price)
        self.volume_buffers[symbol].append(volume)
        self.timestamp_buffers[symbol].append(timestamp)

        # Compter les trades par minute
        current_minute = timestamp // 60000
        if self.trade_count_buffers[symbol] and \
           self.trade_count_buffers[symbol][-1][0] == current_minute:
            # Même minute, incrémenter le compteur
            self.trade_count_buffers[symbol][-1] = (current_minute,
                                                     self.trade_count_buffers[symbol][-1][1] + 1)
        else:
            # Nouvelle minute
            self.trade_count_buffers[symbol].append((current_minute, 1))

    def calculate_features(self, symbol: str) -> Optional[Dict]:
        """
        Calculer les features techniques pour un symbole.

        Returns:
            Dict avec les features ou None si pas assez de données
        """
        if symbol not in self.price_buffers:
            return None

        prices = np.array(list(self.price_buffers[symbol]))
        volumes = np.array(list(self.volume_buffers[symbol]))

        if len(prices) < self.window_size:
            return None

        # Prix récents
        recent_prices = prices[-self.window_size:]
        recent_volumes = volumes[-self.window_size:]

        features = {}

        # ============ PRIX FEATURES ============
        current_price = prices[-1]
        features['price'] = current_price

        # Returns
        features['return_1'] = (prices[-1] - prices[-2]) / prices[-2] if len(prices) > 1 else 0
        features['return_5'] = (prices[-1] - prices[-6]) / prices[-6] if len(prices) > 5 else 0
        features['return_10'] = (prices[-1] - prices[-11]) / prices[-11] if len(prices) > 10 else 0

        # Moving Averages
        features['sma_5'] = np.mean(recent_prices[-5:]) if len(recent_prices) >= 5 else current_price
        features['sma_10'] = np.mean(recent_prices[-10:]) if len(recent_prices) >= 10 else current_price
        features['sma_20'] = np.mean(recent_prices[-20:]) if len(recent_prices) >= 20 else current_price
        features['sma_50'] = np.mean(recent_prices[-50:]) if len(recent_prices) >= 50 else current_price

        # EMA (Exponential Moving Average)
        features['ema_12'] = self._calculate_ema(recent_prices, 12)
        features['ema_26'] = self._calculate_ema(recent_prices, 26)

        # MACD
        features['macd'] = features['ema_12'] - features['ema_26']
        features['macd_signal'] = self._calculate_ema([features['macd']] * 9, 9)
        features['macd_histogram'] = features['macd'] - features['macd_signal']

        # Bollinger Bands
        sma_20 = features['sma_20']
        std_20 = np.std(recent_prices[-20:]) if len(recent_prices) >= 20 else 0
        features['bb_upper'] = sma_20 + (2 * std_20)
        features['bb_lower'] = sma_20 - (2 * std_20)
        features['bb_position'] = (current_price - features['bb_lower']) / (features['bb_upper'] - features['bb_lower']) if std_20 > 0 else 0.5

        # RSI (Relative Strength Index)
        features['rsi'] = self._calculate_rsi(recent_prices, period=14)

        # ============ VOLUME FEATURES ============
        features['volume'] = volumes[-1]
        features['volume_sma_20'] = np.mean(recent_volumes[-20:]) if len(recent_volumes) >= 20 else volumes[-1]
        features['volume_ratio'] = volumes[-1] / features['volume_sma_20'] if features['volume_sma_20'] > 0 else 1

        # VWAP (Volume Weighted Average Price)
        features['vwap'] = np.sum(recent_prices * recent_volumes) / np.sum(recent_volumes) if np.sum(recent_volumes) > 0 else current_price

        # ============ VOLATILITÉ ============
        features['volatility'] = np.std(recent_prices) / np.mean(recent_prices) if np.mean(recent_prices) > 0 else 0
        features['atr'] = self._calculate_atr(recent_prices, period=14)

        # ============ MOMENTUM ============
        features['momentum'] = current_price - recent_prices[-10] if len(recent_prices) >= 10 else 0
        features['rate_of_change'] = (current_price - recent_prices[-10]) / recent_prices[-10] if len(recent_prices) >= 10 and recent_prices[-10] > 0 else 0

        # ============ MARKET MICROSTRUCTURE ============
        if len(self.trade_count_buffers[symbol]) > 0:
            features['trades_per_minute'] = self.trade_count_buffers[symbol][-1][1]
            features['avg_trades_per_minute'] = np.mean([t[1] for t in list(self.trade_count_buffers[symbol])[-20:]])
        else:
            features['trades_per_minute'] = 0
            features['avg_trades_per_minute'] = 0

        # ============ TIMESTAMP ============
        features['timestamp'] = self.timestamp_buffers[symbol][-1]
        features['hour'] = datetime.fromtimestamp(features['timestamp'] / 1000).hour
        features['day_of_week'] = datetime.fromtimestamp(features['timestamp'] / 1000).weekday()

        return features

    def _calculate_ema(self, prices: np.ndarray, period: int) -> float:
        """Calculer l'EMA."""
        if len(prices) < period:
            return np.mean(prices)

        multiplier = 2 / (period + 1)
        ema = np.mean(prices[:period])

        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        return ema

    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculer le RSI."""
        if len(prices) < period + 1:
            return 50

        deltas = np.diff(prices[-period-1:])
        gains = deltas[deltas > 0]
        losses = -deltas[deltas < 0]

        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_atr(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculer l'ATR (Average True Range)."""
        if len(prices) < period + 1:
            return 0

        # Simplification: utiliser les high-low comme approximation
        high_low = np.abs(np.diff(prices[-period-1:]))
        atr = np.mean(high_low)

        return atr

    def get_feature_vector(self, symbol: str) -> Optional[np.ndarray]:
        """
        Obtenir un vecteur de features normalisé pour l'IA.

        Returns:
            Numpy array des features normalisées
        """
        features = self.calculate_features(symbol)

        if features is None:
            return None

        # Sélectionner les features importantes pour l'IA
        feature_keys = [
            'return_1', 'return_5', 'return_10',
            'sma_5', 'sma_10', 'sma_20', 'sma_50',
            'macd', 'macd_signal', 'macd_histogram',
            'bb_position', 'rsi',
            'volume_ratio', 'volatility', 'atr',
            'momentum', 'rate_of_change',
            'trades_per_minute', 'hour', 'day_of_week'
        ]

        # Extraire et normaliser
        feature_vector = []
        for key in feature_keys:
            value = features.get(key, 0)

            # Normalisation simple
            if 'return' in key or 'ratio' in key or 'roc' in key:
                # Limiter les returns extrêmes
                value = np.clip(value, -0.1, 0.1) * 10
            elif key == 'rsi':
                # RSI déjà entre 0-100, normaliser à 0-1
                value = value / 100
            elif key == 'bb_position':
                # Déjà entre 0-1
                pass
            elif key in ['hour', 'day_of_week']:
                # Normaliser temporalité
                value = value / (24 if key == 'hour' else 7)

            feature_vector.append(value)

        return np.array(feature_vector, dtype=np.float32)

    def get_buffer_stats(self, symbol: str) -> Dict:
        """Obtenir les stats du buffer."""
        if symbol not in self.price_buffers:
            return {}

        return {
            'symbol': symbol,
            'buffer_size': len(self.price_buffers[symbol]),
            'current_price': self.price_buffers[symbol][-1] if self.price_buffers[symbol] else None,
            'timestamp': self.timestamp_buffers[symbol][-1] if self.timestamp_buffers[symbol] else None
        }


# Test du processeur
if __name__ == "__main__":
    processor = FeatureProcessor(window_size=100)

    # Simuler des trades
    symbol = "AAPL"
    base_price = 150.0
    timestamp = int(datetime.now().timestamp() * 1000)

    for i in range(200):
        # Simuler variation de prix
        price = base_price + np.random.randn() * 2
        volume = np.random.randint(100, 1000)

        processor.add_trade(symbol, price, volume, timestamp + i * 1000)

    # Calculer les features
    features = processor.calculate_features(symbol)
    print("\n📊 Features calculées:")
    for key, value in features.items():
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    # Obtenir le vecteur de features
    feature_vector = processor.get_feature_vector(symbol)
    print(f"\n🔢 Feature vector shape: {feature_vector.shape}")
    print(f"Feature vector: {feature_vector}")

    # Stats du buffer
    stats = processor.get_buffer_stats(symbol)
    print(f"\n📈 Buffer stats: {stats}")
