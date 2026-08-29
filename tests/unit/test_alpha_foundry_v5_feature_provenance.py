import pandas as pd

from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.planes.derivatives import DerivativesPlane
from alpha_foundry_v5.planes.event_trade import EventTradePlane
from alpha_foundry_v5.provenance import (
    _new_feature_origin,
    audit_feature_provenance,
    build_feature_provenance_manifest,
)


def test_feature_provenance_requires_every_feature_declared():
    frame = pd.DataFrame({
        "asof_ns": [100, 200],
        "symbol": ["BTC", "BTC"],
        "price_fair_value": [100.0, 101.0],
        "event_trade__available_ts_ns": [90, 190],
        "binance__signed_notional_100ms": [1.0, 2.0],
    })
    manifest = {
        "manifest_digest": "abc",
        "features": {
            "price_fair_value": {"origin": "base_state_tape", "governing_clocks": []},
            "binance__signed_notional_100ms": {"origin": "event_trade", "governing_clocks": ["event_trade__available_ts_ns"]},
        },
    }
    result = audit_feature_provenance(frame, manifest)
    assert result.clean is True
    bad = frame.assign(undeclared_alpha=[1.0, 2.0])
    result = audit_feature_provenance(bad, manifest)
    assert result.clean is False
    assert "undeclared_alpha" in result.undeclared_features


def test_event_trade_plane_every_emitted_feature_has_a_provenance_class():
    plane = EventTradePlane(100, ["okx"], ["BTCUSDT"])
    plane.ingest({
        "_source_kind": "book_events",
        "venue": "okx",
        "symbol": "BTCUSDT",
        "receive_ts_ns": 60,
        "source_stream": "books",
        "event_type": "remove",
        "side": "bid",
    })
    plane.ingest({
        "_source_kind": "trades",
        "venue": "okx",
        "symbol": "BTCUSDT",
        "receive_ts_ns": 70,
        "price": 100.0,
        "qty": 1.0,
        "aggressor": "buy",
        "granularity": "individual",
    })
    plane.ingest({
        "_source_kind": "trades",
        "venue": "okx",
        "symbol": "BTCUSDT",
        "receive_ts_ns": 80,
        "price": 101.0,
        "qty": 2.0,
        "aggressor": "sell",
        "granularity": "individual",
    })
    plane.advance(100)
    state = plane.state(100, "BTCUSDT")

    feature_names = [
        name for name in state
        if not name.endswith("_available_ts_ns")
    ]
    unclassified = [
        name for name in feature_names
        if _new_feature_origin(name) is None
    ]
    assert unclassified == []

    for name in feature_names:
        origin = _new_feature_origin(name)
        assert origin is not None
        assert origin[0] == "event_trade"
        assert origin[1] == ("event_trade__available_ts_ns",)

    # Regression for the exact production columns that exposed the gap.
    for name in [
        "okx__trade_size_entropy_last10",
        "okx__trade_size_entropy_last50",
        "okx__trade_size_entropy_last250",
        "okx__large_trade_fraction_last10",
        "okx__large_trade_fraction_last50",
        "okx__large_trade_fraction_last250",
    ]:
        assert _new_feature_origin(name)[0] == "event_trade"


def test_derivatives_plane_every_emitted_feature_has_a_provenance_class():
    plane = DerivativesPlane(["okx"], ["BTCUSDT"], liquidation_windows_ms=[1000, 30000])
    base = {"_source_kind": "derivatives", "venue": "okx", "symbol": "BTCUSDT"}
    plane.ingest(dict(base, receive_ts_ns=10, event_ts_ns=9, kind="open_interest", value=100.0))
    plane.ingest(dict(base, receive_ts_ns=20, event_ts_ns=19, kind="open_interest", value=105.0))
    plane.ingest(dict(base, receive_ts_ns=30, event_ts_ns=29, kind="funding", value=0.0001, next_funding_ts_ns=1_000_000_000))
    plane.ingest(dict(base, receive_ts_ns=40, event_ts_ns=39, kind="mark", value=101.0))
    plane.ingest(dict(base, receive_ts_ns=50, event_ts_ns=49, kind="index", value=100.0))
    plane.ingest(dict(base, receive_ts_ns=60, event_ts_ns=59, kind="premium", value=0.001))
    plane.ingest(dict(base, receive_ts_ns=70, event_ts_ns=69, kind="liquidation", value=1000.0, side="long"))
    plane.advance(100)
    state = plane.state(100, "BTCUSDT")

    feature_names = [name for name in state if not name.endswith("_available_ts_ns")]
    unclassified = [name for name in feature_names if _new_feature_origin(name) is None]
    assert unclassified == []

    for name in feature_names:
        origin = _new_feature_origin(name)
        assert origin is not None
        assert origin[0] == "derivatives"
        assert origin[1] == ("derivatives__available_ts_ns",)

    # Cross-plane liquidation/depth ratios have a stricter dual-clock contract.
    origin = _new_feature_origin("okx__liquidation_to_depth_30000ms")
    assert origin[0] == "cross_plane"
    assert origin[1] == ("book__available_ts_ns", "derivatives__available_ts_ns")


