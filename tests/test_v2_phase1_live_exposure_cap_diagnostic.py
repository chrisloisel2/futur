"""
tests/test_v2_phase1_live_exposure_cap_diagnostic.py

V2 master-prompt Phase 1 diagnostic (see docs/v2/MIGRATION.md, known-defect
verification log; docs/v2/PHASE1_DIAGNOSTIC.md for the full write-up).
Captures CURRENT, OBSERVED behavior of the ALPHA_20 live/paper decision
path — not a spec for desired behavior, must not be read as "PASS = correct."

CORRECTION (2026-07-27, session 3): the first version of this file built
`venue_unsecured_frac={"binance": 0.0}` by hand instead of reproducing
orchestrator._run_one()'s actual call:

    venue_unsecured_frac={spec.venue or "n/a": gross / max(account.nav_usdt(), 1.0)}

i.e. venue_unsecured_frac is DERIVED from gross_usdt/nav, not independent of
it. Forcing it to 0.0 hid a real check (venue_unsecured_cap=0.15) and
produced a false "150% gross passes completely unblocked" conclusion. Fixed
below to build venue_unsecured_frac exactly the way _run_one() does.

Corrected finding: 150% gross/NAV concentrated on one venue DOES trip
venue_unsecured_cap (0.15) and DOES move the state to "risk_reduced" — but
that state turns out to be advisory only downstream (see the PaperBroker /
adapter tests in this file): nothing in src/alpha20 reads
GovernorDecision.scale, and PaperBroker.execute() only special-cases the
literal string "kill". So the corrected verdict is not "gross exposure is
unblocked" but "gross exposure changes the governor's *label*, and that
label is consultative except for kill."

SUPERSEDED IN PART (2026-07-28, Phase 2 rebuild): the paragraph above and
`test_scale_field_is_never_read_outside_its_own_definition` below described
a real defect at the time, per your explicit go-ahead this is now fixed.
`GovernorDecision.scale` is applied by `BasisTermAdapter`/`MHEventsAdapter`
to the requested notional BEFORE `Order()` is constructed (never to
exit/closing orders, which stay unaffected on purpose — reducing risk must
never be blocked by a risk-scale meant to limit new risk). `CarryBasisAdapter`
remains unchanged (still doesn't route through PaperBroker at all -- a
separate, deeper gap, see docs/v2/PHASE1_DIAGNOSTIC.md §6). Section 6 below
covers the new behavior; the PaperBroker-level tests in section 3 remain
accurate descriptions of PaperBroker.execute() in isolation, which was NOT
changed -- it still only special-cases literal "kill", by design: scaling
now happens one layer up, in the adapter, before PaperBroker ever sees the
order.
"""
from __future__ import annotations

import inspect
import types

import pandas as pd
import pytest

from src.alpha20.contracts import GovernorDecision, clamp_scale
from src.alpha20.execution.paper_broker import Fill, Order, PaperBroker
from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.paper_account import PaperAccount
from src.alpha20.tournament.runner_adapters import (
    BasisTermAdapter, CarryBasisAdapter, MHEventsAdapter,
)
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.institutional.portfolio.invariants import (
    InvariantLimits, InvariantViolation, check_portfolio_invariants,
)
from src.institutional.portfolio.position import PortfolioPosition, PositionLeg

CAPITAL_USDT = 100_000.0
VENUE = "binance_usdm"


def _venue_unsecured_frac_as_run_one_builds_it(gross_usdt: float, nav: float,
                                               venue: str = VENUE) -> dict:
    """Exact reproduction of orchestrator._run_one()'s call — see
    src/alpha20/tournament/orchestrator.py:76-79."""
    return {venue or "n/a": gross_usdt / max(nav, 1.0)}


def _snapshot(price: float = 50_000.0, symbol: str = "BTCUSDT") -> MarketSnapshot:
    return MarketSnapshot(
        market_event_id="phase1-diag", cutoff="2026-01-01T00:00:00Z",
        decision_ts="2026-01-01T00:00:00Z", received_ts="2026-01-01T00:00:00Z",
        prices={symbol: {"close": price, "exchange_ts": "2026-01-01T00:00:00Z"}},
    )


