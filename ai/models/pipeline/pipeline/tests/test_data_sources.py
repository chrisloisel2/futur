"""Tests for data_sources module."""
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import ccxt
import pandas as pd
import pytest

from ..data_sources import (
    CcxtDataSource,
    GlassnodeClient,
    merge_onchain_asof,
    ohlcv_to_df,
)


class TestCcxtDataSource:
    """Tests for CcxtDataSource."""

    @pytest.fixture
    def mock_exchange(self):
        """Create mock exchange."""
        exchange = MagicMock(spec=ccxt.Exchange)
        exchange.id = "binance"
        exchange.rateLimit = 1000
        exchange.parse_timeframe = lambda tf: 3600 if tf == "1h" else 86400
        return exchange

    @pytest.fixture
    def data_source(self, mock_exchange):
        """Create data source with mock exchange."""
        return CcxtDataSource(exchange=mock_exchange, cache=None)

    def test_circuit_breaker_opens_after_threshold(self, data_source, mock_exchange):
        """Test circuit breaker opens after consecutive failures."""
        mock_exchange.fetch_ohlcv.side_effect = ccxt.NetworkError("Connection failed")

        # Trigger failures up to threshold
        for _ in range(data_source.circuit_breaker_threshold):
            with pytest.raises(ccxt.NetworkError):
                data_source.fetch_ohlcv("BTC/USDT", "1h")

        # Circuit breaker should now be open
        with pytest.raises(RuntimeError, match="Circuit breaker open"):
            data_source.fetch_ohlcv("BTC/USDT", "1h")

    def test_authentication_error_fails_immediately(self, data_source, mock_exchange):
        """Test authentication errors fail without retries."""
        mock_exchange.fetch_ohlcv.side_effect = ccxt.AuthenticationError("Invalid API key")

        with pytest.raises(ccxt.AuthenticationError):
            data_source.fetch_ohlcv("BTC/USDT", "1h")

        # Should only be called once (no retries)
        assert mock_exchange.fetch_ohlcv.call_count == 1

    def test_rate_limit_uses_backoff(self, data_source, mock_exchange):
        """Test rate limit errors use exponential backoff."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ccxt.RateLimitExceeded("Rate limit")
            return [[1234567890000, 100, 110, 90, 105, 1000]]

        mock_exchange.fetch_ohlcv.side_effect = side_effect

        start = time.time()
        result = data_source.fetch_ohlcv("BTC/USDT", "1h")
        elapsed = time.time() - start

        assert len(result) == 1
        # Should have waited for backoff
        assert elapsed > 0.1
        assert call_count == 3

    def test_network_error_retries(self, data_source, mock_exchange):
        """Test network errors are retried."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ccxt.NetworkError("Timeout")
            return [[1234567890000, 100, 110, 90, 105, 1000]]

        mock_exchange.fetch_ohlcv.side_effect = side_effect

        result = data_source.fetch_ohlcv("BTC/USDT", "1h")

        assert len(result) == 1
        assert call_count == 2


class TestOHLCVConversion:
    """Tests for OHLCV data conversion."""

    def test_ohlcv_to_df_creates_utc_timestamps(self):
        """Test OHLCV conversion creates UTC timezone-aware timestamps."""
        ohlcv = [
            [1609459200000, 29000, 29500, 28500, 29200, 1000],
            [1609462800000, 29200, 29800, 29000, 29600, 1200],
        ]

        df = ohlcv_to_df(ohlcv)

        assert "timestamp" in df.columns
        assert df["timestamp"].dt.tz is not None
        assert str(df["timestamp"].dt.tz) == "UTC"
        assert len(df) == 2

    def test_ohlcv_to_df_has_correct_columns(self):
        """Test OHLCV DataFrame has correct columns."""
        ohlcv = [[1609459200000, 29000, 29500, 28500, 29200, 1000]]
        df = ohlcv_to_df(ohlcv)

        expected_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        assert list(df.columns) == expected_cols


class TestGlassnodeClient:
    """Tests for Glassnode client."""

    @pytest.fixture
    def client(self):
        """Create Glassnode client."""
        return GlassnodeClient(api_key="test_key", cache=None)

    @patch("requests.get")
    def test_fetch_metric_success(self, mock_get, client):
        """Test successful metric fetch."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"t": 1609459200, "v": 1000000},
            {"t": 1609545600, "v": 1100000},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        data = client.fetch_metric("addresses/active_count", asset="BTC")

        assert len(data) == 2
        assert data[0]["t"] == 1609459200

    def test_to_df_creates_utc_timestamps(self):
        """Test Glassnode data conversion creates UTC timestamps."""
        data = [
            {"t": 1609459200, "v": 1000000},
            {"t": 1609545600, "v": 1100000},
        ]

        df = GlassnodeClient.to_df(data)

        assert "timestamp" in df.columns
        assert df["timestamp"].dt.tz is not None
        assert str(df["timestamp"].dt.tz) == "UTC"


class TestMergeOnchainAsof:
    """Tests for merge_onchain_asof."""

    def test_merge_with_matching_timestamps(self):
        """Test merge with matching timestamps."""
        ohlcv_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2021-01-01 00:00", "2021-01-01 01:00"], utc=True
                ),
                "close": [29000, 29500],
            }
        )

        onchain_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2021-01-01 00:00", "2021-01-01 01:00"], utc=True
                ),
                "onchain_value": [1000000, 1100000],
            }
        )

        merged = merge_onchain_asof(ohlcv_df, onchain_df, tolerance="1h")

        assert len(merged) == 2
        assert "close" in merged.columns
        assert "onchain_value" in merged.columns
        assert merged["onchain_value"].notna().all()

    def test_merge_converts_naive_timestamps_to_utc(self):
        """Test merge handles naive timestamps by converting to UTC."""
        # Create naive timestamps
        ohlcv_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2021-01-01 00:00", "2021-01-01 01:00"]),
                "close": [29000, 29500],
            }
        )

        onchain_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2021-01-01 00:00", "2021-01-01 01:00"]),
                "onchain_value": [1000000, 1100000],
            }
        )

        merged = merge_onchain_asof(ohlcv_df, onchain_df)

        assert merged["timestamp"].dt.tz is not None
        assert str(merged["timestamp"].dt.tz) == "UTC"

    def test_merge_with_misaligned_timestamps(self):
        """Test merge with slightly misaligned timestamps."""
        ohlcv_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2021-01-01 00:00", "2021-01-01 01:00"], utc=True
                ),
                "close": [29000, 29500],
            }
        )

        # On-chain data 30 minutes offset
        onchain_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    ["2021-01-01 00:30", "2021-01-01 01:30"], utc=True
                ),
                "onchain_value": [1000000, 1100000],
            }
        )

        merged = merge_onchain_asof(ohlcv_df, onchain_df, tolerance="1h")

        assert len(merged) == 2
        # Should match nearest within tolerance
        assert merged["onchain_value"].notna().all()
