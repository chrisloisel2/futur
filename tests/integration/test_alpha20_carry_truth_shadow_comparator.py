"""tests/integration/test_alpha20_carry_truth_shadow_comparator.py --
Phase 4C commit 4: the differential comparator and its JSONL log.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pandas as pd
import pytest

from src.alpha20.tournament.truth_shadow.comparator import (
    CLASSIFICATIONS,
    ComparisonRow,
    DifferentialComparator,
    DifferentialLog,
)
from src.alpha20.tournament.truth_shadow.mapping import LegLedgerToTruthEvents
from src.alpha20.tournament.truth_shadow.product_specs import ProductSpecRegistry
from src.futur.truth.engine import TruthEngine
from src.futur.truth.reconciliation import ToleranceConfig

VENUE = "binance_usdm"
REGISTRY = ProductSpecRegistry.from_json_file()


def _leg_row(**overrides) -> dict:
    base = {"position_id": "CARRY_1", "leg_id": "leg_1", "asset": "BTCUSDT",
           "leg_type": "CARRY_LONG_SPOT", "position_type": "DELTA_NEUTRAL_CARRY",
           "engine": "carry", "entry_time": "2026-01-01T00:00:00Z", "exit_time": None,
           "qty": 1.5, "notional": 75000.0, "entry_price": 50_000.0, "exit_price": None,
           "price_pnl": 0.0, "funding_pnl": 0.0, "costs": 37.5, "net_pnl": -37.5}
    base.update(overrides)
    return base


def _perp_leg_row(**overrides) -> dict:
    base = _leg_row(leg_id="leg_2", leg_type="CARRY_SHORT_PERP")
    base.update(overrides)
    return base


def _portfolio_row(ts: str, cash: float, equity: float, **overrides) -> dict:
    base = {"timestamp": pd.Timestamp(ts, tz="UTC"), "cash": cash, "equity": equity,
           "fees_total": -75.0, "borrow_total": 0.0, "funding_pnl_total": 0.0,
           "gross_exposure": 0.0, "net_exposure": 0.0}
    base.update(overrides)
    return base


def _setup_engine_and_ledgers(cash_start=200_000.0):
    """Runs the SAME mapper Commit 2/3 already use, on a spot+perp carry
    pair, and builds a matching legacy-style portfolio_ledger by hand from
    the SAME numbers -- this is the happy path both sides should agree on
    exactly."""
    spot_row = _leg_row()
    perp_row = _perp_leg_row()
    leg_ledger = pd.DataFrame([spot_row, perp_row])

    engine = TruthEngine()
    from src.futur.truth.events import CashDepositPayload, Event, EventType
    engine.apply(Event(event_id="seed", event_type=EventType.CASH_DEPOSIT,
                       ts_event="2026-01-01T00:00:00Z", ts_received="2026-01-01T00:00:00Z",
                       payload=CashDepositPayload(cash_start, "USD")))

    mapper = LegLedgerToTruthEvents(venue=VENUE, registry=REGISTRY)
    market_prices = {"BTCUSDT": pd.Series([50_000.0], index=pd.to_datetime(
        ["2026-01-01T00:00:00Z"], utc=True))}
    events = mapper.events_for_cycle(leg_ledger, cycle_ts="2026-01-01T00:00:00Z",
                                     market_prices=market_prices)
    applied = [engine.apply(e) for e in events]

    # legacy numbers matching exactly: cash_start - notional(spot) - fees(both legs)
    expected_cash = cash_start - spot_row["notional"] - spot_row["costs"] - perp_row["costs"]
    portfolio_ledger = pd.DataFrame([
        _portfolio_row("2026-01-01T00:00:00Z", cash=expected_cash, equity=expected_cash,
                      fees_total=-(spot_row["costs"] + perp_row["costs"])),
    ])
    return engine, leg_ledger, portfolio_ledger, applied


# ── happy path: everything agrees ───────────────────────────────────────

def test_matching_legacy_and_truth_numbers_classify_as_match(tmp_path):
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()

    cash_rows = [r for r in rows if r.field == "cash"]
    assert cash_rows and all(r.classification == "MATCH" for r in cash_rows)
    qty_rows = [r for r in rows if r.field in ("spot_qty", "perp_qty")]
    assert qty_rows and all(r.classification == "MATCH" for r in qty_rows)
    assert all(r.classification in CLASSIFICATIONS for r in rows)


def test_margin_used_is_always_expected_legacy_divergence(tmp_path):
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()
    margin_rows = [r for r in rows if r.field == "margin_used"]
    assert margin_rows
    assert all(r.classification == "EXPECTED_LEGACY_DIVERGENCE" for r in margin_rows)
    assert all("no comparable figure" in r.cause for r in margin_rows)


# ── genuine economic divergence ─────────────────────────────────────────

def test_cash_mismatch_beyond_tolerance_is_unexplained_divergence(tmp_path):
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    # sabotage the legacy portfolio_ledger's cash figure
    portfolio_ledger = portfolio_ledger.copy()
    portfolio_ledger["cash"] = portfolio_ledger["cash"] - 500.0
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()
    cash_rows = [r for r in rows if r.field == "cash"]
    assert cash_rows and all(r.classification == "UNEXPLAINED_DIVERGENCE" for r in cash_rows)
    assert all(Decimal(r.difference) == Decimal(500) for r in cash_rows)


def test_cash_mismatch_within_explicit_tolerance_is_match(tmp_path):
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    portfolio_ledger = portfolio_ledger.copy()
    portfolio_ledger["cash"] = portfolio_ledger["cash"] - 0.5
    lenient = ToleranceConfig(per_venue_field={(VENUE, "cash"): Decimal("1.0")})
    comparator = DifferentialComparator(run_id="run1", venue=VENUE, tolerance_config=lenient)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()
    cash_rows = [r for r in rows if r.field == "cash"]
    assert cash_rows and all(r.classification == "MATCH" for r in cash_rows)


def test_no_global_tolerance_default_is_tight(tmp_path):
    """Without an explicit per-(venue, field) override, the default
    tolerance is the same tight default reconciliation.py already uses
    (1e-8) -- proving there's no hidden global fudge factor here either."""
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    portfolio_ledger = portfolio_ledger.copy()
    portfolio_ledger["cash"] = portfolio_ledger["cash"] - 0.01
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)   # default ToleranceConfig
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()
    cash_rows = [r for r in rows if r.field == "cash"]
    assert cash_rows and all(r.classification == "UNEXPLAINED_DIVERGENCE" for r in cash_rows)


