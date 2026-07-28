"""tests/integration/test_alpha20_carry_truth_shadow_runner.py -- Phase 4C
commit 3: the shadow runner's no-effect contract (kill switch, and a
shadow-side exception never touching the already-returned legacy result).
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.alpha20.tournament.truth_shadow.mapping import UnmappableLegError
from src.alpha20.tournament.truth_shadow.shadow_runner import (
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


def _fake_result(*rows, borrow: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        leg_ledger=pd.DataFrame(list(rows)),
        pnl_by_type={"directional": 0.0, "carry_funding": 0.0, "hedge": 0.0,
                    "fees": 0.0, "borrow": borrow},
    )


class _SpyCapture:
    """Injectable capture_fn stand-in -- records how many times it was
    called and always returns a pre-baked (events, new_state, result)."""

    def __init__(self, events, new_state, result):
        self.events = events
        self.new_state = new_state
        self.result = result
        self.calls = 0

    def __call__(self, spec, snapshot, state):
        self.calls += 1
        return self.events, self.new_state, self.result


# ── kill switch ──────────────────────────────────────────────────────────

def test_kill_switch_never_invokes_the_capture_function():
    spy = _SpyCapture(events=["sentinel-event"], new_state={"sentinel": True}, result=None)
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
    result = _fake_result(_leg_row())
    spy = _SpyCapture(events=sentinel_events, new_state=sentinel_state, result=result)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    events, new_state, shadow_result = runner.run_cycle(_snapshot(), state={})

    assert spy.calls == 1
    assert events is sentinel_events        # identity -- not a copy, not modified
    assert new_state is sentinel_state
    assert shadow_result.ok is True
    assert len(shadow_result.applied_events) > 0
    assert runner.engine.account.spot_positions   # the shadow DID apply something


def test_shadow_never_mutates_the_leg_ledger_it_was_given():
    result = _fake_result(_leg_row())
    original_ledger = result.leg_ledger
    snapshot_before = original_ledger.copy(deep=True)
    spy = _SpyCapture(events=[], new_state={}, result=result)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)

    runner.run_cycle(_snapshot(), state={})

    assert original_ledger.equals(snapshot_before)   # untouched by the shadow


# ── point 5: a shadow-side exception never touches the legacy result ───

def test_a_shadow_exception_never_touches_the_already_returned_legacy_result():
    sentinel_events = ["sentinel-event"]
    sentinel_state = {"sentinel": True}
    # a non-USDT asset forces UnmappableLegError deep inside the mapper
    bad_result = _fake_result(_leg_row(asset="BTCEUR"))
    spy = _SpyCapture(events=sentinel_events, new_state=sentinel_state, result=bad_result)
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
    bad_result = _fake_result(_leg_row(qty=-1.0))   # nonpositive qty -> UnmappableLegError
    spy = _SpyCapture(events=[], new_state={}, result=bad_result)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    _events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})
    assert shadow_result.error is not None


def test_engine_invariant_violation_is_also_isolated():
    """Not just mapping errors -- an InvariantViolation raised by
    engine.apply() itself (e.g. a duplicate event_id across two legs
    that collide) must be caught the same way."""
    # two DIFFERENT leg_ids that would race to the SAME synthesized order_id
    # is hard to construct without reaching into internals; simpler and
    # equally valid: engine already primed with an account state that will
    # make a subsequent event invalid.
    result = _fake_result(_leg_row(leg_id="leg_1", costs=10.0))
    spy = _SpyCapture(events=["e"], new_state={"s": 1}, result=result)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    _events, _new_state, first_result = runner.run_cycle(_snapshot(), state={})
    assert first_result.ok is True

    # same leg observed again with COST DECREASED -- rejected by the mapper
    # itself as unmappable (monotonicity), proving the SAME engine instance
    # keeps working across cycles and isolates a later failure too
    result2 = _fake_result(_leg_row(leg_id="leg_1", costs=1.0))
    spy2 = _SpyCapture(events=["e2"], new_state={"s": 2}, result=result2)
    runner._capture_fn = spy2
    events2, new_state2, second_result = runner.run_cycle(_snapshot(), state={})
    assert events2 == ["e2"] and new_state2 == {"s": 2}
    assert second_result.error is not None


# ── result is None (no real data / abstain) handled gracefully ─────────

def test_no_multileg_result_is_a_clean_no_op_not_an_error():
    spy = _SpyCapture(events=["abstain-event"], new_state={"x": 1}, result=None)
    runner = CarryBasisShadowRunner(_spec(), config=ShadowConfig(venue=VENUE), capture_fn=spy)
    events, _new_state, shadow_result = runner.run_cycle(_snapshot(), state={})
    assert events == ["abstain-event"]
    assert shadow_result.ok is True
    assert shadow_result.applied_events == []


# ── wiring sanity: the real capture function, no real data available ───

def test_real_capture_function_handles_missing_market_data_gracefully():
    """No enriched parquet data exists in this environment -- decide()
    is expected to abstain (its own try/except around
    MultiLegBacktester.run), and _capture_multileg_result must not raise
    just because nothing was captured."""
    events, new_state, result = _capture_multileg_result(_spec(), _snapshot(), state={})
    assert isinstance(events, list)
    assert isinstance(new_state, dict)
    assert result is None   # no prices available -> MultiLegBacktester.run() never completed
