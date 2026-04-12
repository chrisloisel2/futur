from __future__ import annotations

import pandas as pd

from pipeline.features.microstructure import compute_microstructure


def compute_fast_features(df: pd.DataFrame) -> pd.DataFrame:
    """Fast features on tick-level: spread, imbalance, update rate, cvd."""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "event_time" in df:
        df["event_time"] = pd.to_datetime(df["event_time"])
        df = df.set_index("event_time")
    fast = pd.DataFrame(index=df.index)
    micro = compute_microstructure(df)
    fast = fast.join(micro)
    # simple cumulative volume delta
    if {"qty", "side"}.issubset(df.columns):
        signed = df["qty"] * df["side"].map({"buy": 1, "sell": -1}).fillna(0)
        fast["x_fast_cvd"] = signed.cumsum()
    fast["symbol"] = df.get("symbol")
    return fast
