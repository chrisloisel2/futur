import pandas as pd

from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.planes.event_trade import EventTradePlane
from alpha_foundry_v5.provenance import _new_feature_origin, audit_feature_provenance


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