# ── 1. Governor is really called, and gross DOES move the state — via
#      venue_unsecured_max, not via a max_gross_exposure check that doesn't
#      exist ────────────────────────────────────────────────────────────────

def test_150pct_gross_triggers_risk_reduced_via_venue_unsecured_max():
    """Reproduces orchestrator._run_one()'s exact call. 150% gross/NAV on one
    venue breaches venue_unsecured_cap=0.15 (configs/alpha20.yaml,
    ALPHA20_LOW_RISK) — NOT because of a max_gross_exposure check (none
    exists in src/alpha20), but because venue_unsecured_frac is itself
    derived from gross/nav for a single-venue runner."""
    account = PaperAccount("phase1_diag_gross", CAPITAL_USDT)
    nav = account.nav_usdt()
    assert nav == CAPITAL_USDT

    gross_usdt = 1.5 * nav  # 150% gross, breaches historical named caps (0.75 / 1.00)
    venue_frac = _venue_unsecured_frac_as_run_one_builds_it(gross_usdt, nav)
    assert venue_frac == {VENUE: 1.5}

    decision = account.evaluate_risk(
        gross_usdt=gross_usdt, net_delta_usdt=0.0, venue_unsecured_frac=venue_frac,
    )

    assert decision.state == "risk_reduced", (
        f"expected risk_reduced (venue_unsecured breach), got {decision.state} "
        f"reasons={decision.reasons}"
    )
    assert "venue_unsecured_max" in decision.reasons
    assert decision.reasons["venue_unsecured_max"] == 1.5
    # the fixed 10%-of-gross margin proxy does NOT breach at 150% gross —
    # confirms this is the venue check firing, not a gross/margin check
    assert "margin_used" not in decision.reasons
    assert decision.scale == 0.5  # SCALES["risk_reduced"] — see below: never consumed


def test_10pct_net_delta_does_trigger_a_real_but_differently_named_cap():
    """Contrast: net_delta_cap (0.05 NAV) is real and fires independently of
    the venue check above."""
    account = PaperAccount("phase1_diag_netdelta", CAPITAL_USDT)
    nav = account.nav_usdt()

    net_delta_usdt = 0.10 * nav
    decision = account.evaluate_risk(
        gross_usdt=0.0, net_delta_usdt=net_delta_usdt,
        venue_unsecured_frac=_venue_unsecured_frac_as_run_one_builds_it(0.0, nav),
    )
    assert decision.state == "risk_reduced"
    assert "net_delta" in decision.reasons


def test_kill_state_forces_scale_zero_but_nothing_reads_scale():
    """Governor is really called and really computes scale=0.0 on a kill --
    the question the rest of this file answers is whether anything
    downstream actually multiplies an order by that number."""
    account = PaperAccount("phase1_diag_kill", CAPITAL_USDT)
    nav = account.nav_usdt()
    # drawdown-based kill: manufacture via extreme net_delta AND venue breach
    # is not enough to reach "kill" (governor only steps one level per cycle,
    # see global_governor._ORDER logic) -- kill is reached directly via dd_kill.
    decision = GovernorDecision(state="kill", scale=0.0, reasons={"dd_kill": 0.03})
    assert decision.scale == 0.0
    # proven separately below: PaperBroker.execute() never reads .scale, only
    # the literal state string "kill"


# ── 2. RETIRED (was: "GovernorDecision.scale is computed but never consumed
#      anywhere"). Phase 2, 2026-07-28: it now is -- orchestrator.py passes
#      decision.scale to adapter.decide(), which applies it to the requested
#      notional before Order() is built. See section 6 for the replacement
#      tests. Kept as a positive assertion (not deleted) so the history of
#      "this was a real gap, then fixed on your explicit instruction" stays
#      visible in the same file that documented the gap. ───────────────────

