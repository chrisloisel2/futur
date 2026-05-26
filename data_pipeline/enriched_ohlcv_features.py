from __future__ import annotations

import json
import math
import warnings
from collections import OrderedDict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning

from data_pipeline.normalization import standardize_ohlcv_columns


FEATURE_VERSION = "ohlcv_enriched_v1"
DEFAULT_HORIZONS: Tuple[int, ...] = (1, 2, 3, 5, 10, 14, 20, 30, 50, 100, 200)
DEFAULT_SEQUENCE_HORIZONS: Tuple[int, ...] = (3, 5, 10, 20)
DEFAULT_TIMEFRAMES: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")
EPS = 1e-12
np.seterr(divide="ignore", invalid="ignore")
warnings.simplefilter("ignore", PerformanceWarning)


_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "daily": 24 * 60 * 60,
    "1d": 24 * 60 * 60,
    "weekly": 7 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}

_PANDAS_RULES = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "daily": "1D",
    "1d": "1D",
    "weekly": "1W",
    "1w": "1W",
}


def compute_enriched_ohlcv_features(
    df: pd.DataFrame,
    *,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    label_horizons: Sequence[int] = DEFAULT_HORIZONS,
    include_labels: bool = True,
    include_sequence_features: bool = True,
    include_multi_timeframe: bool = True,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    source_coverage: Optional[Mapping[str, int]] = None,
) -> pd.DataFrame:
    """
    Build a broad, causal OHLCV-only feature table.

    The function keeps existing columns from the input frame, normalizes OHLCV
    names to lowercase, and adds deterministic features grouped around returns,
    candles, volatility, trend, momentum, mean reversion, volume, market
    structure, regimes, risk, anomalies, patterns, sequence summaries, labels
    and completed higher-timeframe projections.
    """

    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="last")].copy()
    frame = standardize_ohlcv_columns(df)
    required = ["open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise RuntimeError("Missing OHLCV columns for enriched features: %s" % missing)

    frame = frame.copy()
    for col in required + [
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame = frame.dropna(subset=["open", "high", "low", "close"]).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if frame.empty:
        return frame

    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0}))
    label_horizons = tuple(sorted({int(h) for h in label_horizons if int(h) > 0}))
    interval = _canonical_timeframe(interval or _infer_interval(frame.index) or "1h")

    o = frame["open"].astype(float)
    h = frame["high"].astype(float)
    l = frame["low"].astype(float)
    c = frame["close"].astype(float)
    v = frame["volume"].astype(float).clip(lower=0.0)
    qv = frame.get("quote_asset_volume", c * v).astype(float)
    trades = frame.get("number_of_trades", pd.Series(0.0, index=frame.index)).astype(float)
    taker_base = frame.get("taker_buy_base_asset_volume", v * 0.5).astype(float)
    taker_quote = frame.get("taker_buy_quote_asset_volume", qv * 0.5).astype(float)

    features: "OrderedDict[str, object]" = OrderedDict()
    _base_price_features(features, o, h, l, c, v, qv, trades, taker_base, taker_quote)

    log_return_1 = np.log(_safe_div(c, c.shift(1))).replace([np.inf, -np.inf], np.nan)
    simple_return_1 = c.pct_change()
    true_range = _true_range(h, l, c)
    candle_range = (h - l).clip(lower=0.0)
    body = (c - o).abs()
    upper_wick = (h - pd.concat([o, c], axis=1).max(axis=1)).clip(lower=0.0)
    lower_wick = (pd.concat([o, c], axis=1).min(axis=1) - l).clip(lower=0.0)
    close_pos = _safe_div(c - l, candle_range)
    dollar_volume = (c * v).replace([np.inf, -np.inf], np.nan)

    _single_bar_features(
        features,
        o,
        h,
        l,
        c,
        v,
        qv,
        trades,
        taker_base,
        taker_quote,
        simple_return_1,
        log_return_1,
        candle_range,
        body,
        upper_wick,
        lower_wick,
        close_pos,
        dollar_volume,
    )

    common_cache: Dict[str, pd.Series] = {
        "log_return_1": log_return_1,
        "simple_return_1": simple_return_1,
        "true_range": true_range,
        "candle_range": candle_range,
        "body": body,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "close_pos": close_pos,
        "dollar_volume": dollar_volume,
    }

    for n in horizons:
        _horizon_features(features, n, o, h, l, c, v, qv, trades, common_cache)

    _cross_horizon_features(features, horizons, c, v, common_cache)
    _stateful_features(features, o, h, l, c, v, common_cache)
    _technical_pattern_features(features, horizons, o, h, l, c, v, common_cache)
    _positioning_features(features, horizons, h, l, c, v, common_cache)
    _temporal_features(features, frame.index, candle_range, v, log_return_1)

    if include_sequence_features:
        _sequence_features(features, DEFAULT_SEQUENCE_HORIZONS, o, h, l, c, v, log_return_1, candle_range)

    if include_multi_timeframe:
        _multi_timeframe_features(features, frame, interval, timeframes)

    if include_labels:
        _label_features(features, label_horizons, h, l, c, log_return_1, common_cache)

    _canonical_dataset_aliases(features, horizons, h, l, c, v, common_cache)

    additions = pd.DataFrame(features, index=frame.index)
    out = pd.concat([frame, additions], axis=1).copy()
    out = out.loc[:, ~out.columns.duplicated(keep="last")]
    out = out.replace([np.inf, -np.inf], np.nan)

    label_cols = _label_columns(out.columns)
    numeric_cols = [
        col
        for col in out.columns
        if col not in label_cols and pd.api.types.is_numeric_dtype(out[col])
    ]
    if numeric_cols:
        out[numeric_cols] = out[numeric_cols].ffill().fillna(0.0)

    if symbol is not None:
        out["symbol"] = symbol
    elif "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str)
    out["interval"] = interval
    out["feature_version"] = FEATURE_VERSION
    out["feature_horizons"] = json.dumps(list(horizons), separators=(",", ":"))
    out["feature_count"] = int(len([col for col in out.columns if col not in {"symbol", "interval"}]))
    if source_coverage is not None:
        out["source_coverage"] = json.dumps(dict(source_coverage), sort_keys=True, separators=(",", ":"))

    out.index.name = "timestamp"
    return out


def _base_price_features(
    f: "OrderedDict[str, object]",
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    qv: pd.Series,
    trades: pd.Series,
    taker_base: pd.Series,
    taker_quote: pd.Series,
) -> None:
    f["typical_price"] = (h + l + c) / 3.0
    f["median_price"] = (h + l) / 2.0
    f["weighted_close"] = (h + l + 2.0 * c) / 4.0
    f["ohlc_average"] = (o + h + l + c) / 4.0
    f["hl2"] = (h + l) / 2.0
    f["hlc3"] = (h + l + c) / 3.0
    f["oc2"] = (o + c) / 2.0
    f["dollar_volume"] = c * v
    f["quote_volume_effective"] = qv
    f["trades"] = trades
    f["taker_buy_base"] = taker_base
    f["taker_buy_quote"] = taker_quote
    f["taker_buy_ratio_base"] = _safe_div(taker_base, v)
    f["taker_buy_ratio_quote"] = _safe_div(taker_quote, qv)
    f["taker_sell_base"] = (v - taker_base).clip(lower=0.0)
    f["taker_sell_quote"] = (qv - taker_quote).clip(lower=0.0)


def _single_bar_features(
    f: "OrderedDict[str, object]",
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    qv: pd.Series,
    trades: pd.Series,
    taker_base: pd.Series,
    taker_quote: pd.Series,
    ret: pd.Series,
    log_ret: pd.Series,
    candle_range: pd.Series,
    body: pd.Series,
    upper_wick: pd.Series,
    lower_wick: pd.Series,
    close_pos: pd.Series,
    dollar_volume: pd.Series,
) -> None:
    prev_close = c.shift(1)
    f["return_simple_1"] = ret
    f["log_return_1"] = log_ret
    f["open_to_close_return"] = _safe_div(c - o, o)
    f["high_to_low_range"] = _safe_div(h - l, c)
    f["close_to_high_distance"] = _safe_div(h - c, c)
    f["close_to_low_distance"] = _safe_div(c - l, c)
    f["gap_open"] = _safe_div(o - prev_close, prev_close)
    f["gap_high"] = _safe_div(h - h.shift(1), h.shift(1))
    f["gap_low"] = _safe_div(l - l.shift(1), l.shift(1))
    f["price_acceleration_1"] = ret.diff()
    f["roc_1"] = _safe_div(c - prev_close, prev_close)

    f["body_size"] = body
    f["body_size_pct"] = _safe_div(body, c)
    f["upper_wick"] = upper_wick
    f["lower_wick"] = lower_wick
    f["total_candle_range"] = candle_range
    f["body_range_ratio"] = _safe_div(body, candle_range)
    f["wick_body_ratio"] = _safe_div(upper_wick + lower_wick, body)
    f["upper_wick_range"] = _safe_div(upper_wick, candle_range)
    f["lower_wick_range"] = _safe_div(lower_wick, candle_range)
    f["close_position_in_range"] = close_pos
    f["bullish_candle"] = (c > o).astype(float)
    f["bearish_candle"] = (c < o).astype(float)
    f["doji_score"] = (1.0 - _safe_div(body, candle_range)).clip(0.0, 1.0)
    f["marubozu_score"] = (1.0 - _safe_div(upper_wick + lower_wick, candle_range)).clip(0.0, 1.0)
    f["hammer_score"] = (
        (1.0 - _safe_div(body, candle_range)).clip(0.0, 1.0)
        * _safe_div(lower_wick, candle_range)
        * (1.0 - _safe_div(upper_wick, candle_range)).clip(0.0, 1.0)
    )
    f["shooting_star_score"] = (
        (1.0 - _safe_div(body, candle_range)).clip(0.0, 1.0)
        * _safe_div(upper_wick, candle_range)
        * (1.0 - _safe_div(lower_wick, candle_range)).clip(0.0, 1.0)
    )
    prev_body_high = pd.concat([o.shift(1), c.shift(1)], axis=1).max(axis=1)
    prev_body_low = pd.concat([o.shift(1), c.shift(1)], axis=1).min(axis=1)
    body_high = pd.concat([o, c], axis=1).max(axis=1)
    body_low = pd.concat([o, c], axis=1).min(axis=1)
    f["engulfing_bullish"] = ((c > o) & (c.shift(1) < o.shift(1)) & (body_high >= prev_body_high) & (body_low <= prev_body_low)).astype(float)
    f["engulfing_bearish"] = ((c < o) & (c.shift(1) > o.shift(1)) & (body_high >= prev_body_high) & (body_low <= prev_body_low)).astype(float)
    f["inside_bar"] = ((h < h.shift(1)) & (l > l.shift(1))).astype(float)
    f["outside_bar"] = ((h > h.shift(1)) & (l < l.shift(1))).astype(float)

    f["true_range"] = _true_range(h, l, c)
    f["buy_pressure_proxy"] = close_pos * _safe_div(v, v.rolling(20, min_periods=1).mean())
    f["sell_pressure_proxy"] = (1.0 - close_pos) * _safe_div(v, v.rolling(20, min_periods=1).mean())
    f["candle_imbalance"] = _safe_div(c - o, candle_range)
    f["wick_imbalance"] = _safe_div(lower_wick - upper_wick, candle_range)
    f["aggression_proxy"] = _safe_div(body, c) * _safe_div(v, v.rolling(20, min_periods=1).mean())
    f["rejection_proxy"] = _safe_div(upper_wick + lower_wick, candle_range) * (1.0 - _safe_div(body, candle_range))
    f["absorption_proxy_bar"] = _safe_div(v, v.rolling(20, min_periods=1).mean()) * (1.0 - _safe_div(body, candle_range))
    f["closing_strength"] = close_pos
    f["opening_drive"] = _safe_div(c - o, o)
    f["late_rejection"] = np.where(c >= o, _safe_div(upper_wick, candle_range), _safe_div(lower_wick, candle_range))
    f["price_impact_proxy_1"] = _safe_div(ret.abs(), v)
    f["amihud_illiquidity_1"] = _safe_div(ret.abs(), dollar_volume)
    f["liquidity_proxy_1"] = _safe_div(v, candle_range)
    f["range_per_volume"] = _safe_div(candle_range, v)
    f["volume_per_range"] = _safe_div(v, candle_range)
    f["dollar_volume_per_range"] = _safe_div(dollar_volume, candle_range)
    f["thin_market_flag"] = ((_safe_div(v, v.rolling(20, min_periods=1).mean()) < 0.5) & (_safe_div(candle_range, c) > _safe_div(candle_range, c).rolling(20, min_periods=1).mean())).astype(float)


