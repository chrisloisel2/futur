import logging
from typing import Dict, List, Optional

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


def _rsi_divergence(price: pd.Series, rsi: pd.Series, window: int = 14) -> pd.Series:
    """Detect RSI divergence signals."""
    price_highs = price.rolling(window).max() == price
    price_lows = price.rolling(window).min() == price

    rsi_highs = rsi.rolling(window).max() == rsi
    rsi_lows = rsi.rolling(window).min() == rsi

    # Bearish divergence: price makes higher high, RSI makes lower high
    bearish = (price_highs & ~rsi_highs).astype(int)
    # Bullish divergence: price makes lower low, RSI makes higher low
    bullish = (price_lows & ~rsi_lows).astype(int)

    return bullish - bearish


def _volatility_regime(returns: pd.Series, windows: List[int] = [7, 30, 90]) -> pd.DataFrame:
    """Classify volatility into regimes (low, medium, high)."""
    regimes = pd.DataFrame(index=returns.index)

    for w in windows:
        vol = returns.rolling(w).std()
        vol_percentile = vol.rolling(252, min_periods=w).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1])

        regimes[f"vol_regime_{w}"] = pd.cut(
            vol_percentile, bins=[0, 0.33, 0.67, 1.0], labels=[0, 1, 2]
        ).astype(float)

    return regimes


def build_feature_set(
    ohlcv_df: pd.DataFrame,
    onchain_column: Optional[str] = None,
    drop_na: bool = True,
    windows: Optional[Dict[str, List[int]]] = None,
) -> pd.DataFrame:
    """
    Build comprehensive feature set from OHLCV data.

    Args:
        ohlcv_df: DataFrame with OHLCV data
        onchain_column: Optional on-chain metric column name
        drop_na: Whether to drop rows with NaN values
        windows: Custom windows for indicators. Defaults to multi-window approach.
    """
    logging.getLogger(__name__).info(
        "Feature build start: shape=%s, columns=%s, has_timestamp_col=%s, index=%s",
        ohlcv_df.shape,
        list(ohlcv_df.columns),
        "timestamp" in ohlcv_df.columns,
        type(ohlcv_df.index).__name__,
    )

    if windows is None:
        windows = {
            "sma": [5, 10, 20, 50, 100, 200],
            "ema": [8, 12, 21, 34, 55, 89],
            "rsi": [7, 14, 21, 30],
            "volatility": [7, 14, 21, 30, 60],
            "returns": [1, 4, 12, 24],
        }

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

    # Returns with multiple horizons
    for w in windows["returns"]:
        features[f"ret_{w}"] = close.pct_change(w)
        features[f"ret_log_{w}"] = np.log(close).diff(w)

    # Volatility with multiple windows
    log_ret = np.log(close).diff()
    for w in windows["volatility"]:
        features[f"vol_{w}"] = log_ret.rolling(w).std()

    # Volatility regimes
    vol_regimes = _volatility_regime(log_ret)
    features = features.join(vol_regimes)

    # Moving averages
    for window in windows["sma"]:
        features[f"sma_{window}"] = close.rolling(window).mean()
    for span in windows["ema"]:
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

    # Momentum with multiple windows
    for w in windows["rsi"]:
        features[f"rsi_{w}"] = _rsi(close, w)

    # RSI divergence
    features["rsi_divergence"] = _rsi_divergence(close, features["rsi_14"])

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

    # Lag features with autocorrelation
    # Add meaningful lags (1, 7, 30 periods)
    for lag in [1, 7, 30]:
        features[f"close_lag_{lag}"] = close.shift(lag)
        features[f"volume_lag_{lag}"] = volume.shift(lag)
        features[f"ret_lag_{lag}"] = features["ret_1"].shift(lag)

    # On-chain derived if present
    if onchain_column and onchain_column in df.columns:
        onchain_series = df[onchain_column]
        features[f"{onchain_column}_diff"] = onchain_series.diff()
        features[f"{onchain_column}_pct_change"] = onchain_series.pct_change()

        for w in [7, 30, 90]:
            features[f"{onchain_column}_zscore_{w}"] = (
                (onchain_series - onchain_series.rolling(w, min_periods=5).mean())
                / (onchain_series.rolling(w, min_periods=5).std() + 1e-9)
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
