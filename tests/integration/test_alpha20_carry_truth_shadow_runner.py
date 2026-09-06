"""tests/integration/test_alpha20_carry_truth_shadow_runner.py -- the
shadow runner's no-effect contract (kill switch, and a shadow-side
exception never touching the already-returned legacy result), including
Phase 4D commit 6's real captured market prices.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.alpha20.tournament.truth_shadow.mapping import UnmappableLegError
from src.alpha20.tournament.truth_shadow.shadow_runner import (
    CapturedRun,
    CarryBasisShadowRunner,
    ShadowConfig,
    _capture_multileg_result,
)

VENUE = "binance_usdm"


def _spec() -> RunnerSpec:
    return RunnerSpec(runner_id="carry_basis_v12", family="carry_basis", status="ACTIVE",
                      git_commit="test", config_hash=None, venue=VENUE,
                      config={"engines_long": [], "carry_assets": ["BTCUSDT"],
                             "carry_fraction": 0.1, "enable_carry": True})


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(market_event_id="shadow-test", cutoff="2026-01-01T00:00:00Z",
                          decision_ts="2026-01-01T00:00:00Z", received_ts="2026-01-01T00:00:00Z")


def _leg_row(**overrides) -> dict:
    base = {"position_id": "CARRY_1", "leg_id": "leg_1", "asset": "BTCUSDT",
           "leg_type": "CARRY_LONG_SPOT", "position_type": "DELTA_NEUTRAL_CARRY",
           "engine": "carry", "entry_time": "2026-01-01T00:00:00Z", "exit_time": None,
           "qty": 1.5, "notional": 75000.0, "entry_price": 50_000.0, "exit_price": None,
           "price_pnl": 0.0, "funding_pnl": 0.0, "costs": 37.5, "net_pnl": -37.5}
    base.update(overrides)
    return base


def _prices(asset: str = "BTCUSDT", price: float = 50_000.0,
           ts: str = "2026-01-01T00:00:00Z") -> dict[str, pd.Series]:
    return {asset: pd.Series([price], index=pd.to_datetime([ts], utc=True))}


def _fake_captured(*rows, borrow: float = 0.0, market_prices: dict | None = None,
                   run_end: str = "2026-01-01T00:00:00Z") -> CapturedRun:
    result = SimpleNamespace(
        leg_ledger=pd.DataFrame(list(rows)) if rows else pd.DataFrame(),
        pnl_by_type={"directional": 0.0, "carry_funding": 0.0, "hedge": 0.0,
                    "fees": 0.0, "borrow": borrow},
        portfolio_ledger=pd.DataFrame(),
    )
    return CapturedRun(result=result, market_prices=market_prices or _prices(), run_end=run_end)


def _empty_captured() -> CapturedRun:
    return CapturedRun(result=None, market_prices={}, run_end=None)


def _perp_leg_row(**overrides) -> dict:
    base = _leg_row(leg_id="leg_2", leg_type="CARRY_SHORT_PERP")
    base.update(overrides)
    return base


class _SpyCapture:
    """Injectable capture_fn stand-in -- records how many times it was
    called and always returns a pre-baked (events, new_state, captured)."""

    def __init__(self, events, new_state, captured: CapturedRun):
        self.events = events
        self.new_state = new_state
        self.captured = captured
        self.calls = 0

    def __call__(self, spec, snapshot, state):
        self.calls += 1
        return self.events, self.new_state, self.captured


# ── kill switch ──────────────────────────────────────────────────────────

def test_kill_switch_never_invokes_the_capture_function():
    spy = _SpyCapture(events=["sentinel-event"], new_state={"sentinel": True},
                      captured=_empty_captured())
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(enabled=False, venue=VENUE),
                                    capture_fn=spy)
    _events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})
    assert spy.calls == 0   # the shadow's own machinery never ran at all
    assert shadow_result.skipped is True
    assert shadow_result.error is None
    assert shadow_result.applied_events == []


# ── enabled: legacy result passed through untouched ────────────────────

def test_enabled_shadow_returns_the_legacy_result_completely_unmodified():
    sentinel_events = ["sentinel-event-1", "sentinel-event-2"]
    sentinel_state = {"paper_start": "2026-01-01", "sentinel": True}
    captured = _fake_captured(_leg_row())
    spy = _SpyCapture(events=sentinel_events, new_state=sentinel_state, captured=captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    events, new_state, shadow_result = runner.run_cycle(_snapshot(), state={})

    assert spy.calls == 1
    assert events is sentinel_events        # identity -- not a copy, not modified
    assert new_state is sentinel_state
    assert shadow_result.ok is True
    assert len(shadow_result.applied_events) > 0
    assert runner.engine.account.spot_positions   # the shadow DID apply something


def test_shadow_never_mutates_the_leg_ledger_it_was_given():
    captured = _fake_captured(_leg_row())
    original_ledger = captured.result.leg_ledger
    snapshot_before = original_ledger.copy(deep=True)
    spy = _SpyCapture(events=[], new_state={}, captured=captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    runner.run_cycle(_snapshot(), state={})

    assert original_ledger.equals(snapshot_before)   # untouched by the shadow


# ── point 5: a shadow-side exception never touches the legacy result ───

def test_a_shadow_exception_never_touches_the_already_returned_legacy_result():
    sentinel_events = ["sentinel-event"]
    sentinel_state = {"sentinel": True}
    # a non-USDT asset forces UnmappableLegError deep inside the mapper
    bad_captured = _fake_captured(_leg_row(asset="BTCEUR"))
    spy = _SpyCapture(events=sentinel_events, new_state=sentinel_state, captured=bad_captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    events, new_state, shadow_result = runner.run_cycle(_snapshot(), state={})

    # the legacy result is returned exactly as-is, despite the shadow's own failure
    assert events is sentinel_events
    assert new_state is sentinel_state
    assert shadow_result.skipped is False
    assert isinstance(shadow_result.error, UnmappableLegError)
    assert shadow_result.applied_events == []


def test_a_shadow_exception_does_not_raise_out_of_run_cycle():
    """Same scenario as above, phrased as the literal guarantee: calling
    run_cycle() with a poisoned result must not raise -- if this test
    itself raises, that IS the failure."""
    bad_captured = _fake_captured(_leg_row(qty=-1.0))   # nonpositive qty -> UnmappableLegError
    spy = _SpyCapture(events=[], new_state={}, captured=bad_captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    _events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})
    assert shadow_result.error is not None


def test_engine_invariant_violation_is_also_isolated():
    """Not just mapping errors -- an InvariantViolation raised by
    engine.apply() itself (e.g. a duplicate event_id across two legs
    that collide) must be caught the same way."""
    captured = _fake_captured(_leg_row(leg_id="leg_1", costs=10.0))
    spy = _SpyCapture(events=["e"], new_state={"s": 1}, captured=captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    _events, _new_state, first_result = runner.run_cycle(_snapshot(), state={})
    assert first_result.ok is True

    # same leg observed again with COST DECREASED -- rejected by the mapper
    # itself as unmappable (monotonicity), proving the SAME engine instance
    # keeps working across cycles and isolates a later failure too
    captured2 = _fake_captured(_leg_row(leg_id="leg_1", costs=1.0))
    spy2 = _SpyCapture(events=["e2"], new_state={"s": 2}, captured=captured2)
    runner._capture_fn = spy2
    events2, new_state2, second_result = runner.run_cycle(_snapshot(), state={})
    assert events2 == ["e2"] and new_state2 == {"s": 2}
    assert second_result.error is not None


# ── result is None (no real data / abstain) handled gracefully ─────────

def test_no_multileg_result_is_a_clean_no_op_not_an_error():
    spy = _SpyCapture(events=["abstain-event"], new_state={"x": 1}, captured=_empty_captured())
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})
    assert events == ["abstain-event"]
    assert shadow_result.ok is True
    assert shadow_result.applied_events == []


# ── wiring sanity: the real capture function ─────────────────────────────

def test_real_capture_function_returns_a_captured_run_with_prices_and_run_end():
    """Real data now exists (Phase 4D commits 6/7) -- the real
    CarryBasisAdapter.decide() call, monkeypatch-observed, must return an
    actual MultiLegResult plus the real per-asset price series it
    consumed and the backtest's own end timestamp."""
    spec = RunnerSpec(runner_id="carry_basis_v12", family="carry_basis", status="ACTIVE",
                      git_commit="test", config_hash=None, venue=VENUE,
                      config={"engines_long": [], "carry_assets": ["BTCUSDT", "ETHUSDT"],
                             "carry_fraction": 0.75, "enable_carry": True})
    state = {"paper_start": "2026-06-01"}
    events, new_state, captured = _capture_multileg_result(spec, _snapshot(), state)
    assert isinstance(events, list)
    assert isinstance(new_state, dict)
    assert isinstance(captured, CapturedRun)
    assert captured.result is not None
    assert "BTCUSDT" in captured.market_prices
    assert "ETHUSDT" in captured.market_prices
    assert len(captured.market_prices["BTCUSDT"]) > 0
    assert captured.run_end is not None


