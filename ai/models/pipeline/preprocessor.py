"""
Advanced preprocessing for financial time series.

Features:
- Fractional differentiation for stationarity
- Feature selection (Mutual Information + BorutaPy)
- Rolling normalization (z-score)
- Temporal interpolation for NaN handling
- Purged walk-forward cross-validation
- ADF stationarity testing
"""
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.feature_selection import mutual_info_regression
from statsmodels.tsa.stattools import adfuller

logger = logging.getLogger(__name__)

try:
    from boruta import BorutaPy
    from sklearn.ensemble import RandomForestRegressor
    HAS_BORUTA = True
except ImportError:
    logger.warning("BorutaPy not installed. Feature selection will use MI only.")
    HAS_BORUTA = False


class FractionalDifferentiator:
    """
    Implement fractional differentiation to achieve stationarity while preserving memory.

    Based on "Advances in Financial Machine Learning" by Marcos Lopez de Prado.
    """

    def __init__(self, d: float = 0.5, threshold: float = 1e-5):
        """
        Initialize fractional differentiator.

        Args:
            d: Differentiation order (0 < d < 1). Higher = more stationary, less memory
            threshold: Minimum weight threshold for computational efficiency
        """
        self.d = d
        self.threshold = threshold
        self.weights = None

    def _get_weights(self, size: int) -> np.ndarray:
        """Compute fractional differentiation weights."""
        weights = np.array([1.0])

        for k in range(1, size):
            weight = -weights[-1] * (self.d - k + 1) / k
            if abs(weight) < self.threshold:
                break
            weights = np.append(weights, weight)

        return weights[::-1]  # Reverse for convolution

    def fit_transform(self, series: pd.Series) -> pd.Series:
        """
        Apply fractional differentiation to series.

        Args:
            series: Input time series

        Returns:
            Fractionally differentiated series
        """
        self.weights = self._get_weights(len(series))

        # Apply weights via convolution
        diff_series = pd.Series(dtype=float, index=series.index)

        for i in range(len(self.weights), len(series)):
            window = series.iloc[i - len(self.weights) + 1:i + 1].values
            diff_series.iloc[i] = np.dot(window, self.weights)

        return diff_series

    def test_stationarity(self, series: pd.Series, alpha: float = 0.05) -> Dict:
        """
        Test stationarity using Augmented Dickey-Fuller test.

        Args:
            series: Time series to test
            alpha: Significance level

        Returns:
            Dict with test results
        """
        series_clean = series.dropna()

        if len(series_clean) < 20:
            return {
                "is_stationary": False,
                "reason": "Insufficient data for ADF test"
            }

        try:
            adf_result = adfuller(series_clean, autolag='AIC')

            return {
                "is_stationary": adf_result[1] < alpha,
                "adf_statistic": adf_result[0],
                "p_value": adf_result[1],
                "critical_values": adf_result[4],
                "n_lags": adf_result[2],
            }
        except Exception as e:
            logger.error(f"ADF test failed: {e}")
            return {
                "is_stationary": False,
                "error": str(e)
            }


