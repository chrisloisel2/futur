"""
Yahoo Finance WebSocket Collector (non-officiel)
"""
import gzip
import json
import logging
import time
from typing import Callable, List, Optional

import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class YahooFinanceCollector:
    """Collecteur WebSocket pour Yahoo Finance (flux non documenté)."""

    def __init__(self, ws_url: str = "wss://streamer.finance.yahoo.com/?version=2"):
        self.ws_url = ws_url
        self.ws = None
        self.subscribed_symbols: List[str] = []

    async def connect(self) -> bool:
        """Établir la connexion WebSocket."""
        try:
            self.ws = await websockets.connect(self.ws_url)
            logger.info("✅ Yahoo Finance WebSocket connected")
            return True
        except Exception as exc:
            logger.error(f"❌ Yahoo Finance connection error: {exc}")
            return False

    async def subscribe(self, symbols: List[str]):
        """S'abonner à une liste de tickers."""
        if not self.ws:
            raise RuntimeError("Yahoo Finance WebSocket not connected")

        if not symbols:
            logger.warning("⚠️ Aucun symbole Yahoo Finance à abonner")
            return

        subscribe_msg = {"subscribe": symbols}
        await self.ws.send(json.dumps(subscribe_msg))
        self.subscribed_symbols = symbols
        logger.info(f"📡 Yahoo Finance subscribed to: {symbols}")

    def _decode_message(self, message) -> Optional[dict]:
        """Décoder un message (gzip possible)."""
        if isinstance(message, bytes):
            try:
                message = gzip.decompress(message).decode()
            except OSError:
                message = message.decode(errors="ignore")

        try:
            return json.loads(message)
        except json.JSONDecodeError:
            logger.debug(f"🔎 Message non JSON ignoré: {message}")
            return None

    def _extract_trade(self, payload: dict) -> Optional[dict]:
        """Mapper un payload Yahoo vers un trade générique."""
        symbol = payload.get("id") or payload.get("symbol")
        price = (
            payload.get("price")
            or payload.get("regularMarketPrice")
            or payload.get("shortTermPrice")
        )
        volume = payload.get("dayVolume") or payload.get("lastSize") or payload.get("volume", 0)
        timestamp = payload.get("time") or payload.get("regularMarketTime") or int(time.time() * 1000)

        if timestamp and timestamp < 1e12:
            timestamp = int(timestamp * 1000)

        if symbol and price is not None:
            return {
                "type": "trade",
                "symbol": str(symbol),
                "price": float(price),
                "volume": float(volume) if volume is not None else 0.0,
                "timestamp": int(timestamp),
                "raw": payload,
            }
        return None

    async def stream(self, callback: Callable):
        """Stream des données Yahoo en continu."""
        if not self.ws:
            raise RuntimeError("Yahoo Finance WebSocket not connected")

        try:
            async for message in self.ws:
                payload = self._decode_message(message)
                if not payload:
                    continue

                messages = payload if isinstance(payload, list) else [payload]
                for item in messages:
                    trade = self._extract_trade(item)
                    if trade:
                        await callback(trade)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ Yahoo Finance WebSocket closed")
        except Exception as exc:
            logger.error(f"❌ Yahoo Finance stream error: {exc}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 Yahoo Finance WebSocket closed")
