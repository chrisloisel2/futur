"""Tests for cache module."""
from unittest.mock import MagicMock, patch

import pytest
import redis

from ..cache import RedisCache


class TestRedisCache:
    """Tests for RedisCache with fallback."""

    @pytest.fixture
    def mock_redis_success(self):
        """Mock successful Redis connection."""
        with patch("redis.Redis.from_url") as mock:
            client = MagicMock()
            client.ping.return_value = True
            client.get.return_value = '{"test": "value"}'
            client.setex.return_value = True
            mock.return_value = client
            yield client

    @pytest.fixture
    def mock_redis_failure(self):
        """Mock failed Redis connection."""
        with patch("redis.Redis.from_url") as mock:
            client = MagicMock()
            client.ping.side_effect = redis.ConnectionError("Connection refused")
            mock.return_value = client
            yield client

    def test_successful_connection(self, mock_redis_success):
        """Test successful Redis connection."""
        cache = RedisCache()
        assert cache._redis_available is True
        assert cache.client is not None

    def test_failed_connection_falls_back(self, mock_redis_failure):
        """Test failed connection falls back to local cache."""
        cache = RedisCache()
        assert cache._redis_available is False

    def test_get_json_from_redis(self, mock_redis_success):
        """Test get from Redis."""
        cache = RedisCache()
        result = cache.get_json("test_key")
        assert result == {"test": "value"}

    def test_get_json_fallback_to_local(self, mock_redis_failure):
        """Test get falls back to local cache when Redis unavailable."""
        cache = RedisCache()

        # Manually add to local cache
        cache._local_cache["test_key"] = {"local": "value"}

        result = cache.get_json("test_key")
        assert result == {"local": "value"}

    def test_set_json_stores_in_local_cache(self, mock_redis_failure):
        """Test set stores in local cache when Redis unavailable."""
        cache = RedisCache()

        cache.set_json("test_key", {"data": "value"})

        assert cache._local_cache["test_key"] == {"data": "value"}

    def test_connection_error_during_get(self, mock_redis_success):
        """Test connection error during get falls back to local."""
        cache = RedisCache()

        # Simulate connection error
        cache.client.get.side_effect = redis.ConnectionError("Connection lost")

        # Should not raise, should return None
        result = cache.get_json("test_key")
        assert result is None

    def test_timeout_during_operations(self, mock_redis_success):
        """Test timeout during operations."""
        cache = RedisCache(timeout=0.1)

        cache.client.get.side_effect = redis.TimeoutError("Timeout")

        # Should not raise, should return None
        result = cache.get_json("test_key")
        assert result is None
