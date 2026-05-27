from __future__ import annotations

import pandas as pd


def enforce_monotonic(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["q50"] = df[["q05", "q50", "q95"]].median(axis=1)
    df["q05"] = df[["q05", "q50"]].min(axis=1)
    df["q95"] = df[["q50", "q95"]].max(axis=1)
    df["p_hit"] = df["p_hit"].clip(0, 1)
    return df
