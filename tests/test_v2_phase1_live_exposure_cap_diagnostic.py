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
"""
from __future__ import annotations

import inspect

from src.alpha20.contracts import GovernorDecision
from src.alpha20.execution.paper_broker import Order, PaperBroker
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


# ── 2. GovernorDecision.scale is computed but never consumed anywhere in
#      src/alpha20 -- pinned via source inspection so a future wiring shows
#      up as a test change, not a silent assumption ─────────────────────────

def test_scale_field_is_never_read_outside_its_own_definition():
    """`grep -rn "\\.scale" src/alpha20` (run manually this session) found
    exactly one reference outside contracts.py itself: a unit test asserting
    the governor's OWN output (tests/test_alpha20_risk.py). Pin that via
    source inspection of the actual execution-path modules so a future fix
    that wires scale into sizing breaks this test (which is the point --
    update/retire it then)."""
    import src.alpha20.execution.paper_broker as pb_mod
    import src.alpha20.tournament.runner_adapters as ra_mod

    for mod in (pb_mod, ra_mod):
        src_text = inspect.getsource(mod)
        assert ".scale" not in src_text, (
            f"{mod.__name__} now references .scale -- GovernorDecision.scale "
            f"appears to be wired into execution; update PHASE1_DIAGNOSTIC.md "
            f"and MIGRATION.md's verdict, this is no longer accurate."
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
