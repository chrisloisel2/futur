"""
Alpaca WebSocket Collector - Flux temps réel pour stocks/crypto
"""
import asyncio
import json
import websockets
from datetime import datetime
from typing import Callable, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AlpacaCollector:
    """Collecteur WebSocket pour Alpaca Market Data."""

    def __init__(self, api_key: str, api_secret: str, feed: str = "iex"):
        """
        Args:
            api_key: Clé API Alpaca
            api_secret: Secret API Alpaca
            feed: Type de feed ("iex" ou "sip")
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.feed = feed
        self.ws_url = f"wss://stream.data.alpaca.markets/v2/{feed}"
        self.ws = None
        self.subscribed_symbols = set()

    async def connect(self):
        """Établir la connexion WebSocket."""
        try:
            self.ws = await websockets.connect(self.ws_url)

            # Authentification
            auth_msg = {
                "action": "auth",
                "key": self.api_key,
                "secret": self.api_secret
            }
            await self.ws.send(json.dumps(auth_msg))

            # Attendre la confirmation
            response = await self.ws.recv()
            auth_response = json.loads(response)

            if auth_response[0].get("T") == "success":
                logger.info("✅ Alpaca WebSocket connected and authenticated")
                return True
            else:
                logger.error(f"❌ Alpaca auth failed: {auth_response}")
                return False

        except Exception as e:
            logger.error(f"❌ Alpaca connection error: {e}")
            return False

    async def subscribe(self, symbols: List[str], data_types: List[str] = None):
        """
        S'abonner aux symboles.

        Args:
            symbols: Liste des symboles (ex: ["AAPL", "TSLA"])
            data_types: Types de données (trades, quotes, bars, etc.)
        """
        if data_types is None:
            data_types = ["trades", "quotes"]

        subscribe_msg = {
            "action": "subscribe"
        }

        for data_type in data_types:
            subscribe_msg[data_type] = symbols

        await self.ws.send(json.dumps(subscribe_msg))
        self.subscribed_symbols.update(symbols)
        logger.info(f"📊 Subscribed to {symbols} for {data_types}")

    async def stream(self, callback: Callable):
        """
        Stream des données en continu.

        Args:
            callback: Fonction appelée pour chaque message reçu
        """
        try:
            async for message in self.ws:
                data = json.loads(message)

                # Traiter chaque message
                for msg in data:
                    msg_type = msg.get("T")

                    if msg_type == "t":  # Trade
                        await callback({
                            "type": "trade",
                            "symbol": msg["S"],
                            "price": msg["p"],
                            "size": msg["s"],
                            "timestamp": msg["t"],
                            "exchange": msg.get("x", ""),
                            "conditions": msg.get("c", [])
                        })

                    elif msg_type == "q":  # Quote
                        await callback({
                            "type": "quote",
                            "symbol": msg["S"],
                            "bid_price": msg["bp"],
                            "bid_size": msg["bs"],
                            "ask_price": msg["ap"],
                            "ask_size": msg["as"],
                            "timestamp": msg["t"]
                        })

                    elif msg_type == "b":  # Bar (OHLCV)
                        await callback({
                            "type": "bar",
                            "symbol": msg["S"],
                            "open": msg["o"],
                            "high": msg["h"],
                            "low": msg["l"],
                            "close": msg["c"],
                            "volume": msg["v"],
                            "timestamp": msg["t"],
                            "vwap": msg.get("vw", 0)
                        })

        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ Alpaca WebSocket connection closed")
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 Alpaca WebSocket closed")


class AlpacaCryptoCollector:
    """Collecteur WebSocket pour Alpaca Crypto."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
        self.ws = None

    async def connect(self):
        """Établir la connexion WebSocket."""
        try:
            self.ws = await websockets.connect(self.ws_url)

            # Authentification
            auth_msg = {
                "action": "auth",
                "key": self.api_key,
                "secret": self.api_secret
            }
            await self.ws.send(json.dumps(auth_msg))

            # Attendre la confirmation
            response = await self.ws.recv()
            auth_response = json.loads(response)

            if auth_response[0].get("T") == "success":
                logger.info("✅ Alpaca Crypto WebSocket connected")
                return True
            else:
                logger.error(f"❌ Alpaca Crypto auth failed: {auth_response}")
                return False

        except Exception as e:
            logger.error(f"❌ Alpaca Crypto connection error: {e}")
            return False

    async def subscribe(self, symbols: List[str]):
        """S'abonner aux crypto symboles (ex: ["BTC/USD", "ETH/USD"])."""
        subscribe_msg = {
            "action": "subscribe",
            "trades": symbols,
            "quotes": symbols,
            "bars": symbols
        }

        await self.ws.send(json.dumps(subscribe_msg))
        logger.info(f"🪙 Subscribed to crypto: {symbols}")

    async def stream(self, callback: Callable):
        """Stream des données crypto en continu."""
        try:
            async for message in self.ws:
                data = json.loads(message)

                for msg in data:
                    msg_type = msg.get("T")

                    if msg_type == "t":  # Trade
                        await callback({
                            "type": "crypto_trade",
                            "symbol": msg["S"],
                            "price": msg["p"],
                            "size": msg["s"],
                            "timestamp": msg["t"],
                            "taker_side": msg.get("tks", "")
                        })

                    elif msg_type == "q":  # Quote
                        await callback({
                            "type": "crypto_quote",
                            "symbol": msg["S"],
                            "bid_price": msg["bp"],
                            "bid_size": msg["bs"],
                            "ask_price": msg["ap"],
                            "ask_size": msg["as"],
                            "timestamp": msg["t"]
                        })

        except Exception as e:
            logger.error(f"❌ Crypto stream error: {e}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 Alpaca Crypto WebSocket closed")


# Exemple d'utilisation
async def example_callback(data):
    """Fonction callback exemple pour traiter les données."""
    print(f"📨 Received: {data['type']} - {data.get('symbol')} @ {data.get('price', 'N/A')}")


async def main():
    """Test du collecteur."""
    # Remplacer par vos vraies clés API
    API_KEY = "YOUR_API_KEY"
    API_SECRET = "YOUR_API_SECRET"

    # Stocks
    collector = AlpacaCollector(API_KEY, API_SECRET)
    if await collector.connect():
        await collector.subscribe(["AAPL", "TSLA", "MSFT"], ["trades"])
        await collector.stream(example_callback)

    # Crypto
    # crypto_collector = AlpacaCryptoCollector(API_KEY, API_SECRET)
    # if await crypto_collector.connect():
    #     await crypto_collector.subscribe(["BTC/USD", "ETH/USD"])
    #     await crypto_collector.stream(example_callback)


if __name__ == "__main__":
    asyncio.run(main())