class RollingNormalizer:
    """
    Rolling window normalization using z-score.

    Prevents data leakage by using only past data for normalization.
    """

    def __init__(self, window: int = 30, min_periods: int = 10):
        """
        Initialize rolling normalizer.

        Args:
            window: Lookback window for mean/std calculation
            min_periods: Minimum observations required
        """
        self.window = window
        self.min_periods = min_periods

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply rolling z-score normalization.

        Args:
            df: Input DataFrame

        Returns:
            Normalized DataFrame
        """
        normalized = pd.DataFrame(index=df.index)

        for col in df.select_dtypes(include=[np.number]).columns:
            series = df[col]

            # Calculate rolling mean and std
            rolling_mean = series.rolling(
                window=self.window,
                min_periods=self.min_periods
            ).mean()

            rolling_std = series.rolling(
                window=self.window,
                min_periods=self.min_periods
            ).std()

            # Z-score normalization
            normalized[col] = (series - rolling_mean) / (rolling_std + 1e-9)

        return normalized


class FeatureSelector:
    """
    Feature selection using Mutual Information and optionally BorutaPy.
    """

    def __init__(
        self,
        target_col: str,
        mi_threshold: float = 0.01,
        use_boruta: bool = True,
        boruta_max_depth: int = 5,
        boruta_n_estimators: int = 100,
        random_state: int = 42
    ):
        """
        Initialize feature selector.

        Args:
            target_col: Name of target column
            mi_threshold: Minimum mutual information score
            use_boruta: Whether to use BorutaPy
            boruta_max_depth: Max depth for Boruta RF
            boruta_n_estimators: Number of trees for Boruta RF
            random_state: Random seed
        """
        self.target_col = target_col
        self.mi_threshold = mi_threshold
        self.use_boruta = use_boruta and HAS_BORUTA
        self.boruta_max_depth = boruta_max_depth
        self.boruta_n_estimators = boruta_n_estimators
        self.random_state = random_state

        self.selected_features_ = None
        self.mi_scores_ = None
        self.boruta_support_ = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "FeatureSelector":
        """
        Fit feature selector.

        Args:
            X: Feature matrix
            y: Target variable

        Returns:
            Self
        """
        # Remove any NaN rows
        mask = ~(X.isna().any(axis=1) | y.isna())
        X_clean = X[mask]
        y_clean = y[mask]

        logger.info(f"Feature selection on {len(X_clean)} samples, {len(X.columns)} features")

        # 1. Mutual Information
        mi_scores = mutual_info_regression(
            X_clean,
            y_clean,
            random_state=self.random_state
        )

        self.mi_scores_ = pd.Series(mi_scores, index=X.columns)
        mi_selected = self.mi_scores_[self.mi_scores_ > self.mi_threshold].index.tolist()

        logger.info(f"MI selected {len(mi_selected)}/{len(X.columns)} features")

        # 2. BorutaPy (if enabled)
        if self.use_boruta and len(mi_selected) > 0:
            logger.info("Running BorutaPy feature selection...")

            rf = RandomForestRegressor(
                n_estimators=self.boruta_n_estimators,
                max_depth=self.boruta_max_depth,
                random_state=self.random_state,
                n_jobs=-1
            )

            boruta = BorutaPy(
                rf,
                n_estimators='auto',
                random_state=self.random_state,
                verbose=0
            )

            # Use only MI-selected features for Boruta
            X_mi = X_clean[mi_selected]

            try:
                boruta.fit(X_mi.values, y_clean.values)
                self.boruta_support_ = pd.Series(
                    boruta.support_,
                    index=mi_selected
                )

                boruta_selected = [
                    feat for feat, support in zip(mi_selected, boruta.support_)
                    if support
                ]

                logger.info(f"Boruta selected {len(boruta_selected)}/{len(mi_selected)} features")
                self.selected_features_ = boruta_selected

            except Exception as e:
                logger.warning(f"BorutaPy failed: {e}. Using MI selection only.")
                self.selected_features_ = mi_selected
        else:
            self.selected_features_ = mi_selected

        logger.info(f"Final selection: {len(self.selected_features_)} features")

        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """
        Transform by selecting features.

        Args:
            X: Feature matrix

        Returns:
            Filtered DataFrame
        """
        if self.selected_features_ is None:
            raise RuntimeError("Must call fit() before transform()")

        return X[self.selected_features_]

    def fit_transform(self, X: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
        """Fit and transform."""
        self.fit(X, y)
        return self.transform(X)


class TemporalInterpolator:
    """
    Handle missing values with temporal interpolation methods.
    """

    def __init__(self, method: str = "time", limit: int = 5):
        """
        Initialize interpolator.

        Args:
            method: Interpolation method ('time', 'linear', 'spline', 'polynomial')
            limit: Maximum consecutive NaNs to interpolate
        """
        self.method = method
        self.limit = limit

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Interpolate missing values.

        Args:
            df: Input DataFrame with NaNs

        Returns:
            DataFrame with interpolated values
        """
        interpolated = df.copy()

        # Log NaN statistics
        nan_counts = df.isna().sum()
        if nan_counts.sum() > 0:
            logger.info(f"Interpolating {nan_counts.sum()} NaN values")
            logger.debug(f"NaN per column: {nan_counts[nan_counts > 0].to_dict()}")

        # Interpolate each numeric column
        for col in df.select_dtypes(include=[np.number]).columns:
            if df[col].isna().any():
                interpolated[col] = df[col].interpolate(
                    method=self.method,
                    limit=self.limit,
                    limit_direction='both'
                )

                # Forward/backward fill remaining NaNs at edges
                interpolated[col].fillna(method='ffill', inplace=True)
                interpolated[col].fillna(method='bfill', inplace=True)

        return interpolated


