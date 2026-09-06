"""tests/integration/test_multileg_terminal_ledger_coherence.py -- Phase 4E
commit 11: MultiLegBacktester.run()'s terminal portfolio_ledger row must
agree with leg_ledger about which legs are open/closed.

Uses the REAL `MultiLegBacktester.run()` (only `load_enriched` is patched,
via the same `synthetic_load_enriched` fixture already used by
tests/integration/test_portfolio_multileg.py -- no data/enriched/ on a
clean clone). A carry position opened early in the window and never
funding-gate-flipped (constant positive synthetic funding) is still open
when the main per-bar loop ends, so it is only closed by `run()`'s own
post-loop "close residuals" step -- exactly the case Phase 4D commit 9
found broken by direct source inspection (multileg_backtester.py:526 was
appending the terminal portfolio_ledger row DURING the loop, before that
close-residuals step ever ran).

Before the fix: leg_ledger shows both carry legs closed (exit_time set at
the window end) while portfolio_ledger's last row still reports
gross_exposure > 0 (as if the position were open) -- the same two-ledger
contradiction commit 9's replay hit for real
(data/manifests/carry_shadow_differential.jsonl, gross_exposure
legacy=3.11 vs truth=0 at the terminal event). After the fix, both
ledgers agree: the terminal portfolio_ledger row reports zero exposure
and cash/equity reflecting the closed position.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))


def _run_short_carry_window(synthetic_load_enriched):
    from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

    cfg = MultiLegConfig(enable_long=False, enable_carry=True, enable_hedge=False)
    bt = MultiLegBacktester(long_engines=[], config=cfg, carry_assets=["BTCUSDT"])
    # 14 days: > funding-gate warm-up (window_periods=21 funding-hour samples,
    # ~7 days), so a carry position opens a few days in; constant positive
    # synthetic funding_rate (conftest.py) never regime-flips it closed, so
    # it is still open when the main loop's own `grid` ends.
    return bt.run("2024-01-01", "2024-01-15")


def test_carry_position_still_open_at_window_end_is_residually_closed(synthetic_load_enriched):
    """Sanity check on the fixture/scenario itself, independent of the bug:
    a carry position really does open and is still open when the main
    per-bar loop reaches the last grid timestamp (i.e. this scenario
    actually exercises the post-loop residual-close path, not some other
    exit reason)."""
    res = _run_short_carry_window(synthetic_load_enriched)
    assert len(res.leg_ledger) == 2, "expected exactly one CARRY position (spot+perp legs)"
    assert res.leg_ledger["leg_type"].tolist() == ["CARRY_LONG_SPOT", "CARRY_SHORT_PERP"]


def test_terminal_ledgers_agree_on_open_closed_state(synthetic_load_enriched):
    """The actual regression test: after the fix, leg_ledger and the
    terminal portfolio_ledger row must not contradict each other about
    whether the residual position is open or closed."""
    res = _run_short_carry_window(synthetic_load_enriched)

    leg_ledger = res.leg_ledger
    last_row = res.portfolio_ledger.iloc[-1]

    # leg_ledger: both legs of the residual carry position are closed at
    # the window's own last grid timestamp.
    assert leg_ledger["exit_time"].notna().all(), "residual legs must be closed by run()'s own logic"
    last_grid_ts = str(res.equity.index[-1])
    assert (leg_ledger["exit_time"] == last_grid_ts).all()

    # portfolio_ledger's terminal row must reflect that same closed state:
    # zero gross/net exposure, no leftover unrealized PnL in equity vs cash.
    assert last_row["gross_exposure"] == 0.0, (
        f"terminal portfolio_ledger row reports gross_exposure="
        f"{last_row['gross_exposure']!r} but leg_ledger shows every leg closed "
        f"-- portfolio_ledger and leg_ledger disagree about open/closed state "
        f"(Phase 4D commit 9's terminal-ledger inconsistency)")
    assert last_row["net_exposure"] == 0.0
    assert last_row["carry_exposure"] == 0.0
    assert last_row["equity"] == last_row["cash"], (
        "with every leg closed there is no unrealized PnL left -- equity must "
        "equal cash at the terminal row")


def test_terminal_row_cash_matches_leg_ledger_realized_total(synthetic_load_enriched):
    """The terminal row's cash must be internally consistent with what
    leg_ledger itself says was realized -- initial_capital plus the sum of
    every leg's own net_pnl() (price + funding - costs, all already
    unmodified, existing PositionLeg methods), plus the separately
    tracked (not per-leg) borrow total. This is the "no cost omitted, none
    counted twice" property Phase 4E commit 12's validator also checks."""
    from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

    cfg = MultiLegConfig(enable_long=False, enable_carry=True, enable_hedge=False)
    bt = MultiLegBacktester(long_engines=[], config=cfg, carry_assets=["BTCUSDT"])
    res = bt.run("2024-01-01", "2024-01-15")

    last_row = res.portfolio_ledger.iloc[-1]
    realized_total = res.leg_ledger["net_pnl"].sum()
    # borrow_total: compared via portfolio_ledger's own (unrounded) column,
    # not res.pnl_by_type["borrow"] -- that dict is rounded to 2dp for
    # CarryBasisAdapter.decide()'s real tournament LedgerEvents and is
    # deliberately left untouched by Phase 4E (see multileg_backtester.py's
    # own comment above `pnl_by_type = {...}`).
    expected_cash = cfg.initial_capital + realized_total + last_row["borrow_total"]
    assert last_row["cash"] == pytest.approx(expected_cash, abs=1e-6)
