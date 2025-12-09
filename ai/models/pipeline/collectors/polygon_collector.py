"""
Polygon WebSocket Collector - Flux temps réel pour stocks/crypto/forex
"""
import json
import logging
from typing import Callable, List, Optional

import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _normalize_timestamp(ts: Optional[int]) -> Optional[int]:
    """Ramener le timestamp en millisecondes."""
    if ts is None:
        return None

    # Polygon peut renvoyer des timestamps en us ou ns, on ramène en ms
    while ts and ts > 1e13:
        ts = ts // 1000
    return ts


class PolygonCollector:
    """Collecteur WebSocket pour Polygon.io."""

    def __init__(self, api_key: str, market: str = "stocks", channels: Optional[List[str]] = None):
        self.api_key = api_key
        self.market = market
        self.channels = channels or ["T"]  # T = trades par défaut
        self.ws_url = f"wss://socket.polygon.io/{market}"
        self.ws = None
        self.subscriptions: List[str] = []

    async def connect(self) -> bool:
        """Établir la connexion WebSocket et s'authentifier."""
        if not self.api_key:
            logger.error("❌ Polygon API key manquante")
            return False

        try:
            self.ws = await websockets.connect(self.ws_url)

            auth_msg = {"action": "auth", "params": self.api_key}
            await self.ws.send(json.dumps(auth_msg))

            response = await self.ws.recv()
            try:
                data = json.loads(response)
                logger.info(f"✅ Polygon auth response: {data}")
            except json.JSONDecodeError:
                logger.info(f"✅ Polygon auth raw response: {response}")

            return True
        except Exception as exc:
            logger.error(f"❌ Polygon connection error: {exc}")
            return False

    async def subscribe(self, symbols: List[str], channels: Optional[List[str]] = None):
        """S'abonner aux symboles et canaux."""
        if not self.ws:
            raise RuntimeError("Polygon WebSocket not connected")

        active_channels = channels or self.channels
        subs = []

        for symbol in symbols:
            for channel in active_channels:
                if "." in channel:
                    subs.append(channel.format(symbol=symbol))
                elif self.market == "crypto":
                    subs.append(f"XT.{symbol}")
                elif self.market == "forex":
                    subs.append(f"C.{symbol}")
                else:
                    subs.append(f"{channel}.{symbol}")

        if not subs:
            logger.warning("⚠️ Aucun abonnement Polygon à envoyer")
            return

        subscribe_msg = {"action": "subscribe", "params": ",".join(subs)}
        await self.ws.send(json.dumps(subscribe_msg))
        self.subscriptions = subs
        logger.info(f"📡 Polygon subscribed: {subs}")

    async def stream(self, callback: Callable):
        """Stream des messages en continu."""
        if not self.ws:
            raise RuntimeError("Polygon WebSocket not connected")

        try:
            async for message in self.ws:
                events = json.loads(message)
                if not isinstance(events, list):
                    events = [events]

                for event in events:
                    trade = self._parse_event(event)
                    if trade:
                        await callback(trade)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ Polygon WebSocket closed")
        except Exception as exc:
            logger.error(f"❌ Polygon stream error: {exc}")

    def _parse_event(self, event: dict) -> Optional[dict]:
        """Normaliser un event Polygon vers un trade générique."""
        ev = event.get("ev") or event.get("event")

        # Stocks/forex trades
        if ev in {"T", "XT", "C"}:
            symbol = event.get("sym") or event.get("pair")
            price = event.get("p") or event.get("ap") or event.get("a")
            volume = event.get("s") or event.get("v") or event.get("size", 0)
            timestamp = _normalize_timestamp(event.get("t"))

            if symbol and price is not None:
                clean_symbol = symbol.replace("-", "").replace("/", "")
                return {
                    "type": "trade",
                    "symbol": clean_symbol,
                    "price": float(price),
                    "volume": float(volume) if volume is not None else 0.0,
                    "timestamp": timestamp or 0,
                    "raw": event,
                }

        # Aggregates (A events)
        if ev == "A":
            symbol = event.get("sym")
            price = event.get("c") or event.get("o") or event.get("h")
            volume = event.get("v")
            timestamp = _normalize_timestamp(event.get("s"))
            if symbol and price is not None:
                return {
                    "type": "aggregate",
                    "symbol": symbol,
                    "price": float(price),
                    "volume": float(volume) if volume is not None else 0.0,
                    "timestamp": timestamp or 0,
                    "raw": event,
                }

        return None

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info("🔌 Polygon WebSocket closed")
