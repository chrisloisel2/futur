"""Tests for fusion module."""
import pytest
import torch
import numpy as np

from models.fusion import (
    MetaFeatureExtractor,
    MarketRegimeDetector,
    CrossBranchAttention,
    AdaptiveGating,
    AdvancedFusionModule,
    FusionStrategy,
)


class TestMetaFeatureExtractor:
    """Test meta-feature extraction."""

    def test_extract_meta_features(self):
        """Test basic meta-feature extraction."""
        extractor = MetaFeatureExtractor(seq_len=96, window=24)

        # Create fake time series
        batch_size = 32
        seq_len = 96
        features = 7

        x = torch.randn(batch_size, seq_len, features)

        # Extract
        meta_features = extractor(x)

        # Check shape
        assert meta_features.shape == (batch_size, 3)

        # Check values are finite
        assert torch.isfinite(meta_features).all()

    def test_volatility_computation(self):
        """Test that volatility is computed correctly."""
        extractor = MetaFeatureExtractor(seq_len=100, window=50)

        # High volatility series
        x_volatile = torch.randn(10, 100, 5) * 10

        # Low volatility series
        x_stable = torch.randn(10, 100, 5) * 0.1

        meta_volatile = extractor(x_volatile)
        meta_stable = extractor(x_stable)

        # Volatility should be higher for volatile series
        assert meta_volatile[:, 0].mean() > meta_stable[:, 0].mean()


class TestMarketRegimeDetector:
    """Test market regime detection."""

    def test_regime_detection(self):
        """Test regime detection outputs."""
        detector = MarketRegimeDetector(meta_feature_dim=3, hidden_dim=64)

        batch_size = 32
        meta_features = torch.randn(batch_size, 3)

        regime_logits, regime_probs = detector(meta_features)

        # Check shapes
        assert regime_logits.shape == (batch_size, 4)
        assert regime_probs.shape == (batch_size, 4)

        # Check probabilities sum to 1
        assert torch.allclose(regime_probs.sum(dim=1), torch.ones(batch_size))

        # Check probabilities are in [0, 1]
        assert (regime_probs >= 0).all()
        assert (regime_probs <= 1).all()

    def test_different_regimes(self):
        """Test that different inputs produce different regimes."""
        detector = MarketRegimeDetector()

        # High volatility
        high_vol = torch.tensor([[1.0, 0.0, 0.0]])

        # High trend
        high_trend = torch.tensor([[0.0, 1.0, 0.0]])

        _, probs_vol = detector(high_vol)
        _, probs_trend = detector(high_trend)

        # Should produce different distributions
        assert not torch.allclose(probs_vol, probs_trend)


class TestCrossBranchAttention:
    """Test cross-attention mechanism."""

    def test_cross_attention(self):
        """Test cross-attention forward pass."""
        d_model = 256
        n_heads = 8

        attn = CrossBranchAttention(d_model=d_model, n_heads=n_heads)

        batch_size = 32

        query = torch.randn(batch_size, d_model)
        key = torch.randn(batch_size, d_model)
        value = torch.randn(batch_size, d_model)

        output = attn(query, key, value)

        # Check shape
        assert output.shape == (batch_size, d_model)

        # Check output is different from input
        assert not torch.allclose(output, query)

    def test_attention_with_sequence(self):
        """Test attention with sequence dimension."""
        d_model = 128
        attn = CrossBranchAttention(d_model=d_model, n_heads=4)

        batch_size = 16
        seq_len = 10

        query = torch.randn(batch_size, seq_len, d_model)
        key = torch.randn(batch_size, seq_len, d_model)
        value = torch.randn(batch_size, seq_len, d_model)

        output = attn(query, key, value)

        assert output.shape == (batch_size, seq_len, d_model)


class TestAdaptiveGating:
    """Test adaptive gating mechanism."""

    def test_gating_weights(self):
        """Test gating weight computation."""
        n_branches = 2
        n_regimes = 4
        embedding_dim = 256

        gating = AdaptiveGating(
            n_branches=n_branches,
            n_regimes=n_regimes,
            embedding_dim=embedding_dim,
        )

        batch_size = 32

        # Create embeddings
        embeddings = [
            torch.randn(batch_size, embedding_dim),
            torch.randn(batch_size, embedding_dim),
        ]

        # Create regime probabilities
        regime_probs = torch.softmax(torch.randn(batch_size, n_regimes), dim=-1)

        # Compute gates
        gates, final_embedding = gating(embeddings, regime_probs)

        # Check shapes
        assert gates.shape == (batch_size, n_branches)
        assert final_embedding.shape == (batch_size, embedding_dim)

        # Check gates sum to 1
        assert torch.allclose(gates.sum(dim=1), torch.ones(batch_size), atol=1e-6)

    def test_regime_specific_gating(self):
        """Test that different regimes produce different gates."""
        gating = AdaptiveGating(n_branches=2, n_regimes=4, embedding_dim=128)

        batch_size = 32

        embeddings = [
            torch.randn(batch_size, 128),
            torch.randn(batch_size, 128),
        ]

        # Regime 1 dominant
        regime_probs_1 = torch.zeros(batch_size, 4)
        regime_probs_1[:, 0] = 1.0

        # Regime 2 dominant
        regime_probs_2 = torch.zeros(batch_size, 4)
        regime_probs_2[:, 1] = 1.0

        gates_1, _ = gating(embeddings, regime_probs_1)
        gates_2, _ = gating(embeddings, regime_probs_2)

        # Gates should be different for different regimes
        # (not always true due to dynamic gates, but usually)
        assert not torch.allclose(gates_1.mean(dim=0), gates_2.mean(dim=0), atol=0.1)


