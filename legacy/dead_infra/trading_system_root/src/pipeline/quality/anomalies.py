from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_median(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=1).median()


def rolling_mad(series: pd.Series, window: int) -> pd.Series:
    med = rolling_median(series, window)
    return (series - med).abs().rolling(window=window, min_periods=1).median()


def robust_zscore(series: pd.Series, window: int, eps: float = 1e-9) -> pd.Series:
    med = rolling_median(series, window)
    mad = rolling_mad(series, window)
    return 0.6745 * (series - med) / (mad + eps)


def winsorize(series: pd.Series, lower: float, upper: float) -> pd.Series:
    return series.clip(lower=series.quantile(lower), upper=series.quantile(upper))


def detect_spikes(series: pd.Series, threshold: float, window: int) -> pd.Series:
    z = robust_zscore(series, window)
    return z.abs() > threshold


def jump_detector(series: pd.Series, threshold: float) -> pd.Series:
    return series.diff().abs() > threshold


class RollingWindowState:
    def __init__(self, window: int = 50):
        self.window = window
        self.cache: dict[str, pd.Series] = {}

    def update(self, key: str, series: pd.Series) -> None:
        if key not in self.cache:
            self.cache[key] = series.tail(self.window)
        else:
            self.cache[key] = pd.concat([self.cache[key], series]).tail(self.window)

    def get(self, key: str) -> pd.Series:
        return self.cache.get(key, pd.Series(dtype=float))
