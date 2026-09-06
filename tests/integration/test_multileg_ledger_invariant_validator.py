"""tests/integration/test_multileg_ledger_invariant_validator.py -- Phase 4E
commit 12: the independent cross-ledger validator
(src/institutional/backtest/ledger_invariants.py) must both (a) pass a real,
correct MultiLegBacktester.run() output and (b) actually catch a broken one
-- proven here by re-injecting exactly the shape of inconsistency Phase 4D
commit 9 found (and commit 11 fixed) into a real result's portfolio_ledger,
and confirming the validator raises rather than silently passing.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.backtest.ledger_invariants import (
    LedgerInconsistencyError,
    raise_if_inconsistent,
    validate_terminal_ledger_coherence,
)


def _real_result(synthetic_load_enriched):
    from src.institutional.backtest.multileg_backtester import MultiLegBacktester, MultiLegConfig

    cfg = MultiLegConfig(enable_long=False, enable_carry=True, enable_hedge=False)
    bt = MultiLegBacktester(long_engines=[], config=cfg, carry_assets=["BTCUSDT"])
    return bt.run("2024-01-01", "2024-01-15")


def test_real_fixed_backtest_output_is_coherent(synthetic_load_enriched):
    res = _real_result(synthetic_load_enriched)
    report = raise_if_inconsistent(res)   # must not raise
    assert report.ok
    assert report.violations == []
    assert report.recomputed_gross_exposure == 0.0
    assert report.reported_cash == pytest.approx(report.recomputed_cash, abs=1e-6)


def test_stale_terminal_exposure_is_caught(synthetic_load_enriched):
    """Re-injects Phase 4D commit 9's exact bug shape: the terminal
    portfolio_ledger row reports nonzero exposure for a position
    leg_ledger already shows fully closed. The validator must reject
    this, not silently pass it."""
    res = _real_result(synthetic_load_enriched)
    assert not res.leg_ledger.empty, "scenario must actually open a carry position"

    corrupted = res.portfolio_ledger.copy(deep=True)
    corrupted.loc[corrupted.index[-1], "gross_exposure"] = 3.11
    corrupted.loc[corrupted.index[-1], "carry_exposure"] = 3.11
    broken = replace(res, portfolio_ledger=corrupted)

    report = validate_terminal_ledger_coherence(broken)
    assert not report.ok
    assert any("gross_exposure" in v for v in report.violations)

    with pytest.raises(LedgerInconsistencyError, match="gross_exposure"):
        raise_if_inconsistent(broken)


def test_omitted_terminal_cost_is_caught(synthetic_load_enriched):
    """A terminal cash figure that silently omits (or double-counts) a
    real cost must be rejected -- Commit 12's "aucun coût terminal omis
    ou déduit deux fois" requirement."""
    res = _real_result(synthetic_load_enriched)
    corrupted = res.portfolio_ledger.copy(deep=True)
    corrupted.loc[corrupted.index[-1], "cash"] += 500.0   # a real, un-explained $500
    corrupted.loc[corrupted.index[-1], "equity"] += 500.0
    broken = replace(res, portfolio_ledger=corrupted)

    report = validate_terminal_ledger_coherence(broken)
    assert not report.ok
    assert any(v.startswith("cash:") for v in report.violations)


def test_event_after_terminal_snapshot_is_caught(synthetic_load_enriched):
    """A leg_ledger exit_time later than portfolio_ledger's own last
    timestamp would mean an event portfolio_ledger's terminal snapshot
    never accounted for -- must be rejected."""
    res = _real_result(synthetic_load_enriched)
    assert not res.leg_ledger.empty

    corrupted_legs = res.leg_ledger.copy(deep=True)
    last_ts = res.portfolio_ledger.iloc[-1]["timestamp"]
    future_ts = str(last_ts + (last_ts - res.portfolio_ledger.iloc[0]["timestamp"]))
    corrupted_legs.loc[corrupted_legs.index[0], "exit_time"] = future_ts
    broken = replace(res, leg_ledger=corrupted_legs)

    report = validate_terminal_ledger_coherence(broken)
    assert not report.ok
    assert any("after the terminal portfolio_ledger" in v for v in report.violations)


def test_mixed_open_closed_legs_within_one_position_is_caught(synthetic_load_enriched):
    """A single position whose legs disagree about open/closed state at
    the terminal snapshot is structurally invalid regardless of what the
    aggregate exposure numbers happen to say."""
    res = _real_result(synthetic_load_enriched)
    assert len(res.leg_ledger) >= 2

    corrupted_legs = res.leg_ledger.copy(deep=True)
    corrupted_legs.loc[corrupted_legs.index[0], "exit_time"] = None
    broken = replace(res, leg_ledger=corrupted_legs)

    report = validate_terminal_ledger_coherence(broken)
    assert not report.ok
    assert any("mix of open and closed legs" in v for v in report.violations)