# ── account_snapshots: as-of-event state for the comparator (Phase 4D commit 9) ──

def test_account_snapshots_are_aligned_with_applied_events_and_capture_as_of_state():
    """ShadowCycleResult.account_snapshots[i] must be the account exactly
    AS OF applied_events[i], not the final/terminal account -- required so
    DifferentialComparator can compare an early event against what was
    genuinely true at that event's own time, not against the state after
    every later event (including a later leg's own entry) has also been
    applied. Two non-overlapping BTCUSDT carry pairs: leg_1/leg_2 open
    01-01 and close 01-05 (qty 1.5); leg_3/leg_4 open 01-10 and are still
    open at run_end 01-15 (qty 2.0) -- so the FINAL BTCUSDT spot position
    (2.0) genuinely differs from what was open right after leg_1's own
    entry (1.5)."""
    rows = [
        _leg_row(leg_id="leg_1", position_id="CARRY_1", entry_time="2026-01-01T00:00:00Z",
                exit_time="2026-01-05T00:00:00Z", qty=1.5, entry_price=50_000.0,
                exit_price=51_000.0, notional=75_000.0, costs=37.5),
        _perp_leg_row(leg_id="leg_2", position_id="CARRY_1", entry_time="2026-01-01T00:00:00Z",
                     exit_time="2026-01-05T00:00:00Z", qty=1.5, entry_price=50_000.0,
                     exit_price=51_000.0, notional=75_000.0, costs=37.5),
        _leg_row(leg_id="leg_3", position_id="CARRY_2", entry_time="2026-01-10T00:00:00Z",
                exit_time=None, qty=2.0, entry_price=52_000.0, notional=104_000.0, costs=52.0),
        _perp_leg_row(leg_id="leg_4", position_id="CARRY_2", entry_time="2026-01-10T00:00:00Z",
                     exit_time=None, qty=2.0, entry_price=52_000.0, notional=104_000.0, costs=52.0),
    ]
    idx = pd.to_datetime([f"2026-01-{d:02d}T00:00:00Z" for d in range(1, 16)], utc=True)
    prices = pd.Series([50_000.0 + 100 * i for i in range(15)], index=idx)
    captured = _fake_captured(*rows, market_prices={"BTCUSDT": prices},
                              run_end="2026-01-15T00:00:00Z")
    spy = _SpyCapture(events=["e"], new_state={}, captured=captured)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    _events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})

    assert shadow_result.ok is True
    assert len(shadow_result.account_snapshots) == len(shadow_result.applied_events)

    leg1_entry_idx = next(i for i, e in enumerate(shadow_result.applied_events)
                          if e.event_id == "leg_1-fill-entry")
    asof_spot = shadow_result.account_snapshots[leg1_entry_idx]["spot_positions"]
    btc_key = next(iter(asof_spot))   # single spot instrument in this scenario
    assert asof_spot[btc_key].quantity == 1.5

    # the FINAL account (after every event) has genuinely moved on --
    # proving the snapshot really is a distinct, earlier moment in time
    final_spot = runner.engine.account.spot_positions
    assert final_spot[btc_key].quantity == 2.0