class TestAdvancedFusionModule:
    """Test complete fusion module."""

    def test_fusion_forward(self):
        """Test fusion module forward pass."""
        timeseries_dim = 256
        tabular_dim = 128
        fusion_dim = 384

        fusion = AdvancedFusionModule(
            timeseries_dim=timeseries_dim,
            tabular_dim=tabular_dim,
            fusion_dim=fusion_dim,
            seq_len=96,
        )

        batch_size = 16

        ts_embedding = torch.randn(batch_size, timeseries_dim)
        tab_embedding = torch.randn(batch_size, tabular_dim)
        ts_input = torch.randn(batch_size, 96, 7)

        # Forward
        outputs = fusion(ts_embedding, tab_embedding, ts_input)

        # Check outputs
        assert "fused_embedding" in outputs
        assert "regime_probs" in outputs
        assert "gating_weights" in outputs
        assert "meta_features" in outputs

        # Check shapes
        assert outputs["fused_embedding"].shape == (batch_size, fusion_dim)
        assert outputs["regime_probs"].shape == (batch_size, 4)
        assert outputs["gating_weights"].shape == (batch_size, 2)
        assert outputs["meta_features"].shape == (batch_size, 3)

    def test_fusion_without_input(self):
        """Test fusion works without time series input."""
        fusion = AdvancedFusionModule(
            timeseries_dim=256,
            tabular_dim=128,
            fusion_dim=384,
        )

        batch_size = 16

        ts_embedding = torch.randn(batch_size, 256)
        tab_embedding = torch.randn(batch_size, 128)

        # Forward without input
        outputs = fusion(ts_embedding, tab_embedding, timeseries_input=None)

        # Should still work
        assert outputs["fused_embedding"].shape == (batch_size, 384)


class TestFusionStrategy:
    """Test different fusion strategies."""

    def test_concat_strategy(self):
        """Test concatenation strategy."""
        fusion = FusionStrategy(
            strategy="concat",
            timeseries_dim=256,
            tabular_dim=128,
            fusion_dim=384,
        )

        batch_size = 16

        ts_emb = torch.randn(batch_size, 256)
        tab_emb = torch.randn(batch_size, 128)

        outputs = fusion(ts_emb, tab_emb)

        assert "fused_embedding" in outputs
        assert outputs["fused_embedding"].shape == (batch_size, 384)

    def test_weighted_strategy(self):
        """Test weighted strategy."""
        fusion = FusionStrategy(
            strategy="weighted",
            timeseries_dim=256,
            tabular_dim=128,
            fusion_dim=384,
        )

        batch_size = 16

        ts_emb = torch.randn(batch_size, 256)
        tab_emb = torch.randn(batch_size, 128)

        outputs = fusion(ts_emb, tab_emb)

        assert "fused_embedding" in outputs
        assert "weights" in outputs
        assert outputs["fused_embedding"].shape == (batch_size, 384)

        # Weights should sum to 1
        assert torch.allclose(outputs["weights"].sum(), torch.tensor(1.0))

    def test_attention_strategy(self):
        """Test attention strategy."""
        # For attention, dims must match
        fusion = FusionStrategy(
            strategy="attention",
            timeseries_dim=256,
            tabular_dim=256,  # Same as timeseries_dim
            fusion_dim=384,
            n_heads=8,
        )

        batch_size = 16

        ts_emb = torch.randn(batch_size, 256)
        tab_emb = torch.randn(batch_size, 256)

        outputs = fusion(ts_emb, tab_emb)

        assert "fused_embedding" in outputs
        assert outputs["fused_embedding"].shape == (batch_size, 384)

    def test_adaptive_strategy(self):
        """Test adaptive strategy."""
        fusion = FusionStrategy(
            strategy="adaptive",
            timeseries_dim=256,
            tabular_dim=128,
            fusion_dim=384,
            seq_len=96,
        )

        batch_size = 16

        ts_emb = torch.randn(batch_size, 256)
        tab_emb = torch.randn(batch_size, 128)
        ts_input = torch.randn(batch_size, 96, 7)

        outputs = fusion(ts_emb, tab_emb, ts_input)

        assert "fused_embedding" in outputs
        assert "regime_probs" in outputs
        assert "gating_weights" in outputs
        assert outputs["fused_embedding"].shape == (batch_size, 384)

    def test_invalid_strategy(self):
        """Test that invalid strategy raises error."""
        with pytest.raises(ValueError):
            FusionStrategy(
                strategy="invalid",
                timeseries_dim=256,
                tabular_dim=128,
                fusion_dim=384,
            )


class TestGradientFlow:
    """Test gradient flow through fusion modules."""

    def test_fusion_gradients(self):
        """Test that gradients flow through fusion."""
        fusion = AdvancedFusionModule(
            timeseries_dim=256,
            tabular_dim=128,
            fusion_dim=384,
        )

        batch_size = 8

        ts_emb = torch.randn(batch_size, 256, requires_grad=True)
        tab_emb = torch.randn(batch_size, 128, requires_grad=True)

        outputs = fusion(ts_emb, tab_emb)
        fused = outputs["fused_embedding"]

        # Compute loss
        loss = fused.sum()
        loss.backward()

        # Check gradients exist
        assert ts_emb.grad is not None
        assert tab_emb.grad is not None

        # Check gradients are non-zero
        assert not torch.allclose(ts_emb.grad, torch.zeros_like(ts_emb.grad))
        assert not torch.allclose(tab_emb.grad, torch.zeros_like(tab_emb.grad))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
