"""Tests for data quality validation."""
import pandas as pd
import pytest

from ..data_quality import DataQualityValidator


class TestDataQualityValidator:
    """Tests for DataQualityValidator."""

    @pytest.fixture
    def validator(self):
        """Create validator instance."""
        return DataQualityValidator()

    @pytest.fixture
    def valid_ohlcv(self):
        """Create valid OHLCV data."""
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2021-01-01", periods=100, freq="1H"),
                "open": [100 + i for i in range(100)],
                "high": [110 + i for i in range(100)],
                "low": [90 + i for i in range(100)],
                "close": [105 + i for i in range(100)],
                "volume": [1000] * 100,
            }
        )

    def test_valid_data_passes(self, validator, valid_ohlcv):
        """Test valid data passes validation."""
        report = validator.validate(valid_ohlcv, timeframe="1h")

        assert report.is_valid
        assert len(report.errors) == 0

    def test_ohlc_violations_detected(self, validator, valid_ohlcv):
        """Test OHLC violations are detected."""
        # Introduce violation: high < low
        invalid_df = valid_ohlcv.copy()
        invalid_df.loc[10, "high"] = 50
        invalid_df.loc[10, "low"] = 100

        report = validator.validate(invalid_df)

        assert not report.is_valid
        assert len(report.ohlc_violations) > 0
        assert 10 in report.ohlc_violations

    def test_temporal_gaps_detected(self, validator):
        """Test temporal gaps are detected."""
        # Create data with gap
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2021-01-01 00:00",
                        "2021-01-01 01:00",
                        "2021-01-01 05:00",  # 4 hour gap
                        "2021-01-01 06:00",
                    ]
                ),
                "close": [100, 101, 102, 103],
            }
        )

        report = validator.validate(df, timeframe="1h")

        assert len(report.temporal_gaps) > 0
        assert len(report.warnings) > 0

    def test_extreme_outliers_detected(self, validator, valid_ohlcv):
        """Test extreme price movements are detected."""
        # Introduce 100% price jump
        outlier_df = valid_ohlcv.copy()
        outlier_df.loc[50, "close"] = 500

        report = validator.validate(outlier_df)

        assert len(report.extreme_outliers) > 0
        assert len(report.warnings) > 0

    def test_volatility_spikes_detected(self, validator, valid_ohlcv):
        """Test volatility spikes are detected."""
        # Create extreme intrabar volatility
        spike_df = valid_ohlcv.copy()
        spike_df.loc[30, "high"] = 200
        spike_df.loc[30, "low"] = 50

        report = validator.validate(spike_df)

        assert len(report.volatility_spikes) > 0
        assert len(report.warnings) > 0

    def test_missing_values_reported(self, validator, valid_ohlcv):
        """Test missing values are reported."""
        # Introduce NaN values
        missing_df = valid_ohlcv.copy()
        missing_df.loc[20:25, "close"] = None

        report = validator.validate(missing_df)

        assert report.missing_values["close"] == 6
        assert len(report.warnings) > 0
