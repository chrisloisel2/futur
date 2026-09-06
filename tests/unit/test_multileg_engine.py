"""
tests/test_multileg_engine.py
─────────────────────────────────────────────────────────────────────────────
Sanity des 5 interfaces Phase 0 (research/edge_factory/multileg_engine) :
contrats d'Instrument, univers statique, wrapper de coûts (fallback assumed
étiqueté, funding manquant jamais confondu avec un vrai zéro), construction
de jambes, format de résultat compatible gate_sleeve/gate_research. Aucun
réseau, aucune donnée réelle requise.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from research.edge_factory.multileg_engine.instrument import Instrument
from research.edge_factory.multileg_engine.pit_universe import PointInTimeUniverse
from research.edge_factory.multileg_engine.multileg_order import Leg, MultiLegOrder
from research.edge_factory.multileg_engine.backtest_result import MultiLegBacktestResult
from research.edge_factory.multileg_engine import costs as costs_mod


def test_perp_rejects_expiry():
    with pytest.raises(ValueError):
        Instrument(venue="binance", symbol="BTC", instrument_type="perp",
                  expiry=date(2026, 9, 25))


def test_dated_future_requires_expiry():
    with pytest.raises(ValueError):
        Instrument(venue="binance", symbol="BTC", instrument_type="dated_future")


def test_instrument_key_distinguishes_expiry():
    near = Instrument(venue="binance", symbol="BTC", instrument_type="dated_future",
                      expiry=date(2026, 9, 25))
    far = Instrument(venue="binance", symbol="BTC", instrument_type="dated_future",
                     expiry=date(2026, 12, 25))
    assert near.key != far.key


def test_static_universe_ignores_date():
    instruments = [Instrument(venue="binance", symbol="BTC", instrument_type="perp"),
                  Instrument(venue="bybit", symbol="BTC", instrument_type="perp")]
    universe = PointInTimeUniverse.static(instruments)
    assert universe.as_of(date(2021, 1, 1)) == universe.as_of(date(2026, 1, 1)) == instruments


def test_funding_lookup_missing_symbol_is_labelled_assumed(monkeypatch):
    monkeypatch.setattr(costs_mod, "_funding_cache", {})
    monkeypatch.setattr(costs_mod, "FUNDING_DIR", Path("/nonexistent-on-purpose"))
    q = costs_mod.funding_lookup("bybit", "NOSUCHSYMBOL", datetime(2026, 1, 1))
    assert q["source"] == "assumed_missing"
    assert q["rate"] == 0.0


def test_fee_wraps_registry_with_assumed_fallback(monkeypatch, tmp_path):
    from src.alpha20.costs import fee_registry
    monkeypatch.setattr(fee_registry, "SNAP_DIR", tmp_path)
    snap = costs_mod.fee("binance", "BTCUSDT")
    assert snap.source == "assumed"


def test_leg_rejects_zero_or_negative_size():
    instrument = Instrument(venue="binance", symbol="BTC", instrument_type="perp")
    with pytest.raises(ValueError):
        Leg(instrument=instrument, side="long", size=0)


def test_leg_signed_size():
    instrument = Instrument(venue="binance", symbol="BTC", instrument_type="perp")
    long_leg = Leg(instrument=instrument, side="long", size=2.0)
    short_leg = Leg(instrument=instrument, side="short", size=2.0)
    assert long_leg.signed_size == 2.0
    assert short_leg.signed_size == -2.0


def test_multileg_order_holds_legs_and_delta_target():
    instrument = Instrument(venue="binance", symbol="BTC", instrument_type="perp")
    order = MultiLegOrder(legs=[Leg(instrument=instrument, side="long", size=1.0)])
    assert order.delta_target == 0.0
    assert len(order.legs) == 1


def test_backtest_result_feeds_sleeve_gate_without_recomputing_it():
    net_events = pd.Series([0.01, -0.002, 0.015, 0.008])
    result = MultiLegBacktestResult(
        trades=pd.DataFrame(), pnl_daily=pd.Series(dtype=float),
        per_year={"2025": 0.04, "2026": 0.01},
        net_events=net_events, net_events_x2=net_events - 0.005,
        returns_for_dsr=net_events)
    gates = result.run_sleeve_gate()
    assert {g.gate for g in gates} == {
        "pf_min", "costs_x2_positive", "top10_events_removed_positive",
        "no_destructive_recent_year"}
