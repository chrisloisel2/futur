from __future__ import annotations

import pandas as pd


def compute_derivatives_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    if "funding_rate" in df:
        out["deriv_funding_z"] = (df["funding_rate"] - df["funding_rate"].rolling(720, min_periods=30).mean()) / (df["funding_rate"].rolling(720, min_periods=30).std() + 1e-9)
    if "open_interest" in df:
        out["deriv_oi_trend"] = df["open_interest"].diff().rolling(30, min_periods=5).mean()
    out["symbol"] = df.get("symbol")
    return out
