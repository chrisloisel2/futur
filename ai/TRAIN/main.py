import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from pipeline import (
    AdaptiveNormalizer,
    BacktestConfig,
    Backtester,
    CcxtDataSource,
    GlassnodeClient,
    RedisCache,
    build_feature_set,
    merge_onchain_asof,
    ohlcv_to_df,
    plot_backtest_results,
    print_metrics,
)


def build_trend_signals(prices: pd.Series, volumes: pd.Series) -> pd.Series:
    """
    Simple trend + liquidity filter using real OHLCV inputs.
    Signals are shifted by one bar to avoid lookahead.
    """
    sma_fast = prices.rolling(20, min_periods=20).mean()
    sma_slow = prices.rolling(60, min_periods=60).mean()
    raw_signal = np.sign(sma_fast - sma_slow)

    vol_ratio = volumes / (volumes.rolling(20, min_periods=5).mean() + 1e-9)
    filtered = raw_signal.where(vol_ratio > 0.3, 0.0)

    return filtered.shift(1).fillna(0.0).clip(-1, 1)


def run() -> None:
    cache = RedisCache(ttl_seconds=900)
    ccxt_source = CcxtDataSource(cache=cache)

    end = datetime.utcnow()
    start = end - timedelta(days=60)
    symbol = os.getenv("SYMBOL", "BTC/USDT")
    timeframe = os.getenv("TIMEFRAME", "1h")

    ohlcv = ccxt_source.fetch_historical_range(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    ohlcv_df = ohlcv_to_df(ohlcv)

    glassnode = GlassnodeClient(cache=cache)
    merged_df = ohlcv_df
    try:
        raw_onchain = glassnode.fetch_metric(
            endpoint="addresses/active_count",
            asset="BTC",
            params={"i": "24h"},
        )
        onchain_df = glassnode.to_df(raw_onchain)
        merged_df = merge_onchain_asof(ohlcv_df, onchain_df, tolerance="12h")
    except Exception as exc:
        print(f"On-chain fetch failed ({exc}); continuing without on-chain enrichment.")

    onchain_column = "onchain_value" if "onchain_value" in merged_df.columns else None
    feature_df = build_feature_set(merged_df, onchain_column=onchain_column, drop_na=True)

    normalizer = AdaptiveNormalizer(window=500, z_threshold=4.0)
    normalized_df = normalizer.fit_transform(feature_df)

    print("Dernières lignes normalisées :")
    print(normalized_df.tail().to_string())

    prices = merged_df.set_index("timestamp")["close"].sort_index()
    volumes = merged_df.set_index("timestamp")["volume"].sort_index()
    signals = build_trend_signals(prices, volumes)

    backtester = Backtester(BacktestConfig())
    results = backtester.run(prices, signals, volumes=volumes)

    print_metrics(results)

    if os.getenv("PLOT_BACKTEST", "0") == "1":
        fig = plot_backtest_results(results, prices)
        fig.write_html("backtest_report.html")
        print("Interactive plot saved to backtest_report.html")


if __name__ == "__main__":
    run()
