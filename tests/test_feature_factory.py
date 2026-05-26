import numpy as np
import pandas as pd

from data_pipeline.features import FEATURE_VERSION, LABEL_COLS, compute_hourly_features


def _sample_ohlcv(rows=320):
    rng = np.random.default_rng(42)
    timestamp = pd.date_range("2023-01-01", periods=rows, freq="1h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 0.8, rows))
    open_ = close + rng.normal(0, 0.2, rows)
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.8, rows)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.8, rows)
    volume = rng.uniform(100, 500, rows)
    return pd.DataFrame({
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "quote_asset_volume": close * volume,
        "taker_buy_base_asset_volume": volume * rng.uniform(0.35, 0.65, rows),
        "taker_buy_quote_asset_volume": close * volume * 0.5,
        "number_of_trades": rng.integers(50, 250, rows),
    })


def test_hourly_feature_factory_materializes_advanced_features_without_feature_nans():
    out = compute_hourly_features(_sample_ohlcv(), symbol="BTCUSDT", include_labels=True)

    assert out["feature_version"].iloc[-1] == FEATURE_VERSION
    for col in ["sqz_in_squeeze", "ichi_above_cloud", "rsi_7", "obv_slope_12", "future_ret_4h"]:
        assert col in out.columns

    numeric_feature_cols = [
        col for col in out.select_dtypes(include=[np.number]).columns
        if col not in LABEL_COLS
    ]
    assert int(out[numeric_feature_cols].isna().sum().sum()) == 0
    assert out["future_ret_4h"].tail(4).isna().all()