def _horizon_features(
    f: "OrderedDict[str, object]",
    n: int,
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    qv: pd.Series,
    trades: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    ret = cache["simple_return_1"]
    log_ret = cache["log_return_1"]
    tr = cache["true_range"]
    candle_range = cache["candle_range"]
    body = cache["body"]
    upper_wick = cache["upper_wick"]
    lower_wick = cache["lower_wick"]
    close_pos = cache["close_pos"]
    dollar_volume = cache["dollar_volume"]

    minp = _min_periods(n)
    safe_close = c.clip(lower=EPS)
    rolling_high = h.rolling(n, min_periods=minp).max()
    rolling_low = l.rolling(n, min_periods=minp).min()
    rolling_close_mean = c.rolling(n, min_periods=minp).mean()
    rolling_close_std = c.rolling(n, min_periods=minp).std()
    rolling_ret_std = log_ret.rolling(n, min_periods=minp).std()
    atr = tr.ewm(span=max(n, 1), adjust=False).mean()
    sma = rolling_close_mean
    ema = c.ewm(span=max(n, 1), adjust=False).mean()
    wma = _wma(c, n)
    hma = _hma(c, n)
    kama = _kama(c, n)
    dema = _dema(c, n)
    tema = _tema(c, n)
    median = c.rolling(n, min_periods=minp).median()
    q10 = c.rolling(n, min_periods=minp).quantile(0.10)
    q90 = c.rolling(n, min_periods=minp).quantile(0.90)
    vol_mean = v.rolling(n, min_periods=minp).mean()
    vol_std = v.rolling(n, min_periods=minp).std()
    typical = (h + l + c) / 3.0
    rolling_vwap = _safe_div((typical * v).rolling(n, min_periods=minp).sum(), v.rolling(n, min_periods=minp).sum())
    bb_upper = sma + 2.0 * rolling_close_std
    bb_lower = sma - 2.0 * rolling_close_std
    keltner_mid = ema
    keltner_upper = keltner_mid + 2.0 * atr
    keltner_lower = keltner_mid - 2.0 * atr
    donchian_width = (rolling_high - rolling_low).clip(lower=0.0)
    zscore_price = _safe_div(c - sma, rolling_close_std)
    zscore_return = _rolling_z(log_ret, n)
    volume_ratio = _safe_div(v, vol_mean)
    volume_z = _safe_div(v - vol_mean, vol_std)
    rsi = _rsi(c, max(n, 2))
    stoch = _safe_div(c - rolling_low, rolling_high - rolling_low)
    stoch_rsi = _safe_div(rsi - rsi.rolling(n, min_periods=minp).min(), rsi.rolling(n, min_periods=minp).max() - rsi.rolling(n, min_periods=minp).min())
    cci = _safe_div(typical - typical.rolling(n, min_periods=minp).mean(), 0.015 * typical.rolling(n, min_periods=minp).apply(lambda x: np.nanmean(np.abs(x - np.nanmean(x))), raw=True))
    force = c.diff(n) * v
    obv = (np.sign(c.diff()).fillna(0.0) * v).cumsum()
    ad_line = (_safe_div((c - l) - (h - c), h - l) * v).fillna(0.0).cumsum()
    cmf = _safe_div((_safe_div((c - l) - (h - c), h - l) * v).rolling(n, min_periods=minp).sum(), v.rolling(n, min_periods=minp).sum())
    mfi = _mfi(h, l, c, v, max(n, 2))
    eom = _safe_div(((h + l) / 2.0).diff(), _safe_div(v, h - l))
    nvi, pvi = _nvi_pvi(c, v)
    vpt = (ret.fillna(0.0) * v).cumsum()
    high_n_prev = rolling_high.shift(1)
    low_n_prev = rolling_low.shift(1)
    breakout_high = (c > high_n_prev).astype(float)
    breakdown_low = (c < low_n_prev).astype(float)
    wick_break_high = ((h > high_n_prev) & (c <= high_n_prev)).astype(float)
    wick_break_low = ((l < low_n_prev) & (c >= low_n_prev)).astype(float)
    trend_strength = _safe_div(ema.diff(n), atr)
    eff_ratio = _efficiency_ratio(c, n)
    lin_slope = _rolling_slope(c, n)
    lin_r2 = _rolling_r2(c, n)
    adx, di_plus, di_minus = _adx(h, l, c, n)
    aroon_up, aroon_down = _aroon(h, l, n)
    vortex_plus, vortex_minus = _vortex(h, l, c, n)
    choppiness = _choppiness(tr, h, l, n)
    hurst = _rolling_hurst(c, n)
    fractal_dim = 2.0 - hurst
    fdi = _fractal_dimension(c, n)

    prefix = str(n)
    f["return_simple_%s" % prefix] = c.pct_change(n)
    f["log_return_%s" % prefix] = np.log(_safe_div(c, c.shift(n)))
    f["rolling_return_%s" % prefix] = _safe_div(c, c.shift(n)) - 1.0
    f["cumulative_return_%s" % prefix] = (1.0 + ret.fillna(0.0)).rolling(n, min_periods=1).apply(np.prod, raw=True) - 1.0
    f["roc_%s" % prefix] = _safe_div(c - c.shift(n), c.shift(n))
    f["price_acceleration_%s" % prefix] = f["roc_%s" % prefix].diff()
    f["normalized_price_mean_%s" % prefix] = _safe_div(c, sma) - 1.0
    f["normalized_price_vol_%s" % prefix] = _safe_div(c - sma, rolling_close_std)
    f["price_normalized_by_close_open_%s" % prefix] = _safe_div(o, c)
    f["price_normalized_by_close_high_%s" % prefix] = _safe_div(h, c)
    f["price_normalized_by_close_low_%s" % prefix] = _safe_div(l, c)
    f["minmax_norm_close_%s" % prefix] = _safe_div(c - rolling_low, rolling_high - rolling_low)
    f["robust_scaled_close_%s" % prefix] = _safe_div(c - median, (q90 - q10))
    f["return_clipped_%s" % prefix] = f["log_return_%s" % prefix].clip(-0.20, 0.20)
    f["stationary_close_diff_%s" % prefix] = c.diff(n)

    f["atr_%s" % prefix] = atr
    f["atr_percent_%s" % prefix] = _safe_div(atr, safe_close)
    f["rolling_volatility_%s" % prefix] = rolling_ret_std
    f["realized_volatility_%s" % prefix] = rolling_ret_std * math.sqrt(max(n, 1))
    f["parkinson_volatility_%s" % prefix] = np.sqrt(_safe_div((np.log(_safe_div(h, l)) ** 2).rolling(n, min_periods=minp).mean(), 4.0 * math.log(2.0)))
    f["garman_klass_volatility_%s" % prefix] = np.sqrt((0.5 * (np.log(_safe_div(h, l)) ** 2) - (2.0 * math.log(2.0) - 1.0) * (np.log(_safe_div(c, o)) ** 2)).rolling(n, min_periods=minp).mean().clip(lower=0.0))
    f["rogers_satchell_volatility_%s" % prefix] = np.sqrt((np.log(_safe_div(h, c)) * np.log(_safe_div(h, o)) + np.log(_safe_div(l, c)) * np.log(_safe_div(l, o))).rolling(n, min_periods=minp).mean().clip(lower=0.0))
    open_gap = np.log(_safe_div(o, c.shift(1)))
    f["yang_zhang_volatility_%s" % prefix] = np.sqrt((open_gap.pow(2) + np.log(_safe_div(c, o)).pow(2) + f["rogers_satchell_volatility_%s" % prefix].pow(2)).rolling(n, min_periods=minp).mean().clip(lower=0.0))
    f["volatility_acceleration_%s" % prefix] = rolling_ret_std.diff()
    f["volatility_percentile_%s" % prefix] = _rolling_percentile_rank(rolling_ret_std, n)
    f["intrabar_range_expansion_%s" % prefix] = _safe_div(candle_range, candle_range.rolling(n, min_periods=minp).mean())
    f["volatility_compression_%s" % prefix] = (_rolling_percentile_rank(candle_range, n) < 0.20).astype(float)
    f["volatility_breakout_%s" % prefix] = (candle_range > candle_range.rolling(n, min_periods=minp).quantile(0.90).shift(1)).astype(float)
    f["volume_adjusted_volatility_%s" % prefix] = _safe_div(rolling_ret_std, volume_ratio)

    f["sma_%s" % prefix] = sma
    f["ema_%s" % prefix] = ema
    f["wma_%s" % prefix] = wma
    f["hma_%s" % prefix] = hma
    f["kama_%s" % prefix] = kama
    f["dema_%s" % prefix] = dema
    f["tema_%s" % prefix] = tema
    f["price_above_sma_%s" % prefix] = (c > sma).astype(float)
    f["price_above_ema_%s" % prefix] = (c > ema).astype(float)
    f["distance_sma_%s" % prefix] = _safe_div(c - sma, sma)
    f["distance_ema_%s" % prefix] = _safe_div(c - ema, ema)
    f["distance_hma_%s" % prefix] = _safe_div(c - hma, hma)
    f["distance_kama_%s" % prefix] = _safe_div(c - kama, kama)
    f["ema_slope_%s" % prefix] = _safe_div(ema.diff(), c)
    f["sma_slope_%s" % prefix] = _safe_div(sma.diff(), c)
    f["trend_strength_%s" % prefix] = trend_strength
    f["linear_regression_slope_%s" % prefix] = lin_slope
    f["linear_regression_r2_%s" % prefix] = lin_r2
    f["rolling_beta_vs_time_%s" % prefix] = lin_slope
    f["directional_persistence_%s" % prefix] = _safe_div(np.sign(c.diff()).rolling(n, min_periods=1).sum().abs(), n)
    f["higher_highs_count_%s" % prefix] = (h > h.shift(1)).astype(float).rolling(n, min_periods=1).sum()
    f["lower_lows_count_%s" % prefix] = (l < l.shift(1)).astype(float).rolling(n, min_periods=1).sum()
    f["trend_age_%s" % prefix] = _consecutive_same_sign(ema.diff())

    f["rsi_%s" % prefix] = rsi
    f["stochastic_rsi_%s" % prefix] = stoch_rsi
    f["stochastic_k_%s" % prefix] = stoch
    f["stochastic_d_%s" % prefix] = stoch.rolling(3, min_periods=1).mean()
    f["williams_r_%s" % prefix] = -100.0 * _safe_div(rolling_high - c, rolling_high - rolling_low)
    f["momentum_%s" % prefix] = c - c.shift(n)
    fast = c.ewm(span=max(2, n), adjust=False).mean()
    slow = c.ewm(span=max(3, 2 * n), adjust=False).mean()
    macd = fast - slow
    macd_signal = macd.ewm(span=max(2, min(18, n)), adjust=False).mean()
    f["macd_%s" % prefix] = macd
    f["macd_signal_%s" % prefix] = macd_signal
    f["macd_histogram_%s" % prefix] = macd - macd_signal
    f["ppo_%s" % prefix] = 100.0 * _safe_div(fast - slow, slow)
    f["cci_%s" % prefix] = cci
    f["tsi_%s" % prefix] = _tsi(c, max(n, 2))
    f["trix_%s" % prefix] = _trix(c, max(n, 2))
    f["ultimate_oscillator_%s" % prefix] = _ultimate_oscillator(h, l, c, max(2, n), max(3, 2 * n), max(4, 4 * n))
    f["awesome_oscillator_%s" % prefix] = ((h + l) / 2.0).rolling(max(2, min(n, 5)), min_periods=1).mean() - ((h + l) / 2.0).rolling(max(3, min(max(2 * n, 3), 34)), min_periods=1).mean()
    f["kst_%s" % prefix] = _kst(c, n)
    f["momentum_divergence_score_%s" % prefix] = _safe_div(f["roc_%s" % prefix], rolling_ret_std) - _safe_div(rsi - 50.0, 50.0)
    f["momentum_acceleration_%s" % prefix] = f["momentum_%s" % prefix].diff()
    f["momentum_percentile_%s" % prefix] = _rolling_percentile_rank(f["momentum_%s" % prefix], n)

    f["zscore_price_%s" % prefix] = zscore_price
    f["zscore_return_%s" % prefix] = zscore_return
    f["bollinger_mid_%s" % prefix] = sma
    f["bollinger_upper_%s" % prefix] = bb_upper
    f["bollinger_lower_%s" % prefix] = bb_lower
    f["bollinger_width_%s" % prefix] = _safe_div(bb_upper - bb_lower, sma)
    f["bollinger_percent_b_%s" % prefix] = _safe_div(c - bb_lower, bb_upper - bb_lower)
    f["distance_bollinger_upper_%s" % prefix] = _safe_div(bb_upper - c, c)
    f["distance_bollinger_lower_%s" % prefix] = _safe_div(c - bb_lower, c)
    f["keltner_mid_%s" % prefix] = keltner_mid
    f["keltner_upper_%s" % prefix] = keltner_upper
    f["keltner_lower_%s" % prefix] = keltner_lower
    f["keltner_width_%s" % prefix] = _safe_div(keltner_upper - keltner_lower, keltner_mid)
    f["donchian_upper_%s" % prefix] = rolling_high
    f["donchian_lower_%s" % prefix] = rolling_low
    f["donchian_position_%s" % prefix] = _safe_div(c - rolling_low, rolling_high - rolling_low)
    f["price_deviation_vwap_%s" % prefix] = _safe_div(c - rolling_vwap, rolling_vwap)
    f["reversion_pressure_%s" % prefix] = zscore_price.abs() * (1.0 - _safe_div(rsi - 50.0, 50.0).abs()).clip(0.0, 1.0)
    f["overextension_score_%s" % prefix] = zscore_price.abs() * _safe_div(atr, c)
    f["mean_reversion_probability_proxy_%s" % prefix] = ((zscore_price.abs() > 2.0).astype(float) * (f["momentum_acceleration_%s" % prefix].abs() < f["momentum_acceleration_%s" % prefix].rolling(n, min_periods=1).std()).astype(float))

    f["raw_volume_%s" % prefix] = v
    f["volume_sma_%s" % prefix] = vol_mean
    f["volume_ema_%s" % prefix] = v.ewm(span=max(n, 1), adjust=False).mean()
    f["volume_ratio_%s" % prefix] = volume_ratio
    f["volume_zscore_%s" % prefix] = volume_z
    f["volume_percentile_%s" % prefix] = _rolling_percentile_rank(v, n)
    f["volume_acceleration_%s" % prefix] = v.diff(n)
    f["volume_spike_%s" % prefix] = (volume_z > 2.0).astype(float)
    f["volume_dry_up_%s" % prefix] = (volume_ratio < 0.5).astype(float)
    f["obv"] = obv
    f["obv_slope_%s" % prefix] = _rolling_slope(obv, n)
    f["chaikin_money_flow_%s" % prefix] = cmf
    f["accumulation_distribution_line"] = ad_line
    f["money_flow_index_%s" % prefix] = mfi
    f["force_index_%s" % prefix] = force
    f["ease_of_movement_%s" % prefix] = eom.rolling(n, min_periods=minp).mean()
    f["negative_volume_index"] = nvi
    f["positive_volume_index"] = pvi
    f["volume_price_trend"] = vpt
    f["volume_imbalance_proxy_%s" % prefix] = _safe_div((v * (c >= o).astype(float)).rolling(n, min_periods=1).sum() - (v * (c < o).astype(float)).rolling(n, min_periods=1).sum(), v.rolling(n, min_periods=1).sum())

    f["vwap_%s" % prefix] = rolling_vwap
    f["rolling_vwap_%s" % prefix] = rolling_vwap
    f["distance_vwap_%s" % prefix] = _safe_div(c - rolling_vwap, rolling_vwap)
    f["anchored_vwap"] = _safe_div((typical * v).cumsum(), v.cumsum())
    f["volume_weighted_return_%s" % prefix] = _safe_div((ret * v).rolling(n, min_periods=1).sum(), v.rolling(n, min_periods=1).sum())
    f["price_impact_proxy_%s" % prefix] = _safe_div(ret.abs(), v.rolling(n, min_periods=1).sum())
    f["liquidity_proxy_%s" % prefix] = _safe_div(v.rolling(n, min_periods=1).sum(), candle_range.rolling(n, min_periods=1).sum())
    f["illiquidity_amihud_%s" % prefix] = _safe_div(ret.abs().rolling(n, min_periods=1).mean(), dollar_volume.rolling(n, min_periods=1).mean())
    f["volume_confirmed_breakout_%s" % prefix] = ((breakout_high > 0) & (volume_ratio > 1.25)).astype(float)
    f["volume_confirmed_trend_%s" % prefix] = ((trend_strength > 0) & (v > vol_mean)).astype(float) - ((trend_strength < 0) & (v > vol_mean)).astype(float)
    f["divergence_price_volume_%s" % prefix] = np.sign(c.diff(n)) * -np.sign(vol_mean.diff(n))
    f["effort_vs_result_%s" % prefix] = _safe_div(volume_ratio, body.rolling(n, min_periods=1).mean() / safe_close)
    f["absorption_proxy_%s" % prefix] = volume_ratio * (1.0 - _safe_div(body, candle_range)).clip(0.0, 1.0)
    f["climax_volume_%s" % prefix] = ((volume_z > 2.0) & (f["roc_%s" % prefix].abs() > f["roc_%s" % prefix].rolling(n, min_periods=1).std())).astype(float)

    pivot = (h.shift(1) + l.shift(1) + c.shift(1)) / 3.0
    r1 = 2 * pivot - l.shift(1)
    s1 = 2 * pivot - h.shift(1)
    f["rolling_high_%s" % prefix] = rolling_high
    f["rolling_low_%s" % prefix] = rolling_low
    f["distance_rolling_high_%s" % prefix] = _safe_div(rolling_high - c, c)
    f["distance_rolling_low_%s" % prefix] = _safe_div(c - rolling_low, c)
    f["breakout_high_%s" % prefix] = breakout_high
    f["breakdown_low_%s" % prefix] = breakdown_low
    f["pivot_point_%s" % prefix] = pivot
    f["pivot_r1_%s" % prefix] = r1
    f["pivot_s1_%s" % prefix] = s1
    f["distance_pivot_%s" % prefix] = _safe_div(c - pivot, c)
    for ratio in (0.236, 0.382, 0.5, 0.618, 0.786):
        level = rolling_high - ratio * (rolling_high - rolling_low)
        name = str(ratio).replace(".", "")
        f["fibonacci_retracement_%s_%s" % (name, prefix)] = level
        f["distance_fibonacci_%s_%s" % (name, prefix)] = _safe_div(c - level, c)
    f["local_swing_high_%s" % prefix] = ((h == rolling_high) & (h.shift(1) < h)).astype(float)
    f["local_swing_low_%s" % prefix] = ((l == rolling_low) & (l.shift(1) > l)).astype(float)
    tolerance = atr * 0.25
    f["support_touches_%s" % prefix] = ((l - rolling_low).abs() <= tolerance).astype(float).rolling(n, min_periods=1).sum()
    f["resistance_touches_%s" % prefix] = ((h - rolling_high).abs() <= tolerance).astype(float).rolling(n, min_periods=1).sum()
    f["rejection_count_%s" % prefix] = ((upper_wick + lower_wick) > body * 2.0).astype(float).rolling(n, min_periods=1).sum()
    f["support_strength_%s" % prefix] = f["support_touches_%s" % prefix] * volume_ratio
    f["resistance_strength_%s" % prefix] = f["resistance_touches_%s" % prefix] * volume_ratio
    f["distance_nearest_support_%s" % prefix] = _safe_div(c - rolling_low, atr)
    f["distance_nearest_resistance_%s" % prefix] = _safe_div(rolling_high - c, atr)

    f["higher_high_%s" % prefix] = (h > rolling_high.shift(1)).astype(float)
    f["higher_low_%s" % prefix] = (l > rolling_low.shift(1)).astype(float)
    f["lower_high_%s" % prefix] = (h < rolling_high.shift(1)).astype(float)
    f["lower_low_%s" % prefix] = (l < rolling_low.shift(1)).astype(float)
    f["break_of_structure_bullish_%s" % prefix] = breakout_high
    f["break_of_structure_bearish_%s" % prefix] = breakdown_low
    f["change_of_character_%s" % prefix] = (np.sign(f["break_of_structure_bullish_%s" % prefix] - f["break_of_structure_bearish_%s" % prefix]).diff().abs() > 0).astype(float)
    f["swing_direction_%s" % prefix] = np.sign(ema.diff(n)).fillna(0.0)
    f["swing_length_%s" % prefix] = _consecutive_same_sign(ema.diff())
    f["swing_magnitude_%s" % prefix] = _safe_div(c - c.shift(n), atr)
    f["pullback_depth_%s" % prefix] = np.where(f["swing_direction_%s" % prefix] >= 0, _safe_div(rolling_high - c, rolling_high - rolling_low), _safe_div(c - rolling_low, rolling_high - rolling_low))
    f["impulse_pullback_ratio_%s" % prefix] = _safe_div(f["roc_%s" % prefix].abs(), f["pullback_depth_%s" % prefix])
    f["trend_phase_%s" % prefix] = _trend_phase(trend_strength, zscore_price, volume_ratio)
    f["range_regime_%s" % prefix] = ((donchian_width / safe_close) < (donchian_width / safe_close).rolling(n, min_periods=1).quantile(0.35)).astype(float)
    f["breakout_regime_%s" % prefix] = ((breakout_high + breakdown_low) > 0).astype(float)
    f["retest_detection_%s" % prefix] = (((c - high_n_prev).abs() < atr * 0.5) | ((c - low_n_prev).abs() < atr * 0.5)).astype(float)
    f["false_breakout_%s" % prefix] = ((wick_break_high + wick_break_low) > 0).astype(float)
    f["stop_hunt_proxy_%s" % prefix] = f["false_breakout_%s" % prefix] * (upper_wick + lower_wick) / candle_range.replace(0, np.nan)

    f["trend_regime_%s" % prefix] = np.select([trend_strength > 0.25, trend_strength < -0.25], [1.0, -1.0], default=0.0)
    f["volatility_regime_%s" % prefix] = np.select([_rolling_percentile_rank(atr, n) > 0.75, _rolling_percentile_rank(atr, n) < 0.25], [1.0, -1.0], default=0.0)
    f["volume_regime_%s" % prefix] = np.select([volume_ratio > 1.5, volume_ratio < 0.7], [1.0, -1.0], default=0.0)
    f["momentum_regime_%s" % prefix] = np.select([rsi > 55.0, rsi < 45.0], [1.0, -1.0], default=0.0)
    f["mean_reversion_regime_%s" % prefix] = (zscore_price.abs() > 2.0).astype(float)
    f["expansion_regime_%s" % prefix] = (atr > atr.rolling(n, min_periods=1).mean()).astype(float)
    f["compression_regime_%s" % prefix] = (atr < atr.rolling(n, min_periods=1).quantile(0.25)).astype(float)
    f["risk_on_proxy_%s" % prefix] = ((rsi > 55.0) & (volume_ratio > 1.0) & (_rolling_percentile_rank(atr, n) < 0.75)).astype(float)
    f["risk_off_proxy_%s" % prefix] = ((f["roc_%s" % prefix] < 0) & (_rolling_percentile_rank(atr, n) > 0.75)).astype(float)
    f["choppy_market_score_%s" % prefix] = _safe_div(choppiness, 100.0) * (1.0 - lin_r2).clip(0.0, 1.0)
    f["trending_market_score_%s" % prefix] = lin_r2 * _safe_div(adx, 100.0)
    f["noise_to_signal_ratio_%s" % prefix] = _safe_div(c.diff().abs().rolling(n, min_periods=1).sum(), (c - c.shift(n)).abs())

    f["adx_%s" % prefix] = adx
    f["di_plus_%s" % prefix] = di_plus
    f["di_minus_%s" % prefix] = di_minus
    f["di_spread_%s" % prefix] = di_plus - di_minus
    f["aroon_up_%s" % prefix] = aroon_up
    f["aroon_down_%s" % prefix] = aroon_down
    f["aroon_oscillator_%s" % prefix] = aroon_up - aroon_down
    f["vortex_plus_%s" % prefix] = vortex_plus
    f["vortex_minus_%s" % prefix] = vortex_minus
    f["choppiness_index_%s" % prefix] = choppiness
    f["efficiency_ratio_%s" % prefix] = eff_ratio
    f["fractal_dimension_index_%s" % prefix] = fdi
    f["hurst_exponent_%s" % prefix] = hurst
    f["fractal_dimension_%s" % prefix] = fractal_dim
    f["katz_fractal_dimension_%s" % prefix] = _katz_fd(c, n)
    f["higuchi_fractal_dimension_%s" % prefix] = fdi
    f["dfa_exponent_%s" % prefix] = hurst
    f["approximate_entropy_%s" % prefix] = _rolling_entropy(log_ret, n)
    f["sample_entropy_%s" % prefix] = _rolling_entropy(log_ret.diff().abs(), n)
    f["permutation_entropy_%s" % prefix] = _permutation_entropy(c, n)
    f["multi_scale_volatility_%s" % prefix] = rolling_ret_std
    f["multi_scale_trend_%s" % prefix] = lin_slope
    f["noise_ratio_%s" % prefix] = f["noise_to_signal_ratio_%s" % prefix]

    f["bollinger_squeeze_%s" % prefix] = (f["bollinger_width_%s" % prefix] < f["bollinger_width_%s" % prefix].rolling(n, min_periods=1).quantile(0.20)).astype(float)
    f["ttm_squeeze_%s" % prefix] = ((bb_upper < keltner_upper) & (bb_lower > keltner_lower)).astype(float)
    f["range_compression_%s" % prefix] = (candle_range < candle_range.rolling(n, min_periods=1).quantile(0.25)).astype(float)
    f["atr_compression_%s" % prefix] = (atr < atr.rolling(n, min_periods=1).quantile(0.25)).astype(float)
    f["volatility_contraction_pattern_%s" % prefix] = (candle_range.rolling(min(max(n, 3), 10), min_periods=2).apply(lambda x: float(np.all(np.diff(x) <= 0)), raw=True)).fillna(0.0)
    f["expansion_candle_%s" % prefix] = (candle_range > candle_range.rolling(n, min_periods=1).mean() * 1.8).astype(float)
    f["breakout_from_squeeze_%s" % prefix] = (((breakout_high + breakdown_low) > 0) & (f["bollinger_squeeze_%s" % prefix].shift(1) > 0)).astype(float)
    f["range_expansion_ratio_%s" % prefix] = _safe_div(candle_range, candle_range.rolling(n, min_periods=1).mean())

    f["rolling_mean_close_%s" % prefix] = sma
    f["rolling_median_close_%s" % prefix] = median
    f["rolling_std_close_%s" % prefix] = rolling_close_std
    f["rolling_min_close_%s" % prefix] = c.rolling(n, min_periods=minp).min()
    f["rolling_max_close_%s" % prefix] = c.rolling(n, min_periods=minp).max()
    f["rolling_skewness_return_%s" % prefix] = log_ret.rolling(n, min_periods=minp).skew()
    f["rolling_kurtosis_return_%s" % prefix] = log_ret.rolling(n, min_periods=minp).kurt()
    f["rolling_quantile_10_close_%s" % prefix] = q10
    f["rolling_quantile_90_close_%s" % prefix] = q90
    f["rolling_percentile_rank_close_%s" % prefix] = _rolling_percentile_rank(c, n)
    f["rolling_entropy_return_%s" % prefix] = _rolling_entropy(log_ret, n)
    f["rolling_autocorrelation_return_%s" % prefix] = _rolling_autocorr(log_ret, n)
    f["rolling_correlation_price_volume_%s" % prefix] = c.rolling(n, min_periods=minp).corr(v)
    f["rolling_covariance_price_volume_%s" % prefix] = c.rolling(n, min_periods=minp).cov(v)
    roll_peak = c.rolling(n, min_periods=1).max()
    roll_dd = _safe_div(c - roll_peak, roll_peak)
    f["rolling_drawdown_%s" % prefix] = roll_dd
    f["rolling_max_drawdown_%s" % prefix] = roll_dd.rolling(n, min_periods=1).min()
    f["rolling_sharpe_proxy_%s" % prefix] = _safe_div(log_ret.rolling(n, min_periods=minp).mean(), rolling_ret_std)
    downside = log_ret.where(log_ret < 0.0, 0.0).rolling(n, min_periods=minp).std()
    f["rolling_sortino_proxy_%s" % prefix] = _safe_div(log_ret.rolling(n, min_periods=minp).mean(), downside)
    f["rolling_win_rate_%s" % prefix] = (ret > 0.0).astype(float).rolling(n, min_periods=1).mean()
    f["rolling_average_gain_%s" % prefix] = ret.clip(lower=0.0).rolling(n, min_periods=1).mean()
    f["rolling_average_loss_%s" % prefix] = (-ret.clip(upper=0.0)).rolling(n, min_periods=1).mean()

    f["max_drawdown_%s" % prefix] = f["rolling_max_drawdown_%s" % prefix]
    f["downside_volatility_%s" % prefix] = downside
    f["upside_volatility_%s" % prefix] = log_ret.where(log_ret > 0.0, 0.0).rolling(n, min_periods=minp).std()
    f["tail_risk_proxy_%s" % prefix] = (log_ret.abs() > log_ret.abs().rolling(n, min_periods=1).quantile(0.95)).astype(float).rolling(n, min_periods=1).mean()
    f["extreme_return_flag_%s" % prefix] = (zscore_return.abs() > 3.0).astype(float)
    f["crash_score_%s" % prefix] = (-zscore_return).clip(lower=0.0) * volume_ratio * _rolling_percentile_rank(atr, n)
    f["pump_score_%s" % prefix] = zscore_return.clip(lower=0.0) * volume_ratio * _rolling_percentile_rank(atr, n)
    f["volatility_adjusted_return_%s" % prefix] = _safe_div(f["log_return_%s" % prefix], rolling_ret_std)
    f["drawdown_acceleration_%s" % prefix] = roll_dd.diff()

    f["abnormal_return_%s" % prefix] = (zscore_return.abs() > 3.0).astype(float)
    f["abnormal_volume_%s" % prefix] = (volume_z.abs() > 3.0).astype(float)
    f["abnormal_range_%s" % prefix] = (_rolling_z(candle_range, n).abs() > 3.0).astype(float)
    f["abnormal_wick_%s" % prefix] = (_rolling_z(upper_wick + lower_wick, n).abs() > 3.0).astype(float)
    f["abnormal_gap_%s" % prefix] = (_rolling_z(_safe_div(o - c.shift(1), c.shift(1)), n).abs() > 3.0).astype(float)
    f["price_spike_%s" % prefix] = f["abnormal_return_%s" % prefix]
    f["volatility_spike_%s" % prefix] = (_rolling_z(atr, n) > 3.0).astype(float)
    f["liquidity_shock_proxy_%s" % prefix] = ((_rolling_z(candle_range, n) > 2.0) & (volume_ratio < 0.75)).astype(float)
    f["absorption_anomaly_%s" % prefix] = ((volume_z > 2.0) & (_safe_div(body, candle_range) < 0.25)).astype(float)
    f["exhaustion_anomaly_%s" % prefix] = ((volume_z > 2.0) & ((upper_wick + lower_wick) > body * 2.0) & (f["roc_%s" % prefix].abs() > rolling_ret_std)).astype(float)
    f["regime_shift_score_%s" % prefix] = (_rolling_z(rolling_ret_std, n).abs() + _rolling_z(vol_mean, n).abs()) / 2.0

    f["distance_high_%s" % prefix] = _safe_div(rolling_high - c, c)
    f["distance_low_%s" % prefix] = _safe_div(c - rolling_low, c)
    f["distance_atr_stop_long_%s" % prefix] = _safe_div(c - (c - 2.0 * atr), atr)
    f["distance_atr_stop_short_%s" % prefix] = _safe_div((c + 2.0 * atr) - c, atr)
    f["distance_pivot_high_%s" % prefix] = _safe_div(rolling_high - c, atr)
    f["distance_pivot_low_%s" % prefix] = _safe_div(c - rolling_low, atr)
    f["distance_normalized_by_atr_%s" % prefix] = _safe_div(c - sma, atr)
    f["distance_normalized_by_volatility_%s" % prefix] = _safe_div(c - sma, rolling_ret_std * c)

    f["breakout_above_high_%s" % prefix] = breakout_high
    f["breakdown_below_low_%s" % prefix] = breakdown_low
    f["breakout_strength_%s" % prefix] = _safe_div(c - high_n_prev, atr).clip(lower=0.0) - _safe_div(low_n_prev - c, atr).clip(lower=0.0)
    f["breakout_volume_confirmation_%s" % prefix] = ((breakout_high + breakdown_low) > 0).astype(float) * (volume_ratio > 1.25).astype(float)
    f["breakout_volatility_confirmation_%s" % prefix] = ((breakout_high + breakdown_low) > 0).astype(float) * (_rolling_percentile_rank(atr, n) > 0.70).astype(float)
    f["close_above_breakout_level_%s" % prefix] = (c > high_n_prev).astype(float)
    f["wick_only_breakout_%s" % prefix] = ((wick_break_high + wick_break_low) > 0).astype(float)
    f["failed_breakout_%s" % prefix] = ((breakout_high.shift(1) > 0) & (c < high_n_prev.shift(1))).astype(float) + ((breakdown_low.shift(1) > 0) & (c > low_n_prev.shift(1))).astype(float)
    f["retest_success_%s" % prefix] = ((f["retest_detection_%s" % prefix] > 0) & (np.sign(c.diff()) == np.sign(c.diff(n)))).astype(float)
    f["retest_failure_%s" % prefix] = ((f["retest_detection_%s" % prefix] > 0) & (np.sign(c.diff()) != np.sign(c.diff(n)))).astype(float)
    f["breakout_after_squeeze_%s" % prefix] = f["breakout_from_squeeze_%s" % prefix]
    f["breakout_continuation_score_%s" % prefix] = breakout_high * volume_ratio * lin_r2 - breakdown_low * volume_ratio * lin_r2

    f["pullback_duration_%s" % prefix] = _consecutive_same_sign(-ema.diff())
    f["pullback_volume_%s" % prefix] = _safe_div(v.rolling(n, min_periods=1).mean(), vol_mean)
    f["pullback_volatility_%s" % prefix] = _safe_div(atr, atr.rolling(n, min_periods=1).mean())
    f["pullback_to_ema_%s" % prefix] = (_safe_div((c - ema).abs(), atr) < 0.5).astype(float)
    f["pullback_to_vwap_%s" % prefix] = (_safe_div((c - rolling_vwap).abs(), atr) < 0.5).astype(float)
    f["pullback_to_support_%s" % prefix] = (_safe_div(c - rolling_low, atr) < 1.0).astype(float)
    f["pullback_slope_%s" % prefix] = _rolling_slope(c - ema, n)
    f["impulse_before_pullback_%s" % prefix] = f["roc_%s" % prefix].shift(1)
    f["pullback_quality_score_%s" % prefix] = (lin_r2 * (1.0 - _rolling_percentile_rank(atr, n))).clip(0.0, 1.0)
    f["shallow_pullback_%s" % prefix] = (f["pullback_depth_%s" % prefix] < 0.382).astype(float)
    f["deep_pullback_%s" % prefix] = (f["pullback_depth_%s" % prefix] > 0.618).astype(float)

    f["dollar_volume_%s" % prefix] = dollar_volume
    f["turnover_proxy_%s" % prefix] = volume_ratio
    f["slippage_proxy_%s" % prefix] = _safe_div(candle_range, c) * _safe_div(1.0, volume_ratio)
    f["liquidity_regime_%s" % prefix] = np.select([volume_ratio > 1.5, volume_ratio < 0.5], [1.0, -1.0], default=0.0)
    f["impact_score_%s" % prefix] = _safe_div(ret.abs(), dollar_volume)

    f["fourier_dominant_cycle_%s" % prefix] = _dominant_cycle(c, n)
    f["fourier_component_1_%s" % prefix] = _fft_component(c, n, 1)
    f["fourier_component_2_%s" % prefix] = _fft_component(c, n, 2)
    f["hilbert_trendline_%s" % prefix] = ema
    f["hilbert_sine_wave_%s" % prefix] = np.sin(np.arctan2(c - ema, atr))
    f["ehlers_lowpass_%s" % prefix] = ema
    f["lowpass_filtered_price_%s" % prefix] = ema
    f["highpass_filtered_price_%s" % prefix] = c - ema
    f["bandpass_signal_%s" % prefix] = c.ewm(span=max(2, n), adjust=False).mean() - c.ewm(span=max(3, 2 * n), adjust=False).mean()
    f["spectral_entropy_%s" % prefix] = _spectral_entropy(c, n)
    f["cycle_strength_%s" % prefix] = _safe_div(f["bandpass_signal_%s" % prefix].abs(), atr)
    f["phase_angle_%s" % prefix] = np.arctan2(f["bandpass_signal_%s" % prefix], atr)


def _cross_horizon_features(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    c: pd.Series,
    v: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    for n in horizons:
        longer = 200 if n < 200 else None
        if longer and "rolling_volatility_%s" % longer in f:
            f["volatility_ratio_%s_%s" % (n, longer)] = _safe_div(f["rolling_volatility_%s" % n], f["rolling_volatility_%s" % longer])
        if longer and "sma_%s" % longer in f:
            f["ma_spread_%s_%s" % (n, longer)] = _safe_div(f["sma_%s" % n] - f["sma_%s" % longer], f["sma_%s" % longer])
            f["moving_average_crossover_%s_%s" % (n, longer)] = (f["sma_%s" % n] > f["sma_%s" % longer]).astype(float)
    if "ema_20" in f and "ema_50" in f:
        f["fast_ema_cross"] = (f["ema_20"] > f["ema_50"]).astype(float)
    if "sma_20" in f and "sma_50" in f and "sma_200" in f:
        f["long_term_trend_score"] = ((c > f["sma_200"]).astype(float) + (f["sma_20"] > f["sma_50"]).astype(float) + (f["sma_50"] > f["sma_200"]).astype(float)) / 3.0
        f["monthly_trend"] = _safe_div(c - c.shift(min(720, len(c))), c.shift(min(720, len(c))))
        f["weekly_trend"] = _safe_div(c - c.shift(min(168, len(c))), c.shift(min(168, len(c))))
        f["sma_200_regime"] = np.where(c > f["sma_200"], 1.0, -1.0)
    if "rolling_volatility_200" in f:
        f["long_term_volatility_percentile"] = _rolling_percentile_rank(f["rolling_volatility_200"], 200)
    if "obv" in f:
        f["long_term_volume_trend"] = _rolling_slope(f["obv"], min(200, max(horizons)))


def _stateful_features(
    f: "OrderedDict[str, object]",
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    peak = c.cummax()
    trough = c.cummin()
    drawdown = _safe_div(c - peak, peak)
    runup = _safe_div(c - trough, trough)
    f["current_drawdown"] = drawdown
    f["current_runup"] = runup
    f["time_under_water"] = _time_under_water(c)
    f["recovery_factor"] = _safe_div(runup.abs(), drawdown.abs())
    f["long_term_drawdown"] = drawdown
    f["macro_regime_proxy_ohlcv"] = np.select(
        [(c > c.ewm(span=200, adjust=False).mean()) & (drawdown > -0.10), drawdown < -0.20],
        [1.0, -1.0],
        default=0.0,
    )
    f["bull_market_score"] = ((c > c.ewm(span=200, adjust=False).mean()).astype(float) + (drawdown > -0.10).astype(float)) / 2.0
    f["bear_market_score"] = ((c < c.ewm(span=200, adjust=False).mean()).astype(float) + (drawdown < -0.20).astype(float)) / 2.0
    f["accumulation_score_long_term"] = (v > v.rolling(100, min_periods=1).mean()).astype(float) * (cache["candle_range"] < cache["candle_range"].rolling(100, min_periods=1).median()).astype(float)
    f["distribution_score_long_term"] = f["accumulation_score_long_term"] * (c < c.rolling(50, min_periods=1).max()).astype(float)
    f["major_breakout_score"] = (c > h.rolling(200, min_periods=20).max().shift(1)).astype(float)

    session_key = pd.Series(c.index.date, index=c.index)
    session_open = o.groupby(session_key).transform("first")
    session_high = h.groupby(session_key).cummax()
    session_low = l.groupby(session_key).cummin()
    session_volume = v.groupby(session_key).cumsum()
    typical = (h + l + c) / 3.0
    session_vwap = _safe_div((typical * v).groupby(session_key).cumsum(), v.groupby(session_key).cumsum())
    f["session_open"] = session_open
    f["session_high"] = session_high
    f["session_low"] = session_low
    f["session_close"] = c
    f["session_vwap"] = session_vwap
    f["session_volume"] = session_volume
    f["session_range"] = session_high - session_low
    f["distance_session_high"] = _safe_div(session_high - c, c)
    f["distance_session_low"] = _safe_div(c - session_low, c)
    first_hour = c.index.hour == 0
    opening_high = h.where(first_hour).groupby(session_key).cummax().groupby(session_key).ffill()
    opening_low = l.where(first_hour).groupby(session_key).cummin().groupby(session_key).ffill()
    f["opening_range_high"] = opening_high
    f["opening_range_low"] = opening_low
    f["opening_range_breakout"] = ((c > opening_high) | (c < opening_low)).astype(float)
    f["session_trend"] = _safe_div(c - session_open, session_open)
    f["session_volatility"] = cache["log_return_1"].groupby(session_key).expanding().std().reset_index(level=0, drop=True)
    f["session_volume_percentile"] = _rolling_percentile_rank(session_volume, 20)


def _technical_pattern_features(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    for n in horizons:
        minp = _min_periods(n)
        rh = h.rolling(n, min_periods=minp).max()
        rl = l.rolling(n, min_periods=minp).min()
        atr = f.get("atr_%s" % n, cache["true_range"].rolling(n, min_periods=1).mean())
        tolerance = atr * 0.5
        high_touches = ((rh - h).abs() <= tolerance).astype(float).rolling(n, min_periods=1).sum()
        low_touches = ((l - rl).abs() <= tolerance).astype(float).rolling(n, min_periods=1).sum()
        prefix = str(n)
        f["double_top_%s" % prefix] = ((high_touches >= 2) & (c < c.rolling(max(2, n // 2), min_periods=1).mean())).astype(float)
        f["double_bottom_%s" % prefix] = ((low_touches >= 2) & (c > c.rolling(max(2, n // 2), min_periods=1).mean())).astype(float)
        f["triple_top_%s" % prefix] = (high_touches >= 3).astype(float)
        f["triple_bottom_%s" % prefix] = (low_touches >= 3).astype(float)
        left = max(2, n // 3)
        f["head_and_shoulders_%s" % prefix] = ((h.shift(2 * left) < h.shift(left)) & (h < h.shift(left)) & (c < c.shift(left))).astype(float)
        f["inverse_head_and_shoulders_%s" % prefix] = ((l.shift(2 * left) > l.shift(left)) & (l > l.shift(left)) & (c > c.shift(left))).astype(float)
        high_slope = _rolling_slope(h, n)
        low_slope = _rolling_slope(l, n)
        f["ascending_triangle_%s" % prefix] = ((high_slope.abs() < _safe_div(atr, c)) & (low_slope > 0)).astype(float)
        f["descending_triangle_%s" % prefix] = ((low_slope.abs() < _safe_div(atr, c)) & (high_slope < 0)).astype(float)
        f["symmetrical_triangle_%s" % prefix] = ((high_slope < 0) & (low_slope > 0)).astype(float)
        impulse = _safe_div(c - c.shift(n), c.shift(n)).abs()
        narrow = cache["candle_range"].rolling(n, min_periods=1).mean() < cache["candle_range"].rolling(max(n * 2, 2), min_periods=1).mean()
        f["flag_%s" % prefix] = ((impulse > impulse.rolling(max(n * 2, 2), min_periods=1).quantile(0.75)) & narrow).astype(float)
        f["pennant_%s" % prefix] = (f["flag_%s" % prefix] * f["symmetrical_triangle_%s" % prefix]).astype(float)
        f["wedge_rising_%s" % prefix] = ((high_slope > 0) & (low_slope > 0) & (low_slope > high_slope)).astype(float)
        f["wedge_falling_%s" % prefix] = ((high_slope < 0) & (low_slope < 0) & (low_slope < high_slope)).astype(float)
        f["cup_and_handle_%s" % prefix] = ((c > c.shift(n)) & (c.rolling(n, min_periods=minp).min() < c.shift(n) * 0.95)).astype(float)
        f["range_breakout_%s" % prefix] = ((c > rh.shift(1)) | (c < rl.shift(1))).astype(float)
        f["range_fakeout_%s" % prefix] = ((h > rh.shift(1)) & (c < rh.shift(1)) | ((l < rl.shift(1)) & (c > rl.shift(1)))).astype(float)
        ma = c.rolling(n, min_periods=minp).mean()
        f["pullback_to_ma_%s" % prefix] = (_safe_div((c - ma).abs(), atr) < 0.5).astype(float)
        f["retest_after_breakout_%s" % prefix] = (((c - rh.shift(1)).abs() < atr * 0.5) | ((c - rl.shift(1)).abs() < atr * 0.5)).astype(float)


def _positioning_features(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    for n in horizons:
        atr = f.get("atr_%s" % n, cache["true_range"].rolling(n, min_periods=1).mean())
        rolling_high = f.get("rolling_high_%s" % n, h.rolling(n, min_periods=1).max())
        rolling_low = f.get("rolling_low_%s" % n, l.rolling(n, min_periods=1).min())
        expected_move = atr * math.sqrt(max(n, 1))
        stop_long = c - 2.0 * atr
        stop_short = c + 2.0 * atr
        take_profit_long = rolling_high
        take_profit_short = rolling_low
        risk_long = (c - stop_long).abs()
        risk_short = (stop_short - c).abs()
        prefix = str(n)
        f["atr_stop_distance_%s" % prefix] = 2.0 * atr
        f["volatility_stop_distance_%s" % prefix] = f.get("rolling_volatility_%s" % n, cache["log_return_1"].rolling(n, min_periods=1).std()) * c
        f["recent_swing_stop_long_%s" % prefix] = c - rolling_low
        f["recent_swing_stop_short_%s" % prefix] = rolling_high - c
        f["take_profit_distance_long_%s" % prefix] = take_profit_long - c
        f["take_profit_distance_short_%s" % prefix] = c - take_profit_short
        f["risk_reward_ratio_long_%s" % prefix] = _safe_div(take_profit_long - c, risk_long)
        f["risk_reward_ratio_short_%s" % prefix] = _safe_div(c - take_profit_short, risk_short)
        f["distance_to_invalidation_long_%s" % prefix] = risk_long
        f["distance_to_invalidation_short_%s" % prefix] = risk_short
        f["expected_move_%s" % prefix] = expected_move
        f["position_size_proxy_%s" % prefix] = _safe_div(0.01, _safe_div(risk_long, c))
        f["trade_duration_estimate_%s" % prefix] = _safe_div(expected_move, atr)
        f["exit_pressure_score_%s" % prefix] = f.get("reversal_score_%s" % prefix, 0.0)

    if {"trend_strength_20", "adx_20", "higher_high_20", "higher_low_20"}.issubset(f.keys()):
        f["trend_score"] = (
            np.tanh(f["trend_strength_20"]) + _safe_div(f["adx_20"], 50.0) + f["higher_high_20"] + f["higher_low_20"]
        ) / 4.0
    if {"rsi_14", "macd_histogram_14", "roc_14"}.issubset(f.keys()):
        f["momentum_score"] = (_safe_div(f["rsi_14"] - 50.0, 50.0) + np.tanh(f["macd_histogram_14"]) + np.tanh(f["roc_14"] * 10.0)) / 3.0
    if {"atr_percent_20", "bollinger_width_20"}.issubset(f.keys()):
        f["volatility_score"] = _rolling_percentile_rank(f["atr_percent_20"], 100) * 0.5 + _rolling_percentile_rank(f["bollinger_width_20"], 100) * 0.5
    if {"breakout_strength_20", "volume_ratio_20", "range_expansion_ratio_20"}.issubset(f.keys()):
        f["breakout_score"] = np.tanh(f["breakout_strength_20"]) * f["volume_ratio_20"] * f["range_expansion_ratio_20"]
    if {"zscore_price_20", "momentum_acceleration_20", "upper_wick_range", "lower_wick_range"}.issubset(f.keys()):
        f["reversal_score"] = f["zscore_price_20"].abs() * (1.0 - np.tanh(f["momentum_acceleration_20"].abs())) * (f["upper_wick_range"] + f["lower_wick_range"])
    if {"trend_score", "pullback_depth_20", "pullback_quality_score_20"}.issubset(f.keys()):
        f["pullback_score"] = f["trend_score"] * (1.0 - f["pullback_depth_20"].clip(0.0, 1.0)) * f["pullback_quality_score_20"]
    if {"dollar_volume_20", "impact_score_20"}.issubset(f.keys()):
        f["liquidity_score"] = _rolling_percentile_rank(f["dollar_volume_20"], 100) * (1.0 - _rolling_percentile_rank(f["impact_score_20"], 100))
    if {"volatility_score", "current_drawdown", "range_expansion_ratio_20"}.issubset(f.keys()):
        f["risk_score"] = f["volatility_score"] + f["current_drawdown"].abs() + _rolling_percentile_rank(f["range_expansion_ratio_20"], 100)
    if {"choppy_market_score_20", "adx_20"}.issubset(f.keys()):
        f["choppiness_score"] = f["choppy_market_score_20"] * (1.0 - _safe_div(f["adx_20"], 100.0))
    if {"volume_ratio_20", "obv_slope_20", "range_compression_20"}.issubset(f.keys()):
        f["accumulation_score"] = f["volume_ratio_20"] * (f["obv_slope_20"] > 0).astype(float) * f["range_compression_20"]
        f["distribution_score"] = f["volume_ratio_20"] * (f["obv_slope_20"] < 0).astype(float) * f["range_compression_20"]
    if {"exhaustion_anomaly_20", "climax_volume_20"}.issubset(f.keys()):
        f["exhaustion_score"] = f["exhaustion_anomaly_20"] + f["climax_volume_20"]
    if {"trend_score", "pullback_score", "volume_confirmed_trend_20"}.issubset(f.keys()):
        f["continuation_score"] = f["trend_score"] * (1.0 + f["pullback_score"]) * (1.0 + f["volume_confirmed_trend_20"].abs())
    if {"zscore_price_20", "momentum_acceleration_20"}.issubset(f.keys()):
        f["mean_reversion_score"] = f["zscore_price_20"].abs() * (f["momentum_acceleration_20"].abs() < f["momentum_acceleration_20"].rolling(20, min_periods=1).std()).astype(float)
    if {"trend_score", "liquidity_score", "choppiness_score"}.issubset(f.keys()):
        f["market_quality_score"] = f["trend_score"].abs() * f["liquidity_score"] * (1.0 - f["choppiness_score"]).clip(0.0, 1.0)


def _temporal_features(
    f: "OrderedDict[str, object]",
    index: pd.DatetimeIndex,
    candle_range: pd.Series,
    volume: pd.Series,
    log_return: pd.Series,
) -> None:
    idx = pd.DatetimeIndex(index)
    hour = pd.Series(idx.hour, index=index)
    dow = pd.Series(idx.dayofweek, index=index)
    month = pd.Series(idx.month, index=index)
    day = pd.Series(idx.day, index=index)
    week_of_month = ((day - 1) // 7 + 1).astype(float)
    f["hour_of_day"] = hour
    f["day_of_week"] = dow
    f["week_of_month"] = week_of_month
    f["month"] = month
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    f["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    f["month_sin"] = np.sin(2 * np.pi * month / 12.0)
    f["month_cos"] = np.cos(2 * np.pi * month / 12.0)
    f["session_asia"] = ((hour >= 0) & (hour < 8)).astype(float)
    f["session_europe"] = ((hour >= 7) & (hour < 16)).astype(float)
    f["session_us"] = ((hour >= 13) & (hour < 22)).astype(float)
    f["session"] = np.select([f["session_asia"] > 0, f["session_europe"] > 0, f["session_us"] > 0], [0.0, 1.0, 2.0], default=3.0)
    f["market_open_flag"] = (((hour == 0) | (hour == 8) | (hour == 13))).astype(float)
    f["market_close_flag"] = (((hour == 7) | (hour == 16) | (hour == 21))).astype(float)
    f["lunch_period_flag"] = ((hour >= 11) & (hour <= 13)).astype(float)
    f["weekend_gap"] = ((dow == 0) & (hour == 0)).astype(float) * log_return.abs()
    f["pre_news_proxy"] = (candle_range < candle_range.rolling(20, min_periods=1).quantile(0.25)).astype(float)
    f["intraday_volume_profile"] = _expanding_group_mean(volume, hour)
    f["intraday_volatility_profile"] = _expanding_group_mean(log_return.abs(), hour)
    session_start_hour = np.select([hour < 8, hour < 16, hour < 22], [0, 8, 13], default=22)
    session_end_hour = np.select([hour < 8, hour < 16, hour < 22], [8, 16, 22], default=24)
    f["time_since_session_open"] = (hour - session_start_hour).astype(float)
    f["time_until_session_close"] = (session_end_hour - hour).astype(float)
    first_hour_mask = (hour == session_start_hour).astype(float)
    f["first_hour_range"] = (candle_range * first_hour_mask).replace(0, np.nan).ffill().fillna(0.0)
    f["opening_range_breakout_time"] = (candle_range > f["first_hour_range"]).astype(float)


def _sequence_features(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    log_return: pd.Series,
    candle_range: pd.Series,
) -> None:
    f["consecutive_green_candles"] = _consecutive_true(c > o)
    f["consecutive_red_candles"] = _consecutive_true(c < o)
    f["consecutive_higher_closes"] = _consecutive_true(c > c.shift(1))
    f["consecutive_lower_closes"] = _consecutive_true(c < c.shift(1))
    f["consecutive_volume_increase"] = _consecutive_true(v > v.shift(1))
    f["consecutive_range_contraction"] = _consecutive_true(candle_range < candle_range.shift(1))
    for n in horizons:
        if n <= 0:
            continue
        prefix = str(n)
        f["last_returns_mean_%s" % prefix] = log_return.rolling(n, min_periods=1).mean()
        f["last_returns_std_%s" % prefix] = log_return.rolling(n, min_periods=1).std()
        f["last_ranges_mean_%s" % prefix] = candle_range.rolling(n, min_periods=1).mean()
        f["last_volumes_mean_%s" % prefix] = v.rolling(n, min_periods=1).mean()
        f["last_candle_directions_sum_%s" % prefix] = np.sign(c - o).rolling(n, min_periods=1).sum()
        f["sequence_entropy_%s" % prefix] = _rolling_entropy(np.sign(c - o), n)
        f["pattern_embedding_%s" % prefix] = _safe_div(f["last_candle_directions_sum_%s" % prefix], n)
        f["nbar_return_path_%s" % prefix] = _safe_div(c - c.shift(n), c.shift(n))
        if n <= 20:
            f["seq_last_returns_%s" % prefix] = _rolling_list(log_return, n)
            f["seq_last_candle_directions_%s" % prefix] = _rolling_list(np.sign(c - o), n)
            norm = pd.DataFrame({
                "o": _safe_div(o, c) - 1.0,
                "h": _safe_div(h, c) - 1.0,
                "l": _safe_div(l, c) - 1.0,
                "c": c * 0.0,
                "v": _safe_div(v, v.rolling(n, min_periods=1).mean()) - 1.0,
            }, index=c.index)
            f["seq_last_candles_norm_%s" % prefix] = _rolling_nested_list(norm, n)


def _multi_timeframe_features(
    f: "OrderedDict[str, object]",
    frame: pd.DataFrame,
    interval: str,
    timeframes: Sequence[str],
) -> None:
    base_seconds = _INTERVAL_SECONDS.get(_canonical_timeframe(interval), 0)
    htf_trends = []
    close = frame["close"].astype(float)
    for tf in timeframes:
        canonical = _canonical_timeframe(tf)
        seconds = _INTERVAL_SECONDS.get(canonical)
        if seconds is None or seconds < base_seconds:
            continue

        if seconds == base_seconds:
            htf = frame[["open", "high", "low", "close", "volume"]].copy()
        else:
            htf = _resample_ohlcv(frame, canonical)
        if htf.empty:
            continue

        htf_close = htf["close"].astype(float)
        htf_high = htf["high"].astype(float)
        htf_low = htf["low"].astype(float)
        htf_vol = htf["volume"].astype(float)
        htf_ret = np.log(_safe_div(htf_close, htf_close.shift(1)))
        htf_sma20 = htf_close.rolling(20, min_periods=5).mean()
        htf_ema21 = htf_close.ewm(span=21, adjust=False).mean()
        htf_ema50 = htf_close.ewm(span=50, adjust=False).mean()
        htf_vwap = _safe_div((((htf_high + htf_low + htf_close) / 3.0) * htf_vol).rolling(20, min_periods=1).sum(), htf_vol.rolling(20, min_periods=1).sum())
        htf_features = pd.DataFrame(
            {
                "higher_timeframe_trend_%s" % canonical: np.sign(htf_ema21 - htf_ema50),
                "higher_timeframe_rsi_%s" % canonical: _rsi(htf_close, 14),
                "higher_timeframe_volatility_%s" % canonical: htf_ret.rolling(20, min_periods=5).std(),
                "higher_timeframe_support_%s" % canonical: htf_low.rolling(20, min_periods=5).min(),
                "higher_timeframe_resistance_%s" % canonical: htf_high.rolling(20, min_periods=5).max(),
                "higher_timeframe_vwap_%s" % canonical: htf_vwap,
                "higher_timeframe_distance_sma_%s" % canonical: _safe_div(htf_close - htf_sma20, htf_sma20),
            },
            index=htf.index,
        )
        # For larger candles, only completed higher-timeframe bars are known.
        if seconds > base_seconds:
            htf_features = htf_features.shift(1)
        aligned = htf_features.reindex(frame.index, method="ffill")
        for col in aligned.columns:
            f[col] = aligned[col]
        htf_trends.append(aligned["higher_timeframe_trend_%s" % canonical])

    if htf_trends:
        trend_matrix = pd.concat(htf_trends, axis=1)
        base_trend = np.sign(close.diff()).replace(0, np.nan)
        f["alignment_score"] = _safe_div((trend_matrix.eq(base_trend, axis=0)).sum(axis=1), trend_matrix.notna().sum(axis=1))
        f["conflict_score"] = _safe_div((trend_matrix.ne(base_trend, axis=0)).sum(axis=1), trend_matrix.notna().sum(axis=1))
        f["lower_timeframe_momentum"] = close.pct_change(3)
        f["long_term_trend_short_term_pullback"] = ((trend_matrix.mean(axis=1) > 0) & (close.pct_change(3) < 0)).astype(float)
        f["short_term_breakout_in_long_term_trend"] = ((trend_matrix.mean(axis=1) > 0) & (close > close.rolling(20, min_periods=5).max().shift(1))).astype(float)


def _label_features(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    log_return: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    for n in horizons:
        prefix = str(n)
        future_close = c.shift(-n)
        future_return = _safe_div(future_close - c, c)
        future_log = np.log(_safe_div(future_close, c))
        future_high = h.shift(-1).rolling(n, min_periods=1).max().shift(-(n - 1))
        future_low = l.shift(-1).rolling(n, min_periods=1).min().shift(-(n - 1))
        max_upside = _safe_div(future_high - c, c)
        max_downside = _safe_div(future_low - c, c)
        future_vol = log_return.shift(-n).rolling(n, min_periods=1).std()
        f["future_return_%s" % prefix] = future_return
        f["future_log_return_%s" % prefix] = future_log
        f["direction_%s" % prefix] = np.sign(future_return)
        f["max_future_upside_%s" % prefix] = max_upside
        f["max_future_downside_%s" % prefix] = max_downside
        f["future_volatility_%s" % prefix] = future_vol
        f["future_drawdown_%s" % prefix] = max_downside
        f["future_breakout_%s" % prefix] = (future_high > h.rolling(n, min_periods=1).max()).astype(float)
        f["future_trend_continuation_%s" % prefix] = (np.sign(future_return) == np.sign(c.diff(n))).astype(float)
        mean = c.rolling(n, min_periods=1).mean()
        f["future_mean_reversion_%s" % prefix] = (np.sign(future_return) == -np.sign(c - mean)).astype(float)
        f["future_high_reached_%s" % prefix] = max_upside
        f["future_low_reached_%s" % prefix] = max_downside
        f["risk_reward_label_%s" % prefix] = _safe_div(max_upside, max_downside.abs())
        tp = cache["true_range"].rolling(max(n, 2), min_periods=1).mean() * 2.0
        sl = cache["true_range"].rolling(max(n, 2), min_periods=1).mean() * 1.5
        f["triple_barrier_label_%s" % prefix] = _triple_barrier_label(c, h, l, n, tp, sl)
        f["time_to_target_%s" % prefix] = _time_to_level(c, h, n, c + tp, above=True)
        f["time_to_stop_%s" % prefix] = _time_to_level(c, l, n, c - sl, above=False)
        f["best_action_%s" % prefix] = np.select([max_upside > max_downside.abs() * 1.25, max_downside.abs() > max_upside * 1.25], [1.0, -1.0], default=0.0)
        f["position_sizing_target_%s" % prefix] = _safe_div(future_return.abs(), future_vol)
        f["trade_quality_score_%s" % prefix] = _safe_div(max_upside - max_downside.abs(), future_vol)


def _canonical_dataset_aliases(
    f: "OrderedDict[str, object]",
    horizons: Sequence[int],
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    v: pd.Series,
    cache: Mapping[str, pd.Series],
) -> None:
    """Expose stable, human-readable aliases used by the training/UI contract."""

    for n in horizons:
        prefix = str(n)
        if "return_simple_%s" % prefix in f:
            f["returns_%s" % prefix] = f["return_simple_%s" % prefix]
        if "rolling_volatility_%s" % prefix in f:
            f["volatility_%s" % prefix] = f["rolling_volatility_%s" % prefix]
        if "bollinger_width_%s" % prefix in f:
            f["bb_width_%s" % prefix] = f["bollinger_width_%s" % prefix]
        if "bollinger_percent_b_%s" % prefix in f:
            f["bb_percent_b_%s" % prefix] = f["bollinger_percent_b_%s" % prefix]
        if "zscore_price_%s" % prefix in f:
            f["zscore_%s" % prefix] = f["zscore_price_%s" % prefix]
        if "chaikin_money_flow_%s" % prefix in f:
            f["cmf_%s" % prefix] = f["chaikin_money_flow_%s" % prefix]
        if "money_flow_index_%s" % prefix in f:
            f["mfi_%s" % prefix] = f["money_flow_index_%s" % prefix]
        if "choppiness_index_%s" % prefix in f:
            f["choppiness_%s" % prefix] = f["choppiness_index_%s" % prefix]
        if "macd_histogram_%s" % prefix in f:
            f["macd_hist_%s" % prefix] = f["macd_histogram_%s" % prefix]
        if "stop_hunt_proxy_%s" % prefix in f:
            f["stop_run_proxy_%s" % prefix] = f["stop_hunt_proxy_%s" % prefix]
        if "highpass_filtered_price_%s" % prefix in f:
            hp = f["highpass_filtered_price_%s" % prefix]
            f["wavelet_energy_%s" % prefix] = hp.pow(2).rolling(max(2, n), min_periods=1).mean()
            f["wavelet_decomposition_%s" % prefix] = hp
        if "rolling_volatility_%s" % prefix in f:
            f["last_volatility_mean_%s" % prefix] = f["rolling_volatility_%s" % prefix].rolling(max(2, n), min_periods=1).mean()

    for period in (9, 21):
        if "ema_%s" % period not in f:
            ema = c.ewm(span=period, adjust=False).mean()
            f["ema_%s" % period] = ema
            f["distance_ema_%s" % period] = _safe_div(c - ema, ema)

    alias_pairs = {
        "macd": "macd_14",
        "macd_signal": "macd_signal_14",
        "macd_hist": "macd_histogram_14",
        "stoch_k": "stochastic_k_14",
        "stoch_d": "stochastic_d_14",
        "distance_vwap": "distance_vwap_20",
        "distance_high_20": "distance_high_20",
        "distance_low_20": "distance_low_20",
        "obv": "obv",
        "adx_14": "adx_14",
        "di_plus_14": "di_plus_14",
        "di_minus_14": "di_minus_14",
        "choppiness_14": "choppiness_index_14",
        "donchian_position_20": "donchian_position_20",
    }
    for target, source in alias_pairs.items():
        if source in f:
            f[target] = f[source]

    # Multi-timeframe generic aliases point to the highest completed timeframe
    # available in the current run, preferring 4h then 1d/1w.
    for target, bases in {
        "higher_tf_trend": ["higher_timeframe_trend_4h", "higher_timeframe_trend_1d", "higher_timeframe_trend_1w"],
        "higher_tf_rsi": ["higher_timeframe_rsi_4h", "higher_timeframe_rsi_1d", "higher_timeframe_rsi_1w"],
        "higher_tf_volatility": ["higher_timeframe_volatility_4h", "higher_timeframe_volatility_1d", "higher_timeframe_volatility_1w"],
        "higher_tf_distance_sma": ["higher_timeframe_distance_sma_4h", "higher_timeframe_distance_sma_1d", "higher_timeframe_distance_sma_1w"],
    }.items():
        source = next((name for name in bases if name in f), None)
        if source:
            f[target] = f[source]

    # Contract labels.
    if "direction_10" in f:
        f["target_direction_10"] = f["direction_10"]
    if "triple_barrier_label_10" in f:
        f["target_triple_barrier"] = f["triple_barrier_label_10"]

    # Short-term timing aliases.
    if "return_simple_1" in f:
        f["one_bar_return"] = f["return_simple_1"]
    if "momentum_3" in f:
        f["three_bar_momentum"] = f["momentum_3"]
    if "rolling_volatility_5" in f:
        f["five_bar_volatility"] = f["rolling_volatility_5"]
    if "pullback_depth_3" in f:
        f["micro_pullback"] = f["pullback_depth_3"]
    if "breakout_above_high_3" in f and "breakdown_below_low_3" in f:
        f["micro_breakout"] = f["breakout_above_high_3"] - f["breakdown_below_low_3"]
    if "expansion_candle_3" in f:
        f["candle_impulse"] = f["expansion_candle_3"]
    if "close_position_in_range" in f and "body_range_ratio" in f:
        f["last_candle_strength"] = f["close_position_in_range"] * f["body_range_ratio"]
    if "distance_vwap_5" in f:
        f["short_term_vwap_distance"] = f["distance_vwap_5"]
    if "volume_spike_5" in f:
        f["short_term_volume_spike"] = f["volume_spike_5"]
    if "reversal_score" in f:
        f["short_term_reversal_score"] = f["reversal_score"]
    elif "mean_reversion_score" in f:
        f["short_term_reversal_score"] = f["mean_reversion_score"]
    if "continuation_score" in f:
        f["short_term_continuation_score"] = f["continuation_score"]
    if "stop_hunt_proxy_5" in f:
        f["local_liquidity_sweep_proxy"] = f["stop_hunt_proxy_5"]
    if "rsi_5" in f:
        f["fast_rsi"] = f["rsi_5"]
    if "stochastic_k_5" in f:
        f["fast_stochastic"] = f["stochastic_k_5"]

    # Long-period convenience features.
    if "sma_200" in f:
        f["long_term_support_distance"] = _safe_div(c - c.rolling(200, min_periods=20).min(), c)
        f["long_term_resistance_distance"] = _safe_div(c.rolling(200, min_periods=20).max() - c, c)
    f["long_term_momentum_3m"] = _safe_div(c - c.shift(90), c.shift(90))
    f["long_term_momentum_6m"] = _safe_div(c - c.shift(180), c.shift(180))
    f["long_term_momentum_12m"] = _safe_div(c - c.shift(365), c.shift(365))

    # Naming requested by the architecture table.
    for n in (20, 50, 200):
        if "distance_sma_%s" % n in f:
            f["distance_to_sma_%s" % n] = f["distance_sma_%s" % n]
    for n in (9, 21):
        if "distance_ema_%s" % n in f:
            f["distance_to_ema_%s" % n] = f["distance_ema_%s" % n]


def _safe_div(num: object, den: object, default: float = 0.0) -> pd.Series:
    if isinstance(den, (pd.Series, pd.DataFrame)):
        safe_den = den + EPS
        result = num / safe_den
    else:
        result = num / (den + EPS)
    if not isinstance(result, (pd.Series, pd.DataFrame)):
        return pd.Series(result).replace([np.inf, -np.inf], np.nan).fillna(default)
    if isinstance(result, pd.DataFrame):
        return result.replace([np.inf, -np.inf], np.nan).fillna(default)
    return result.replace([np.inf, -np.inf], np.nan).fillna(default)


def _min_periods(n: int) -> int:
    if n <= 1:
        return 1
    return min(n, max(2, n // 3))


def _true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    return pd.concat([(h - l).abs(), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)


def _wma(series: pd.Series, n: int) -> pd.Series:
    weights = np.arange(1, n + 1, dtype=float)
    return series.rolling(n, min_periods=_min_periods(n)).apply(lambda x: float(np.dot(x, weights[-len(x):]) / weights[-len(x):].sum()), raw=True)


def _hma(series: pd.Series, n: int) -> pd.Series:
    half = max(1, n // 2)
    sqrt_n = max(1, int(math.sqrt(n)))
    return _wma(2 * _wma(series, half) - _wma(series, n), sqrt_n)


def _dema(series: pd.Series, n: int) -> pd.Series:
    ema1 = series.ewm(span=max(n, 1), adjust=False).mean()
    ema2 = ema1.ewm(span=max(n, 1), adjust=False).mean()
    return 2 * ema1 - ema2


def _tema(series: pd.Series, n: int) -> pd.Series:
    ema1 = series.ewm(span=max(n, 1), adjust=False).mean()
    ema2 = ema1.ewm(span=max(n, 1), adjust=False).mean()
    ema3 = ema2.ewm(span=max(n, 1), adjust=False).mean()
    return 3 * ema1 - 3 * ema2 + ema3


def _kama(series: pd.Series, n: int, fast: int = 2, slow: int = 30) -> pd.Series:
    change = (series - series.shift(n)).abs()
    volatility = series.diff().abs().rolling(n, min_periods=1).sum()
    er = _safe_div(change, volatility)
    sc = (er * (2.0 / (fast + 1.0) - 2.0 / (slow + 1.0)) + 2.0 / (slow + 1.0)) ** 2
    out = np.empty(len(series), dtype=float)
    values = series.to_numpy(dtype=float)
    sc_values = sc.to_numpy(dtype=float)
    out[:] = np.nan
    for i, value in enumerate(values):
        if not np.isfinite(value):
            out[i] = out[i - 1] if i else np.nan
        elif i == 0 or not np.isfinite(out[i - 1]):
            out[i] = value
        else:
            out[i] = out[i - 1] + (sc_values[i] if np.isfinite(sc_values[i]) else 0.0) * (value - out[i - 1])
    return pd.Series(out, index=series.index)


def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=max(n - 1, 1), adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=max(n - 1, 1), adjust=False).mean()
    rs = _safe_div(gain, loss)
    return 100.0 - (100.0 / (1.0 + rs))


def _mfi(h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series, n: int) -> pd.Series:
    typical = (h + l + c) / 3.0
    money_flow = typical * v
    pos = money_flow.where(typical > typical.shift(1), 0.0).rolling(n, min_periods=1).sum()
    neg = money_flow.where(typical < typical.shift(1), 0.0).rolling(n, min_periods=1).sum()
    return 100.0 - 100.0 / (1.0 + _safe_div(pos, neg))


def _nvi_pvi(c: pd.Series, v: pd.Series) -> Tuple[pd.Series, pd.Series]:
    ret = c.pct_change().fillna(0.0)
    nvi = pd.Series(1000.0, index=c.index)
    pvi = pd.Series(1000.0, index=c.index)
    for i in range(1, len(c)):
        nvi.iloc[i] = nvi.iloc[i - 1] * (1.0 + ret.iloc[i]) if v.iloc[i] < v.iloc[i - 1] else nvi.iloc[i - 1]
        pvi.iloc[i] = pvi.iloc[i - 1] * (1.0 + ret.iloc[i]) if v.iloc[i] > v.iloc[i - 1] else pvi.iloc[i - 1]
    return nvi, pvi


def _tsi(c: pd.Series, n: int) -> pd.Series:
    m = c.diff()
    slow = max(3, 2 * n)
    fast = max(2, n)
    num = m.ewm(span=slow, adjust=False).mean().ewm(span=fast, adjust=False).mean()
    den = m.abs().ewm(span=slow, adjust=False).mean().ewm(span=fast, adjust=False).mean()
    return 100.0 * _safe_div(num, den)


def _trix(c: pd.Series, n: int) -> pd.Series:
    ema1 = c.ewm(span=n, adjust=False).mean()
    ema2 = ema1.ewm(span=n, adjust=False).mean()
    ema3 = ema2.ewm(span=n, adjust=False).mean()
    return ema3.pct_change() * 100.0


def _ultimate_oscillator(h: pd.Series, l: pd.Series, c: pd.Series, n1: int, n2: int, n3: int) -> pd.Series:
    prev_close = c.shift(1)
    bp = c - pd.concat([l, prev_close], axis=1).min(axis=1)
    tr = pd.concat([h, prev_close], axis=1).max(axis=1) - pd.concat([l, prev_close], axis=1).min(axis=1)
    avg1 = _safe_div(bp.rolling(n1, min_periods=1).sum(), tr.rolling(n1, min_periods=1).sum())
    avg2 = _safe_div(bp.rolling(n2, min_periods=1).sum(), tr.rolling(n2, min_periods=1).sum())
    avg3 = _safe_div(bp.rolling(n3, min_periods=1).sum(), tr.rolling(n3, min_periods=1).sum())
    return 100.0 * (4.0 * avg1 + 2.0 * avg2 + avg3) / 7.0


def _kst(c: pd.Series, n: int) -> pd.Series:
    parts = []
    for mult, weight in ((1, 1), (2, 2), (3, 3), (4, 4)):
        roc = c.pct_change(max(1, n * mult))
        parts.append(roc.rolling(max(1, n), min_periods=1).mean() * weight)
    return sum(parts)


def _efficiency_ratio(c: pd.Series, n: int) -> pd.Series:
    return _safe_div((c - c.shift(n)).abs(), c.diff().abs().rolling(n, min_periods=1).sum())


def _rolling_slope(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=_min_periods(n)).apply(_slope_array, raw=True)


def _rolling_r2(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=_min_periods(n)).apply(_r2_array, raw=True)


def _slope_array(arr: np.ndarray) -> float:
    y = np.asarray(arr, dtype=float)
    mask = np.isfinite(y)
    if mask.sum() < 2:
        return 0.0
    y = y[mask]
    x = np.arange(len(y), dtype=float)
    x = x - x.mean()
    denom = np.dot(x, x)
    if denom <= EPS:
        return 0.0
    return float(np.dot(x, y - y.mean()) / denom)


def _r2_array(arr: np.ndarray) -> float:
    y = np.asarray(arr, dtype=float)
    mask = np.isfinite(y)
    if mask.sum() < 3:
        return 0.0
    y = y[mask]
    x = np.arange(len(y), dtype=float)
    slope = _slope_array(y)
    pred = slope * (x - x.mean()) + y.mean()
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 0.0 if ss_tot <= EPS else max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def _rolling_percentile_rank(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=1).apply(lambda x: float(np.sum(x <= x[-1]) / max(len(x), 1)), raw=True)


def _rolling_z(series: pd.Series, n: int) -> pd.Series:
    mean = series.rolling(n, min_periods=1).mean()
    std = series.rolling(n, min_periods=1).std()
    return _safe_div(series - mean, std)


def _rolling_entropy(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=1).apply(_entropy_array, raw=True)


def _entropy_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return 0.0
    hist, _ = np.histogram(x, bins=min(10, max(2, len(x) // 2)))
    p = hist.astype(float)
    p = p[p > 0] / max(float(p.sum()), EPS)
    return float(-np.sum(p * np.log(p + EPS)) / math.log(len(p) + EPS))


def _rolling_autocorr(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=_min_periods(n)).apply(_autocorr_array, raw=True)


def _autocorr_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3 or np.nanstd(x) <= EPS:
        return 0.0
    return float(np.corrcoef(x[1:], x[:-1])[0, 1])


def _adx(h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=h.index)
    tr = _true_range(h, l, c).ewm(alpha=1.0 / max(n, 1), adjust=False).mean()
    plus_di = 100.0 * _safe_div(plus_dm.ewm(alpha=1.0 / max(n, 1), adjust=False).mean(), tr)
    minus_di = 100.0 * _safe_div(minus_dm.ewm(alpha=1.0 / max(n, 1), adjust=False).mean(), tr)
    dx = 100.0 * _safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    adx = dx.ewm(alpha=1.0 / max(n, 1), adjust=False).mean()
    return adx, plus_di, minus_di


def _aroon(h: pd.Series, l: pd.Series, n: int) -> Tuple[pd.Series, pd.Series]:
    def up_func(x: np.ndarray) -> float:
        return 100.0 * (len(x) - 1 - int(np.nanargmax(x))) / max(len(x) - 1, 1)

    def down_func(x: np.ndarray) -> float:
        return 100.0 * (len(x) - 1 - int(np.nanargmin(x))) / max(len(x) - 1, 1)

    return h.rolling(n, min_periods=1).apply(up_func, raw=True), l.rolling(n, min_periods=1).apply(down_func, raw=True)


def _vortex(h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> Tuple[pd.Series, pd.Series]:
    tr = _true_range(h, l, c).rolling(n, min_periods=1).sum()
    vip = (h - l.shift(1)).abs().rolling(n, min_periods=1).sum()
    vin = (l - h.shift(1)).abs().rolling(n, min_periods=1).sum()
    return _safe_div(vip, tr), _safe_div(vin, tr)


def _choppiness(tr: pd.Series, h: pd.Series, l: pd.Series, n: int) -> pd.Series:
    denom = (h.rolling(n, min_periods=1).max() - l.rolling(n, min_periods=1).min()).clip(lower=EPS)
    return 100.0 * _safe_div(np.log10(_safe_div(tr.rolling(n, min_periods=1).sum(), denom)), math.log10(max(n, 2)))


def _rolling_hurst(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=min(n, 10)).apply(_hurst_array, raw=True)


def _hurst_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return 0.5
    lags = np.arange(2, min(20, len(x) // 2) + 1)
    if len(lags) < 2:
        return 0.5
    tau = [np.sqrt(np.std(x[lag:] - x[:-lag])) for lag in lags]
    tau = np.asarray(tau)
    mask = (tau > 0) & np.isfinite(tau)
    if mask.sum() < 2:
        return 0.5
    slope = np.polyfit(np.log(lags[mask]), np.log(tau[mask]), 1)[0]
    return float(np.clip(slope * 2.0, 0.0, 1.0))


def _fractal_dimension(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=min(n, 5)).apply(_fractal_dimension_array, raw=True)


def _fractal_dimension_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return 1.5
    length = np.sum(np.abs(np.diff(x)))
    distance = abs(x[-1] - x[0])
    if distance <= EPS or length <= EPS:
        return 1.0
    return float(np.clip(math.log(len(x)) / (math.log(len(x)) + math.log(distance / length + EPS)), 1.0, 2.0))


def _katz_fd(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=min(n, 5)).apply(_katz_fd_array, raw=True)


def _katz_fd_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return 1.0
    distances = np.abs(x - x[0])
    d = np.max(distances)
    length = np.sum(np.abs(np.diff(x)))
    if d <= EPS or length <= EPS:
        return 1.0
    return float(np.clip(math.log10(len(x)) / (math.log10(d / length + EPS) + math.log10(len(x))), 1.0, 2.0))


def _permutation_entropy(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=min(n, 4)).apply(_perm_entropy_array, raw=True)


def _perm_entropy_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 4:
        return 0.0
    patterns: Dict[Tuple[int, int, int], int] = {}
    for i in range(len(x) - 2):
        key = tuple(np.argsort(x[i:i + 3]).tolist())
        patterns[key] = patterns.get(key, 0) + 1
    p = np.asarray(list(patterns.values()), dtype=float)
    p = p / max(p.sum(), EPS)
    return float(-np.sum(p * np.log(p + EPS)) / math.log(6.0))


def _dominant_cycle(c: pd.Series, n: int) -> pd.Series:
    return c.rolling(n, min_periods=min(n, 8)).apply(_dominant_cycle_array, raw=True)


def _dominant_cycle_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 8:
        return 0.0
    x = x - x.mean()
    spectrum = np.abs(np.fft.rfft(x))
    if len(spectrum) <= 2:
        return 0.0
    idx = int(np.argmax(spectrum[1:]) + 1)
    return float(len(x) / max(idx, 1))


def _fft_component(c: pd.Series, n: int, component: int) -> pd.Series:
    return c.rolling(n, min_periods=min(n, 8)).apply(lambda x: _fft_component_array(x, component), raw=True)


def _fft_component_array(arr: np.ndarray, component: int) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < component + 2:
        return 0.0
    x = x - x.mean()
    spectrum = np.abs(np.fft.rfft(x))
    return float(spectrum[component] / max(len(x), 1))


def _spectral_entropy(c: pd.Series, n: int) -> pd.Series:
    return c.rolling(n, min_periods=min(n, 8)).apply(_spectral_entropy_array, raw=True)


def _spectral_entropy_array(arr: np.ndarray) -> float:
    x = np.asarray(arr, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 8:
        return 0.0
    spectrum = np.abs(np.fft.rfft(x - x.mean())) ** 2
    spectrum = spectrum[1:]
    if spectrum.sum() <= EPS:
        return 0.0
    p = spectrum / spectrum.sum()
    return float(-np.sum(p * np.log(p + EPS)) / math.log(len(p) + EPS))


def _consecutive_true(mask: pd.Series) -> pd.Series:
    arr = mask.fillna(False).astype(bool).to_numpy()
    out = np.zeros(len(arr), dtype=float)
    count = 0
    for i, value in enumerate(arr):
        count = count + 1 if value else 0
        out[i] = count
    return pd.Series(out, index=mask.index)


def _consecutive_same_sign(series: pd.Series) -> pd.Series:
    sign = np.sign(series.fillna(0.0).to_numpy(dtype=float))
    out = np.zeros(len(sign), dtype=float)
    count = 0
    prev = 0.0
    for i, value in enumerate(sign):
        if value != 0 and value == prev:
            count += 1
        elif value != 0:
            count = 1
            prev = value
        else:
            count = 0
        out[i] = count
    return pd.Series(out, index=series.index)


def _time_under_water(c: pd.Series) -> pd.Series:
    peak = c.cummax()
    under = c < peak
    return _consecutive_true(under)


def _trend_phase(trend_strength: pd.Series, zscore: pd.Series, volume_ratio: pd.Series) -> pd.Series:
    return pd.Series(
        np.select(
            [
                (trend_strength > 0.25) & (volume_ratio > 1.0),
                (trend_strength < -0.25) & (volume_ratio > 1.0),
                zscore.abs() < 0.5,
                zscore.abs() > 2.0,
            ],
            [1.0, -1.0, 0.0, 2.0],
            default=0.0,
        ),
        index=trend_strength.index,
    )


def _expanding_group_mean(series: pd.Series, groups: pd.Series) -> pd.Series:
    out = pd.Series(index=series.index, dtype=float)
    for _, idx in groups.groupby(groups).groups.items():
        values = series.loc[idx]
        out.loc[idx] = values.expanding(min_periods=1).mean().shift(1)
    return out.fillna(series.expanding(min_periods=1).mean())


def _rolling_list(series: pd.Series, n: int) -> pd.Series:
    values = series.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)
    out: List[Optional[List[float]]] = [None] * len(values)
    for i in range(len(values)):
        start = max(0, i - n + 1)
        window = values[start:i + 1]
        if len(window) < n:
            window = np.concatenate([np.zeros(n - len(window)), window])
        out[i] = [float(x) for x in window]
    return pd.Series(out, index=series.index)


def _rolling_nested_list(frame: pd.DataFrame, n: int) -> pd.Series:
    values = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)
    out: List[Optional[List[List[float]]]] = [None] * len(values)
    zero = np.zeros((max(n, 1), values.shape[1]), dtype=float)
    for i in range(len(values)):
        start = max(0, i - n + 1)
        window = values[start:i + 1]
        if len(window) < n:
            padded = zero.copy()
            padded[-len(window):] = window
            window = padded
        out[i] = [[float(x) for x in row] for row in window]
    return pd.Series(out, index=frame.index)


def _triple_barrier_label(c: pd.Series, h: pd.Series, l: pd.Series, n: int, tp: pd.Series, sl: pd.Series) -> pd.Series:
    labels = np.zeros(len(c), dtype=float)
    close = c.to_numpy(dtype=float)
    high = h.to_numpy(dtype=float)
    low = l.to_numpy(dtype=float)
    tpv = tp.fillna(0.0).to_numpy(dtype=float)
    slv = sl.fillna(0.0).to_numpy(dtype=float)
    for i in range(len(close)):
        end = min(len(close), i + n + 1)
        target = close[i] + tpv[i]
        stop = close[i] - slv[i]
        label = 0.0
        for j in range(i + 1, end):
            if high[j] >= target:
                label = 1.0
                break
            if low[j] <= stop:
                label = -1.0
                break
        labels[i] = label
    if n > 0:
        labels[-n:] = np.nan
    return pd.Series(labels, index=c.index)


def _time_to_level(c: pd.Series, probe: pd.Series, n: int, level: pd.Series, *, above: bool) -> pd.Series:
    out = np.full(len(c), np.nan, dtype=float)
    probe_values = probe.to_numpy(dtype=float)
    levels = level.to_numpy(dtype=float)
    for i in range(len(c)):
        end = min(len(c), i + n + 1)
        for j in range(i + 1, end):
            if (probe_values[j] >= levels[i]) if above else (probe_values[j] <= levels[i]):
                out[i] = float(j - i)
                break
    return pd.Series(out, index=c.index)


def _resample_ohlcv(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = _PANDAS_RULES[_canonical_timeframe(timeframe)]
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    optional = {
        "quote_asset_volume": "sum",
        "number_of_trades": "sum",
        "taker_buy_base_asset_volume": "sum",
        "taker_buy_quote_asset_volume": "sum",
    }
    for col, method in optional.items():
        if col in frame.columns:
            agg[col] = method
    return frame.resample(rule).agg(agg).dropna(subset=["open", "high", "low", "close"])


def _canonical_timeframe(value: Optional[str]) -> str:
    raw = (value or "1h").strip().lower()
    aliases = {
        "m1": "1m",
        "1min": "1m",
        "minute": "1m",
        "5min": "5m",
        "15min": "15m",
        "60m": "1h",
        "h1": "1h",
        "hour": "1h",
        "1hour": "1h",
        "4hour": "4h",
        "day": "1d",
        "d": "1d",
        "1day": "1d",
        "week": "1w",
        "w": "1w",
        "1week": "1w",
    }
    return aliases.get(raw, raw)


def _infer_interval(index: pd.DatetimeIndex) -> Optional[str]:
    if len(index) < 3:
        return None
    diffs = pd.Series(index).diff().dropna()
    if diffs.empty:
        return None
    seconds = int(diffs.median().total_seconds())
    closest = min(_INTERVAL_SECONDS.items(), key=lambda kv: abs(kv[1] - seconds))
    return closest[0]


def _label_columns(columns: Iterable[str]) -> set:
    prefixes = (
        "future_",
        "direction_",
        "triple_barrier_label_",
        "max_future_",
        "risk_reward_label_",
        "time_to_target_",
        "time_to_stop_",
        "best_action_",
        "position_sizing_target_",
        "trade_quality_score_",
    )
    return {col for col in columns if col.startswith(prefixes)}