# ── mapping self-consistency (shadow-side bug detection) ───────────────

def test_desynced_leg_ledger_and_truth_state_flags_shadow_mapping_error(tmp_path):
    """If the leg_ledger handed to the comparator disagrees with what was
    ACTUALLY fed into TruthEngine (a stale/desynced snapshot -- exactly
    the kind of bug this shadow package itself could introduce), the
    per-instrument check must catch it and blame the SHADOW, not the
    legacy engine."""
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    tampered_ledger = leg_ledger.copy()
    tampered_ledger.loc[tampered_ledger["leg_id"] == "leg_1", "qty"] = 99.0
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, tampered_ledger, portfolio_ledger, log)
    log.close()
    spot_qty_rows = [r for r in rows if r.field == "spot_qty"]
    assert spot_qty_rows and all(r.classification == "SHADOW_MAPPING_ERROR" for r in spot_qty_rows)


# ── log file ─────────────────────────────────────────────────────────────

def test_log_is_valid_jsonl_with_all_required_fields(tmp_path):
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    comparator = DifferentialComparator(run_id="run-xyz", venue=VENUE)
    log_path = tmp_path / "diff.jsonl"
    log = DifferentialLog(log_path)
    comparator.compare_cycle(engine, applied, leg_ledger, portfolio_ledger, log)
    log.close()

    lines = log_path.read_text().splitlines()
    assert len(lines) > 0
    required = {"run_id", "sequence", "event_id", "timestamp", "field", "legacy_value",
               "truth_value", "difference", "tolerance_applied", "classification", "cause"}
    for line in lines:
        record = json.loads(line)
        assert required <= record.keys()
        assert record["run_id"] == "run-xyz"
        assert record["classification"] in CLASSIFICATIONS


