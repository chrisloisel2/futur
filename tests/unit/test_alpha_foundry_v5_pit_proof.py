import pandas as pd

from alpha_foundry_v5.quality import audit_point_in_time


def test_structural_only_frame_is_not_full_pit_proof():
    frame = pd.DataFrame({
        "asof_ns": [100, 200, 300],
        "symbol": ["BTC", "BTC", "BTC"],
        "feature": [1.0, 2.0, 3.0],
    })
    result = audit_point_in_time(frame)
    assert result.structural_clean is True
    assert result.availability_proved is False
    assert result.clean is False
    assert result.proof_level == "STRUCTURAL_ONLY"


def test_explicit_receive_clock_can_prove_pit():
    frame = pd.DataFrame({
        "asof_ns": [100, 200, 300],
        "symbol": ["BTC", "BTC", "BTC"],
        "book_receive_ts_ns": [90, 190, 290],
    })
    result = audit_point_in_time(frame)
    assert result.structural_clean is True
    assert result.availability_proved is True
    assert result.clean is True
    assert result.proof_level == "FULL_AVAILABILITY"