def test_orchestrator_now_passes_decision_scale_to_adapter_decide():
    """Replaces test_scale_field_is_never_read_outside_its_own_definition
    (session 2-4), which asserted the opposite of what's now true by
    design. orchestrator._run_one's call to adapter.decide() must pass
    decision.scale, not just decision.state -- source-inspection pin so a
    future refactor that silently drops the argument breaks this test."""
    import src.alpha20.tournament.orchestrator as orch_mod

    src_text = inspect.getsource(orch_mod._run_one)
    assert "decision.scale" in src_text, (
        "orchestrator._run_one no longer passes decision.scale to "
        "adapter.decide() -- this reintroduces the gap Phase 2 fixed."
    )


# ── 3. PaperBroker.execute(): only "kill" changes fill behavior ────────────

def test_paper_broker_fills_full_notional_under_risk_on():
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", VENUE, +1, 10_000.0), _snapshot(), risk_state="risk_on",
    )
    assert fills["observed"].filled_notional == 10_000.0
    assert not fills["observed"].rejected


def test_paper_broker_fills_full_notional_under_risk_reduced():
    """This is the crux of the corrected verdict: risk_reduced does NOT
    reduce the filled notional. The 0.5 scale factor computed by the
    governor is not applied here or anywhere upstream (see previous test)."""
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", VENUE, +1, 10_000.0), _snapshot(), risk_state="risk_reduced",
    )
    assert fills["observed"].filled_notional == 10_000.0
    assert not fills["observed"].rejected


def test_paper_broker_fills_full_notional_under_cash():
    """Same as risk_reduced: "cash" (scale=0.0 in the governor's own output)
    still fills the order at full requested notional in PaperBroker."""
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", VENUE, +1, 10_000.0), _snapshot(), risk_state="cash",
    )
    assert fills["observed"].filled_notional == 10_000.0
    assert not fills["observed"].rejected


def test_paper_broker_rejects_new_orders_under_kill():
    fills = PaperBroker().execute(
        Order("r1", "BTCUSDT", VENUE, +1, 10_000.0), _snapshot(), risk_state="kill",
    )
    assert fills["observed"].rejected
    assert fills["observed"].reject_reason == "kill_switch_active"
    assert fills["observed"].filled_notional == 0.0


def test_paper_broker_kill_does_not_block_exits():
    """order.is_exit=True is explicitly exempted from the kill check --
    positions can always be closed, only new/increasing risk is blocked."""
    exit_order = Order("r1", "BTCUSDT", VENUE, -1, 10_000.0, is_exit=True)
    fills = PaperBroker().execute(exit_order, _snapshot(), risk_state="kill")
    assert not fills["observed"].rejected
    assert fills["observed"].filled_notional == 10_000.0


# ── 4. Per-adapter check: how (or whether) each of the 3 runner adapters
#      consults risk_state at all ───────────────────────────────────────────

def test_carry_basis_adapter_never_reads_risk_state():
    """CarryBasisAdapter.decide() accepts risk_state as a parameter but its
    body never references it: it doesn't route orders through PaperBroker at
    all (gross_usdt comes from its own internal MultiLegBacktester replay,
    per its own comment "ce runner ne route PAS ses jambes par le broker
    paper partagé"). Net effect: for this adapter, not even a kill state
    blocks anything -- there is no risk_state check anywhere in its decide().
    This is a stronger finding than "only kill blocks": for this specific
    adapter, nothing blocks."""
    body = inspect.getsource(CarryBasisAdapter.decide)
    occurrences = body.count("risk_state")
    assert occurrences == 1, (
        f"expected exactly 1 occurrence of 'risk_state' (the parameter in the "
        f"signature) and zero uses in the body, found {occurrences}. If this "
        f"increased, CarryBasisAdapter now consults risk_state -- update the "
        f"verdict in PHASE1_DIAGNOSTIC.md and MIGRATION.md."
    )


