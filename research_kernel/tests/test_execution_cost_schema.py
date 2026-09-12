"""execution_cost_schema: a cost is worth its weakest provenance, and a declared number settles nothing."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import execution_cost_schema as S  # noqa: E402


def test_provenance_is_ordered_from_measured_to_declared():
    assert S.PROVENANCE[0] == "account_actual" and S.PROVENANCE[-1] == "declared"
    assert S.weakest(["account_actual", "official_published"]) == "official_published"
    assert S.weakest(["account_actual", "declared", "official_published"]) == "declared"
    with pytest.raises(S.SchemaError):
        S.weakest(["invented"])


def test_round_trip_charges_fees_twice_and_scales_with_legs():
    assert S.round_trip(5.0, 8.0, 6.0) == 24.0
    assert S.round_trip(5.0, 8.0, 6.0, n_legs=2) == 48.0
    with pytest.raises(S.SchemaError):
        S.round_trip(5.0, 8.0, 6.0, n_legs=0)


def test_build_cost_records_the_weakest_link():
    measured = S.build_cost("perp", 5.0, "account_actual", 1.0, "account_actual", 2.0, "account_actual")
    assert measured["round_trip_bps"] == 13.0 and measured["wall_3x_bps"] == 39.0 and measured["is_measured"] is True
    mixed = S.build_cost("perp", 5.0, "official_published", 8.0, "declared", 6.0, "declared")
    assert mixed["weakest_provenance"] == "declared" and mixed["is_measured"] is False
    S.validate("cost", mixed)


def test_comparison_cannot_conclude_on_a_declared_chain():
    mixed = S.build_cost("perp", 5.0, "official_published", 8.0, "declared", 6.0, "declared")
    assert S.compare_to_assumption(mixed, 24.0)["status"] == "unknown"          # meme egal, on ne conclut pas
    measured = S.build_cost("perp", 5.0, "account_actual", 8.0, "account_actual", 6.0, "account_actual")
    assert S.compare_to_assumption(measured, 24.0)["status"] == "confirmed"
    assert S.compare_to_assumption(measured, 12.0)["status"] == "contradicted"
    assert S.compare_to_assumption(measured, 12.0)["difference_bps"] == 12.0


def test_realised_slippage_signs_by_side():
    assert S.realised_slippage_bps(101.0, 100.0, "buy") == 100.0               # paye plus cher : glissement positif
    assert S.realised_slippage_bps(99.0, 100.0, "sell") == 100.0               # vendu moins cher : aussi positif
    assert S.realised_slippage_bps(100.0, 100.0, "buy") == 0.0
    with pytest.raises(S.SchemaError):
        S.realised_slippage_bps(0.0, 100.0, "buy")


def test_validate_refuses_missing_fields_and_unknown_provenance():
    with pytest.raises(S.SchemaError):
        S.validate("fee", {"venue": "binance"})
    with pytest.raises(S.SchemaError):
        S.validate("cost", {**S.build_cost("perp", 1, "declared", 1, "declared", 1, "declared"), "weakest_provenance": "guess"})
