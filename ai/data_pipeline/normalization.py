from typing import Dict

import numpy as np
import pandas as pd


class AdaptiveNormalizer:
    def __init__(self, window: int = 500, z_threshold: float = 4.0, eps: float = 1e-9) -> None:
        self.window = window
        self.z_threshold = z_threshold
        self.eps = eps
        self.state: Dict[str, Dict[str, float]] = {}

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._normalize(df, update_state=True)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._normalize(df, update_state=False)

    def _normalize(self, df: pd.DataFrame, update_state: bool) -> pd.DataFrame:
        numeric = df.select_dtypes(include="number")
        normalized = pd.DataFrame(index=df.index)
        for col in numeric.columns:
            series = numeric[col].copy()
            windowed = series.dropna().iloc[-self.window :]
            median = windowed.median()
            mad = (windowed - median).abs().median()
            if mad > 0:
                score = 0.6745 * (series - median).abs() / mad
                outliers = score > self.z_threshold
                series.loc[outliers] = median + (
                    np.sign(series[outliers] - median)
                    * mad
                    * self.z_threshold
                    / 0.6745
                )

            iqr = windowed.quantile(0.75) - windowed.quantile(0.25)
            if iqr == 0:
                iqr = windowed.std()
            normalized[col] = (series - median) / (iqr + self.eps)
            if update_state:
                self.state[col] = {"median": median, "iqr": iqr, "mad": mad}
        return normalized
