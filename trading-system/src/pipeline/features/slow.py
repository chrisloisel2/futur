from __future__ import annotations

import pandas as pd


def compute_slow_features(df: pd.DataFrame) -> pd.DataFrame:
    """Slow features capturing funding/basis/macro drift."""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "event_time" in df:
        df["event_time"] = pd.to_datetime(df["event_time"])
        df = df.set_index("event_time")
    out = pd.DataFrame(index=df.index)
    if "funding_rate" in df:
        out["s_slow_funding_z"] = (df["funding_rate"] - df["funding_rate"].rolling(1440, min_periods=10).mean()) / (df["funding_rate"].rolling(1440, min_periods=10).std() + 1e-9)
    if "open_interest" in df:
        out["s_slow_oi_accel"] = df["open_interest"].diff().rolling(60, min_periods=5).mean()
    if "basis" in df:
        out["s_slow_basis_z"] = (df["basis"] - df["basis"].rolling(720, min_periods=30).mean()) / (df["basis"].rolling(720, min_periods=30).std() + 1e-9)
    out["symbol"] = df.get("symbol")
    return out
