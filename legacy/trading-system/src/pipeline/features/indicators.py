from __future__ import annotations

import pandas as pd


def rolling_return(series: pd.Series, window: str) -> pd.Series:
    return series.pct_change().rolling(window).sum().fillna(0)


def rolling_vol(series: pd.Series, window: str) -> pd.Series:
    return series.pct_change().rolling(window).std().fillna(0)


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))
