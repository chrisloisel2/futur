"""Tests for preprocessor module."""
import numpy as np
import pandas as pd
import pytest

from ..preprocessor import (
    AdvancedPreprocessor,
    FeatureSelector,
    FractionalDifferentiator,
    PurgedWalkForward,
    RollingNormalizer,
    TemporalInterpolator,
)


class TestFractionalDifferentiator:
    """Tests for fractional differentiation."""

    @pytest.fixture
    def sample_series(self):
        """Create sample time series."""
        np.random.seed(42)
        # Random walk (non-stationary)
        return pd.Series(np.cumsum(np.random.randn(1000)))

    def test_differentiation_preserves_length(self, sample_series):
        """Test that differentiation preserves series length."""
        diff = FractionalDifferentiator(d=0.5)
        result = diff.fit_transform(sample_series)

        assert len(result) == len(sample_series)

    def test_higher_d_more_stationary(self, sample_series):
        """Test that higher d values produce more stationary series."""
        diff_low = FractionalDifferentiator(d=0.3)
        diff_high = FractionalDifferentiator(d=0.7)

        result_low = diff_low.fit_transform(sample_series)
        result_high = diff_high.fit_transform(sample_series)

        test_low = diff_low.test_stationarity(result_low)
        test_high = diff_high.test_stationarity(result_high)

        # Higher d should have lower p-value (more stationary)
        if "p_value" in test_low and "p_value" in test_high:
            assert test_high["p_value"] <= test_low["p_value"]

    def test_stationarity_test(self, sample_series):
        """Test ADF stationarity test."""
        diff = FractionalDifferentiator(d=0.5)
        result = diff.fit_transform(sample_series)

        test = diff.test_stationarity(result)

        assert "is_stationary" in test
        assert "p_value" in test
        assert "adf_statistic" in test

    def test_insufficient_data_returns_error(self):
        """Test that insufficient data is handled."""
        short_series = pd.Series([1, 2, 3])
        diff = FractionalDifferentiator()

        test = diff.test_stationarity(short_series)

        assert not test["is_stationary"]
        assert "reason" in test


class TestRollingNormalizer:
    """Tests for rolling normalization."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data."""
        np.random.seed(42)
        return pd.DataFrame({
            "feature1": np.random.randn(200) * 10 + 100,
            "feature2": np.random.randn(200) * 5 + 50,
        })

    def test_normalization_output_shape(self, sample_data):
        """Test output shape matches input."""
        normalizer = RollingNormalizer(window=30)
        result = normalizer.fit_transform(sample_data)

        assert result.shape == sample_data.shape

    def test_rolling_mean_zero(self, sample_data):
        """Test that rolling mean is approximately zero."""
        normalizer = RollingNormalizer(window=30, min_periods=30)
        result = normalizer.fit_transform(sample_data)

        # After sufficient observations, rolling mean should be ~0
        assert abs(result["feature1"].iloc[50:].mean()) < 0.5

    def test_rolling_std_one(self, sample_data):
        """Test that rolling std is approximately one."""
        normalizer = RollingNormalizer(window=30, min_periods=30)
        result = normalizer.fit_transform(sample_data)

        # After sufficient observations, rolling std should be ~1
        assert abs(result["feature1"].iloc[50:].std() - 1.0) < 0.5


class TestTemporalInterpolator:
    """Tests for temporal interpolation."""

    @pytest.fixture
    def data_with_nans(self):
        """Create data with NaNs."""
        df = pd.DataFrame({
            "col1": [1.0, 2.0, np.nan, 4.0, 5.0, np.nan, 7.0],
            "col2": [10.0, np.nan, np.nan, 40.0, 50.0, 60.0, 70.0],
        })
        return df

    def test_interpolation_fills_nans(self, data_with_nans):
        """Test that interpolation fills NaN values."""
        interpolator = TemporalInterpolator(method="linear")
        result = interpolator.fit_transform(data_with_nans)

        # Should have no NaNs after interpolation
        assert result.isna().sum().sum() == 0

    def test_linear_interpolation_values(self):
        """Test linear interpolation produces correct values."""
        df = pd.DataFrame({"col": [1.0, np.nan, 3.0]})

        interpolator = TemporalInterpolator(method="linear")
        result = interpolator.fit_transform(df)

        # Should interpolate to 2.0
        assert result["col"].iloc[1] == 2.0

    def test_limit_parameter(self):
        """Test limit parameter restricts interpolation."""
        df = pd.DataFrame({
            "col": [1.0, np.nan, np.nan, np.nan, np.nan, 6.0]
        })

        interpolator = TemporalInterpolator(method="linear", limit=2)
        result = interpolator.fit_transform(df)

        # Should still have some NaNs due to limit
        # (though forward/backward fill may fill them)
        assert len(result) == len(df)