def test_basis_term_adapter_blocks_new_positions_only_on_literal_kill():
    body = inspect.getsource(BasisTermAdapter.decide)
    assert 'risk_state == "kill"' in body
    assert ".scale" not in body
    # sizing is a fixed fraction of capital, not scaled by the governor's
    # risk_reduced/cash multiplier
    assert 'cfg["sizing_frac"]' in body


def test_mh_events_adapter_blocks_new_positions_only_on_literal_kill():
    body = inspect.getsource(MHEventsAdapter.decide)
    assert 'risk_state == "kill"' in body
    assert ".scale" not in body
    assert 'cfg["weight_per_decision"]' in body


# ── 5. session 4 -- closes the session-2/3 open question: does
#      CarryBasisAdapter's internal MultiLegBacktester replay (the one place
#      check_portfolio_invariants is transitively reachable from the live
#      path, per PHASE1_DIAGNOSTIC.md §3) constrain real paper capital? ─────
#
# Answer: no, for two independent reasons proven below.
#
# (a) src/institutional/portfolio/invariants.py::InvariantLimits declares
#     max_gross_exposure=1.00 and max_net_long_exposure=0.75 -- the exact
#     pair of names/values the master prompt quotes verbatim -- but
#     check_portfolio_invariants() computes gross_exposure/net_long_exposure
#     and returns them WITHOUT ever comparing them to those limits (it only
#     raises on hedge_exposure, naked shorts, unlinked short legs, and carry
#     delta tolerance). This is a DIFFERENT file from the one prior sessions
#     checked for this claim (src/institutional/portfolio/constraints.py,
#     "not reproduced" in session 1's EXECUTION_STATE.md entry) -- that file
#     has its own, separately-enforced max_gross_exposure=0.75 (no
#     max_net_long_exposure field at all), reachable only from
#     meta_allocator.py, not from invariants.py or the live path at all. The
#     master prompt's literal claim, verified against the file it actually
#     names, is CONFIRMED true, not "not reproduced."
#
# (b) Even so, it would not matter for live paper capital: CarryBasisAdapter
#     .decide() wraps the entire MultiLegBacktester(...).run() call in a
#     blanket `except Exception` (runner_adapters.py, immediately after the
#     MultiLegBacktester(...).run(start, end) call) and converts ANY
#     exception -- including a genuine InvariantViolation from the checks
#     that DO fire (naked short, hedge cap, carry-delta tolerance) -- into an
#     ordinary "abstain" decision event. No halt, no alert, no visibility to
#     the governor or other runners, no consequence beyond skipping one
#     mark-to-market update for this runner this cycle.

def test_check_portfolio_invariants_never_enforces_its_own_gross_or_net_caps():
    """Two long-only positions worth 2x equity -- no shorts, no hedges, so
    neither the naked-short nor the hedge-cap check can mask this -- blow
    both max_gross_exposure (1.00) and max_net_long_exposure (0.75) by 2x
    and check_portfolio_invariants() still returns cleanly, no
    InvariantViolation raised. It computes the exact numbers that would be
    needed to enforce the cap and then does not compare them to it."""
    equity = 100_000.0
    positions = [
        PortfolioPosition("p1", "DIRECTIONAL_LONG", "eng", "BTCUSDT", "t0", legs=[
            PositionLeg("l1", "p1", "BTCUSDT", "LONG_SPOT", "t0", 50_000.0, 1.0,
                       50_000.0, mark_price=50_000.0),
        ]),
        PortfolioPosition("p2", "DIRECTIONAL_LONG", "eng", "ETHUSDT", "t0", legs=[
            PositionLeg("l2", "p2", "ETHUSDT", "LONG_SPOT", "t0", 150_000.0, 1.0,
                       150_000.0, mark_price=150_000.0),
        ]),
    ]
    limits = InvariantLimits()
    assert (limits.max_gross_exposure, limits.max_net_long_exposure) == (1.00, 0.75)

    exposures = check_portfolio_invariants(positions, equity, limits)  # must not raise

    assert exposures["gross_exposure"] == 2.0       # 2x the declared 1.00 cap
    assert exposures["net_long_exposure"] == 2.0     # 2x the declared 0.75 cap
    assert exposures["gross_exposure"] > limits.max_gross_exposure
    assert exposures["net_long_exposure"] > limits.max_net_long_exposure