def test_new_feature_origin_recognizes_the_other_derivatives_plane_clock_name():
    # alpha_foundry_v5/data_planes/derivatives.py (the newer plane builder,
    # used by build_alpha_foundry_v5_data_planes.py and standalone historical
    # panels) emits "deriv__available_ts_ns", not this module's
    # "derivatives__available_ts_ns" -- both must be recognized depending on
    # which one is actually present in the tensor being audited, or no tensor
    # from the newer builder could ever pass this audit.
    known = frozenset({"asof_ns", "symbol", "binance__open_interest", "deriv__available_ts_ns"})
    origin = _new_feature_origin("binance__open_interest", known)
    assert origin[0] == "derivatives"
    assert origin[1] == ("deriv__available_ts_ns",)

    # With no hint at all (no known_columns passed), the older name remains
    # the default -- existing callers that don't pass known_columns keep
    # exactly their previous behavior.
    assert _new_feature_origin("binance__open_interest")[1] == ("derivatives__available_ts_ns",)

    # If somehow both are present, both are accepted as governing clocks.
    both = frozenset({"derivatives__available_ts_ns", "deriv__available_ts_ns"})
    origin_both = _new_feature_origin("binance__open_interest", both)
    assert set(origin_both[1]) == {"derivatives__available_ts_ns", "deriv__available_ts_ns"}


def test_provenance_uses_union_of_all_parquet_partition_schemas(tmp_path):
    base = tmp_path / "base"
    tensor = tmp_path / "tensor"
    base.mkdir()
    tensor.mkdir()

    # A base feature appears only in the second base chunk.
    pd.DataFrame({
        "asof_ns": [100],
        "symbol": ["BTCUSDT"],
        "price_fair_value": [100.0],
    }).to_parquet(base / "part-00000.parquet", index=False)
    pd.DataFrame({
        "asof_ns": [200],
        "symbol": ["BTCUSDT"],
        "okx__mid": [100.1],
    }).to_parquet(base / "part-00001.parquet", index=False)

    # A derivative feature appears only in the second tensor chunk, exactly
    # like production liquidation columns that first become material later.
    pd.DataFrame({
        "asof_ns": [100],
        "symbol": ["BTCUSDT"],
        "price_fair_value": [100.0],
        "event_trade__available_ts_ns": [90],
        "okx__signed_notional_100ms": [10.0],
    }).to_parquet(tensor / "part-00000.parquet", index=False)
    pd.DataFrame({
        "asof_ns": [200],
        "symbol": ["BTCUSDT"],
        "okx__mid": [100.1],
        "derivatives__available_ts_ns": [190],
        "okx__liquidation_count_30000ms": [1.0],
    }).to_parquet(tensor / "part-00001.parquet", index=False)

    manifest = build_feature_provenance_manifest(str(tensor), str(base))
    assert manifest["version"] == 2
    assert manifest["tensor_parts_scanned"] == 2
    assert manifest["base_parts_scanned"] == 2
    assert manifest["unclassified_columns"] == []
    assert manifest["features"]["okx__mid"]["origin"] == "base_state_tape"
    assert manifest["features"]["okx__liquidation_count_30000ms"]["origin"] == "derivatives"


def test_a14_namespace_does_not_match_deriv_columns():
    registry = LabRegistry()
    frame = pd.DataFrame({
        "asof_ns": [1, 2],
        "symbol": ["BTC", "BTC"],
        "deriv__median_oi_change_pct": [0.1, 0.2],
        "deriv__basis_dispersion_bps": [1.0, 2.0],
    })
    assert registry.readiness("A14", frame)["ready"] is False
    frame["option__iv_atm"] = [0.5, 0.6]
    frame["option__gamma"] = [10.0, 11.0]
    assert registry.readiness("A14", frame)["ready"] is True


def test_a7_requires_depth_and_a8_requires_price():
    registry = LabRegistry()
    rows = 120
    frame = pd.DataFrame({
        "asof_ns": list(range(1, rows + 1)),
        "symbol": ["BTC"] * rows,
        "binance__liquidation_total_usd_30000ms": [100.0] * rows,
        "binance__open_interest": [1000.0] * rows,
        "binance__open_interest_change_pct": [0.01] * rows,
        "binance__funding": [0.0001] * rows,
    })
    assert registry.readiness("A7", frame)["ready"] is False
    assert registry.readiness("A8", frame)["ready"] is False
    frame["binance__buy_notional_10bps"] = [100000.0] * rows
    frame["price_fair_value"] = [50000.0] * rows
    assert registry.readiness("A7", frame)["ready"] is True
    assert registry.readiness("A8", frame)["ready"] is True
