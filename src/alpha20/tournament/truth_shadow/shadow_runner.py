"""src/alpha20/tournament/truth_shadow/shadow_runner.py -- Phase 4C commit 3:
a read-only, no-effect "double run" of CarryBasisAdapter onto TruthEngine.

Contract:

  1. The legacy path (CarryBasisAdapter.decide(), UNMODIFIED) produces
     decisions and orders alone. `run_cycle()` calls it exactly once,
     exactly the way the real orchestrator would, and returns its
     (events, new_state) UNTOUCHED -- this module never edits them,
     never uses them to influence what it feeds TruthEngine, and never
     feeds TruthEngine's output back into them.
  2. TruthEngine only ever sees a DEEP COPY of the leg_ledger read from
     the legacy computation's result -- it cannot mutate what the legacy
     path produced even in principle.
  3. TruthEngine cannot: modify an order (no Order object is ever passed
     to it -- events.mapping.py's FILL events are Truth-domain values
     built FROM the leg_ledger, not references to legacy objects), change
     sizing (never touches `new_state`), block a decision (the real
     decide() call and its return happen unconditionally, before the
     shadow observation even starts), write to the legacy ledger (no
     import of, or reference to, any legacy ledger-writing code), or
     contact a venue (zero network code anywhere in this package).
  4. `ShadowConfig.enabled=False` is the kill switch: when off,
     `run_cycle()` calls the real decide() directly and returns
     immediately -- the shadow's own machinery (the capture monkeypatch,
     the mapper, TruthEngine.apply) is never even invoked. Toggling it
     never touches CarryBasisAdapter or the orchestrator.
  5. Any exception raised while capturing, mapping, or applying to
     TruthEngine is caught INSIDE `_observe()` and returned as
     `ShadowCycleResult.error` -- it never propagates out of
     `run_cycle()`, so it can never affect the legacy result already
     computed and already returned (see
     test_shadow_runner.py::test_a_shadow_exception_never_touches_the_
     already_returned_legacy_result for the guarantee this is verified,
     not just asserted in a docstring).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_adapters import build_adapter
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.alpha20.tournament.truth_shadow.mapping import LegLedgerToTruthEvents, borrow_delta_event
from src.futur.truth.engine import TruthEngine
from src.futur.truth.events import Event


@dataclass
class ShadowConfig:
    """`enabled=False` is the kill switch (Phase 4C commit 3 point 4) --
    see the module docstring's point 4 for exactly what it skips."""
    enabled: bool = True
    venue: str = "binance_usdm"


@dataclass
class ShadowCycleResult:
    skipped: bool
    applied_events: list[Event] = field(default_factory=list)
    error: BaseException | None = None
    leg_ledger: pd.DataFrame | None = None
    pnl_by_type: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.skipped and self.error is None


def _capture_multileg_result(spec: RunnerSpec, snapshot: MarketSnapshot, state: dict
                             ) -> tuple[list, dict, Any]:
    """Calls the REAL, unmodified CarryBasisAdapter.decide() and captures
    the MultiLegResult it computes internally, via a narrow, reversible
    monkeypatch of MultiLegBacktester.run -- restored in a `finally`, even
    if decide() raises. This is the "double run" the mission names this
    commit after: the SAME computation CarryBasisAdapter.decide() already
    performs, observed rather than reimplemented -- reimplementing
    MultiLegConfig's ~20-field construction here would risk silently
    drifting from the real one over time; capturing the real call's own
    return value cannot drift, by construction.

    NOT safe to call concurrently with another decide() call on the same
    runner in the same process -- the patch is process-global for its
    (short) duration. Single-consumer, same constraint as the rest of
    this package."""
    import src.institutional.backtest.multileg_backtester as mlb_mod

    captured: dict[str, Any] = {}
    original_run = mlb_mod.MultiLegBacktester.run

    def _capturing_run(self: Any, start: str, end: str) -> Any:
        result = original_run(self, start, end)
        captured["result"] = result
        return result

    adapter = build_adapter(spec)
    mlb_mod.MultiLegBacktester.run = _capturing_run   # type: ignore[method-assign]
    try:
        events, new_state = adapter.decide(snapshot, broker=None, state=state)
    finally:
        mlb_mod.MultiLegBacktester.run = original_run   # type: ignore[method-assign]
    return events, new_state, captured.get("result")


class CarryBasisShadowRunner:
    """Read-only shadow of one CarryBasisAdapter runner onto a TruthEngine.
    See the module docstring for the full no-effect contract."""

    def __init__(self, spec: RunnerSpec, config: ShadowConfig | None = None,
                engine: TruthEngine | None = None,
                capture_fn: Callable[[RunnerSpec, MarketSnapshot, dict],
                                     tuple[list, dict, Any]] = _capture_multileg_result):
        self.spec = spec
        self.config = config or ShadowConfig(venue=spec.venue or "binance_usdm")
        self.engine = engine or TruthEngine()
        self._capture_fn = capture_fn
        self._mapper = LegLedgerToTruthEvents(venue=self.config.venue)
        self._cycle_index = 0
        self._last_borrow_cumulative = 0.0

    def run_cycle(self, snapshot: MarketSnapshot, state: dict
                 ) -> tuple[list, dict, ShadowCycleResult]:
        """Point 4 (kill switch): when disabled, calls the real decide()
        directly -- none of the shadow's own machinery runs at all."""
        if not self.config.enabled:
            adapter = build_adapter(self.spec)
            events, new_state = adapter.decide(snapshot, broker=None, state=state)
            return events, new_state, ShadowCycleResult(skipped=True)

        events, new_state, result = self._capture_fn(self.spec, snapshot, state)
        shadow_result = self._observe(result)
        return events, new_state, shadow_result

    def _observe(self, result: Any) -> ShadowCycleResult:
        """Point 5: every exception raised below is caught HERE, inside
        this method, never left to propagate out of run_cycle()."""
        self._cycle_index += 1
        try:
            if result is None:
                return ShadowCycleResult(skipped=False)
            leg_ledger = result.leg_ledger.copy(deep=True)   # point 2: immutable copy
            pnl_by_type = dict(result.pnl_by_type)
            cycle_ts = str(pd.Timestamp.now(tz="UTC"))
            truth_events = self._mapper.events_for_cycle(leg_ledger, cycle_ts)
            borrow_ev = borrow_delta_event(
                pnl_by_type.get("borrow", 0.0), self._last_borrow_cumulative,
                cycle_ts, self._cycle_index)
            self._last_borrow_cumulative = pnl_by_type.get("borrow", 0.0)
            if borrow_ev is not None:
                truth_events.append(borrow_ev)
            applied = [self.engine.apply(ev) for ev in truth_events]
            return ShadowCycleResult(skipped=False, applied_events=applied,
                                     leg_ledger=leg_ledger, pnl_by_type=pnl_by_type)
        except Exception as exc:   # noqa: BLE001 -- shadow isolation, see class docstring
            return ShadowCycleResult(skipped=False, error=exc)
