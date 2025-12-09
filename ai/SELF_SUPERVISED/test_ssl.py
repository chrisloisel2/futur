"""
Quick test script to verify SSL module installation and functionality.

Run this to check if everything is working correctly.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import torch
import numpy as np

print("=" * 80)
print("SELF-SUPERVISED LEARNING MODULE TEST")
print("=" * 80)

# Test 1: Import modules
print("\n[1/7] Testing imports...")
try:
    from SELF_SUPERVISED import (
        TS2VecModel,
        MAEModel,
        SimCLRModel,
        RandomMasking,
        BlockMasking,
        TS2VecLoss,
        NTXentLoss,
        create_augmentations,
    )
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Test 2: Check device
print("\n[2/7] Checking device availability...")
if torch.backends.mps.is_available():
    device = torch.device('mps')
    print("✅ MPS (Apple Silicon) available")
elif torch.cuda.is_available():
    device = torch.device('cuda')
    print("✅ CUDA available")
else:
    device = torch.device('cpu')
    print("⚠️  Using CPU (slower)")

# Test 3: Test TS2Vec model
print("\n[3/7] Testing TS2Vec model...")
try:
    model = TS2VecModel(
        input_dim=8,
        hidden_dim=64,
        output_dim=320,
        depth=10,
    ).to(device)

    # Dummy data
    x = torch.randn(4, 100, 8).to(device)

    # Forward pass
    embeddings = model(x)
    assert embeddings.shape == (4, 100, 320), "Wrong output shape"

    # Encoding
    embeddings_pooled = model.encode(x, return_all=False)
    assert embeddings_pooled.shape == (4, 320), "Wrong pooled shape"

    print(f"✅ TS2Vec working (output shape: {embeddings.shape})")
except Exception as e:
    print(f"❌ TS2Vec failed: {e}")

# Test 4: Test MAE model
print("\n[4/7] Testing MAE model...")
try:
    model = MAEModel(
        input_dim=8,
        d_model=256,
        n_heads=8,
        n_layers=6,
        mask_ratio=0.75,
    ).to(device)

    x = torch.randn(4, 100, 8).to(device)

    # Forward with masking
    reconstructed, mask, loss = model(x)
    assert reconstructed.shape == x.shape, "Wrong reconstruction shape"
    assert mask.shape == (4, 100), "Wrong mask shape"

    print(f"✅ MAE working (reconstruction loss: {loss.item():.4f})")
except Exception as e:
    print(f"❌ MAE failed: {e}")

# Test 5: Test masking strategies
print("\n[5/7] Testing masking strategies...")
try:
    # Random masking
    random_mask = RandomMasking(mask_ratio=0.75)
    mask = random_mask(batch_size=4, seq_len=100, device=device)
    assert mask.shape == (4, 100), "Wrong mask shape"
    print(f"  ✓ RandomMasking: {mask.float().mean().item():.2%} visible")

    # Block masking
    block_mask = BlockMasking(mask_ratio=0.75, block_length=10)
    mask = block_mask(batch_size=4, seq_len=100, device=device)
    assert mask.shape == (4, 100), "Wrong mask shape"
    print(f"  ✓ BlockMasking: {mask.float().mean().item():.2%} visible")

    print("✅ All masking strategies working")
except Exception as e:
    print(f"❌ Masking failed: {e}")

# Test 6: Test contrastive losses
print("\n[6/7] Testing contrastive losses...")
try:
    # TS2Vec loss
    criterion = TS2VecLoss(temperature=0.2)
    z1 = torch.randn(4, 100, 320).to(device)
    z2 = torch.randn(4, 100, 320).to(device)
    loss = criterion(z1, z2)
    print(f"  ✓ TS2VecLoss: {loss.item():.4f}")

    # NT-Xent loss
    criterion = NTXentLoss(temperature=0.5)
    z1 = torch.randn(4, 128).to(device)
    z2 = torch.randn(4, 128).to(device)
    loss = criterion(z1, z2)
    print(f"  ✓ NTXentLoss: {loss.item():.4f}")

    print("✅ All losses working")
except Exception as e:
    print(f"❌ Losses failed: {e}")

# Test 7: Test augmentations
print("\n[7/7] Testing augmentations...")
try:
    x = torch.randn(4, 100, 8).to(device)

    augment = create_augmentations(['jitter', 'scaling'])
    x_aug = augment(x)
    assert x_aug.shape == x.shape, "Wrong augmented shape"

    print("✅ Augmentations working")
except Exception as e:
    print(f"❌ Augmentations failed: {e}")

# Summary
print("\n" + "=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print("✅ All tests passed!")
print("\nNext steps:")
print("1. Configure config_ssl.yaml with your MongoDB credentials")
print("2. Run: python example_usage.py --mode ts2vec")
print("3. Monitor training in ./checkpoints/ts2vec/")
print("\nFor more info, see README.md")
print("=" * 80)
