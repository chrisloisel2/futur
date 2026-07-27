"""
tests/test_v2_phase1_live_exposure_cap_diagnostic.py

V2 master-prompt Phase 1 diagnostic (see docs/v2/MIGRATION.md, known-defect
verification log). This test captures CURRENT, OBSERVED behavior of the
ALPHA_20 live/paper decision path — it is not a spec for desired behavior
and must not be read as "PASS = correct." It exists to prove, by execution
rather than by static reading, whether the literal `max_gross_exposure` /
`max_net_long_exposure` caps declared in configs/portfolio_v1_1_parallel_50.yaml
(0.75 / 0.50) and src/institutional/portfolio/invariants.py (1.00 / 0.75)
actually gate a decision on the live/paper path exercised by
src/alpha20/tournament/orchestrator.py::_run_one (line: `account.evaluate_risk(
gross_usdt=..., net_delta_usdt=..., venue_unsecured_frac=...)`).

Traced call chain: orchestrator._run_one -> PaperAccount.evaluate_risk ->
PaperAccount.risk_metrics -> src.alpha20.risk.global_governor.evaluate.
That governor (profile ALPHA20_LOW_RISK, configs/alpha20.yaml) checks
drawdown, daily_loss, weekly_loss, es99_1d, net_delta_cap (0.05 NAV),
margin_used_cap (0.20 NAV, computed as `gross_usdt * 0.10 / nav` — a fixed
10% initial-margin proxy, NOT a direct gross-exposure ratio), and
venue_unsecured_cap (0.15). It never reads `max_gross_exposure` or
`max_net_long_exposure` — those names/values exist only in
src/institutional/portfolio/{constraints,invariants}.py, which is wired into
the BACKTEST path (backtest/portfolio_backtester.py,
backtest/multileg_backtester.py) and is never imported by src/alpha20.

If this test starts failing after a future change wires the live path into
those caps (or replaces global_governor with something that does), that is
the fix landing — update/retire this test at that point rather than treating
the failure as a regression.
"""
from __future__ import annotations

from src.alpha20.tournament.paper_account import PaperAccount

CAPITAL_USDT = 100_000.0

# Named caps this test is checking against (NOT read by the code under test —
# that is exactly the point).
NAMED_MAX_GROSS_EXPOSURE_PORTFOLIO_V1_1 = 0.75      # configs/portfolio_v1_1_parallel_50.yaml
NAMED_MAX_GROSS_EXPOSURE_INSTITUTIONAL = 1.00       # src/institutional/portfolio/invariants.py
NAMED_MAX_NET_LONG_EXPOSURE_PORTFOLIO_V1_1 = 0.50   # configs/portfolio_v1_1_parallel_50.yaml


def test_150pct_gross_exposure_is_not_blocked_by_the_live_governor():
    """150% gross/NAV breaches BOTH named gross caps above (0.75 and 1.00),
    but stays under the live path's actual proxy (margin_used = gross*0.10/nav
    = 0.15 < margin_used_cap 0.20) — so the live governor lets it through."""
    account = PaperAccount("phase1_diag_gross", CAPITAL_USDT)
    nav = account.nav_usdt()
    assert nav == CAPITAL_USDT  # fresh ledger, no flows yet — sanity check on the premise

    gross_usdt = 1.5 * nav  # 150% gross
    assert (gross_usdt / nav) > NAMED_MAX_GROSS_EXPOSURE_PORTFOLIO_V1_1
    assert (gross_usdt / nav) > NAMED_MAX_GROSS_EXPOSURE_INSTITUTIONAL

    decision = account.evaluate_risk(
        gross_usdt=gross_usdt, net_delta_usdt=0.0,
        venue_unsecured_frac={"binance": 0.0},
    )

    # OBSERVED on HEAD ecd93ad-derived v2/foundation: the live governor does
    # not block this. state stays "risk_on" and no reason fires.
    assert decision.state == "risk_on", (
        f"expected current (unenforced) behavior 'risk_on', got {decision.state} "
        f"reasons={decision.reasons} — if this now blocks, the gross-exposure "
        f"gap has been closed; update this test's docstring and assertion."
    )
    assert "margin_used" not in decision.reasons


def test_10pct_net_delta_does_trigger_a_real_but_differently_named_cap():
    """Contrast case: net_delta_cap (0.05 NAV) is real and DOES fire — the
    live path is not unmanaged, it just doesn't use the
    max_net_long_exposure name or its 0.50 threshold. 10% net delta is far
    under the historical 0.50 cap yet still trips the live governor's much
    tighter 0.05 cap."""
    account = PaperAccount("phase1_diag_netdelta", CAPITAL_USDT)
    nav = account.nav_usdt()

    net_delta_usdt = 0.10 * nav  # 10% net long
    assert (net_delta_usdt / nav) < NAMED_MAX_NET_LONG_EXPOSURE_PORTFOLIO_V1_1

    decision = account.evaluate_risk(
        gross_usdt=0.0, net_delta_usdt=net_delta_usdt,
        venue_unsecured_frac={"binance": 0.0},
    )

    assert decision.state == "risk_reduced"
    assert "net_delta" in decision.reasons
