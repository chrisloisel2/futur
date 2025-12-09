import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import ccxt
import pandas as pd
import requests

from .cache import RedisCache

logger = logging.getLogger(__name__)


def _default_exchange() -> ccxt.Exchange:
    exchange = ccxt.binance({"enableRateLimit": True})
    exchange.load_markets()
    return exchange


def _ms_timestamp(dt: datetime) -> int:
    """Convert datetime to milliseconds timestamp, ensuring UTC."""
    if dt.tzinfo is None:
        logger.warning(f"Naive datetime passed to _ms_timestamp, assuming UTC: {dt}")
        dt = dt.replace(tzinfo=pd.Timestamp.utcnow().tzinfo)
    return int(dt.timestamp() * 1000)


class CcxtDataSource:
    def __init__(
        self,
        exchange: Optional[ccxt.Exchange] = None,
        cache: Optional[RedisCache] = None,
        max_retries: int = 5,
        backoff_factor: float = 2.0,
        default_limit: int = 1000,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: int = 300,
    ) -> None:
        self.exchange = exchange or _default_exchange()
        self.cache = cache
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.default_limit = default_limit

        # Circuit breaker state
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_timeout = circuit_breaker_timeout
        self._consecutive_failures = 0
        self._circuit_open_until: Optional[float] = None

    def _cache_key(self, symbol: str, timeframe: str, since: Optional[int], limit: int) -> str:
        return f"ccxt:{self.exchange.id}:{symbol}:{timeframe}:{since or 'none'}:{limit}"

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open and raise exception if so."""
        if self._circuit_open_until is not None:
            if time.time() < self._circuit_open_until:
                raise RuntimeError(
                    f"Circuit breaker open until {datetime.fromtimestamp(self._circuit_open_until)}. "
                    f"Too many consecutive failures ({self._consecutive_failures})."
                )
            else:
                # Circuit breaker timeout expired, reset
                logger.info("Circuit breaker timeout expired, resetting failure count")
                self._consecutive_failures = 0
                self._circuit_open_until = None

    def _record_success(self) -> None:
        """Reset failure count on successful call."""
        if self._consecutive_failures > 0:
            logger.info(f"Request succeeded, resetting failure count from {self._consecutive_failures}")
            self._consecutive_failures = 0
            self._circuit_open_until = None

    def _record_failure(self) -> None:
        """Increment failure count and open circuit breaker if threshold reached."""
        self._consecutive_failures += 1
        logger.warning(f"Consecutive failures: {self._consecutive_failures}/{self.circuit_breaker_threshold}")

        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._circuit_open_until = time.time() + self.circuit_breaker_timeout
            logger.error(
                f"Circuit breaker opened until {datetime.fromtimestamp(self._circuit_open_until)} "
                f"after {self._consecutive_failures} failures"
            )

    def _with_backoff(self, fn: Any) -> Any:
        """Execute function with backoff and error handling."""
        self._check_circuit_breaker()

        for attempt in range(self.max_retries):
            try:
                result = fn()
                self._record_success()
                return result

            except ccxt.AuthenticationError as e:
                logger.error(f"Authentication failed: {e}")
                self._record_failure()
                raise

            except ccxt.InvalidOrder as e:
                logger.error(f"Invalid order/symbol: {e}")
                self._record_failure()
                raise

            except ccxt.ExchangeNotAvailable as e:
                if "maintenance" in str(e).lower():
                    logger.warning(f"Exchange in maintenance, pausing 5 minutes: {e}")
                    time.sleep(300)
                    continue
                logger.error(f"Exchange unavailable: {e}")
                if attempt < self.max_retries - 1:
                    sleep_for = self.backoff_factor ** attempt
                    logger.info(f"Retrying in {sleep_for}s (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(sleep_for)
                    continue
                self._record_failure()
                raise

            except ccxt.RateLimitExceeded as e:
                sleep_for = (self.backoff_factor ** attempt) * (self.exchange.rateLimit / 1000)
                logger.warning(f"Rate limit exceeded, backing off {sleep_for:.2f}s: {e}")
                time.sleep(sleep_for)
                continue

            except ccxt.NetworkError as e:
                if attempt < self.max_retries - 1:
                    sleep_for = self.backoff_factor ** attempt
                    logger.warning(f"Network error, retrying in {sleep_for}s: {e}")
                    time.sleep(sleep_for)
                    continue
                logger.error(f"Network error after {self.max_retries} attempts: {e}")
                self._record_failure()
                raise

            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {type(e).__name__}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_factor ** attempt)
                    continue
                self._record_failure()
                raise

        # Final attempt without catching
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


def ohlcv_to_df(ohlcv: List[List[float]]) -> pd.DataFrame:
    """Convert OHLCV data to DataFrame with UTC timestamps."""
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    # Ensure timestamp is timezone-aware
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

    return df


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

    @staticmethod
    def to_df(data: List[Dict[str, Any]], ts_key: str = "t", value_key: str = "v") -> pd.DataFrame:
        """Convert Glassnode data to DataFrame with UTC timestamps."""
        df = pd.DataFrame(data)

        if ts_key in df.columns:
            df["timestamp"] = pd.to_datetime(df[ts_key], unit="s", utc=True)
        elif "timestamp" not in df.columns:
            raise ValueError("Glassnode payload missing timestamp column")

        # Ensure timestamp is timezone-aware
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

        if value_key in df.columns:
            df.rename(columns={value_key: "onchain_value"}, inplace=True)

        return df[["timestamp", *(col for col in df.columns if col != "timestamp")]]


def merge_onchain_asof(
    ohlcv_df: pd.DataFrame, onchain_df: pd.DataFrame, tolerance: str = "6h"
) -> pd.DataFrame:
    """
    Merge OHLCV and on-chain data using nearest timestamp matching.

    Ensures both DataFrames have UTC timezone-aware timestamps before merging.
    """
    ohlcv = ohlcv_df.copy()
    onchain = onchain_df.copy()

    # Ensure both have timezone-aware timestamps
    if ohlcv["timestamp"].dt.tz is None:
        logger.warning("OHLCV DataFrame has naive timestamps, converting to UTC")
        ohlcv["timestamp"] = ohlcv["timestamp"].dt.tz_localize("UTC")
    else:
        ohlcv["timestamp"] = ohlcv["timestamp"].dt.tz_convert("UTC")

    if onchain["timestamp"].dt.tz is None:
        logger.warning("On-chain DataFrame has naive timestamps, converting to UTC")
        onchain["timestamp"] = onchain["timestamp"].dt.tz_localize("UTC")
    else:
        onchain["timestamp"] = onchain["timestamp"].dt.tz_convert("UTC")

    merged = pd.merge_asof(
        ohlcv.sort_values("timestamp"),
        onchain.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    return merged