def test_log_is_append_only_across_multiple_writes(tmp_path):
    path = tmp_path / "diff.jsonl"
    log = DifferentialLog(path)
    row = ComparisonRow(run_id="r", sequence=0, event_id="e0", timestamp="t0", field="cash",
                        legacy_value="1", truth_value="1", difference="0",
                        tolerance_applied="0.00000001", classification="MATCH", cause="")
    log.write(row)
    log.close()
    log2 = DifferentialLog(path)   # reopen -- must append, not truncate
    log2.write(row)
    log2.close()
    assert len(path.read_text().splitlines()) == 2


def test_comparison_row_rejects_unknown_classification():
    with pytest.raises(ValueError, match="classification"):
        ComparisonRow(run_id="r", sequence=0, event_id="e0", timestamp="t0", field="cash",
                     legacy_value="1", truth_value="1", difference="0",
                     tolerance_applied="0", classification="SOMETHING_ELSE", cause="")


# ── as-of-event vs as-of-terminal (Phase 4D commit 9 fix) ───────────────

def _setup_two_sequential_carry_pairs():
    """Two NON-overlapping CARRY_LONG_SPOT/CARRY_SHORT_PERP pairs on the
    SAME asset (BTCUSDT): leg_1/leg_2 open 01-01, close 01-05; leg_3/leg_4
    open 01-10, still open at cycle_ts 01-15. A single real decide() call
    maps and applies EVERY event across the whole window in one batch --
    exactly the real replay's shape -- so by the time all events are
    applied, the FINAL BTCUSDT spot position reflects ONLY leg_3 (qty=2.0),
    while leg_1's own entry event (01-01) should be compared against what
    was open AT THAT TIME (qty=1.5, leg_1 alone). This is the exact
    mismatch class that produced 1238 UNEXPLAINED_DIVERGENCE rows on the
    first real 60-day replay before this fix."""
    leg1_spot = _leg_row(leg_id="leg_1", position_id="CARRY_1",
                        entry_time="2026-01-01T00:00:00Z", exit_time="2026-01-05T00:00:00Z",
                        qty=1.5, entry_price=50_000.0, exit_price=51_000.0,
                        notional=75_000.0, costs=37.5)
    leg1_perp = _perp_leg_row(leg_id="leg_2", position_id="CARRY_1",
                             entry_time="2026-01-01T00:00:00Z", exit_time="2026-01-05T00:00:00Z",
                             qty=1.5, entry_price=50_000.0, exit_price=51_000.0,
                             notional=75_000.0, costs=37.5)
    leg2_spot = _leg_row(leg_id="leg_3", position_id="CARRY_2",
                        entry_time="2026-01-10T00:00:00Z", exit_time=None,
                        qty=2.0, entry_price=52_000.0, notional=104_000.0, costs=52.0)
    leg2_perp = _perp_leg_row(leg_id="leg_4", position_id="CARRY_2",
                             entry_time="2026-01-10T00:00:00Z", exit_time=None,
                             qty=2.0, entry_price=52_000.0, notional=104_000.0, costs=52.0)
    leg_ledger = pd.DataFrame([leg1_spot, leg1_perp, leg2_spot, leg2_perp])

    engine = TruthEngine()
    from src.futur.truth.events import CashDepositPayload, Event, EventType
    engine.apply(Event(event_id="seed", event_type=EventType.CASH_DEPOSIT,
                       ts_event="2026-01-01T00:00:00Z", ts_received="2026-01-01T00:00:00Z",
                       payload=CashDepositPayload(500_000.0, "USD")))

    mapper = LegLedgerToTruthEvents(venue=VENUE, registry=REGISTRY)
    idx = pd.to_datetime([f"2026-01-{d:02d}T00:00:00Z" for d in range(1, 16)], utc=True)
    prices = pd.Series([50_000.0 + 100 * i for i in range(15)], index=idx)
    events = mapper.events_for_cycle(leg_ledger, cycle_ts="2026-01-15T00:00:00Z",
                                     market_prices={"BTCUSDT": prices})

    applied = []
    account_snapshots = []
    for e in events:
        applied.append(engine.apply(e))
        account_snapshots.append(engine.account.snapshot())
    return engine, leg_ledger, applied, account_snapshots


