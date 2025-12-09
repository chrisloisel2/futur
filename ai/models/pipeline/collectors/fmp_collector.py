"""
Financial Modeling Prep (FMP) WebSocket Collector
"""
import json
import logging
import time
from typing import Callable, List, Optional

import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FMPCollector:
    """Collecteur WebSocket pour Financial Modeling Prep."""

    def __init__(self, api_key: str, ws_url: str = "wss://ws.fmpcloud.io"):
        self.api_key = api_key
        self.ws_url = ws_url
        self.ws = None
        self.subscribed_symbols: List[str] = []

    async def connect(self) -> bool:
        """Établir la connexion WebSocket."""
        if not self.api_key:
            logger.error("❌ FMP API key manquante")
            return False

        try:
            url = self.ws_url
            if "apikey=" not in url and "token=" not in url:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}apikey={self.api_key}"

            self.ws = await websockets.connect(url)
            logger.info("✅ FMP WebSocket connected")
            return True
        except Exception as exc:
            logger.error(f"❌ FMP connection error: {exc}")
            return False

    async def subscribe(self, symbols: List[str]):
        """S'abonner à une liste de symboles."""
        if not self.ws:
            raise RuntimeError("FMP WebSocket not connected")

        if not symbols:
            logger.warning("⚠️ Aucun symbole FMP à abonner")
            return

        subscribe_msg = {"event": "subscribe", "symbols": ",".join(symbols)}
        await self.ws.send(json.dumps(subscribe_msg))
        self.subscribed_symbols = symbols
        logger.info(f"📡 FMP subscribed to: {symbols}")

    def _extract_trade(self, payload: dict) -> Optional[dict]:
        """Mapper un payload FMP vers un trade générique."""
        symbol = payload.get("symbol") or payload.get("s") or payload.get("ticker")
        price = payload.get("price") or payload.get("p") or payload.get("last")
        volume = payload.get("volume") or payload.get("v") or payload.get("size", 0)
        timestamp = payload.get("timestamp") or payload.get("t") or int(time.time() * 1000)

        if symbol and price is not None:
            return {
                "type": payload.get("type", "trade"),
                "symbol": str(symbol),
                "price": float(price),
                "volume": float(volume) if volume is not None else 0.0,
                "timestamp": int(timestamp),
                "raw": payload,
            }
        return None

    async def stream(self, callback: Callable):
        """Stream des données en continu."""
        if not self.ws:
            raise RuntimeError("FMP WebSocket not connected")

        try:
            async for message in self.ws:
                data = json.loads(message)

                # Certains messages peuvent être une liste
                messages = data if isinstance(data, list) else [data]
                for payload in messages:
                    trade = self._extract_trade(payload)
                    if trade:
                        await callback(trade)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ FMP WebSocket closed")
        except Exception as exc:
            logger.error(f"❌ FMP stream error: {exc}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 FMP WebSocket closed")
