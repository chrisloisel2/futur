from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from common.logging.setup import get_logger

logger = get_logger(__name__)


class ExecutionClient:
    def __init__(self, adapter: Any, rate_limit_tokens: float = 10.0):
        self.adapter = adapter
        self.rate_limit_tokens = rate_limit_tokens

    def _consume(self, tokens: float = 1.0) -> bool:
        if self.rate_limit_tokens >= tokens:
            self.rate_limit_tokens -= tokens
            return True
        return False

    def place_order(self, intent: Dict) -> Dict:
        if not self._consume():
            raise RuntimeError("rate_limit")
        return self.adapter.place_order(intent)

    def cancel_order(self, client_order_id: str) -> Dict:
        return self.adapter.cancel_order(client_order_id)

    def replace_order(self, client_order_id: str, new_price: float, new_qty: float) -> Dict:
        return self.adapter.replace_order(client_order_id, new_price, new_qty)

    def fetch_open_orders(self):
        return self.adapter.fetch_open_orders()

    def fetch_fills(self, since: Optional[int] = None):
        return self.adapter.fetch_fills(since)

    def ping(self) -> bool:
        try:
            self.adapter.ping()
            return True
        except Exception as exc:  # pragma: no cover
            logger.error({"msg": "ping failed", "error": str(exc)})
            return False

    def ws_subscribe_fills(self, callback: Callable[[Dict], None]) -> None:
        if hasattr(self.adapter, "ws_subscribe_fills"):
            self.adapter.ws_subscribe_fills(callback)
