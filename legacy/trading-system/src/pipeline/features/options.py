from __future__ import annotations

import pandas as pd


def compute_options_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    if "iv_bid" in df and "iv_ask" in df:
        out["opt_iv_spread"] = df["iv_ask"] - df["iv_bid"]
    if "gamma" in df:
        out["opt_gamma_risk"] = df["gamma"].rolling(10, min_periods=1).mean()
    out["symbol"] = df.get("symbol")
    return out
