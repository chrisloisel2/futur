from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd


BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def symbol_to_asset(symbol: str) -> str:
    clean = normalize_symbol(symbol)
    for quote in ("USDT", "USD", "BUSD", "USDC", "BTC", "ETH"):
        if clean.endswith(quote) and len(clean) > len(quote):
            return clean[: -len(quote)]
    return clean


def timestamp_unit(value: object) -> str:
    try:
        raw = int(float(value))
    except (TypeError, ValueError):
        return "ms"
    digits = len(str(abs(raw)))
    return "us" if digits >= 16 else "ms"


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_timestamp_column(df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    out = df.copy()
    if timestamp_col not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out[timestamp_col] = out.index
        elif "datetime" in out.columns:
            out[timestamp_col] = out["datetime"]
        else:
            raise ValueError("No timestamp column or DatetimeIndex found")
    out[timestamp_col] = pd.to_datetime(out[timestamp_col], utc=True, errors="coerce")
    out = out.dropna(subset=[timestamp_col]).sort_values(timestamp_col)
    return out


def standardize_ohlcv_columns(df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    """Return a frame with lowercase OHLCV and Binance taker/trade column names."""

    out = df.copy()
    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "datetime": timestamp_col,
        "Taker_Buy_Base": "taker_buy_base_asset_volume",
        "Taker_Buy_Quote": "taker_buy_quote_asset_volume",
        "Quote_Volume": "quote_asset_volume",
        "Trades": "number_of_trades",
        "trades": "number_of_trades",
        "taker_buy_base": "taker_buy_base_asset_volume",
        "taker_buy_quote": "taker_buy_quote_asset_volume",
        "quote_volume": "quote_asset_volume",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    out = out.loc[:, ~pd.Index(out.columns).duplicated(keep="last")].copy()
    out = ensure_timestamp_column(out, timestamp_col=timestamp_col)
    out = out.set_index(timestamp_col)
    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "number_of_trades" in out.columns:
        out["number_of_trades"] = pd.to_numeric(out["number_of_trades"], errors="coerce").fillna(0)
    if "quote_asset_volume" not in out.columns and {"close", "volume"}.issubset(out.columns):
        out["quote_asset_volume"] = out["close"] * out["volume"]
    # NOTE: taker_buy_base_asset_volume / taker_buy_quote_asset_volume are
    # deliberately NOT synthesized here when absent. A prior version filled
    # them with volume * 0.5 / quote_asset_volume * 0.5, which fabricated a
    # constant 50/50 aggressor split baked into data/enriched — see
    # data_pipeline/taker_flow_guard.py. Leave them missing so callers can
    # tell "no real flow data" apart from "flat 50/50 flow".
    if "number_of_trades" not in out.columns:
        out["number_of_trades"] = 0
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def binance_klines_to_frame(
    rows: Iterable[Iterable[object]],
    *,
    symbol: str,
    interval: str,
    market_type: str,
    source: str = "binance_vision",
) -> pd.DataFrame:
    data = list(rows)
    if not data:
        return pd.DataFrame()
    frame = pd.DataFrame(data, columns=BINANCE_KLINE_COLUMNS[: len(data[0])])
    unit = timestamp_unit(frame["open_time"].iloc[0])
    frame["timestamp"] = pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True)
    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "number_of_trades" in frame.columns:
        frame["number_of_trades"] = pd.to_numeric(frame["number_of_trades"], errors="coerce").fillna(0).astype(int)
    return ensure_raw_schema(frame, source=source, symbol=symbol, market_type=market_type, interval=interval)


def read_binance_vision_zip(
    path: Path,
    *,
    symbol: str,
    interval: str,
    market_type: str,
    source: str = "binance_vision",
) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        csv_names = [name for name in zf.namelist() if name.endswith(".csv")]
        if not csv_names:
            raise ValueError("No CSV found in %s" % path)
        with zf.open(csv_names[0]) as fh:
            frame = pd.read_csv(fh, header=None, names=BINANCE_KLINE_COLUMNS, low_memory=False)
    return binance_klines_to_frame(
        frame.values.tolist(),
        symbol=symbol,
        interval=interval,
        market_type=market_type,
        source=source,
    )


def ensure_raw_schema(
    df: pd.DataFrame,
    *,
    source: str,
    symbol: Optional[str],
    market_type: str,
    interval: Optional[str],
) -> pd.DataFrame:
    out = ensure_timestamp_column(df)
    out["source"] = source
    if symbol is not None:
        raw_symbol = symbol
    elif "symbol" in out.columns and len(out):
        raw_symbol = out["symbol"].iloc[0]
    else:
        raw_symbol = "GLOBAL"
    out["symbol"] = normalize_symbol(str(raw_symbol))
    out["asset"] = out["symbol"].map(symbol_to_asset)
    out["market_type"] = market_type
    out["interval"] = interval or ""
    out["ingested_at"] = utc_now_naive()
    if "raw_payload" not in out.columns:
        out["raw_payload"] = None
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


def raw_payload_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
