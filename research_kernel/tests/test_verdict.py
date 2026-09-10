from __future__ import annotations

import pytest

from research_kernel.verdict import (
    TERMINAL_STATUSES,
    Verdict,
    VerdictIntegrityError,
    VerdictStatus,
)


def _promotable(**over):
    payload = dict(
        mechanism_id="demo_v1",
        status=VerdictStatus.PAPER_ELIGIBLE,
        net_edge_bps_cost_x2=3.0,
        n_independent=250,
        profit_factor=1.4,
        forward_seal_id="demo_v1_2026-09-11",
        forward_result_id="demo_v1_2026-09-11_result",
    )
    payload.update(over)
    return payload


def test_a_terminal_status_needs_no_evidence():
    v = Verdict("demo_v1", VerdictStatus.COST_WALL)
    assert v.is_terminal
    assert v.status in TERMINAL_STATUSES


def test_paper_eligible_without_a_seal_is_refused():
    with pytest.raises(VerdictIntegrityError, match="without a sealed forward"):
        Verdict(**_promotable(forward_seal_id=None))


def test_paper_eligible_without_a_forward_result_is_refused():
    with pytest.raises(VerdictIntegrityError, match="without a recorded forward result"):
        Verdict(**_promotable(forward_result_id=None))


def test_paper_eligible_needs_a_positive_edge_at_double_cost():
    with pytest.raises(VerdictIntegrityError, match="double cost"):
        Verdict(**_promotable(net_edge_bps_cost_x2=-0.1))


def test_paper_eligible_needs_two_hundred_episodes():
    with pytest.raises(VerdictIntegrityError, match="independent events"):
        Verdict(**_promotable(n_independent=199))


def test_live_needs_three_hundred_episodes_and_thirty_days():
    with pytest.raises(VerdictIntegrityError, match="300 independent"):
        Verdict(**_promotable(status=VerdictStatus.LIVE_MICRO_ELIGIBLE, n_independent=250, paper_days=45))
    with pytest.raises(VerdictIntegrityError, match="30 days of paper"):
        Verdict(**_promotable(status=VerdictStatus.LIVE_MICRO_ELIGIBLE, n_independent=350, paper_days=10))
    with pytest.raises(VerdictIntegrityError, match="profit factor"):
        Verdict(
            **_promotable(
                status=VerdictStatus.LIVE_MICRO_ELIGIBLE,
                n_independent=350,
                paper_days=45,
                profit_factor=1.1,
            )
        )


def test_a_complete_promotion_is_accepted():
    v = Verdict(**_promotable(status=VerdictStatus.LIVE_MICRO_ELIGIBLE, n_independent=350, paper_days=45))
    assert v.status is VerdictStatus.LIVE_MICRO_ELIGIBLE
    assert not v.is_terminal


def test_round_trip_through_json():
    v = Verdict("demo_v1", VerdictStatus.OVERFIT, t_stat=1.2, failed_gates=["gate 5"])
    back = Verdict.from_dict(v.to_dict())
    assert back.status is VerdictStatus.OVERFIT
    assert back.failed_gates == ["gate 5"]


def test_there_is_no_validated_status():
    assert "VALIDATED" not in {s.value for s in VerdictStatus}
