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


def test_frozen_feature_set_digest_is_unaffected_by_unrelated_columns_added_later(tmp_path):
    # A FeatureSet is frozen once, from a real dataset's column list at that moment,
    # and is immutable (write_feature_set refuses to overwrite). Its digest therefore
    # cannot change no matter what a LATER dataset looks like -- resolve_feature_columns
    # resolving the same base columns plus noise must still land on the identical
    # column tuple (and hence the identical digest, were it refrozen), not a
    # noise-dependent one.
    registry = LabRegistry()
    spec = registry.spec("A2")
    base_columns = ("asof_ns", "symbol", "binance__price_dislocation_bps", "binance__price_mid", "price_fair_value")
    fs = FeatureSet(feature_set_id="a2-v1", lab_id="A2", columns=resolve_feature_columns(spec, base_columns))
    out = tmp_path / "fs.json"
    write_feature_set(fs, str(out))
    frozen = load_feature_set(str(out))

    noisy_columns = base_columns + tuple(f"junk_column_{i}" for i in range(100))
    resolved_again = resolve_feature_columns(spec, noisy_columns)
    assert resolved_again == frozen.columns
    assert FeatureSet(feature_set_id="a2-v1", lab_id="A2", columns=resolved_again).digest == frozen.digest


def test_A16_markout_target_not_feature():
    # exec__post_fill_markout_bps is A16's TARGET (targets.py's post_fill_markout) --
    # it must never be selectable as a feature even though it starts with "exec__"
    # and (before this was fixed) matched ExecutionPlugin's own runtime "markout" token.
    registry = LabRegistry()
    spec = registry.spec("A16")
    all_columns = (
        "asof_ns", "symbol",
        "exec__post_fill_markout_bps",  # the target -- must be excluded
        "exec__queue_ahead_usd", "exec__fill_probability",  # legitimate features
        "binance__unrelated_spread_bps",
    )
    columns = resolve_feature_columns(spec, all_columns)
    assert "exec__post_fill_markout_bps" not in columns
    assert "exec__queue_ahead_usd" in columns
    assert "exec__fill_probability" in columns


# One representative column per plugin family's own token/prefix rule (plugins.py),
# so every one of the 16 labs gets something real to select from -- not just A2/A8.
_ALL_LAB_REPRESENTATIVE_COLUMNS: tuple[str, ...] = (
    "asof_ns", "symbol", "price_fair_value",
    "binance__price_dislocation_bps", "binance__price_mid",  # cross_venue (A1, A2)
    "binance__ofi_l1_grid", "binance__queue_imbalance_l5", "binance__cancel_count",  # event_microstructure (A3, A4, A5)
    "binance__spread_bps", "binance__depth_5bps",  # shock_propagation (A6)
    "binance__liquidation_total_usd_1000ms", "binance__open_interest",  # leverage (A7, A8)
    "binance__funding", "binance__perp_spot_basis_bps", "binance__time_to_funding_ms",  # funding_basis (A9, A10)
    "wallet__score_weighted_flow_bps", "wallet__scored_flow_coverage",  # wallet (A11)
    "cross_asset__leader_innovation_bps", "btcusdt__residual", "ethusdt__beta_720h",  # cross_asset (A12, A13)
    "option__iv_atm", "option__skew_25d",  # options (A14)
    "onchain__exchange_netflow_usd",  # onchain (A15)
    "exec__queue_ahead_usd", "exec__fill_probability", "exec__post_fill_markout_bps",  # execution (A16)
)


def test_feature_set_all_labs_A1_A16():
    registry = LabRegistry()
    assert set(registry.specs) == {f"A{i}" for i in range(1, 17)}
    for lab_id in registry.specs:
        columns = resolve_feature_columns(registry.spec(lab_id), _ALL_LAB_REPRESENTATIVE_COLUMNS)
        assert columns, f"{lab_id} ({registry.spec(lab_id).plugin}) resolved an empty feature set"
        assert "exec__post_fill_markout_bps" not in columns, f"{lab_id} leaked A16's target column"
