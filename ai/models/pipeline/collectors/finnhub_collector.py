"""
Finnhub WebSocket Collector - Flux temps réel pour stocks, forex, crypto
"""
import asyncio
import json
import websockets
from typing import Callable, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FinnhubCollector:
    """Collecteur WebSocket pour Finnhub."""

    def __init__(self, api_key: str):
        """
        Args:
            api_key: Clé API Finnhub (gratuite sur finnhub.io)
        """
        self.api_key = api_key
        self.ws_url = f"wss://ws.finnhub.io?token={api_key}"
        self.ws = None
        self.subscribed_symbols = set()

    async def connect(self):
        """Établir la connexion WebSocket."""
        try:
            self.ws = await websockets.connect(self.ws_url)
            logger.info("✅ Finnhub WebSocket connected")
            return True
        except Exception as e:
            logger.error(f"❌ Finnhub connection error: {e}")
            return False

    async def subscribe_stocks(self, symbols: List[str]):
        """
        S'abonner aux actions US.

        Args:
            symbols: Liste des symboles (ex: ["AAPL", "TSLA"])
        """
        for symbol in symbols:
            subscribe_msg = {
                "type": "subscribe",
                "symbol": symbol
            }
            await self.ws.send(json.dumps(subscribe_msg))
            self.subscribed_symbols.add(symbol)

        logger.info(f"📊 Finnhub subscribed to stocks: {symbols}")

    async def subscribe_forex(self, pairs: List[str]):
        """
        S'abonner au forex.

        Args:
            pairs: Liste des paires (ex: ["OANDA:EUR_USD", "OANDA:GBP_USD"])
        """
        for pair in pairs:
            subscribe_msg = {
                # Finnhub attend toujours "subscribe" même pour forex
                "type": "subscribe",
                "symbol": pair
            }
            await self.ws.send(json.dumps(subscribe_msg))
            self.subscribed_symbols.add(pair)

        logger.info(f"💱 Finnhub subscribed to forex: {pairs}")

    async def subscribe_crypto(self, symbols: List[str], exchange: str = "BINANCE"):
        """
        S'abonner aux cryptos.

        Args:
            symbols: Liste des symboles (ex: ["BTCUSDT", "ETHUSDT"])
            exchange: Exchange (BINANCE, COINBASE, etc.)
        """
        for symbol in symbols:
            full_symbol = f"{exchange}:{symbol}"
            subscribe_msg = {
                # Finnhub attend toujours "subscribe" pour tous les marchés
                "type": "subscribe",
                "symbol": full_symbol
            }
            await self.ws.send(json.dumps(subscribe_msg))
            self.subscribed_symbols.add(full_symbol)

        logger.info(f"🪙 Finnhub subscribed to crypto: {symbols} on {exchange}")

    async def stream(self, callback: Callable):
        """
        Stream des données en continu.

        Args:
            callback: Fonction appelée pour chaque message reçu
        """
        try:
            async for message in self.ws:
                data = json.loads(message)

                # Message type: trade
                if data.get("type") == "trade":
                    for trade in data.get("data", []):
                        await callback({
                            "type": "trade",
                            "symbol": trade.get("s"),
                            "price": trade.get("p"),
                            "volume": trade.get("v"),
                            "timestamp": trade.get("t"),
                            "conditions": trade.get("c", [])
                        })

                # Message type: ping (heartbeat)
                elif data.get("type") == "ping":
                    # Répondre au ping
                    pong_msg = {"type": "pong"}
                    await self.ws.send(json.dumps(pong_msg))

                # Message type: error
                elif data.get("type") == "error":
                    logger.error(f"❌ Finnhub error: {data.get('msg')}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ Finnhub WebSocket connection closed")
        except Exception as e:
            logger.error(f"❌ Finnhub stream error: {e}")

    async def unsubscribe(self, symbol: str):
        """Désabonner d'un symbole."""
        unsubscribe_msg = {
            "type": "unsubscribe",
            "symbol": symbol
        }
        await self.ws.send(json.dumps(unsubscribe_msg))
        self.subscribed_symbols.discard(symbol)
        logger.info(f"🔕 Unsubscribed from {symbol}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 Finnhub WebSocket closed")


# Exemple d'utilisation
async def example_callback(data):
    """Fonction callback exemple."""
    print(f"📨 {data['type']}: {data.get('symbol')} @ ${data.get('price')} | Vol: {data.get('volume')}")


async def main():
    """Test du collecteur Finnhub."""
    # Remplacer par votre vraie clé API
    API_KEY = "YOUR_FINNHUB_API_KEY"

    collector = FinnhubCollector(API_KEY)

    if await collector.connect():
        # S'abonner à différents types d'actifs
        await collector.subscribe_stocks(["AAPL", "TSLA", "MSFT"])
        await collector.subscribe_crypto(["BTCUSDT", "ETHUSDT"], exchange="BINANCE")
        await collector.subscribe_forex(["OANDA:EUR_USD"])

        # Stream des données
        await collector.stream(example_callback)


if __name__ == "__main__":
    asyncio.run(main())