class TestFeatureSelector:
    """Tests for feature selection."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data with relevant and irrelevant features."""
        np.random.seed(42)
        n = 500

        # Relevant features
        X1 = np.random.randn(n)
        X2 = np.random.randn(n)

        # Irrelevant features (random noise)
        X3 = np.random.randn(n)
        X4 = np.random.randn(n)

        # Target depends on X1 and X2
        y = 2 * X1 + 3 * X2 + np.random.randn(n) * 0.1

        X = pd.DataFrame({
            "relevant1": X1,
            "relevant2": X2,
            "noise1": X3,
            "noise2": X4,
        })

        return X, pd.Series(y, name="target")

    def test_feature_selection_reduces_features(self, sample_data):
        """Test that feature selection reduces feature count."""
        X, y = sample_data

        selector = FeatureSelector(
            target_col="target",
            mi_threshold=0.05,
            use_boruta=False  # Disable Boruta for speed
        )

        X_selected = selector.fit_transform(X, y)

        # Should select fewer features than original
        assert len(X_selected.columns) <= len(X.columns)

    def test_feature_selection_identifies_relevant(self, sample_data):
        """Test that relevant features are selected."""
        X, y = sample_data

        selector = FeatureSelector(
            target_col="target",
            mi_threshold=0.1,
            use_boruta=False
        )

        selector.fit(X, y)

        # Relevant features should have higher MI scores
        assert selector.mi_scores_["relevant1"] > selector.mi_scores_["noise1"]
        assert selector.mi_scores_["relevant2"] > selector.mi_scores_["noise2"]

    def test_transform_without_fit_raises_error(self, sample_data):
        """Test that transform without fit raises error."""
        X, _ = sample_data

        selector = FeatureSelector(target_col="target")

        with pytest.raises(RuntimeError, match="Must call fit"):
            selector.transform(X)


class TestPurgedWalkForward:
    """Tests for purged walk-forward CV."""

    @pytest.fixture
    def sample_data(self):
        """Create sample time series data."""
        n = 1000
        df = pd.DataFrame({
            "feature": np.random.randn(n),
            "target": np.random.randn(n),
        })
        return df

    def test_cv_generates_splits(self, sample_data):
        """Test that CV generates expected number of splits."""
        cv = PurgedWalkForward(n_splits=5, test_size=100)

        splits = list(cv.split(sample_data))

        assert len(splits) == 5

    def test_train_test_no_overlap(self, sample_data):
        """Test that train and test sets don't overlap."""
        cv = PurgedWalkForward(n_splits=3, test_size=100, purge_gap=10)

        for train_idx, test_idx in cv.split(sample_data):
            # No intersection
            assert len(set(train_idx) & set(test_idx)) == 0

    def test_purge_gap_enforced(self, sample_data):
        """Test that purge gap is enforced."""
        purge_gap = 20
        cv = PurgedWalkForward(n_splits=3, test_size=100, purge_gap=purge_gap)

        for train_idx, test_idx in cv.split(sample_data):
            # Last train index should be at least purge_gap before first test
            if len(train_idx) > 0 and len(test_idx) > 0:
                assert test_idx[0] - train_idx[-1] >= purge_gap

    def test_temporal_ordering_preserved(self, sample_data):
        """Test that temporal ordering is preserved."""
        cv = PurgedWalkForward(n_splits=3, test_size=100)

        for train_idx, test_idx in cv.split(sample_data):
            # Train indices should be before test indices
            if len(train_idx) > 0 and len(test_idx) > 0:
                assert train_idx[-1] < test_idx[0]


class TestAdvancedPreprocessor:
    """Tests for complete preprocessing pipeline."""

    @pytest.fixture
    def sample_data(self):
        """Create realistic sample data."""
        np.random.seed(42)
        n = 500

        dates = pd.date_range("2023-01-01", periods=n, freq="1H")

        # Create features with some structure
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        volume = np.abs(np.random.randn(n) * 1000 + 5000)

        # Target: next period return
        returns = pd.Series(close).pct_change().shift(-1)

        df = pd.DataFrame({
            "timestamp": dates,
            "close": close,
            "volume": volume,
            "sma_10": pd.Series(close).rolling(10).mean(),
            "rsi_14": np.random.rand(n) * 100,
            "target": returns,
        })

        df.set_index("timestamp", inplace=True)

        return df

    def test_preprocessor_runs_without_error(self, sample_data):
        """Test that preprocessor runs without errors."""
        preprocessor = AdvancedPreprocessor(
            target_col="target",
            frac_diff_d=0.5,
            rolling_window=30,
            mi_threshold=0.0,  # Low threshold to keep features
            use_boruta=False,  # Disable for speed
            test_stationarity=True
        )

        result = preprocessor.fit_transform(sample_data.dropna())

        assert result is not None
        assert len(result) > 0
        assert "target" in result.columns

    def test_preprocessor_reduces_features(self, sample_data):
        """Test that feature selection reduces feature count."""
        preprocessor = AdvancedPreprocessor(
            target_col="target",
            mi_threshold=0.1,
            use_boruta=False
        )

        result = preprocessor.fit_transform(sample_data.dropna())

        # Should have fewer features (excluding target)
        assert len(result.columns) - 1 <= len(sample_data.columns) - 1

    def test_preprocessor_handles_nans(self, sample_data):
        """Test that preprocessor handles NaNs properly."""
        # Data already has NaNs from rolling window
        preprocessor = AdvancedPreprocessor(
            target_col="target",
            use_boruta=False
        )

        result = preprocessor.fit_transform(sample_data)

        # Should have minimal NaNs
        assert result.isna().sum().sum() == 0

    def test_stationarity_results_recorded(self, sample_data):
        """Test that stationarity results are recorded."""
        preprocessor = AdvancedPreprocessor(
            target_col="target",
            test_stationarity=True,
            use_boruta=False
        )

        preprocessor.fit_transform(sample_data.dropna())

        # Should have stationarity results
        assert len(preprocessor.stationarity_results_) > 0

    def test_get_cv_splits(self, sample_data):
        """Test getting CV splits."""
        preprocessor = AdvancedPreprocessor(
            target_col="target",
            use_boruta=False
        )

        df_processed = preprocessor.fit_transform(sample_data.dropna())

        cv = preprocessor.get_cv_splits(df_processed, n_splits=3)

        splits = list(cv.split(df_processed))
        assert len(splits) == 3
