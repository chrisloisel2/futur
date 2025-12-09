import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import redis

logger = logging.getLogger(__name__)


class RedisCache:
    def __init__(
        self,
        url: Optional[str] = None,
        ttl_seconds: int = 300,
        timeout: float = 2.0,
        max_retries: int = 3,
        reconnect_backoff: float = 1.0,
    ) -> None:
        self.url = url or "redis://localhost:6379/0"
        self.ttl = ttl_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        self.reconnect_backoff = reconnect_backoff

        self.client: Optional[redis.Redis] = None
        self._local_cache: Dict[str, Any] = {}
        self._redis_available = True
        self._last_reconnect_attempt = 0.0

        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Redis."""
        try:
            self.client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=self.timeout,
                socket_connect_timeout=self.timeout,
            )
            self.client.ping()
            self._redis_available = True
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.warning(f"Redis connection failed, using local cache fallback: {e}")
            self.client = None
            self._redis_available = False

    def _try_reconnect(self) -> None:
        """Try to reconnect to Redis with exponential backoff."""
        now = time.time()
        if now - self._last_reconnect_attempt < self.reconnect_backoff:
            return

        self._last_reconnect_attempt = now
        logger.info("Attempting to reconnect to Redis...")
        self._connect()

        if self._redis_available:
            self.reconnect_backoff = 1.0
        else:
            self.reconnect_backoff = min(self.reconnect_backoff * 2, 60.0)

    def get_json(self, key: str) -> Any:
        """Get value from cache with Redis fallback to local cache."""
        if self._redis_available and self.client:
            try:
                raw = self.client.get(key)
                return json.loads(raw) if raw else None
            except redis.ConnectionError:
                logger.warning(f"Redis connection lost on GET, falling back to local cache")
                self._redis_available = False
                self._try_reconnect()
            except redis.TimeoutError:
                logger.warning(f"Redis timeout on GET {key}, falling back to local cache")
            except Exception as e:
                logger.error(f"Redis error on GET {key}: {e}")

        # Fallback to local cache
        return self._local_cache.get(key)

    def set_json(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Set value in cache with Redis fallback to local cache."""
        ttl = ttl_seconds or self.ttl
        serialized = json.dumps(value, default=str)

        # Always update local cache as fallback
        self._local_cache[key] = json.loads(serialized)

        if self._redis_available and self.client:
            try:
                self.client.setex(key, ttl, serialized)
            except redis.ConnectionError:
                logger.warning(f"Redis connection lost on SET, using local cache only")
                self._redis_available = False
                self._try_reconnect()
            except redis.TimeoutError:
                logger.warning(f"Redis timeout on SET {key}, value stored in local cache")
            except Exception as e:
                logger.error(f"Redis error on SET {key}: {e}")
        elif not self._redis_available:
            self._try_reconnect()

    def cached(self, key_fn: Callable[..., str], ttl_seconds: Optional[int] = None) -> Callable:
        def decorator(func: Callable) -> Callable:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = key_fn(*args, **kwargs)
                cached_value = self.get_json(key)
                if cached_value is not None:
                    return cached_value
                result = func(*args, **kwargs)
                self.set_json(key, result, ttl_seconds)
                return result

            return wrapper

        return decorator