class PurgedWalkForward:
    """
    Purged walk-forward cross-validation for time series.

    Prevents data leakage by purging overlapping observations between
    train and test sets.
    """

    def __init__(
        self,
        n_splits: int = 5,
        test_size: int = 100,
        purge_gap: int = 10,
        embargo_gap: int = 5
    ):
        """
        Initialize purged walk-forward CV.

        Args:
            n_splits: Number of splits
            test_size: Number of observations in test set
            purge_gap: Number of observations to purge before test
            embargo_gap: Number of observations to embargo after test
        """
        self.n_splits = n_splits
        self.test_size = test_size
        self.purge_gap = purge_gap
        self.embargo_gap = embargo_gap

    def split(
        self,
        X: pd.DataFrame,
        y: Optional[pd.Series] = None
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate train/test splits.

        Args:
            X: Feature matrix
            y: Target (unused, for sklearn compatibility)

        Yields:
            Tuples of (train_indices, test_indices)
        """
        n_samples = len(X)
        indices = np.arange(n_samples)

        # Calculate split points
        test_starts = np.linspace(
            n_samples // (self.n_splits + 1),
            n_samples - self.test_size,
            self.n_splits,
            dtype=int
        )

        for test_start in test_starts:
            # Test set
            test_end = min(test_start + self.test_size, n_samples)
            test_idx = indices[test_start:test_end]

            # Train set (before test, with purge gap)
            train_end = test_start - self.purge_gap

            if train_end <= 0:
                logger.warning(f"Skipping split: insufficient training data")
                continue

            train_idx = indices[:train_end]

            # Apply embargo (remove indices after test set)
            embargo_start = test_end
            embargo_end = min(test_end + self.embargo_gap, n_samples)

            logger.info(
                f"Split: train={len(train_idx)}, test={len(test_idx)}, "
                f"purge={self.purge_gap}, embargo={embargo_end - embargo_start}"
            )

            yield train_idx, test_idx


class AdvancedPreprocessor:
    """
    Complete preprocessing pipeline for financial time series.
    """

    def __init__(
        self,
        target_col: str,
        frac_diff_d: float = 0.5,
        rolling_window: int = 30,
        mi_threshold: float = 0.01,
        use_boruta: bool = True,
        interpolation_method: str = "time",
        test_stationarity: bool = True
    ):
        """
        Initialize preprocessor.

        Args:
            target_col: Target column name
            frac_diff_d: Fractional differentiation order
            rolling_window: Window for rolling normalization
            mi_threshold: Mutual information threshold
            use_boruta: Whether to use BorutaPy
            interpolation_method: Method for NaN interpolation
            test_stationarity: Whether to test stationarity with ADF
        """
        self.target_col = target_col
        self.frac_diff_d = frac_diff_d
        self.rolling_window = rolling_window
        self.mi_threshold = mi_threshold
        self.use_boruta = use_boruta
        self.interpolation_method = interpolation_method
        self.test_stationarity = test_stationarity

        # Components
        self.frac_diff = FractionalDifferentiator(d=frac_diff_d)
        self.interpolator = TemporalInterpolator(method=interpolation_method)
        self.normalizer = RollingNormalizer(window=rolling_window)
        self.feature_selector = None

        # State
        self.stationarity_results_ = {}
        self.selected_features_ = None

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Full preprocessing pipeline.

        Args:
            df: Input DataFrame with features and target

        Returns:
            Preprocessed DataFrame
        """
        logger.info("=" * 60)
        logger.info("ADVANCED PREPROCESSING PIPELINE")
        logger.info("=" * 60)

        # 0. Validate
        if self.target_col not in df.columns:
            raise ValueError(f"Target column '{self.target_col}' not found")

        # 1. Interpolate NaNs
        logger.info("\n[1/5] Interpolating missing values...")
        df_interp = self.interpolator.fit_transform(df)

        # 2. Fractional differentiation for stationarity
        logger.info("\n[2/5] Applying fractional differentiation...")
        df_diff = df_interp.copy()

        # Apply to all numeric columns
        for col in df_interp.select_dtypes(include=[np.number]).columns:
            logger.info(f"  Differentiating {col} (d={self.frac_diff_d})")
            df_diff[col] = self.frac_diff.fit_transform(df_interp[col])

            # Test stationarity
            if self.test_stationarity:
                result = self.frac_diff.test_stationarity(df_diff[col])
                self.stationarity_results_[col] = result

                if result.get("is_stationary"):
                    logger.info(f"    ✓ Stationary (p={result.get('p_value', 0):.4f})")
                else:
                    logger.warning(
                        f"    ✗ Non-stationary (p={result.get('p_value', 1):.4f})"
                    )

        # Remove NaNs introduced by differencing
        df_diff = df_diff.dropna()

        # 3. Rolling normalization
        logger.info(f"\n[3/5] Applying rolling z-score (window={self.rolling_window})...")

        # Separate features and target
        feature_cols = [c for c in df_diff.columns if c != self.target_col]

        df_features_norm = self.normalizer.fit_transform(df_diff[feature_cols])
        df_norm = df_features_norm.copy()
        df_norm[self.target_col] = df_diff[self.target_col]

        # Remove NaNs from rolling windows
        df_norm = df_norm.dropna()

        logger.info(f"  {len(df_norm)} samples after normalization")

        # 4. Feature selection
        logger.info(f"\n[4/5] Feature selection (MI + {'Boruta' if self.use_boruta else 'MI only'})...")

        self.feature_selector = FeatureSelector(
            target_col=self.target_col,
            mi_threshold=self.mi_threshold,
            use_boruta=self.use_boruta
        )

        X = df_norm.drop(columns=[self.target_col])
        y = df_norm[self.target_col]

        X_selected = self.feature_selector.fit_transform(X, y)
        self.selected_features_ = self.feature_selector.selected_features_

        # Combine with target
        df_final = X_selected.copy()
        df_final[self.target_col] = y

        # 5. Summary
        logger.info("\n[5/5] Preprocessing complete!")
        logger.info("=" * 60)
        logger.info(f"Original shape: {df.shape}")
        logger.info(f"Final shape: {df_final.shape}")
        logger.info(f"Selected features: {len(self.selected_features_)}")
        logger.info(f"Stationary features: {sum(r.get('is_stationary', False) for r in self.stationarity_results_.values())}/{len(self.stationarity_results_)}")
        logger.info("=" * 60)

        return df_final

    def get_cv_splits(
        self,
        df: pd.DataFrame,
        n_splits: int = 5,
        test_size: int = 100,
        purge_gap: int = 10
    ) -> PurgedWalkForward:
        """
        Get purged walk-forward CV splitter.

        Args:
            df: DataFrame to split
            n_splits: Number of splits
            test_size: Test set size
            purge_gap: Purge gap

        Returns:
            PurgedWalkForward instance
        """
        return PurgedWalkForward(
            n_splits=n_splits,
            test_size=test_size,
            purge_gap=purge_gap
        )
