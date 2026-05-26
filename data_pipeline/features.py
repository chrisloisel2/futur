from __future__ import annotations

import json
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from ai.level_0.feature_engineering import (
    compute_event_features,
    compute_flow_features,
    compute_long_features,
    compute_short_features,
    compute_vwap_features,
)
from ai.level_0.labels import compute_label_columns, compute_long_regime_col, compute_regime_col
from ai.level_0.live_features import compute_live_features, compute_macro_features
from ai.level_0.short_features import compute_all_short_features
from ai.level_0.technical_indicators import (
    compute_ichimoku_features,
    compute_rsi_features,
    compute_volume_features,
)
from ai.level_0.tradingview_indicators import compute_tradingview_features
from core.features.minute import compute_all_features as compute_all_minute_features
from data_pipeline.normalization import standardize_ohlcv_columns


FEATURE_VERSION = "max_public_v1"


LABEL_COLS = {
    "future_ret_h",
    "future_ret_4h",
    "future_ret_h8_min",
    "future_ret_h8_max",
    "future_ret_15m",
    "future_ret_30m",
    "future_ret_60m",
}


def compute_training_features(
    df: pd.DataFrame,
    *,
    symbol: Optional[str] = None,
    interval: str = "1h",
    include_labels: bool = True,
    include_advanced: bool = True,
    source_coverage: Optional[dict] = None,
) -> pd.DataFrame:
    if interval in ("1m", "minute"):
        return compute_minute_features(
            df,
            symbol=symbol,
            include_labels=include_labels,
            source_coverage=source_coverage,
        )
    return compute_hourly_features(
        df,
        symbol=symbol,
        include_labels=include_labels,
        include_advanced=include_advanced,
        source_coverage=source_coverage,
    )


def compute_hourly_features(
    df: pd.DataFrame,
    *,
    symbol: Optional[str] = None,
    include_labels: bool = True,
    include_advanced: bool = True,
    source_coverage: Optional[dict] = None,
) -> pd.DataFrame:
    """Canonical 1h feature factory used by batch scripts and training loaders."""

    frame = standardize_ohlcv_columns(df)
    required = ["open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise RuntimeError("Missing OHLCV columns for hourly features: %s" % missing)

    frame = compute_live_features(frame)
    frame = compute_macro_features(frame)
    frame["atr_14"] = _atr_abs(frame["high"], frame["low"], frame["close"])
    frame = compute_long_features(frame)   # inclut compute_macro_cross_features
    frame = compute_short_features(frame)  # features short de base
    frame = compute_flow_features(frame)
    frame = compute_event_features(frame)
    frame = compute_vwap_features(frame)
    # Gamechanger short : 55 features contrariantes (crowding, breakdown, failed_breakout,
    # liquidity_stress, squeeze_risk) — requièrent macro z-scores + vwap déjà calculés.
    frame = compute_all_short_features(frame)
    if include_advanced:
        frame = compute_ichimoku_features(frame)
        frame = compute_rsi_features(frame)
        frame = compute_volume_features(frame)
        frame = compute_tradingview_features(frame)

    frame = _rename_training_ohlcv(frame)
    if include_labels:
        frame = compute_label_columns(frame)
        frame = compute_regime_col(frame)
        frame = compute_long_regime_col(frame)

    return _finalize_features(
        frame,
        symbol=symbol,
        interval="1h",
        source_coverage=source_coverage,
    )


def compute_minute_features(
    df: pd.DataFrame,
    *,
    symbol: Optional[str] = None,
    include_labels: bool = True,
    source_coverage: Optional[dict] = None,
) -> pd.DataFrame:
    """Canonical 1m feature factory with multi-timeframe context."""

    frame = standardize_ohlcv_columns(df)
    frame = compute_all_minute_features(frame)
    if include_labels:
        frame = add_minute_labels(frame)
    frame = frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return _finalize_features(
        frame,
        symbol=symbol,
        interval="1m",
        source_coverage=source_coverage,
    )


def add_minute_labels(
    df: pd.DataFrame,
    horizons: Iterable[int] = (15, 30, 60),
    close_col: str = "close",
) -> pd.DataFrame:
    out = df.copy()
    if close_col not in out.columns:
        close_col = "Close" if "Close" in out.columns else close_col
    if close_col not in out.columns:
        raise RuntimeError("Cannot add minute labels without close column")
    log_close = np.log(pd.to_numeric(out[close_col], errors="coerce").clip(lower=1e-9))
    for minutes in horizons:
        out["future_ret_%dm" % minutes] = log_close.shift(-minutes) - log_close
    return out


def _atr_abs(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    hl = high - low
    hpc = (high - close.shift(1)).abs()
    lpc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(span=14, adjust=False).mean().ffill().fillna(0.0)


def _rename_training_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "taker_buy_base_asset_volume": "Taker_Buy_Base",
        "taker_buy_quote_asset_volume": "Taker_Buy_Quote",
        "quote_asset_volume": "Quote_Volume",
        "number_of_trades": "Trades",
    }
    return df.rename(columns={k: v for k, v in rename.items() if k in df.columns})


def _finalize_features(
    df: pd.DataFrame,
    *,
    symbol: Optional[str],
    interval: str,
    source_coverage: Optional[dict],
) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "timestamp" in out.columns:
            out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
            out = out.set_index("timestamp")
        elif "datetime" in out.columns:
            out["datetime"] = pd.to_datetime(out["datetime"], utc=True, errors="coerce")
            out = out.set_index("datetime")
    out = out.sort_index()
    out.index.name = "timestamp"

    numeric_cols = [
        col for col in out.columns
        if col not in LABEL_COLS and pd.api.types.is_numeric_dtype(out[col])
    ]
    if numeric_cols:
        out[numeric_cols] = out[numeric_cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)

    if symbol:
        out["symbol"] = symbol
    out["interval"] = interval
    out["feature_version"] = FEATURE_VERSION
    coverage = source_coverage or {}
    out["source_coverage"] = json.dumps(coverage, sort_keys=True)
    return out
