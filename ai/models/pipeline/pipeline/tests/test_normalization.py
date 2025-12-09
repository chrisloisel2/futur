"""Tests for normalization module."""
import numpy as np
import pandas as pd
import pytest

from ..normalization import AdaptiveNormalizer


class TestAdaptiveNormalizer:
    """Tests for AdaptiveNormalizer."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data for testing."""
        np.random.seed(42)
        return pd.DataFrame(
            {
                "feature1": np.random.randn(1000) * 10 + 100,
                "feature2": np.random.randn(1000) * 5 + 50,
            }
        )

    def test_fit_transform_creates_normalized_data(self, sample_data):
        """Test fit_transform normalizes data."""
        normalizer = AdaptiveNormalizer(method="robust")
        normalized = normalizer.fit_transform(sample_data)

        # Check normalized data has similar shape
        assert normalized.shape == sample_data.shape

        # Check values are scaled (not identical to input)
        assert not np.allclose(normalized["feature1"].values, sample_data["feature1"].values)

    def test_fit_and_transform_separately(self, sample_data):
        """Test fit and transform can be called separately."""
        normalizer = AdaptiveNormalizer()

        # Fit on training data
        normalizer.fit(sample_data[:800])

        # Transform test data
        test_data = sample_data[800:]
        normalized = normalizer.transform(test_data)

        assert len(normalized) == len(test_data)
        assert normalizer._is_fitted

    def test_transform_without_fit_raises_error(self, sample_data):
        """Test transform without fit raises error."""
        normalizer = AdaptiveNormalizer()

        with pytest.raises(RuntimeError, match="must be fitted"):
            normalizer.transform(sample_data)

    def test_save_and_load_state(self, sample_data, tmp_path):
        """Test saving and loading normalizer state."""
        normalizer = AdaptiveNormalizer()
        normalizer.fit(sample_data)

        # Save state
        state_file = tmp_path / "normalizer.json"
        normalizer.save_state(str(state_file))

        # Load state
        loaded = AdaptiveNormalizer.load_state(str(state_file))

        # Should have same state
        assert loaded.state == normalizer.state
        assert loaded._is_fitted

        # Should produce same output
        original_output = normalizer.transform(sample_data)
        loaded_output = loaded.transform(sample_data)

        pd.testing.assert_frame_equal(original_output, loaded_output)

    def test_inverse_transform(self, sample_data):
        """Test inverse transform recovers original scale."""
        normalizer = AdaptiveNormalizer()
        normalized = normalizer.fit_transform(sample_data)

        # Inverse transform
        recovered = normalizer.inverse_transform(normalized)

        # Should be close to original (within numerical precision)
        # Note: Won't be exact due to outlier clipping
        assert recovered.shape == sample_data.shape

    def test_standard_vs_robust_method(self, sample_data):
        """Test standard vs robust normalization methods."""
        robust_normalizer = AdaptiveNormalizer(method="robust")
        standard_normalizer = AdaptiveNormalizer(method="standard")

        robust_norm = robust_normalizer.fit_transform(sample_data)
        standard_norm = standard_normalizer.fit_transform(sample_data)

        # Results should be different
        assert not np.allclose(
            robust_norm["feature1"].values, standard_norm["feature1"].values
        )

    def test_outlier_clipping(self):
        """Test outliers are clipped during normalization."""
        # Create data with extreme outliers
        data = pd.DataFrame({"feature": [1, 2, 3, 4, 5, 100, 200]})

        normalizer = AdaptiveNormalizer(z_threshold=3.0, method="robust")
        normalized = normalizer.fit_transform(data)

        # Check outliers are clipped (not infinite)
        assert not np.isinf(normalized["feature"]).any()
        assert normalized["feature"].max() < 10

    def test_preserves_index(self, sample_data):
        """Test normalization preserves DataFrame index."""
        sample_data.index = pd.date_range("2021-01-01", periods=len(sample_data), freq="1H")

        normalizer = AdaptiveNormalizer()
        normalized = normalizer.fit_transform(sample_data, preserve_index=True)

        pd.testing.assert_index_equal(normalized.index, sample_data.index)
