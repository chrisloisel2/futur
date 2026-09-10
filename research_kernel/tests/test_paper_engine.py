from __future__ import annotations

import json

import pytest

from paper_engine.decision_engine import (
    DecisionEngine,
    EligibleMechanism,
    RiskLimits,
    RiskState,
    Signal,
    load_eligible,
)
from paper_engine.fill_simulator import Book, simulate_taker_fill
from paper_engine.journal import Journal
from paper_engine.paper_broker import PaperBroker
from paper_engine.run_paper_live import daily_report, run_cycle
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.verdict import Verdict, VerdictStatus

SPEC = {
    "mechanism_id": "demo_v1",
    "hypothesis": (
        "A demo mechanism used only to exercise the paper engine, with no claim about "
        "any market whatsoever."
    ),
    "economic_reason": (
        "None: this exists to test that the engine refuses to trade what has not been "
        "sealed, and it is labelled so nobody mistakes it for a claim."
    ),
    "data_sources": ["synthetic"],
    "universe": ["BTCUSDT"],
    "timeframe": "1s",
    "horizon": "60s",
    "side_mode": "long_short",
    "entry_rule": {"z": 3.0},
    "exit_rule": {"holding_seconds": 60},
    "cost_model": {"taker_fee_bps": 5.0, "spread_bps": 1.0, "maker_ratio": 0.0},
    "validation_window": {"start": "2026-01-01", "end": "2026-02-01"},
    "placebo_tests": ["direction_flip"],
    "declustering_rule": {"method": "complete_link", "window_seconds": 300},
    "multiplicity_family": "microstructure",
    "kill_criteria": {},
    "promotion_criteria": {},
}


def _eligible():
    spec = MechanismSpec.from_dict(SPEC)
    verdict = Verdict(
        mechanism_id="demo_v1",
        status=VerdictStatus.PAPER_ELIGIBLE,
        net_edge_bps_cost_x2=5.0,
        n_independent=400,
        profit_factor=1.4,
        forward_seal_id="demo_v1_2026-09-11",
        forward_result_id="demo_v1_2026-09-11_result",
        rules_hash=spec.rules_hash(),
    )
    return EligibleMechanism(spec=spec, verdict=verdict, cost=spec.cost())


def signal(**over):
    payload = dict(
        mechanism_id="demo_v1",
        exchange="binance",
        symbol="BTCUSDT",
        side="long",
        gross_edge_bps_est=40.0,
        data_latency_ms=120.0,
        notional=1000.0,
        timestamp="2026-09-10T12:00:00Z",
    )
    payload.update(over)
    return Signal(**payload)


# ------------------------------------------------------------------ eligibility
def test_nothing_is_eligible_without_a_promoted_verdict(tmp_path):
    d = tmp_path / "mechanisms" / "demo_v1" / "results"
    d.mkdir(parents=True)
    (d.parent / "spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (d / "verdict.json").write_text(
        json.dumps({"mechanism_id": "demo_v1", "status": "PROMISING_NEEDS_FORWARD"}),
        encoding="utf-8",
    )
    assert load_eligible(tmp_path / "mechanisms") == []


def test_a_verdict_whose_rule_moved_is_dropped(tmp_path):
    d = tmp_path / "mechanisms" / "demo_v1" / "results"
    d.mkdir(parents=True)
    (d.parent / "spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (d / "verdict.json").write_text(
        json.dumps(
            {
                "mechanism_id": "demo_v1",
                "status": "PAPER_ELIGIBLE",
                "net_edge_bps_cost_x2": 5.0,
                "n_independent": 400,
                "profit_factor": 1.4,
                "forward_seal_id": "s",
                "forward_result_id": "r",
                "rules_hash": "a_hash_from_a_different_rule",
            }
        ),
        encoding="utf-8",
    )
    assert load_eligible(tmp_path / "mechanisms") == []


def test_an_unknown_mechanism_is_refused():
    engine = DecisionEngine(eligible=[])
    out = engine.decide(signal(), RiskState())
    assert out["decision"] == "REJECT"
    assert out["reject_reason"] == "mechanism is not PAPER_ELIGIBLE"


# ----------------------------------------------------------------- the decision
def test_an_eligible_signal_is_accepted():
    engine = DecisionEngine(eligible=[_eligible()])
    out = engine.decide(signal(), RiskState())
    assert out["decision"] == "PAPER_ACCEPT"
    assert out["net_edge_bps_est"] == pytest.approx(40.0 - 11.0)
    assert out["position_notional_paper"] == 1000.0


