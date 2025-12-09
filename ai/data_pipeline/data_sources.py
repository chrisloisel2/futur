import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import ccxt
import requests

from .cache import RedisCache


def _default_exchange() -> ccxt.Exchange:
    exchange = ccxt.binance({"enableRateLimit": True})
    exchange.load_markets()
    return exchange


def _ms_timestamp(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class CcxtDataSource:
    def __init__(
        self,
        exchange: Optional[ccxt.Exchange] = None,
        cache: Optional[RedisCache] = None,
        max_retries: int = 5,
        backoff_factor: float = 2.0,
        default_limit: int = 1000,
    ) -> None:
        self.exchange = exchange or _default_exchange()
        self.cache = cache
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.default_limit = default_limit

    def _cache_key(self, symbol: str, timeframe: str, since: Optional[int], limit: int) -> str:
        return f"ccxt:{self.exchange.id}:{symbol}:{timeframe}:{since or 'none'}:{limit}"

    def _with_backoff(self, fn: Any) -> Any:
        for attempt in range(self.max_retries):
            try:
                return fn()
            except ccxt.RateLimitExceeded:
                sleep_for = (self.backoff_factor ** attempt) * (self.exchange.rateLimit / 1000)
                time.sleep(sleep_for)
            except ccxt.NetworkError:
                time.sleep(self.backoff_factor ** attempt)
        return fn()

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        use_cache: bool = True,
        cache_ttl: int = 900,
    ) -> List[List[float]]:
        limit = limit or self.default_limit
        cache_key = self._cache_key(symbol, timeframe, since, limit)
        if use_cache and self.cache:
            cached = self.cache.get_json(cache_key)
            if cached:
                return cached

        data = self._with_backoff(
            lambda: self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        )
        if use_cache and self.cache:
            self.cache.set_json(cache_key, data, cache_ttl)
        return data

    def fetch_historical_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: Optional[datetime] = None,
        limit_per_call: Optional[int] = None,
        use_cache: bool = True,
    ) -> List[List[float]]:
        start_ms = _ms_timestamp(start)
        end_ms = _ms_timestamp(end) if end else None
        step_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        limit = limit_per_call or self.default_limit
        candles: List[List[float]] = []
        since = start_ms

        while True:
            chunk = self.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since,
                limit=limit,
                use_cache=use_cache,
            )
            if not chunk:
                break
            candles.extend(chunk)
            last_ts = chunk[-1][0]
            next_since = last_ts + step_ms
            if end_ms and next_since >= end_ms:
                break
            if len(chunk) < limit:
                break
            since = next_since
        return self._dedupe(candles)

    @staticmethod
    def _dedupe(rows: List[List[float]]) -> List[List[float]]:
        seen = set()
        output: List[List[float]] = []
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                output.append(row)
        return sorted(output, key=lambda x: x[0])


class GlassnodeClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        cache: Optional[RedisCache] = None,
        base_url: str = "https://api.glassnode.com/v1/metrics",
    ) -> None:
        self.api_key = api_key or os.getenv("GLASSNODE_API_KEY", "")
        self.cache = cache
        self.base_url = base_url.rstrip("/")

    def fetch_metric(
        self,
        endpoint: str,
        asset: str = "BTC",
        params: Optional[Dict[str, Any]] = None,
        cache_ttl: int = 900,
    ) -> List[Dict[str, Any]]:
        params = params.copy() if params else {}
        params.setdefault("a", asset)
        if self.api_key:
            params["api_key"] = self.api_key
        cache_key = f"glassnode:{endpoint}:{json.dumps(sorted(params.items()))}"
        if self.cache:
            cached = self.cache.get_json(cache_key)
            if cached:
                return cached

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        if self.cache:
            self.cache.set_json(cache_key, payload, cache_ttl)
        return payload
