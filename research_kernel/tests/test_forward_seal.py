from __future__ import annotations

import pytest

from research_kernel.forward_seal import (
    ForwardSeal,
    ForwardSealError,
    expire,
    find_seal,
    record_result,
    seal_forward,
    verify_unchanged,
)
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.multiplicity import MultiplicityLedger


def spec(**over):
    payload = dict(
        mechanism_id="demo_v1",
        hypothesis=(
            "When the crowd is most long an asset it underperforms the assets the crowd "
            "is least long over the following day."
        ),
        economic_reason=(
            "The account census is dominated by small margin-constrained positions that "
            "are liquidated into the move, and whoever absorbs that flow is paid for it."
        ),
        data_sources=["binance_positioning"],
        universe=["BTCUSDT"],
        timeframe="1d",
        horizon="1d",
        side_mode="market_neutral",
        entry_rule={"basket": 15},
        exit_rule={"holding_days": 1},
        cost_model={"taker_fee_bps": 5.0},
        validation_window={"start": "2021-01-01", "end": "2024-01-01"},
        placebo_tests=["direction_flip"],
        declustering_rule={"method": "fixed_windows", "window_seconds": 86400},
        multiplicity_family="crowd_positioning",
        kill_criteria={},
        promotion_criteria={},
    )
    payload.update(over)
    return MechanismSpec.from_dict(payload)


def test_a_window_that_starts_in_the_past_is_not_a_forward_window(tmp_path):
    with pytest.raises(ForwardSealError, match="in the past"):
        seal_forward(spec(), "2020-01-01", "2020-02-01", root=tmp_path, now="2026-09-10T00:00:00Z")


def test_sealing_writes_an_active_file_and_hashes_the_rule(tmp_path):
    s = spec()
    seal = seal_forward(s, "2026-09-11", "2026-12-11", root=tmp_path, now="2026-09-10T00:00:00Z")
    assert (tmp_path / "active" / seal.filename()).exists()
    assert seal.rules_hash == s.rules_hash()
    assert seal.covers(s)


def test_a_rule_edited_during_the_window_voids_the_seal(tmp_path):
    s = spec()
    seal = seal_forward(s, "2026-09-11", "2026-12-11", root=tmp_path, now="2026-09-10T00:00:00Z")
    moved = spec(entry_rule={"basket": 20})
    with pytest.raises(ForwardSealError, match="rule changed"):
        verify_unchanged(moved, seal)


def test_a_second_active_seal_is_refused(tmp_path):
    s = spec()
    seal_forward(s, "2026-09-11", "2026-12-11", root=tmp_path, now="2026-09-10T00:00:00Z")
    with pytest.raises(ForwardSealError, match="already has an active seal"):
        seal_forward(s, "2026-09-12", "2026-12-12", root=tmp_path, now="2026-09-10T00:00:00Z")


def test_a_burned_period_is_refused(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_contamination(
        "crowd_positioning", [("2020-09-01", "2026-12-31")], "already swept"
    )
    with pytest.raises(ForwardSealError, match="burned"):
        seal_forward(
            spec(),
            "2026-09-11",
            "2026-12-11",
            root=tmp_path,
            ledger=led,
            now="2026-09-10T00:00:00Z",
        )


def test_the_seal_records_the_threshold_in_force(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    for i in range(87):
        led.record_trial("crowd_positioning", "past_v%d" % i, "h%d" % i)
    seal = seal_forward(
        spec(),
        "2027-01-01",
        "2027-04-01",
        root=tmp_path,
        ledger=led,
        now="2026-09-10T00:00:00Z",
    )
    assert seal.family_size_at_seal == 87
    assert seal.threshold_t_at_seal > 3.0


def test_an_edited_seal_file_is_detected(tmp_path):
    import json

    s = spec()
    seal = seal_forward(s, "2026-09-11", "2026-12-11", root=tmp_path, now="2026-09-10T00:00:00Z")
    path = tmp_path / "active" / seal.filename()
    payload = json.loads(path.read_text())
    payload["promotion_thresholds"] = {"net_edge_bps_min": -100}
    path.write_text(json.dumps(payload))
    with pytest.raises(ForwardSealError, match="edited"):
        ForwardSeal.read(path)


def test_active_expired_and_result_lifecycle(tmp_path):
    s = spec()
    seal = seal_forward(s, "2026-09-11", "2026-12-11", root=tmp_path, now="2026-09-10T00:00:00Z")
    assert find_seal("demo_v1", root=tmp_path) is not None
    assert seal.is_active("2026-10-01T00:00:00Z")
    assert not seal.is_active("2027-01-01T00:00:00Z")
    assert seal.has_elapsed("2027-01-01T00:00:00Z")
    result = record_result(seal, {"status": "FORWARD_FAILED"}, root=tmp_path)
    assert result.exists()
    expire(seal, root=tmp_path)
    assert find_seal("demo_v1", root=tmp_path) is None
    assert (tmp_path / "expired" / seal.filename()).exists()
