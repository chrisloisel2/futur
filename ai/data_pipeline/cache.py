import json
from typing import Any, Callable, Optional

import redis


class RedisCache:
    def __init__(self, url: Optional[str] = None, ttl_seconds: int = 300) -> None:
        self.client = redis.Redis.from_url(
            url or "redis://localhost:6379/0", decode_responses=True
        )
        self.ttl = ttl_seconds

    def get_json(self, key: str) -> Any:
        raw = self.client.get(key)
        return json.loads(raw) if raw else None

    def set_json(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        self.client.setex(key, ttl_seconds or self.ttl, json.dumps(value, default=str))

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
