"""
tests/institutional/test_column_normalizer.py
═══════════════════════════════════════════════════════════════════════════════
Tests du ColumnNormalizer.

Chaque test vérifie un schéma source spécifique.
Le test passe si les colonnes canoniques (open/high/low/close/volume)
sont correctement identifiées ou si l'erreur appropriée est levée.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from institutional.data.column_normalizer import (
    ColumnMappingReport,
    infer_ohlcv_columns,
    infer_timestamp_column,
    normalize_ohlcv_columns,
    validate_ohlcv_schema,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

_UTC = timezone.utc
_IDX = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")


def _make_df(cols: dict, index=None) -> pd.DataFrame:
    """Crée un DataFrame avec les colonnes données."""
    return pd.DataFrame(cols, index=index or _IDX)


def _make_ohlcv(**kwargs) -> dict:
    """Valeurs OHLCV numériques de base."""
    base = {
        "open":   [100.0, 101.0, 99.0,  102.0, 103.0],
        "high":   [102.0, 103.0, 101.0, 104.0, 105.0],
        "low":    [98.0,  99.0,  97.0,  100.0, 101.0],
        "close":  [101.0, 100.0, 100.0, 103.0, 104.0],
        "volume": [500.0, 600.0, 400.0, 700.0, 800.0],
    }
    base.update(kwargs)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# Tests infer_timestamp_column
# ══════════════════════════════════════════════════════════════════════════════


class TestInferTimestampColumn:

    def test_index_is_datetime_returns_none(self) -> None:
        df = _make_df(_make_ohlcv())
        assert infer_timestamp_column(df) is None

    def test_timestamp_col_detected(self) -> None:
        df = pd.DataFrame(_make_ohlcv() | {"timestamp": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")})
        assert infer_timestamp_column(df) == "timestamp"

    def test_datetime_col_detected(self) -> None:
        df = pd.DataFrame(_make_ohlcv() | {"datetime": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")})
        assert infer_timestamp_column(df) == "datetime"

    def test_open_time_col_detected(self) -> None:
        df = pd.DataFrame(_make_ohlcv() | {"open_time": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")})
        assert infer_timestamp_column(df) == "open_time"

    def test_datetime64_dtype_detected_by_type(self) -> None:
        df = pd.DataFrame(_make_ohlcv() | {"trade_date": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")})
        assert infer_timestamp_column(df) == "trade_date"

    def test_no_timestamp_col_no_datetime_index_returns_none(self) -> None:
        df = pd.DataFrame(_make_ohlcv())  # RangeIndex
        assert infer_timestamp_column(df) is None


# ══════════════════════════════════════════════════════════════════════════════
# Tests infer_ohlcv_columns
# ══════════════════════════════════════════════════════════════════════════════


class TestInferOhlcvColumns:

    def test_lowercase_detected(self) -> None:
        df = _make_df(_make_ohlcv())
        m = infer_ohlcv_columns(df)
        assert m["open"]   == "open"
        assert m["high"]   == "high"
        assert m["low"]    == "low"
        assert m["close"]  == "close"
        assert m["volume"] == "volume"

    def test_capitalized_detected(self) -> None:
        df = _make_df({k.capitalize(): v for k, v in _make_ohlcv().items()})
        m = infer_ohlcv_columns(df)
        assert m["open"]  == "Open"
        assert m["close"] == "Close"

    def test_uppercase_detected(self) -> None:
        df = _make_df({k.upper(): v for k, v in _make_ohlcv().items()})
        m = infer_ohlcv_columns(df)
        assert m["open"]  == "OPEN"
        assert m["close"] == "CLOSE"

    def test_price_prefix_detected(self) -> None:
        df = _make_df({
            "price_open": [1.0] * 5, "price_high": [2.0] * 5,
            "price_low":  [0.5] * 5, "price_close": [1.5] * 5,
            "volume": [100.0] * 5,
        })
        m = infer_ohlcv_columns(df)
        assert m["open"]  == "price_open"
        assert m["close"] == "price_close"

    def test_volume_fallback_quote_asset_volume(self) -> None:
        df = _make_df({
            "open": [1.0]*5, "high": [2.0]*5, "low": [0.5]*5, "close": [1.5]*5,
            "quote_asset_volume": [1000.0]*5,
        })
        m = infer_ohlcv_columns(df)
        assert m["volume"] == "quote_asset_volume"

    def test_close_only_file_detected_as_close_absent_ohlc(self) -> None:
        """binance_eth.parquet a seulement eth_close — ne doit pas être mappé sur close."""
        df = _make_df({"eth_close": [1.0]*5})
        m = infer_ohlcv_columns(df)
        # "eth_close" ne doit pas être reconnu comme "close" (pas dans les aliases)
        assert m["open"]  is None
        assert m["high"]  is None
        assert m["low"]   is None
        # close: les aliases ne contiennent pas "eth_close"
        assert m["close"] is None

    def test_no_columns_all_none(self) -> None:
        df = _make_df({"garbage_col": [1.0]*5})
        m = infer_ohlcv_columns(df)
        assert all(v is None for v in m.values())


# ══════════════════════════════════════════════════════════════════════════════
# Tests normalize_ohlcv_columns
# ══════════════════════════════════════════════════════════════════════════════


class TestNormalizeOhlcvColumns:

    # ── Schemas valides ────────────────────────────────────────────────────────

    def test_lowercase_passthrough(self) -> None:
        df = _make_df(_make_ohlcv())
        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "futures")
        assert report.is_valid
        assert "open"   in df_out.columns
        assert "close"  in df_out.columns
        assert "volume" in df_out.columns
        assert isinstance(df_out.index, pd.DatetimeIndex)

    def test_uppercase_normalized(self) -> None:
        df = _make_df({k.upper(): v for k, v in _make_ohlcv().items()})
        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "futures")
        assert report.is_valid
        assert "open"  in df_out.columns
        assert "close" in df_out.columns

    def test_binance_csv_style(self) -> None:
        """Format Binance CSV : Open time, Open, High, Low, Close, Volume."""
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "Open time": ts,
            "Open":   [100.0]*5, "High": [102.0]*5,
            "Low":    [98.0]*5,  "Close": [101.0]*5,
            "Volume": [500.0]*5,
        })
        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "csv")
        assert report.is_valid
        assert df_out.index.name == "timestamp"
        assert "open" in df_out.columns

    def test_snake_case_open_time(self) -> None:
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "open_time": ts,
            "open": [100.0]*5, "high": [102.0]*5,
            "low":  [98.0]*5,  "close": [101.0]*5,
            "volume": [500.0]*5,
        })
        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "binance")
        assert report.is_valid
        assert "open" in df_out.columns

    def test_price_prefix_schema(self) -> None:
        df = _make_df({
            "price_open": [100.0]*5, "price_high": [102.0]*5,
            "price_low":  [98.0]*5,  "price_close": [101.0]*5,
            "volume": [500.0]*5,
        })
        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "custom")
        assert report.is_valid
        assert "open"  in df_out.columns
        assert "close" in df_out.columns

    def test_timestamp_column_becomes_index(self) -> None:
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame(_make_ohlcv() | {"timestamp": ts})
        df_out, report = normalize_ohlcv_columns(df, "X", "Y")
        assert isinstance(df_out.index, pd.DatetimeIndex)
        assert df_out.index.name == "timestamp"
        assert "timestamp" not in df_out.columns

    def test_datetime_column_renamed_to_timestamp(self) -> None:
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame(_make_ohlcv() | {"datetime": ts})
        df_out, report = normalize_ohlcv_columns(df, "X", "Y")
        assert df_out.index.name == "timestamp"
        assert report.timestamp_source == "datetime"

    def test_volume_fallback_produces_warning(self) -> None:
        df = _make_df({
            "open": [1.0]*5, "high": [2.0]*5, "low": [0.5]*5, "close": [1.5]*5,
            "quote_asset_volume": [1000.0]*5,
        })
        df_out, report = normalize_ohlcv_columns(df, "X", "Y")
        assert report.is_valid
        assert report.volume_source == "quote_asset_volume"
        assert any("fallback" in w.lower() or "secours" in w.lower() for w in report.warnings)

    def test_no_volume_produces_warning_not_error(self) -> None:
        df = _make_df({
            "open": [1.0]*5, "high": [2.0]*5, "low": [0.5]*5, "close": [1.5]*5,
        })
        # Ne lève pas d'exception (volume non obligatoire)
        df_out, report = normalize_ohlcv_columns(df, "X", "Y")
        assert report.is_valid  # open/high/low/close suffisent pour is_valid
        assert any("volume" in w.lower() or "absent" in w.lower() for w in report.warnings)

    # ── Schemas invalides → ValueError ────────────────────────────────────────

    def test_close_absent_raises(self) -> None:
        df = _make_df({
            "open": [1.0]*5, "high": [2.0]*5, "low": [0.5]*5,
            # pas de close
        })
        with pytest.raises(ValueError, match="close"):
            normalize_ohlcv_columns(df, "X", "Y")

    def test_all_ohlc_absent_raises(self) -> None:
        df = _make_df({"garbage": [1.0]*5})
        with pytest.raises(ValueError):
            normalize_ohlcv_columns(df, "X", "Y")

    def test_no_timestamp_raises(self) -> None:
        """RangeIndex + pas de colonne timestamp → ValueError."""
        df = pd.DataFrame(_make_ohlcv())   # RangeIndex, pas de datetime
        with pytest.raises(ValueError, match="timestamp"):
            normalize_ohlcv_columns(df, "X", "Y")

    def test_close_only_file_raises(self) -> None:
        """binance_eth.parquet style : seulement timestamp + eth_close."""
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame({"timestamp": ts, "eth_close": [3000.0]*5})
        with pytest.raises(ValueError, match="obligatoires"):
            normalize_ohlcv_columns(df, "ETHUSDT", "data_out")

    # ── Rapport ───────────────────────────────────────────────────────────────

    def test_report_summary_ok(self) -> None:
        df = _make_df(_make_ohlcv())
        _, report = normalize_ohlcv_columns(df, "BTCUSDT", "futures")
        summary = report.summary()
        assert "BTCUSDT" in summary
        assert "OK" in summary

    def test_report_asset_source(self) -> None:
        df = _make_df(_make_ohlcv())
        _, report = normalize_ohlcv_columns(df, "ETHUSDT", "enriched")
        assert report.asset  == "ETHUSDT"
        assert report.source == "enriched"

    def test_report_mapped_fields(self) -> None:
        df = _make_df({k.upper(): v for k, v in _make_ohlcv().items()})
        _, report = normalize_ohlcv_columns(df, "X", "Y")
        assert report.mapped["close"] == "CLOSE"

    def test_report_missing_required_empty_when_valid(self) -> None:
        df = _make_df(_make_ohlcv())
        _, report = normalize_ohlcv_columns(df, "X", "Y")
        assert report.missing_required == ()


# ══════════════════════════════════════════════════════════════════════════════
# Tests validate_ohlcv_schema
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateOhlcvSchema:

    def test_valid_schema(self) -> None:
        df = _make_df(_make_ohlcv())
        report = validate_ohlcv_schema(df, "BTCUSDT", "futures")
        assert report.is_valid

    def test_close_only_invalid(self) -> None:
        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame({"timestamp": ts, "eth_close": [3000.0]*5})
        report = validate_ohlcv_schema(df, "ETHUSDT", "data_out")
        assert not report.is_valid
        assert "open" in report.missing_required
        assert "high" in report.missing_required
        assert "low"  in report.missing_required
        assert "close" in report.missing_required

    def test_no_timestamp_invalid(self) -> None:
        df = pd.DataFrame(_make_ohlcv())   # RangeIndex
        report = validate_ohlcv_schema(df, "X", "Y")
        assert not report.is_valid
        assert any("timestamp" in w.lower() for w in report.warnings)

    def test_is_frozen(self) -> None:
        df = _make_df(_make_ohlcv())
        report = validate_ohlcv_schema(df, "X", "Y")
        with pytest.raises((AttributeError, TypeError)):
            report.is_valid = False  # type: ignore[misc]
