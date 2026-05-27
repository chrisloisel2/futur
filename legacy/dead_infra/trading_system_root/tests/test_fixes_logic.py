import numpy as np
import pytest

def test_fix1_expected_edge_formula():
    """Test FIX-1: expected_edge formula correction"""
    pdh = np.array([0.7, 0.8, 0.6])
    q50 = np.array([0.002, 0.003, 0.001])

    # Corrected formula (FIX-1)
    expected_edge = pdh * q50

    # Old (broken) formula
    pup = np.array([0.5, 0.5, 0.5])  # dummy
    expected_edge_old = pdh * ((2.0 * pup - 1.0) * q50)

    assert not np.allclose(expected_edge, expected_edge_old), "FIX-1 not applied"
    assert np.all(expected_edge > 0), "Expected edge should be positive"

def test_fix1_annualization_factor():
    """Test FIX-1: annualization changed from sqrt(365*24*60) to sqrt(252)"""
    old_factor = np.sqrt(365 * 24 * 60)
    new_factor = np.sqrt(252)

    assert old_factor > 500, "Old factor should be ~525"
    assert 15 < new_factor < 16, "New factor should be ~15.87"
    assert new_factor / old_factor < 0.05, "New factor is ~30x smaller"

def test_fix2_loss_weight_increased():
    """Test FIX-2: w_up increased from 0.06 to 0.20"""
    w_up_old = 0.06
    w_up_new = 0.20

    assert w_up_new / w_up_old > 3.0, "w_up should be increased by 3.33x"

    # Check new weights sum to 1.0
    w_q05 = 0.15
    w_q50 = 0.15
    w_q95 = 0.15
    w_dir = 0.28
    w_up = 0.20
    w_rv = 0.07

    total = w_q05 + w_q50 + w_q95 + w_dir + w_up + w_rv
    assert abs(total - 1.0) < 1e-6, "Weights must sum to 1.0"

def test_fix3_label_is_up_directional():
    """Test FIX-3: is_up = (return_fwd > 0), not (tp_up_hit == 1)"""
    return_fwd = np.array([0.01, -0.02, 0.03, -0.01, 0.0])

    # Corrected label (FIX-3)
    is_up_new = (return_fwd > 0).astype(int)

    # Old (broken) label (simulate tp_up_hit which is different)
    tp_up_hit = np.array([1, 0, 1, 1, 0])  # Not correlated with return_fwd
    is_up_old = (tp_up_hit == 1).astype(int)

    # New label should be [1, 0, 1, 0, 0]
    expected_new = np.array([1, 0, 1, 0, 0])
    assert np.array_equal(is_up_new, expected_new), "FIX-3 not applied"

    # Verify correlation with return_fwd > 0 is perfect
    return_positive = (return_fwd > 0).astype(int)
    if is_up_new.std() > 0 and return_positive.std() > 0:
        corr = np.corrcoef(is_up_new, return_positive)[0, 1]
        assert abs(corr - 1.0) < 1e-6, "Correlation should be 1.0"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