def test_carry_basis_adapter_swallows_invariant_violations_as_silent_abstain(monkeypatch):
    """Even the invariants check_portfolio_invariants DOES enforce (naked
    short / hedge cap / carry-delta) would never reach live paper capital
    either: CarryBasisAdapter.decide() catches them as a generic Exception
    and downgrades to an "abstain" ledger event, indistinguishable from a
    routine no-op cycle."""
    import src.institutional.backtest.multileg_backtester as mlb_mod

    violation_message = "SHORT NU détecté (pos fake, injected by this test)"

    class _RaisingBacktester:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, start, end):
            raise InvariantViolation(violation_message)

    monkeypatch.setattr(mlb_mod, "MultiLegBacktester", _RaisingBacktester)

    spec = RunnerSpec("carry_basis_test", "carry_basis", "ACTIVE", "deadbeef", None)
    adapter = CarryBasisAdapter(spec)
    events, new_state = adapter.decide(_snapshot(), PaperBroker(), {})

    assert new_state == {}, "state must be unchanged on the exception path"
    assert len(events) == 1
    assert events[0].meta["signal"] == "abstain"
    assert events[0].meta["reason"] == f"backtest_error: {violation_message}"
    # nothing marks this differently from any other caught exception (a
    # missing-data error, a bug, ...) -- InvariantViolation gets no special
    # handling, confirming (b) above.


# ── 6. Phase 2, 2026-07-28: GovernorDecision.scale wired into execution,
#      per your explicit decision. Four acceptance criteria, each proven
#      below: (a) strict [0,1] bounds, (b) scale=0 forbids the order
#      (nothing submitted, not a $0 order), (c) scale=0.5 exactly halves the
#      requested notional, (d) nothing downstream can re-inflate the size
#      (scale is applied once, before Order() is built; PaperBroker and the
#      fill-scenario math only ever shrink notional via fill_frac <= 1.0,
#      confirmed in section 3's tests, which are unchanged). gross/net/
#      margin/concentration stay priority because they are exactly what
#      determines `state` (and hence `scale`) upstream, in
#      global_governor.evaluate() -- unchanged by this commit; the
#      venue_unsecured/net_delta tests in section 1 above still pass
#      unmodified, confirming this. ───────────────────────────────────────

class _RecordingBroker:
    """Stand-in for PaperBroker that records every Order it's asked to
    execute and fills it in full -- lets a test assert exactly what notional
    an adapter decided to submit, without needing PaperBroker's own fee/
    slippage arithmetic (already covered separately in section 3)."""

    def __init__(self):
        self.orders: list[Order] = []

    def execute(self, order, snapshot, risk_state="risk_on"):
        self.orders.append(order)
        f = Fill("observed", order.notional_usdt, 100.0, 0.0, 0.0, "test",
                 0.0, "test", 150, False, None, 0.0)
        return {"observed": f}


def test_governor_decision_scale_rejects_out_of_range_values():
    """Strict [0, 1] bounds, enforced at construction -- a GovernorDecision
    with scale > 1.0 (would inflate) or < 0.0 (nonsensical) can't even be
    built."""
    with pytest.raises(ValueError):
        GovernorDecision(state="risk_on", scale=1.5, reasons={})
    with pytest.raises(ValueError):
        GovernorDecision(state="cash", scale=-0.1, reasons={})
    # boundary values are valid
    GovernorDecision(state="risk_on", scale=1.0, reasons={})
    GovernorDecision(state="cash", scale=0.0, reasons={})