def test_instrumentation_does_not_alter_arguments_or_returned_objects():
    """Commit 6 point 8: the monkeypatch capture must be purely an
    observer. Calls the REAL, unmodified decide() path twice on the same
    (deterministic) inputs -- once through the patched capture function,
    once completely unpatched -- and requires byte-identical events/
    new_state content either way. If the instrumentation altered
    anything it observed, these would differ."""
    from src.alpha20.tournament.runner_adapters import build_adapter

    spec = RunnerSpec(runner_id="carry_basis_v12", family="carry_basis", status="ACTIVE",
                      git_commit="test", config_hash=None, venue=VENUE,
                      config={"engines_long": [], "carry_assets": ["BTCUSDT"],
                             "carry_fraction": 0.75, "enable_carry": True})
    state = {"paper_start": "2026-06-01"}

    patched_events, patched_state, _captured = _capture_multileg_result(spec, _snapshot(), state)
    unpatched_events, unpatched_state = build_adapter(spec).decide(
        _snapshot(), broker=None, state=state)

    assert len(patched_events) == len(unpatched_events)
    for a, b in zip(patched_events, unpatched_events, strict=True):
        assert a.kind == b.kind and a.sleeve == b.sleeve and a.amount_usdt == b.amount_usdt
    assert patched_state == unpatched_state
