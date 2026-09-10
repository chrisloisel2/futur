from __future__ import annotations

import json

import pytest

from research_kernel.mechanism_spec import MechanismSpec, SpecError, parse_duration_seconds


def base(**over):
    payload = dict(
        mechanism_id="demo_v1",
        hypothesis=(
            "When the funding rate is extreme, the leveraged side is forced to pay and "
            "unwinds over the following day."
        ),
        economic_reason=(
            "The paying side is levered and margin constrained, so it exits into the "
            "move rather than out of it, and whoever holds the other side is paid for "
            "the balance sheet they lend."
        ),
        data_sources=["binance_funding"],
        universe=["BTCUSDT", "ETHUSDT"],
        timeframe="8h",
        horizon="1d",
        side_mode="long_short",
        entry_rule={"funding_z_min": 2.5},
        exit_rule={"holding_days": 1},
        cost_model={"maker_fee_bps": 2.0, "taker_fee_bps": 5.0, "spread_bps": 1.0},
        validation_window={"start": "2021-01-01", "end": "2024-01-01"},
        placebo_tests=["direction_flip"],
        declustering_rule={"method": "fixed_windows", "window_seconds": 86400},
        multiplicity_family="funding",
        kill_criteria={},
        promotion_criteria={},
    )
    payload.update(over)
    return payload


def test_a_well_formed_spec_loads():
    s = MechanismSpec.from_dict(base())
    assert s.n_trials() == 1


def test_the_hash_covers_the_rule_and_ignores_prose():
    a = MechanismSpec.from_dict(base())
    b = MechanismSpec.from_dict(base(notes="a different comment"))
    assert a.rules_hash() == b.rules_hash()
    c = MechanismSpec.from_dict(base(entry_rule={"funding_z_min": 2.6}))
    assert c.rules_hash() != a.rules_hash()


def test_universe_order_does_not_change_the_hash():
    a = MechanismSpec.from_dict(base(universe=["BTCUSDT", "ETHUSDT"]))
    b = MechanismSpec.from_dict(base(universe=["ETHUSDT", "BTCUSDT"]))
    assert a.rules_hash() == b.rules_hash()


@pytest.mark.parametrize(
    "rule",
    [
        {"funding_z_min": [2.0, 2.5, 3.0]},
        {"funding_z_min": "entre 2 et 4"},
        {"funding_z_min": "2..4"},
        {"nested": {"z": [1, 2]}},
    ],
)
def test_a_range_is_a_family_in_disguise(rule):
    with pytest.raises(SpecError, match="gate 5"):
        MechanismSpec.from_dict(base(entry_rule=rule))


def test_at_most_two_sensitivities():
    MechanismSpec.from_dict(base(sensitivities={"z_2": 2.0, "z_3": 3.0}))
    with pytest.raises(SpecError, match="at most"):
        MechanismSpec.from_dict(base(sensitivities={"a": 1, "b": 2, "c": 3}))


def test_sensitivities_are_charged_as_trials():
    s = MechanismSpec.from_dict(base(sensitivities={"z_2": 2.0, "z_3": 3.0}))
    assert s.n_trials() == 3


@pytest.mark.parametrize(
    "text",
    [
        "quand le RSI est bas ca monte et c est tres fiable sur tous les actifs",
        "the model will find the pattern in the features we give it, reliably",
        "feature X predicts the return over the next day across the whole universe",
    ],
)
def test_gate_one_refuses_a_coincidence(text):
    with pytest.raises(SpecError, match="gate 1"):
        MechanismSpec.from_dict(base(hypothesis=text))


def test_gate_one_refuses_a_hypothesis_too_short_to_falsify():
    with pytest.raises(SpecError, match="too short"):
        MechanismSpec.from_dict(base(hypothesis="funding works"))


def test_an_entry_without_an_exit_is_not_a_rule():
    with pytest.raises(SpecError, match="exit_rule"):
        MechanismSpec.from_dict(base(exit_rule={}))


def test_the_declustering_method_must_be_declared():
    with pytest.raises(SpecError, match="declustering_rule.method"):
        MechanismSpec.from_dict(base(declustering_rule={"window_seconds": 86400}))


def test_the_family_must_exist():
    with pytest.raises(SpecError, match="multiplicity_family"):
        MechanismSpec.from_dict(base(multiplicity_family="intuition"))


def test_the_id_must_carry_a_version():
    with pytest.raises(SpecError, match="mechanism_id"):
        MechanismSpec.from_dict(base(mechanism_id="demo"))


@pytest.mark.parametrize(
    "horizon,budget_ms",
    [("60s", 15_000.0), ("4h", 3_600_000.0), ("1d", 21_600_000.0), ("5s_60s", 1_250.0)],
)
def test_latency_budget_is_the_horizon_over_four(horizon, budget_ms):
    s = MechanismSpec.from_dict(base(horizon=horizon))
    assert s.max_data_latency_ms() == pytest.approx(budget_ms)


def test_an_inverted_horizon_is_refused():
    with pytest.raises(SpecError, match="inverted"):
        MechanismSpec.from_dict(base(horizon="60s_5s"))


def test_durations():
    assert parse_duration_seconds("250ms") == pytest.approx(0.25)
    assert parse_duration_seconds("2w") == pytest.approx(1209600)
    with pytest.raises(SpecError):
        parse_duration_seconds("soon")


def test_unknown_keys_are_refused():
    with pytest.raises(SpecError, match="unknown spec keys"):
        MechanismSpec.from_dict(base(secret_threshold=3))


def test_the_shipped_specs_are_all_valid(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "mechanisms"
    specs = sorted(root.glob("*/spec.json"))
    assert specs, "no mechanism is shipped"
    for p in specs:
        s = MechanismSpec.from_json(p)
        assert s.mechanism_id == p.parent.name
        assert s.rules_hash()