def test_clamp_scale_never_inflates_only_shrinks_or_passes_through():
    """The defense-in-depth clamp used by adapters: within [0,1] it's a
    no-op, outside it always moves TOWARD the range, never away from it --
    i.e. it can never turn a smaller number into a larger one."""
    assert clamp_scale(0.5) == 0.5
    assert clamp_scale(0.0) == 0.0
    assert clamp_scale(1.0) == 1.0
    assert clamp_scale(1.7) == 1.0     # shrinks, doesn't pass through
    assert clamp_scale(-0.3) == 0.0    # clamps up to the floor, not below


def _snapshot_with_quarterly(spot_price=50_000.0, symbol="BTCUSDT",
                             quarterly_symbol="BTCUSDT_Q", days_to_expiry=30):
    basis_price = spot_price * (1 + 0.10 * days_to_expiry / 365)  # ~10% ann.
    return MarketSnapshot(
        market_event_id="scale-wiring-test", cutoff="2026-01-01T00:00:00Z",
        decision_ts="2026-01-01T00:00:00Z", received_ts="2026-01-01T00:00:00Z",
        prices={symbol: {"close": spot_price, "exchange_ts": "2026-01-01T00:00:00Z"}},
        quarterlies={symbol: [{"symbol": quarterly_symbol,
                               "days_to_expiry": days_to_expiry,
                               "price": basis_price}]},
    )


def _basis_term_spec():
    return RunnerSpec(
        "basis_term_test", "basis_term", "ACTIVE", "deadbeef", None,
        assets=["BTCUSDT"], venue=VENUE,
        config={"entry_threshold_ann": 0.05, "min_days_to_expiry": 10,
               "max_days_to_expiry": 120, "sizing_frac": 0.25})


def test_basis_term_adapter_scale_zero_forbids_the_order():
    adapter = BasisTermAdapter(_basis_term_spec())
    broker = _RecordingBroker()
    events, new_state = adapter.decide(_snapshot_with_quarterly(), broker, {},
                                       risk_state="risk_on", scale=0.0)
    assert broker.orders == [], "scale=0 must forbid order creation entirely, not submit a $0 order"
    assert new_state.get("positions", {}) == {}
    rejects = [e for e in events if e.kind == "reject"]
    assert len(rejects) == 1
    assert rejects[0].meta["reason"] == "governor_scale_zero"
    assert rejects[0].meta["governor_scale"] == 0.0


def test_basis_term_adapter_scale_half_exactly_halves_requested_notional():
    spec = _basis_term_spec()
    full = BasisTermAdapter(spec)
    broker_full = _RecordingBroker()
    full.decide(_snapshot_with_quarterly(), broker_full, {}, risk_state="risk_on", scale=1.0)
    full_notional = broker_full.orders[0].notional_usdt
    assert full_notional == pytest.approx(spec.capital_standalone_eur * 0.25)

    half = BasisTermAdapter(spec)
    broker_half = _RecordingBroker()
    half.decide(_snapshot_with_quarterly(), broker_half, {}, risk_state="risk_on", scale=0.5)
    half_notional = broker_half.orders[0].notional_usdt
    assert half_notional == pytest.approx(full_notional / 2)
    # both legs (spot + quarterly) get the same scaled notional
    assert all(o.notional_usdt == pytest.approx(half_notional) for o in broker_half.orders)


def test_basis_term_adapter_exit_orders_are_never_scaled_down():
    """Closing an existing position must never be shrunk or blocked by the
    current cycle's risk-scale -- scale exists to limit NEW risk, and
    withholding an exit would leave MORE risk on, the opposite of what a
    risk-reduced/cash state is for. Tested at scale=0.0, the most extreme
    case: the exit still executes at full original notional."""
    spec = _basis_term_spec()
    adapter = BasisTermAdapter(spec)
    broker = _RecordingBroker()
    state = {"positions": {"BTCUSDT": {
        "symbol": "OLD_Q", "notional_usdt": 50_000.0, "basis_entry": 0.05,
        "days_to_expiry_at_open": 30, "cycles_elapsed": 0,
    }}}
    # OLD_Q no longer among live quarterlies -> adapter treats it as converged/rolled
    snapshot = _snapshot_with_quarterly(quarterly_symbol="NEW_Q")
    events, new_state = adapter.decide(snapshot, broker, state,
                                       risk_state="risk_on", scale=0.0)
    assert len(broker.orders) == 2, "both exit legs (spot + quarterly) must execute"
    assert all(o.notional_usdt == 50_000.0 for o in broker.orders), \
        "exit notional must equal the position's actual size, unscaled"
    assert new_state["positions"] == {}


