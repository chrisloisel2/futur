"""
tests/test_alpha20_tournament_broker.py
─────────────────────────────────────────────────────────────────────────────
Broker paper unique : scénarios simultanés (observed/coûts×1.5/×2/latence
hostile/fills partiels/panne venue), frais ASSUMED quand aucun snapshot réel,
kill switch bloque les OUVERTURES mais jamais les SORTIES, jambe nue
dénouée immédiatement (paire dépareillée = paire entière rejetée). Aucun
réseau, aucun ledger.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.contracts import CostSnapshot
from src.alpha20.costs import fee_registry
from src.alpha20.execution.paper_broker import Order, PaperBroker
from src.alpha20.tournament.market_bus import MarketSnapshot

SNAP = MarketSnapshot(market_event_id="e1", cutoff="2026-07-20T00:00:00Z",
                      decision_ts="2026-07-20T00:00:00Z",
                      received_ts="2026-07-20T00:00:00Z",
                      prices={"BTCUSDT": {"close": 64000.0, "exchange_ts": "t"}})


def test_all_six_scenarios_computed_simultaneously():
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP)
    assert set(fills) == {"observed", "cost_x1.5", "cost_x2", "latency_hostile",
                          "partial_fills", "venue_outage"}
    assert fills["cost_x2"].fee_bp == pytest.approx(fills["observed"].fee_bp * 2, rel=0.01)
    assert fills["partial_fills"].filled_notional < fills["observed"].filled_notional
    assert fills["partial_fills"].unfilled_notional > 0
    assert fills["venue_outage"].rejected and fills["venue_outage"].filled_notional == 0.0
    assert fills["latency_hostile"].avg_price != fills["observed"].avg_price


def test_scenarios_never_alter_the_booked_fill():
    """Les scénarios servent la robustesse — jamais le sizing/la décision."""
    a = PaperBroker().execute(Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP)
    b = PaperBroker().execute(Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP)
    assert a["observed"].filled_notional == b["observed"].filled_notional
    assert a["observed"].avg_price == b["observed"].avg_price


def test_fees_assumed_when_no_signed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(fee_registry, "SNAP_DIR", tmp_path)
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP)
    assert fills["observed"].fee_source == "assumed"
    assert fills["observed"].fee_bp > 0            # jamais silencieusement zéro


def test_fees_real_when_signed_snapshot_present(tmp_path, monkeypatch):
    monkeypatch.setattr(fee_registry, "SNAP_DIR", tmp_path)
    fee_registry.save_snapshot(CostSnapshot(
        venue="binance_usdm", instrument="BTCUSDT", maker_bp=1.0, taker_bp=3.0,
        as_of="2026-07-20", source="api_signed"))
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0, urgency="taker"), SNAP)
    assert fills["observed"].fee_source == "api_signed"
    assert fills["observed"].fee_bp == pytest.approx(3.0, abs=0.01)


def test_no_price_in_snapshot_rejects_cleanly():
    empty = MarketSnapshot(market_event_id="e2", cutoff="t", decision_ts="t",
                           received_ts="t")
    fills = PaperBroker().execute(
        Order("r1", "ETHUSDT", "binance_usdm", 1, 1000.0), empty)
    assert fills["observed"].rejected
    assert fills["observed"].reject_reason == "no_price_in_snapshot"


def test_kill_blocks_new_orders_but_never_exits():
    entry = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP, risk_state="kill")
    assert entry["observed"].rejected
    assert entry["observed"].reject_reason == "kill_switch_active"
    exit_ = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", -1, 10000.0, is_exit=True),
        SNAP, risk_state="kill")
    assert not exit_["observed"].rejected


def test_execute_pair_both_filled_no_naked_leg():
    snap2 = MarketSnapshot(market_event_id="e3", cutoff="t", decision_ts="t",
                           received_ts="t",
                           prices={"BTCUSDT": {"close": 64000.0, "exchange_ts": "t"},
                                  "BTCUSDT_260925": {"close": 64500.0, "exchange_ts": "t"}})
    r = PaperBroker().execute_pair(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0, "spot"),
        Order("r1", "BTCUSDT_260925", "binance_usdm", -1, 10000.0, "quarterly"),
        snap2)
    assert r["pair_status"] == "both_filled"
    assert r["naked_age_s"] == 0.0


def test_execute_pair_mismatch_unwinds_immediately():
    """Une jambe sans prix (rejetée) + une jambe valide → la PAIRE ENTIÈRE
    est rejetée, jamais une exposition nue conservée au-delà du cycle."""
    r = PaperBroker().execute_pair(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0, "spot"),
        Order("r1", "NOPRICEUSDT", "binance_usdm", -1, 10000.0, "quarterly"),
        SNAP)
    assert r["pair_status"] == "leg_mismatch_unwound"
    assert r["naked_age_s"] == 0.0


def test_kill_rejects_both_legs_of_a_pair_cleanly():
    """Kill sur une paire d'OUVERTURE (aucune jambe is_exit) : les deux jambes
    sont rejetées ensemble — jamais une seule, jamais de jambe nue."""
    r = PaperBroker().execute_pair(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0, "spot"),
        Order("r1", "BTCUSDT", "binance_usdm", -1, 10000.0, "quarterly"),
        SNAP, risk_state="kill")
    assert r["pair_status"] == "both_rejected"
    assert r["naked_age_s"] == 0.0


def test_venue_outage_scenario_present_for_robustness_even_when_observed_fills():
    """La télémétrie venue_outage existe TOUJOURS en parallèle de l'observed
    rempli — sert l'audit de robustesse, jamais la décision réelle."""
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", "binance_usdm", 1, 10000.0), SNAP)
    assert not fills["observed"].rejected
    assert fills["venue_outage"].rejected
    assert fills["venue_outage"].reject_reason == "venue_outage"