def test_a_signal_that_does_not_clear_double_cost_is_refused():
    engine = DecisionEngine(eligible=[_eligible()])
    out = engine.decide(signal(gross_edge_bps_est=20.0), RiskState())
    assert out["decision"] == "REJECT"
    assert out["reject_reason"] == "net_edge_bps_est <= 0 after cost_x2"


def test_a_stale_signal_is_refused():
    engine = DecisionEngine(eligible=[_eligible()])
    out = engine.decide(signal(data_latency_ms=20_000.0), RiskState())
    assert "data latency" in out["reject_reason"]


@pytest.mark.parametrize(
    "risk,expected",
    [
        (RiskState(daily_pnl=-600.0), "daily loss limit reached"),
        (RiskState(drawdown=2500.0), "drawdown limit reached"),
        (RiskState(open_exposure=19_500.0), "gross exposure limit reached"),
        (RiskState(consecutive_losses=2), "two consecutive losses"),
    ],
)
def test_risk_limits_stop_a_good_signal(risk, expected):
    engine = DecisionEngine(eligible=[_eligible()], limits=RiskLimits())
    out = engine.decide(signal(), risk)
    assert out["reject_reason"] == expected


# ------------------------------------------------------------------- the fills
def test_an_order_larger_than_the_book_is_deferred_not_invented():
    book = Book(bid=100.0, ask=100.1, bid_qty=1.0, ask_qty=1.0)
    fill = simulate_taker_fill("BTCUSDT", "long", notional=10_000.0, book=book)
    assert fill.filled_notional == pytest.approx(100.1)
    assert fill.deferred_notional == pytest.approx(10_000.0 - 100.1)
    assert fill.capped_by_depth


def test_a_small_order_is_filled_whole_and_pays_half_the_spread():
    book = Book(bid=100.0, ask=100.1, bid_qty=100.0, ask_qty=100.0)
    fill = simulate_taker_fill("BTCUSDT", "long", notional=50.0, book=book)
    assert fill.filled_notional == 50.0
    assert fill.deferred_notional == 0.0
    assert fill.slippage_bps == pytest.approx(fill.spread_bps / 2, rel=1e-6)


def test_an_empty_book_fills_nothing():
    fill = simulate_taker_fill("BTCUSDT", "long", 100.0, Book(0.0, 0.0, 0.0, 0.0))
    assert fill.filled_notional == 0.0
    assert fill.reason == "no valid book"


# ------------------------------------------------------------------ the broker
def test_the_broker_charges_fees_and_tracks_deferred_notional():
    broker = PaperBroker(starting_cash=10_000.0, fee_bps_per_side=5.0)
    book = Book(bid=100.0, ask=100.1, bid_qty=1.0, ask_qty=1.0)
    broker.apply(simulate_taker_fill("BTCUSDT", "long", 1_000.0, book))
    assert broker.fees_paid > 0
    assert broker.deferred_notional > 0
    assert broker.cash < 10_000.0


def test_marking_to_market_names_what_it_could_not_mark():
    broker = PaperBroker()
    book = Book(bid=100.0, ask=100.1, bid_qty=100.0, ask_qty=100.0)
    broker.apply(simulate_taker_fill("BTCUSDT", "long", 1_000.0, book))
    mtm = broker.mark_to_market({})
    assert mtm["unmarked_symbols"] == ["BTCUSDT"]
    marked = broker.mark_to_market({"BTCUSDT": 110.0})
    assert marked["unrealised_pnl"] > 0
    assert marked["mark_to_market"] is True


# ----------------------------------------------------------------- the journal
def test_refusals_are_journalled_too(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    engine = DecisionEngine(eligible=[_eligible()])
    journal.record(engine.decide(signal(gross_edge_bps_est=1.0), RiskState()))
    journal.record(engine.decide(signal(), RiskState()))
    entries = journal.read()
    assert len(entries) == 2
    assert {e["decision"] for e in entries} == {"REJECT", "PAPER_ACCEPT"}
    assert journal.verify()


def test_an_incomplete_journal_entry_is_refused(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    with pytest.raises(ValueError, match="missing"):
        journal.record({"mechanism_id": "demo_v1"})


# -------------------------------------------------------------------- the cycle
def test_a_cycle_with_nothing_eligible_trades_nothing(tmp_path):
    out = run_cycle(
        mechanisms_root=tmp_path / "mechanisms",
        journal_path=tmp_path / "j.jsonl",
        signals=[signal()],
    )
    assert out["eligible_mechanisms"] == []
    assert out["n_accepted"] == 0


def test_the_daily_report_says_so_when_nothing_traded(tmp_path):
    text = daily_report(Journal(tmp_path / "j.jsonl"), "2026-09-10")
    assert "No signal reached the engine today" in text
    assert "PAPER_ELIGIBLE" in text
