from __future__ import annotations

import pandas as pd

from pipeline.features.indicators import rolling_return, rolling_vol, rsi


def compute_mid_features(df: pd.DataFrame) -> pd.DataFrame:
    """Mid-horizon features on minutes scale."""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "event_time" in df:
        df["event_time"] = pd.to_datetime(df["event_time"])
        df = df.set_index("event_time")
    price = df.get("mid_price", df.get("price"))
    if price is None:
        return pd.DataFrame()
    price = price.ffill()
    out = pd.DataFrame(index=price.index)
    out["x_mid_ret_1m"] = rolling_return(price, window="1min")
    out["x_mid_rv_5m"] = rolling_vol(price, window="5min")
    out["x_mid_rv_1h"] = rolling_vol(price, window="60min")
    out["x_mid_rsi_14"] = rsi(price, window=14)
    out["symbol"] = df.get("symbol")
    return out