def test_asof_account_snapshot_makes_early_event_instrument_comparison_match(tmp_path):
    engine, leg_ledger, applied, account_snapshots = _setup_two_sequential_carry_pairs()
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, pd.DataFrame(), log,
                                    account_snapshots=account_snapshots)
    log.close()

    leg1_entry_qty_rows = [r for r in rows
                           if r.event_id == "leg_1-fill-entry" and r.field == "spot_qty"]
    assert leg1_entry_qty_rows
    assert all(r.classification == "MATCH" for r in leg1_entry_qty_rows), leg1_entry_qty_rows
    assert all(Decimal(r.legacy_value) == Decimal("1.5") for r in leg1_entry_qty_rows)
    assert all(Decimal(r.truth_value) == Decimal("1.5") for r in leg1_entry_qty_rows)


def test_omitting_account_snapshots_reproduces_the_terminal_state_mismatch(tmp_path):
    """Guards the wiring itself: if a caller forgets to pass
    account_snapshots (e.g. a future edit to the replay driver), the
    comparator falls back to the live/final engine.account for every
    event -- which is EXACTLY the bug commit 9 fixed. leg_1's own entry
    event (open BTCUSDT qty=1.5 as of 01-01) gets compared against the
    FINAL account state (BTCUSDT qty=2.0, only leg_3 still open by
    01-15), a real 0.5 mismatch -- SHADOW_MAPPING_ERROR, not MATCH."""
    engine, leg_ledger, applied, _ = _setup_two_sequential_carry_pairs()
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, pd.DataFrame(), log)
    log.close()

    leg1_entry_qty_rows = [r for r in rows
                           if r.event_id == "leg_1-fill-entry" and r.field == "spot_qty"]
    assert leg1_entry_qty_rows
    assert all(r.classification == "SHADOW_MAPPING_ERROR" for r in leg1_entry_qty_rows)
    assert all(Decimal(r.difference) == Decimal("0.5") for r in leg1_entry_qty_rows)


def test_portfolio_fields_are_compared_exactly_once_at_terminal_event(tmp_path):
    """Portfolio-level fields (cash/nav/fees/...) must appear exactly once
    per run -- against the TERMINAL event -- never once per state-changing
    event, now that a single decide() call can span many real events."""
    engine, leg_ledger, applied, account_snapshots = _setup_two_sequential_carry_pairs()
    state_changing = [e for e in applied if e.event_type.value in
                      ("FILL", "MARK", "FUNDING", "FEE", "BORROW_COST")]
    assert len(state_changing) > 1   # sanity: this scenario really is multi-event
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    log = DifferentialLog(tmp_path / "diff.jsonl")
    rows = comparator.compare_cycle(engine, applied, leg_ledger, pd.DataFrame(), log,
                                    account_snapshots=account_snapshots)
    log.close()

    cash_rows = [r for r in rows if r.field == "cash"]
    assert len(cash_rows) == 1
    last_event = state_changing[-1]
    assert cash_rows[0].event_id == last_event.event_id


# ── only state-changing events are compared ─────────────────────────────

class _NullLog:
    def write(self, row):
        pass


def test_order_lifecycle_events_are_not_compared():
    engine, leg_ledger, portfolio_ledger, applied = _setup_engine_and_ledgers()
    order_events = [e for e in applied if e.event_type.value in
                    ("ORDER_SUBMITTED", "ORDER_ACKNOWLEDGED")]
    assert order_events   # sanity: the fixture really does include some
    comparator = DifferentialComparator(run_id="run1", venue=VENUE)
    rows = comparator.compare_cycle(engine, order_events, leg_ledger, portfolio_ledger, _NullLog())
    assert rows == []
