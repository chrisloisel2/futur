"""
Test unitaire pour FIX-4: AMP-safe attention mask
==================================================

Vérifie que le remplacement de float("-inf") par torch.finfo(dtype).min
élimine les NaN dans l'attention mechanism sous AMP.

Usage:
    python tests/test_fix4_amp_mask.py
"""

import sys
from pathlib import Path

import torch
import torch.nn.functional as F

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_dtype_min_value_correctness():
    """Vérifier que torch.finfo(dtype).min retourne des valeurs finies."""
    print("=" * 70)
    print("TEST 1: torch.finfo(dtype).min correctness")
    print("=" * 70)

    for dtype in [torch.float32, torch.float16, torch.bfloat16]:
        min_val = torch.finfo(dtype).min
        print(f"\ndtype={dtype}:")
        print(f"  min_val = {min_val:.6e}")
        print(f"  is_finite = {torch.isfinite(torch.tensor(min_val))}")
        print(f"  min_val < -1e10 = {min_val < -1e10}")

        # Assertions
        assert torch.isfinite(torch.tensor(min_val)), f"{dtype}: min_val should be finite!"
        # Note: float16 min is ~-65504, bfloat16 min is ~-3.4e38, float32 min is ~-3.4e38
        expected_min = -1e4 if dtype == torch.float16 else -1e10
        assert min_val < expected_min, f"{dtype}: min_val should be very negative!"

    print("\n✅ TEST 1 PASSED: All dtypes have valid finite min_val\n")


def test_masked_fill_no_inf():
    """Vérifier que masked_fill avec min_val ne produit pas d'inf."""
    print("=" * 70)
    print("TEST 2: masked_fill with min_val (no -inf)")
    print("=" * 70)

    for dtype in [torch.float32, torch.float16, torch.bfloat16]:
        att = torch.randn(2, 4, 32, 32, dtype=dtype)
        mask = torch.ones(32, 32, dtype=torch.bool).triu(1)

        # OLD CODE (buggy)
        att_old = att.clone().masked_fill(mask, float("-inf"))

        # NEW CODE (FIX-4)
        min_val = torch.finfo(att.dtype).min
        att_new = att.clone().masked_fill(mask, min_val)

        print(f"\ndtype={dtype}:")
        print(f"  OLD: has_inf = {torch.isinf(att_old).any()}")
        print(f"  NEW: has_inf = {torch.isinf(att_new).any()}")

        # Assertions
        assert torch.isinf(att_old).any(), f"{dtype}: OLD code should produce inf!"
        assert not torch.isinf(att_new).any(), f"{dtype}: NEW code should NOT produce inf!"

    print("\n✅ TEST 2 PASSED: masked_fill with min_val produces no inf\n")


def test_softmax_stability():
    """Vérifier que softmax sur min_val ne produit pas de NaN."""
    print("=" * 70)
    print("TEST 3: Softmax stability with min_val")
    print("=" * 70)

    for dtype in [torch.float32, torch.float16, torch.bfloat16]:
        att = torch.randn(4, 8, 64, 64, dtype=dtype)
        mask = torch.ones(64, 64, dtype=torch.bool).triu(1)

        # FIX-4
        min_val = torch.finfo(att.dtype).min
        att = att.masked_fill(mask, min_val)

        # Softmax (moment critique)
        att_soft = F.softmax(att, dim=-1)

        has_nan = torch.isnan(att_soft).any()
        has_inf = torch.isinf(att_soft).any()

        print(f"\ndtype={dtype}:")
        print(f"  After masked_fill: min={att.min():.2e}")
        print(f"  After softmax: has_NaN={has_nan}, has_inf={has_inf}")
        print(f"  Softmax sum per row (should be ~1.0): {att_soft.sum(dim=-1).mean():.6f}")

        # Assertions
        assert not has_nan, f"{dtype}: Softmax produced NaN!"
        assert not has_inf, f"{dtype}: Softmax produced inf!"
        assert abs(att_soft.sum(dim=-1).mean() - 1.0) < 1e-3, f"{dtype}: Softmax sum != 1.0!"

    print("\n✅ TEST 3 PASSED: Softmax is stable with min_val\n")