def _fake_mh_module(tmp_path, book_rows):
    ledger_path = tmp_path / "shadow_decisions.parquet"
    pd.DataFrame(book_rows).to_parquet(ledger_path)
    rmh_ns = types.SimpleNamespace(SHADOW_LEDGER=ledger_path,
                                   select_book=lambda df, ts: df)
    return types.SimpleNamespace(
        rmh=rmh_ns,
        _closes=lambda symbol: pd.Series(dtype=float),
        replay_decision=lambda row, closes, cost_bp: (0.01, "t0", "t1", 0.01),
        LABEL_COST_RT_BP=14.0,
    )


def _mh_events_spec():
    return RunnerSpec(
        "mh_events_test", "mh_events", "ACTIVE", "deadbeef", None, venue=VENUE,
        config={"tier_filter": "book", "horizon_filter": "MH_consensus",
               "weight_per_decision": 0.20, "max_open": 5})


def _mh_book_row():
    return [{"horizon": "MH_consensus_h4", "event_time": "2026-01-01T00:00:00Z",
             "symbol": "BTCUSDT", "engine": "test_engine", "score": 0.75,
             "net_labeled": 0.01}]


def test_mh_events_adapter_scale_zero_forbids_the_order(monkeypatch, tmp_path):
    spec = _mh_events_spec()
    adapter = MHEventsAdapter(spec)
    monkeypatch.setattr(adapter, "_mod", lambda: _fake_mh_module(tmp_path, _mh_book_row()))
    broker = _RecordingBroker()
    events, new_state = adapter.decide(_snapshot(), broker, {},
                                       risk_state="risk_on", scale=0.0)
    assert broker.orders == [], "scale=0 must forbid order creation entirely"
    rejects = [e for e in events if e.kind == "reject"]
    assert len(rejects) == 1
    assert rejects[0].meta["reason"] == "governor_scale_zero"
    assert rejects[0].meta["governor_scale"] == 0.0


def test_mh_events_adapter_scale_half_exactly_halves_requested_notional(monkeypatch, tmp_path):
    spec = _mh_events_spec()

    full = MHEventsAdapter(spec)
    monkeypatch.setattr(full, "_mod", lambda: _fake_mh_module(tmp_path, _mh_book_row()))
    broker_full = _RecordingBroker()
    full.decide(_snapshot(), broker_full, {}, risk_state="risk_on", scale=1.0)
    full_notional = broker_full.orders[0].notional_usdt
    assert full_notional == pytest.approx(spec.capital_standalone_eur * 0.20)

    half = MHEventsAdapter(spec)
    monkeypatch.setattr(half, "_mod", lambda: _fake_mh_module(tmp_path, _mh_book_row()))
    broker_half = _RecordingBroker()
    half.decide(_snapshot(), broker_half, {}, risk_state="risk_on", scale=0.5)
    half_notional = broker_half.orders[0].notional_usdt
    assert half_notional == pytest.approx(full_notional / 2)


def test_scale_default_is_full_size_backward_compatible():
    """Callers that don't pass scale (old call sites, if any remain) get
    scale=1.0 -- full requested size, identical to pre-Phase-2 behavior."""
    spec = _basis_term_spec()
    adapter = BasisTermAdapter(spec)
    broker = _RecordingBroker()
    adapter.decide(_snapshot_with_quarterly(), broker, {}, risk_state="risk_on")
    assert broker.orders[0].notional_usdt == pytest.approx(spec.capital_standalone_eur * 0.25)
