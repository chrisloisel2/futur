from __future__ import annotations

import pytest

from research_kernel.multiplicity import (
    MultiplicityError,
    MultiplicityLedger,
    sharpe_needed_for_years,
    threshold_t,
)


@pytest.mark.parametrize(
    "n,expected",
    [(1, 1.64), (5, 2.33), (10, 2.58), (52, 3.10), (700, 3.80)],
)
def test_threshold_is_derived_from_the_count(n, expected):
    assert threshold_t(n) == pytest.approx(expected, abs=0.005)


def test_the_threshold_reads_no_constant():
    assert threshold_t(6) > threshold_t(5)
    with pytest.raises(MultiplicityError):
        threshold_t(0)


def test_sealing_five_more_raises_the_bar_on_the_first_five(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    for i in range(5):
        led.record_trial("microstructure", "m_v%d" % i, "hash%d" % i)
    before = led.current_threshold("microstructure")
    quote = led.current_threshold("microstructure", extra=5)
    for i in range(5, 10):
        led.record_trial("microstructure", "m_v%d" % i, "hash%d" % i)
    after = led.current_threshold("microstructure")
    assert after > before
    assert after == pytest.approx(quote)


def test_a_reseal_is_not_free(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_trial("funding", "f_v1", "abc")
    with pytest.raises(MultiplicityError, match="already sealed"):
        led.record_trial("funding", "f_v1_again", "abc")


def test_editing_a_rule_needs_a_version_bump(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_trial("funding", "f_v1", "abc")
    with pytest.raises(MultiplicityError, match="bump the version"):
        led.record_trial("funding", "f_v1", "def")


def test_sensitivities_cost_trials(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_trial("basis", "b_v1", "h1", n_trials=3)
    assert led.family_size("basis") == 3


def test_families_do_not_contaminate_each_other(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_trial("basis", "b_v1", "h1", n_trials=10)
    assert led.family_size("news") == 0
    assert led.current_threshold("news") == pytest.approx(threshold_t(1))


def test_contamination_burns_a_period_and_charges_nothing(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_contamination("liquidation", [("2022-01-01", "2025-01-01")], "swept")
    assert led.family_size("liquidation") == 0
    assert led.is_burned("liquidation", "2024-06-01", "2024-07-01")
    assert led.is_burned("liquidation", "2025-06-01", "2025-07-01") is None


def test_an_empty_contamination_is_a_comment(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    with pytest.raises(MultiplicityError):
        led.record_contamination("news", [], "nothing in particular")


def test_the_chain_detects_an_edit(tmp_path):
    import json

    path = tmp_path / "m.json"
    led = MultiplicityLedger(path)
    led.record_trial("onchain", "o_v1", "h1")
    led.record_trial("onchain", "o_v2", "h2")
    assert led.verify()

    payload = json.loads(path.read_text())
    payload[0]["threshold_at_seal"] = 1.0
    path.write_text(json.dumps(payload))
    with pytest.raises(MultiplicityError):
        led.verify()


def test_a_threshold_left_behind_its_count_breaks_verification(tmp_path):
    import json

    path = tmp_path / "m.json"
    led = MultiplicityLedger(path)
    led.record_trial("onchain", "o_v1", "h1")
    payload = json.loads(path.read_text())
    payload.append(
        {
            "kind": "trial",
            "family": "onchain",
            "mechanism_id": "o_v2",
            "rules_hash": "h2",
            "n_trials": 1,
            "recorded_at": "",
            "note": "",
            "index": 1,
            "prev_hash": payload[0]["entry_hash"],
            "threshold_at_seal": payload[0]["threshold_at_seal"],
        }
    )
    path.write_text(json.dumps(payload))
    with pytest.raises(MultiplicityError):
        led.verify()


def test_retroactive_penalty_reports_what_each_seal_now_owes(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    led.record_trial("cross_exchange", "x_v1", "h1")
    led.record_trial("cross_exchange", "x_v2", "h2")
    pen = led.retroactive_penalty()["cross_exchange"]
    assert pen["x_v1"] > 0
    assert pen["x_v2"] == pytest.approx(0.0, abs=1e-9)


def test_unknown_family_is_refused(tmp_path):
    led = MultiplicityLedger(tmp_path / "m.json")
    with pytest.raises(MultiplicityError):
        led.record_trial("astrology", "a_v1", "h")


def test_sharpe_identity():
    assert sharpe_needed_for_years(1) == pytest.approx(5.60, abs=0.01)
    assert sharpe_needed_for_years(4) == pytest.approx(2.80, abs=0.01)
