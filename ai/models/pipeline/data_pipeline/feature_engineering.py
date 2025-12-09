import logging
from typing import Optional

import numpy as np
import pandas as pd


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist})


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> pd.DataFrame:
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    k_percent = 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-9)
    d_percent = k_percent.rolling(d).mean()
    return pd.DataFrame({"stoch_k": k_percent, "stoch_d": d_percent})


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = low.diff().abs()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (low.diff() < 0), 0.0)

    tr = _atr(high, low, close, window)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / window, adjust=False).mean() / (tr + 1e-9))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / window, adjust=False).mean() / (tr + 1e-9))
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9) * 100
    return dx.ewm(alpha=1 / window, adjust=False).mean()


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    sma = tp.rolling(window).mean()
    mad = (tp - sma).abs().rolling(window).mean()
    return (tp - sma) / (0.015 * mad + 1e-9)


def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    mf = tp * volume
    up = mf.where(tp > tp.shift(1), 0.0)
    down = mf.where(tp < tp.shift(1), 0.0)
    ratio = up.rolling(window).sum() / (down.rolling(window).sum() + 1e-9)
    return 100 - (100 / (1 + ratio))


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (volume * direction).cumsum()


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    tp = (high + low + close) / 3
    cum_vol = volume.cumsum()
    cum_vp = (tp * volume).cumsum()
    return cum_vp / (cum_vol + 1e-9)


def build_feature_set(
    ohlcv_df: pd.DataFrame, onchain_column: Optional[str] = None, drop_na: bool = True
) -> pd.DataFrame:
    logging.getLogger(__name__).info(
        "Feature build start: shape=%s, columns=%s, has_timestamp_col=%s, index=%s",
        ohlcv_df.shape,
        list(ohlcv_df.columns),
        "timestamp" in ohlcv_df.columns,
        type(ohlcv_df.index).__name__,
    )

    df = ohlcv_df.copy()
    if "timestamp" in df.columns:
        df.set_index("timestamp", inplace=True)
    elif isinstance(df.index, pd.DatetimeIndex):
        df.index.name = "timestamp"
    else:
        raise KeyError("timestamp not found as column or datetime index during feature building.")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    open_ = df["open"]

    features = pd.DataFrame(index=df.index)

    # Returns and volatility
    features["ret_1"] = close.pct_change()
    features["ret_log_1"] = np.log(close).diff()
    features["ret_4"] = close.pct_change(4)
    features["ret_12"] = close.pct_change(12)
    features["vol_14"] = features["ret_log_1"].rolling(14).std()
    features["vol_30"] = features["ret_log_1"].rolling(30).std()
    features["vol_60"] = features["ret_log_1"].rolling(60).std()

    # Moving averages
    for window in [5, 10, 20, 50, 100, 200]:
        features[f"sma_{window}"] = close.rolling(window).mean()
    for span in [8, 12, 21, 34, 55, 89]:
        features[f"ema_{span}"] = close.ewm(span=span, adjust=False).mean()

    # Price positioning
    features["close_over_sma20"] = close / (features["sma_20"] + 1e-9)
    features["close_over_sma50"] = close / (features["sma_50"] + 1e-9)
    features["close_over_sma200"] = close / (features["sma_200"] + 1e-9)

    # Bollinger Bands
    bb_mid = features["sma_20"]
    bb_std = close.rolling(20).std()
    features["bb_up"] = bb_mid + 2 * bb_std
    features["bb_low"] = bb_mid - 2 * bb_std
    features["bb_width"] = (features["bb_up"] - features["bb_low"]) / (bb_mid + 1e-9)

    # Momentum
    for w in [7, 14, 21]:
        features[f"rsi_{w}"] = _rsi(close, w)
    stoch = _stochastic(high, low, close)
    features = features.join(stoch)
    macd_df = _macd(close)
    features = features.join(macd_df)

    # Volatility/Trend
    features["atr_14"] = _atr(high, low, close, 14)
    features["adx_14"] = _adx(high, low, close, 14)
    features["obv"] = _obv(close, volume)
    features["cci_20"] = _cci(high, low, close, 20)
    features["cci_50"] = _cci(high, low, close, 50)
    features["mfi_14"] = _mfi(high, low, close, volume, 14)
    features["vwap"] = _vwap(high, low, close, volume)

    # Distribution of returns
    features["ret_skew_30"] = features["ret_log_1"].rolling(30).skew()
    features["ret_kurt_30"] = features["ret_log_1"].rolling(30).kurt()

    # Candle shape
    features["range"] = high - low
    features["body"] = close - open_
    features["upper_shadow"] = high - df[["close", "open"]].max(axis=1)
    features["lower_shadow"] = df[["close", "open"]].min(axis=1) - low

    # Price extremes
    features["close_over_rollmax_50"] = close / (close.rolling(50).max() + 1e-9)
    features["close_over_rollmin_50"] = close / (close.rolling(50).min() + 1e-9)

    # Volume stats
    features["vol_sma_20"] = volume.rolling(20).mean()
    features["vol_sma_50"] = volume.rolling(50).mean()
    features["vol_z_20"] = (volume - features["vol_sma_20"]) / (volume.rolling(20).std() + 1e-9)

    # On-chain derived if present
    if onchain_column and onchain_column in df.columns:
        features[f"{onchain_column}_diff"] = df[onchain_column].diff()
        features[f"{onchain_column}_zscore"] = (
            (df[onchain_column] - df[onchain_column].rolling(30, min_periods=10).mean())
            / (df[onchain_column].rolling(30, min_periods=10).std() + 1e-9)
        )

    # Restore timestamp column directly from the index (handles duplicate timestamps)
    features = features.copy()
    features["timestamp"] = features.index

    if drop_na:
        features.dropna(inplace=True)
    features.sort_index(inplace=True)
    logging.getLogger(__name__).info(
        "Feature build done: shape=%s, columns=%s, has_timestamp=%s, head=%s",
        features.shape,
        list(features.columns),
        "timestamp" in features.columns,
        features.head(2).to_dict(orient="list"),
    )
    return features