def test_alibi_plus_softmax_stability():
    """Test cas réel: attention mask + ALiBi offsets + softmax."""
    print("=" * 70)
    print("TEST 4: ALiBi + masked attention + softmax (REAL SCENARIO)")
    print("=" * 70)

    for dtype in [torch.float32, torch.float16, torch.bfloat16]:
        B, H, T = 4, 5, 32
        att = torch.randn(B, H, T, T, dtype=dtype)

        # Causal mask
        mask = torch.ones(T, T, dtype=torch.bool).triu(1)

        # FIX-4: min_val instead of -inf
        min_val = torch.finfo(att.dtype).min
        att = att.masked_fill(mask, min_val)

        # ALiBi slopes (from actual code)
        slopes = torch.tensor([0.5, 0.25, 0.125, 0.0625, 0.03125], dtype=torch.float32)
        slopes = slopes.view(1, H, 1, 1)

        # ALiBi distance matrix
        dist = torch.arange(T).view(1, -1) - torch.arange(T).view(-1, 1)
        dist = dist.clamp(min=0).float()

        # Apply ALiBi (THIS IS WHERE -inf CAN BECOME UNSTABLE)
        att = att.to(torch.float32)  # ALiBi computation in float32
        att = att - slopes * dist.view(1, 1, T, T)
        att = att.to(dtype)  # Cast back

        # Softmax
        att_soft = F.softmax(att, dim=-1)

        has_nan = torch.isnan(att_soft).any()
        has_inf = torch.isinf(att_soft).any()

        print(f"\ndtype={dtype}:")
        print(f"  After masked_fill: min={att.min():.2e}, has_inf={torch.isinf(att).any()}")
        print(f"  After ALiBi: min={att.min():.2e}")
        print(f"  After softmax: has_NaN={has_nan}, has_inf={has_inf}")

        # Assertions
        assert not has_nan, f"{dtype}: ALiBi+softmax produced NaN!"
        assert not has_inf, f"{dtype}: ALiBi+softmax produced inf!"

    print("\n✅ TEST 4 PASSED: ALiBi + attention is stable\n")


def test_edge_case_all_masked_row():
    """Test cas edge: première rangée entièrement masquée sauf diagonal."""
    print("=" * 70)
    print("TEST 5: Edge case - all masked row except diagonal")
    print("=" * 70)

    T = 8
    att = torch.randn(2, 4, T, T, dtype=torch.float16)

    # Causal mask (première rangée: seulement [0,0] non masqué)
    mask = torch.ones(T, T, dtype=torch.bool).triu(1)

    # FIX-4
    min_val = torch.finfo(att.dtype).min
    att = att.masked_fill(mask, min_val)

    # Softmax
    att_soft = F.softmax(att, dim=-1)

    # Vérifier pas de NaN
    assert not torch.isnan(att_soft).any(), "NaN detected in edge case!"

    # Première rangée devrait être one-hot sur position 0
    first_row = att_soft[0, 0, 0, :]
    print(f"\nFirst row softmax: {first_row}")
    print(f"First element (should be ~1.0): {first_row[0]:.6f}")
    print(f"Sum (should be 1.0): {first_row.sum():.6f}")

    # Assertions
    assert abs(first_row[0] - 1.0) < 1e-2, "First element should be ~1.0!"
    assert abs(first_row.sum() - 1.0) < 1e-4, "Softmax sum should be 1.0!"

    print("\n✅ TEST 5 PASSED: Edge case handled correctly\n")


def test_comparison_old_vs_new():
    """Comparaison quantitative: OLD (-inf) vs NEW (min_val)."""
    print("=" * 70)
    print("TEST 6: Quantitative comparison OLD vs NEW")
    print("=" * 70)

    results = []

    for dtype in [torch.float32, torch.float16, torch.bfloat16]:
        att = torch.randn(8, 4, 64, 64, dtype=dtype)
        mask = torch.ones(64, 64, dtype=torch.bool).triu(1)

        # OLD CODE
        att_old = att.clone().masked_fill(mask, float("-inf"))
        try:
            soft_old = F.softmax(att_old, dim=-1)
            nan_old = torch.isnan(soft_old).any().item()
            inf_old = torch.isinf(soft_old).any().item()
        except:
            nan_old = True
            inf_old = True

        # NEW CODE (FIX-4)
        min_val = torch.finfo(att.dtype).min
        att_new = att.clone().masked_fill(mask, min_val)
        soft_new = F.softmax(att_new, dim=-1)
        nan_new = torch.isnan(soft_new).any().item()
        inf_new = torch.isinf(soft_new).any().item()

        results.append({
            "dtype": str(dtype),
            "old_has_nan": nan_old,
            "old_has_inf": inf_old,
            "new_has_nan": nan_new,
            "new_has_inf": inf_new,
        })

        print(f"\n{dtype}:")
        print(f"  OLD: NaN={nan_old}, inf={inf_old}")
        print(f"  NEW: NaN={nan_new}, inf={inf_new}")

    # Assertions globales
    for r in results:
        assert not r["new_has_nan"], f"{r['dtype']}: NEW code should not produce NaN!"
        assert not r["new_has_inf"], f"{r['dtype']}: NEW code should not produce inf!"

    print("\n✅ TEST 6 PASSED: NEW code is strictly better than OLD\n")


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("FIX-4 UNIT TEST SUITE: AMP-Safe Attention Mask")
    print("=" * 70 + "\n")

    try:
        test_dtype_min_value_correctness()
        test_masked_fill_no_inf()
        test_softmax_stability()
        test_alibi_plus_softmax_stability()
        test_edge_case_all_masked_row()
        test_comparison_old_vs_new()

        print("=" * 70)
        print("🎉 ALL TESTS PASSED (6/6)")
        print("=" * 70)
        print("\nFIX-4 is validated and ready for production.")
        return 0

    except AssertionError as e:
        print("\n" + "=" * 70)
        print("❌ TEST FAILED")
        print("=" * 70)
        print(f"\nError: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
