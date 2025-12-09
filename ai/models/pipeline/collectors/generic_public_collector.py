"""
Collecteur WebSocket générique pour sources publiques (Massive, community feeds, etc.)
"""
import json
import logging
import time
from typing import Callable, Dict, List, Optional

import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GenericPublicCollector:
    """Collecteur configurable pour des flux WebSocket simples."""

    def __init__(
        self,
        name: str,
        ws_url: str,
        api_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        auth_message: Optional[Dict] = None,
        subscribe_template: Optional[Dict] = None,
        symbol_field: str = "symbol",
        price_field: str = "price",
        volume_field: str = "volume",
        timestamp_field: str = "timestamp",
    ):
        self.name = name
        self.ws_url = ws_url
        self.api_key = api_key
        self.headers = headers or {}
        self.auth_message = auth_message
        self.subscribe_template = subscribe_template or {"type": "subscribe", "symbols": []}
        self.symbol_field = symbol_field
        self.price_field = price_field
        self.volume_field = volume_field
        self.timestamp_field = timestamp_field
        self.ws = None
        self.subscribed_symbols: List[str] = []

    async def connect(self) -> bool:
        """Établir la connexion WebSocket et envoyer l'auth si besoin."""
        if not self.ws_url:
            logger.error(f"❌ {self.name}: ws_url manquant")
            return False

        try:
            if self.api_key and "Authorization" not in self.headers:
                self.headers["Authorization"] = f"Bearer {self.api_key}"

            self.ws = await websockets.connect(self.ws_url, extra_headers=self.headers or None)

            if self.auth_message:
                auth_msg = json.dumps(self.auth_message)
                await self.ws.send(auth_msg)
                logger.info(f"🔑 {self.name}: auth message sent")

            logger.info(f"✅ {self.name} WebSocket connected")
            return True
        except Exception as exc:
            logger.error(f"❌ {self.name} connection error: {exc}")
            return False

    async def subscribe(self, symbols: List[str]):
        """S'abonner à des symboles via le template fourni."""
        if not self.ws:
            raise RuntimeError(f"{self.name} WebSocket not connected")

        if not symbols:
            logger.warning(f"⚠️ {self.name}: aucun symbole à abonner")
            return

        payload = dict(self.subscribe_template)
        # Supporte soit liste, soit string join
        if "symbols" in payload:
            payload["symbols"] = symbols
        elif "symbol" in payload:
            payload["symbol"] = symbols[0] if len(symbols) == 1 else symbols
        elif "params" in payload:
            payload["params"] = ",".join(symbols)

        await self.ws.send(json.dumps(payload))
        self.subscribed_symbols = symbols
        logger.info(f"📡 {self.name} subscribed to: {symbols}")

    def _extract_trade(self, payload: Dict) -> Optional[Dict]:
        """Mapper un payload générique vers un trade."""
        symbol = payload.get(self.symbol_field) or payload.get("s") or payload.get("ticker")
        price = payload.get(self.price_field) or payload.get("p") or payload.get("last")
        volume = payload.get(self.volume_field) or payload.get("v") or payload.get("size", 0)
        timestamp = payload.get(self.timestamp_field) or payload.get("t") or int(time.time() * 1000)

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
        """Stream des messages en continu."""
        if not self.ws:
            raise RuntimeError(f"{self.name} WebSocket not connected")

        try:
            async for message in self.ws:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    logger.debug(f"🔎 {self.name}: message non JSON ignoré")
                    continue

                messages = payload if isinstance(payload, list) else [payload]
                for item in messages:
                    trade = self._extract_trade(item)
                    if trade:
                        await callback(trade)
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"⚠️ {self.name} WebSocket closed")
        except Exception as exc:
            logger.error(f"❌ {self.name} stream error: {exc}")

    async def close(self):
        """Fermer la connexion."""
        if self.ws:
            await self.ws.close()
            logger.info(f"🔌 {self.name} WebSocket closed")
