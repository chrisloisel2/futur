import pandas as pd


def ohlcv_to_df(ohlcv):
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def merge_onchain_asof(
    ohlcv_df: pd.DataFrame, onchain_df: pd.DataFrame, tolerance: str = "6h"
) -> pd.DataFrame:
    merged = pd.merge_asof(
        ohlcv_df.sort_values("timestamp"),
        onchain_df.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    return merged
