from __future__ import annotations

import pytest

from alpha_foundry_v5.feature_sets import (
    FeatureSet,
    load_feature_set,
    resolve_feature_columns,
    write_feature_set,
)
from alpha_foundry_v5.labs.registry import LabRegistry


def test_feature_set_rejects_empty_columns():
    with pytest.raises(ValueError, match="at least one column"):
        FeatureSet(feature_set_id="x", lab_id="A2", columns=())


def test_feature_set_rejects_duplicate_columns():
    with pytest.raises(ValueError, match="unique"):
        FeatureSet(feature_set_id="x", lab_id="A2", columns=("a", "a"))


def test_feature_set_write_is_immutable(tmp_path):
    fs = FeatureSet(feature_set_id="x", lab_id="A2", columns=("binance__price_mid",))
    out = tmp_path / "fs.json"
    write_feature_set(fs, str(out))
    with pytest.raises(FileExistsError):
        write_feature_set(fs, str(out))


def test_feature_set_round_trips(tmp_path):
    fs = FeatureSet(feature_set_id="x", lab_id="A2", columns=("binance__price_mid", "okx__price_mid"))
    out = tmp_path / "fs.json"
    write_feature_set(fs, str(out))
    loaded = load_feature_set(str(out))
    assert loaded == fs
    assert loaded.digest == fs.digest


def test_resolve_feature_columns_cross_venue_selects_dislocation_and_mid_only():
    registry = LabRegistry()
    spec = registry.spec("A2")
    all_columns = (
        "asof_ns", "symbol",
        "binance__price_dislocation_bps", "binance__price_mid", "binance__spread_bps",
        "okx__dislocation_bps",
        "price_fair_value",
        "unrelated_column",
    )
    columns = resolve_feature_columns(spec, all_columns)
    assert set(columns) == {
        "binance__price_dislocation_bps",
        "binance__price_mid",
        "okx__dislocation_bps",
        "price_fair_value",
    }
    assert "binance__spread_bps" not in columns  # cross_venue plugin doesn't want raw spread
    assert "unrelated_column" not in columns


def test_resolve_feature_columns_leverage_selects_only_leverage_tokens():
    registry = LabRegistry()
    spec = registry.spec("A8")
    all_columns = (
        "asof_ns", "symbol",
        "binance__open_interest_usd", "binance__funding_rate_bps", "binance__basis_bps",
        "binance__price_dislocation_bps",  # not a leverage token, must be excluded
    )
    columns = resolve_feature_columns(spec, all_columns)
    assert set(columns) == {"binance__open_interest_usd", "binance__funding_rate_bps", "binance__basis_bps"}


def test_resolve_feature_columns_is_order_stable_and_matches_input_order():
    registry = LabRegistry()
    spec = registry.spec("A2")
    all_columns = ("okx__price_mid", "asof_ns", "binance__price_mid", "symbol")
    columns = resolve_feature_columns(spec, all_columns)
    assert columns == ("okx__price_mid", "binance__price_mid")
