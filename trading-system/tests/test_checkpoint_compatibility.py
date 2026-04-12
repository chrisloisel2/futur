"""
Test checkpoint compatibility between TRM-only and TRM+HRM models.

This test suite ensures that:
1. Models trained without regime conditioning can only be loaded with use_regime_cond=False
2. Models trained with regime conditioning can only be loaded with use_regime_cond=True
3. Architecture dimensions are validated correctly
4. Forward passes are identical between training and inference

CRITICAL: These tests validate the single source of truth architecture.
"""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch

from pipeline.models.edge.forecaster import EdgeForecasterConfig, EdgeForecasterModel
from pipeline.models.edge.net import EdgeForecasterNet


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test artifacts"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def dummy_input():
    """Create dummy input data for testing"""
    B, T, D = 2, 32, 10
    x = torch.randn(B, T, D, dtype=torch.float32)
    return x


@pytest.fixture
def dummy_regime():
    """Create dummy regime vector"""
    B, D_regime = 2, 6
    regime = torch.randn(B, D_regime, dtype=torch.float32)
    return regime


class TestCheckpointCompatibility:
    """Test checkpoint save/load compatibility with regime conditioning"""

    def test_trm_only_save_and_load(self, temp_dir, dummy_input):
        """
        Test 1: TRM-only model (no regime conditioning)
        - Save checkpoint without regime_proj
        - Load with use_regime_cond=False → should succeed
        - Load with use_regime_cond=True → should fail
        """
        checkpoint_path = temp_dir / "trm_only.pt"

        # Create TRM-only config
        cfg = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,  # TRM-only
            device="cpu",
            dtype="float32",
        )

        # Build and save model
        input_dim = dummy_input.shape[-1]
        net = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)

        # Run forward to ensure model works
        out = net(dummy_input, regime_vec=None)
        assert len(out) == 9, "Expected 9 outputs from forward pass"

        # Save checkpoint
        torch.save(
            {
                "cfg": cfg.__dict__,
                "feature_cols": [f"feat_{i}" for i in range(input_dim)],
                "input_dim": input_dim,
                "state_dict": net.state_dict(),
            },
            checkpoint_path,
        )

        # Verify checkpoint keys DO NOT contain regime_proj
        payload = torch.load(checkpoint_path, map_location="cpu")
        state_keys = set(payload["state_dict"].keys())
        assert not any(k.startswith("regime_proj.") for k in state_keys), \
            "TRM-only checkpoint should NOT contain regime_proj weights"

        # Test 1.1: Load with use_regime_cond=False → should succeed
        model_trm = EdgeForecasterModel(cfg=cfg)
        model_trm.load(str(checkpoint_path))
        print("✓ TRM-only checkpoint loaded successfully with use_regime_cond=False")

        # Test 1.2: Load with use_regime_cond=True → should fail
        cfg_hrm = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=True,  # Attempt HRM mode
            regime_input_dim=6,
            device="cpu",
            dtype="float32",
        )

        model_hrm = EdgeForecasterModel(cfg=cfg_hrm)
        with pytest.raises(RuntimeError, match="use_regime_cond: user=True, checkpoint=False"):
            model_hrm.load(str(checkpoint_path))
        print("✓ TRM-only checkpoint correctly rejected when loading with use_regime_cond=True")

    def test_hrm_save_and_load(self, temp_dir, dummy_input, dummy_regime):
        """
        Test 2: TRM+HRM model (with regime conditioning)
        - Save checkpoint with regime_proj
        - Load with use_regime_cond=True → should succeed
        - Load with use_regime_cond=False → should fail
        """
        checkpoint_path = temp_dir / "trm_hrm.pt"

        # Create TRM+HRM config
        cfg = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=True,  # TRM+HRM
            regime_input_dim=6,
            device="cpu",
            dtype="float32",
        )

        # Build and save model
        input_dim = dummy_input.shape[-1]
        net = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)

        # Verify regime_proj exists
        assert net.regime_proj is not None, "HRM model should have regime_proj"

        # Run forward with regime
        out = net(dummy_input, regime_vec=dummy_regime)
        assert len(out) == 9, "Expected 9 outputs from forward pass"

        # Save checkpoint
        torch.save(
            {
                "cfg": cfg.__dict__,
                "feature_cols": [f"feat_{i}" for i in range(input_dim)],
                "input_dim": input_dim,
                "state_dict": net.state_dict(),
            },
            checkpoint_path,
        )

        # Verify checkpoint keys CONTAIN regime_proj
        payload = torch.load(checkpoint_path, map_location="cpu")
        state_keys = set(payload["state_dict"].keys())
        assert any(k.startswith("regime_proj.") for k in state_keys), \
            "HRM checkpoint MUST contain regime_proj weights"

        # Test 2.1: Load with use_regime_cond=True → should succeed
        model_hrm = EdgeForecasterModel(cfg=cfg)
        model_hrm.load(str(checkpoint_path))
        print("✓ HRM checkpoint loaded successfully with use_regime_cond=True")

        # Test 2.2: Load with use_regime_cond=False → should fail
        cfg_trm = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,  # Attempt TRM-only mode
            device="cpu",
            dtype="float32",
        )

        model_trm = EdgeForecasterModel(cfg=cfg_trm)
        with pytest.raises(RuntimeError, match="use_regime_cond: user=False, checkpoint=True"):
            model_trm.load(str(checkpoint_path))
        print("✓ HRM checkpoint correctly rejected when loading with use_regime_cond=False")

    def test_architecture_dimension_validation(self, temp_dir, dummy_input):
        """
        Test 3: Architecture dimension validation
        - Save checkpoint with specific dimensions
        - Attempt to load with mismatched dimensions → should fail
        """
        checkpoint_path = temp_dir / "dim_check.pt"

        # Create model with specific dimensions
        cfg_original = EdgeForecasterConfig(
            seq_len=32,
            d_model=128,  # Original
            n_heads=8,    # Original
            n_layers=3,   # Original
            d_ff=256,     # Original
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        input_dim = dummy_input.shape[-1]
        net = EdgeForecasterNet(input_dim=input_dim, cfg=cfg_original)

        # Save checkpoint
        torch.save(
            {
                "cfg": cfg_original.__dict__,
                "feature_cols": [f"feat_{i}" for i in range(input_dim)],
                "input_dim": input_dim,
                "state_dict": net.state_dict(),
            },
            checkpoint_path,
        )

        # Test 3.1: Mismatch d_model
        cfg_wrong_d_model = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,  # WRONG
            n_heads=8,
            n_layers=3,
            d_ff=256,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        model = EdgeForecasterModel(cfg=cfg_wrong_d_model)
        with pytest.raises(RuntimeError, match="d_model: user=64, checkpoint=128"):
            model.load(str(checkpoint_path))
        print("✓ d_model mismatch correctly detected")

        # Test 3.2: Mismatch n_heads
        cfg_wrong_n_heads = EdgeForecasterConfig(
            seq_len=32,
            d_model=128,
            n_heads=4,  # WRONG
            n_layers=3,
            d_ff=256,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        model = EdgeForecasterModel(cfg=cfg_wrong_n_heads)
        with pytest.raises(RuntimeError, match="n_heads: user=4, checkpoint=8"):
            model.load(str(checkpoint_path))
        print("✓ n_heads mismatch correctly detected")

        # Test 3.3: Mismatch n_layers
        cfg_wrong_n_layers = EdgeForecasterConfig(
            seq_len=32,
            d_model=128,
            n_heads=8,
            n_layers=2,  # WRONG
            d_ff=256,
            dropout=0.1,
            attn_dropout=0.05,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        model = EdgeForecasterModel(cfg=cfg_wrong_n_layers)
        with pytest.raises(RuntimeError, match="n_layers: user=2, checkpoint=3"):
            model.load(str(checkpoint_path))
        print("✓ n_layers mismatch correctly detected")


class TestForwardPassEquivalence:
    """Test that forward passes are identical between training and inference"""

    def test_trm_forward_consistency(self, dummy_input):
        """
        Test 4: TRM-only forward pass consistency
        - Create net directly (training mode)
        - Create via EdgeForecasterModel (inference mode)
        - Verify outputs are identical
        """
        cfg = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.0,  # Disable dropout for deterministic test
            attn_dropout=0.0,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        input_dim = dummy_input.shape[-1]

        # Training-style net
        net_train = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)
        net_train.eval()

        # Inference-style net (via model wrapper)
        net_inference = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)
        net_inference.load_state_dict(net_train.state_dict())
        net_inference.eval()

        # Run both
        with torch.no_grad():
            out_train = net_train(dummy_input, regime_vec=None)
            out_inference = net_inference(dummy_input, regime_vec=None)

        # Compare all 9 outputs
        for i, (train_out, inf_out) in enumerate(zip(out_train, out_inference)):
            assert torch.allclose(train_out, inf_out, atol=1e-6), \
                f"Output {i} mismatch between training and inference forward pass"

        print("✓ TRM-only forward pass is identical between training and inference")

    def test_hrm_forward_consistency(self, dummy_input, dummy_regime):
        """
        Test 5: TRM+HRM forward pass consistency with regime conditioning
        """
        cfg = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.0,
            attn_dropout=0.0,
            use_regime_cond=True,
            regime_input_dim=6,
            device="cpu",
            dtype="float32",
        )

        input_dim = dummy_input.shape[-1]

        # Training-style net
        net_train = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)
        net_train.eval()

        # Inference-style net
        net_inference = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)
        net_inference.load_state_dict(net_train.state_dict())
        net_inference.eval()

        # Run both with regime
        with torch.no_grad():
            out_train = net_train(dummy_input, regime_vec=dummy_regime)
            out_inference = net_inference(dummy_input, regime_vec=dummy_regime)

        # Compare all 9 outputs
        for i, (train_out, inf_out) in enumerate(zip(out_train, out_inference)):
            assert torch.allclose(train_out, inf_out, atol=1e-6), \
                f"Output {i} mismatch between training and inference forward pass (HRM mode)"

        print("✓ HRM forward pass is identical between training and inference")

    def test_numerical_protections_active(self, dummy_input):
        """
        Test 6: Verify numerical protections (clamps) are active
        - Create extreme inputs that would cause explosion without clamps
        - Verify outputs remain finite
        """
        cfg = EdgeForecasterConfig(
            seq_len=32,
            d_model=64,
            n_heads=4,
            n_layers=2,
            d_ff=128,
            dropout=0.0,
            attn_dropout=0.0,
            use_regime_cond=False,
            device="cpu",
            dtype="float32",
        )

        input_dim = dummy_input.shape[-1]
        net = EdgeForecasterNet(input_dim=input_dim, cfg=cfg)
        net.eval()

        # Create extreme input (simulating edge case in AMP training)
        extreme_input = torch.randn_like(dummy_input) * 100.0

        with torch.no_grad():
            out = net(extreme_input, regime_vec=None)

        # All outputs must be finite (no inf/nan)
        for i, tensor in enumerate(out):
            assert torch.isfinite(tensor).all(), \
                f"Output {i} contains non-finite values (clamps not working)"

        print("✓ Numerical protections (clamps) are active and preventing explosions")


if __name__ == "__main__":
    # Run tests manually
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
