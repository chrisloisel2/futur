import json
from pathlib import Path
from typing import Dict, Literal, Optional

import numpy as np
import pandas as pd


class AdaptiveNormalizer:
    def __init__(
        self,
        window: int = 500,
        z_threshold: float = 4.0,
        eps: float = 1e-9,
        method: Literal["robust", "standard"] = "robust",
    ) -> None:
        """
        Initialize normalizer.

        Args:
            window: Rolling window size for computing statistics
            z_threshold: Threshold for outlier clipping
            eps: Small value to avoid division by zero
            method: "robust" (median/IQR) or "standard" (mean/std)
        """
        self.window = window
        self.z_threshold = z_threshold
        self.eps = eps
        self.method = method
        self.state: Dict[str, Dict[str, float]] = {}
        self._is_fitted = False

    def fit(self, df: pd.DataFrame) -> "AdaptiveNormalizer":
        """
        Fit normalizer on training data.
        Uses only the data provided to compute normalization parameters.
        """
        numeric = df.select_dtypes(include="number")

        for col in numeric.columns:
            series = numeric[col].dropna()
            if len(series) == 0:
                continue

            # Use last window for fitting to get recent statistics
            windowed = series.iloc[-self.window :] if len(series) > self.window else series

            if self.method == "robust":
                center = windowed.median()
                mad = (windowed - center).abs().median()
                spread = windowed.quantile(0.75) - windowed.quantile(0.25)
                if spread == 0:
                    spread = windowed.std()
            else:  # standard
                center = windowed.mean()
                spread = windowed.std()
                mad = spread

            self.state[col] = {
                "center": float(center),
                "spread": float(spread),
                "mad": float(mad),
            }

        self._is_fitted = True
        return self

    def transform(self, df: pd.DataFrame, preserve_index: bool = True) -> pd.DataFrame:
        """
        Transform data using fitted parameters.
        Does NOT refit on the new data to avoid data leakage.
        """
        if not self._is_fitted:
            raise RuntimeError("Normalizer must be fitted before transform. Call fit() first.")

        numeric = df.select_dtypes(include="number")
        normalized = pd.DataFrame(index=df.index if preserve_index else range(len(df)))

        for col in numeric.columns:
            if col not in self.state:
                # Column not seen during fit, skip normalization
                normalized[col] = numeric[col].values
                continue

            series = numeric[col].copy()
            params = self.state[col]
            center = params["center"]
            spread = params["spread"]
            mad = params["mad"]

            # Clip outliers using MAD
            if mad > 0 and self.method == "robust":
                score = 0.6745 * (series - center).abs() / mad
                outliers = score > self.z_threshold
                series.loc[outliers] = center + (
                    np.sign(series[outliers] - center) * mad * self.z_threshold / 0.6745
                )

            # Normalize
            normalized[col] = (series - center) / (spread + self.eps)

        return normalized

    def fit_transform(self, df: pd.DataFrame, preserve_index: bool = True) -> pd.DataFrame:
        """
        Fit on data and transform it.
        Use this only for training data, never for validation/test.
        """
        self.fit(df)
        return self.transform(df, preserve_index=preserve_index)

    def inverse_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reverse normalization to get original scale.
        """
        if not self._is_fitted:
            raise RuntimeError("Normalizer must be fitted before inverse_transform.")

        denormalized = pd.DataFrame(index=df.index)

        for col in df.columns:
            if col not in self.state:
                denormalized[col] = df[col]
                continue

            params = self.state[col]
            denormalized[col] = df[col] * params["spread"] + params["center"]

        return denormalized

    def save_state(self, path: str) -> None:
        """Save normalizer state to JSON file."""
        state_dict = {
            "window": self.window,
            "z_threshold": self.z_threshold,
            "eps": self.eps,
            "method": self.method,
            "state": self.state,
            "is_fitted": self._is_fitted,
        }
        Path(path).write_text(json.dumps(state_dict, indent=2))

    @classmethod
    def load_state(cls, path: str) -> "AdaptiveNormalizer":
        """Load normalizer state from JSON file."""
        state_dict = json.loads(Path(path).read_text())
        normalizer = cls(
            window=state_dict["window"],
            z_threshold=state_dict["z_threshold"],
            eps=state_dict["eps"],
            method=state_dict["method"],
        )
        normalizer.state = state_dict["state"]
        normalizer._is_fitted = state_dict["is_fitted"]
        return normalizer
