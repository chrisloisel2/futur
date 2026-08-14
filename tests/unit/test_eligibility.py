"""
tests/unit/test_eligibility.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, sections 6-11: eligibility masks depend ONLY on feature
existence, causal warmup, and (RVD) cross-sectional population size --
never on a return/PnL/label. These tests lock that in and exercise the
concrete failure modes each mask exists to catch: a value present without
its full warmup, a fallback path allowed to fire when its own data is
missing, and the standard "current bar judging itself" leakage class.

Gate:
    python3 -m pytest tests/unit/test_eligibility.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.events import eligibility as elig

TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _grid(start: str, n: int, freq: str = "5min") -> pd.DatetimeIndex:
    return pd.date_range(TS(start), periods=n, freq=freq)


def test_residual_std_30d_nan_before_full_window():
    grid = _grid("2024-01-01", elig.RESIDUAL_STD_WINDOW_BARS + 5)
    residual = pd.Series(np.random.RandomState(0).normal(size=len(grid)), index=grid)
    out = elig.residual_std_30d(residual)
    # fewer than a full 30d of PRIOR observations -- must be NaN throughout
    assert out.iloc[: elig.RESIDUAL_STD_WINDOW_BARS - 1].isna().all()


def test_residual_std_30d_non_nan_once_full_window_elapsed():
    grid = _grid("2024-01-01", elig.RESIDUAL_STD_WINDOW_BARS + 5)
    residual = pd.Series(np.random.RandomState(0).normal(size=len(grid)), index=grid)
    out = elig.residual_std_30d(residual)
    assert out.iloc[elig.RESIDUAL_STD_WINDOW_BARS:].notna().all()


def test_residual_std_30d_excludes_current_bar_not_circular():
    # a std computed INCLUDING the current bar would react to a single
    # huge outlier at that same bar; shift(1) means the outlier at row i
    # can only ever affect rows AFTER i, never row i's own std.
    grid = _grid("2024-01-01", elig.RESIDUAL_STD_WINDOW_BARS + 10)
    residual = pd.Series(0.001, index=grid)
    spike_idx = elig.RESIDUAL_STD_WINDOW_BARS
    residual.iloc[spike_idx] = 100.0
    out = elig.residual_std_30d(residual)
    assert out.iloc[spike_idx] == 0.0  # the spike hasn't happened yet, as far as this row's std is concerned
    assert out.iloc[spike_idx + 1] > 0.0  # the very next row now sees it


def test_funding_settlement_warmup_false_before_90d_elapsed():
    grid = pd.Series(_grid("2024-01-01", 40, freq="1D"))
    is_settlement = pd.Series([i % 3 == 0 for i in range(40)], index=grid.index)  # settlement every 3 days
    out = elig.funding_settlement_warmup(is_settlement, grid)
    assert not out.iloc[10]  # only ~10 days since first settlement


def test_funding_settlement_warmup_true_once_90d_elapsed():
    grid = pd.Series(_grid("2024-01-01", 200, freq="1D"))
    is_settlement = pd.Series([i % 3 == 0 for i in range(200)], index=grid.index)
    out = elig.funding_settlement_warmup(is_settlement, grid)
    assert out.iloc[150]  # well past 90 days since first settlement


def test_funding_settlement_warmup_false_when_no_settlement_ever_seen():
    grid = pd.Series(_grid("2024-01-01", 40, freq="1D"))
    is_settlement = pd.Series(False, index=grid.index)
    out = elig.funding_settlement_warmup(is_settlement, grid)
    assert not out.any()


def _base_panel(n: int) -> pd.DataFrame:
    grid = _grid("2024-01-01", n)
    return pd.DataFrame({
        "timestamp": grid, "open": 1.0, "close": 1.0, "volume": 1.0,
        "oi": 1.0, "oi_delta_pct_1h": 0.01, "aggressive_sell_usd": 1.0,
        "aggressive_buy_usd": 1.0, "signed_volume": 1.0,
        "residual_return_1h": 0.001, "residual_return_15m": 0.0005,
        "research_available_at": grid, "funding_rate": 0.0001,
        "funding_rate_percentile_90d": 0.5, "basis_z_1d": 0.1,
        "liq_feed_available": False,
    }, index=range(n))


def test_eligible_deleveraging_requires_all_columns_and_warmup():
    # residual_std_30d_col is the actual std VALUE series (NaN = warmup
    # not yet satisfied), not a boolean flag -- mirrors what
    # eligibility.residual_std_30d actually returns.
    panel = _base_panel(5)
    warmup_ok = pd.Series([0.01, 0.01, 0.01, 0.01, 0.01])
    out = elig.eligible_deleveraging(panel, warmup_ok)
    assert out.all()

    panel_missing_oi = panel.copy()
    panel_missing_oi.loc[2, "oi"] = np.nan
    out2 = elig.eligible_deleveraging(panel_missing_oi, warmup_ok)
    assert not out2.iloc[2] and out2.iloc[0] and out2.iloc[1]

    warmup_not_ready = pd.Series([np.nan, 0.01, 0.01, 0.01, 0.01])
    out3 = elig.eligible_deleveraging(panel, warmup_not_ready)
    assert not out3.iloc[0] and out3.iloc[1]


def test_eligible_deleveraging_does_not_require_liquidation():
    # liquidation stays OPTIONAL per protocol -- absence of a liq column
    # entirely must not affect eligibility.
    panel = _base_panel(3)
    assert "liq_long_usd_5m" not in panel.columns
    warmup_ok = pd.Series([True, True, True])
    out = elig.eligible_deleveraging(panel, warmup_ok)
    assert out.all()


def test_eligible_crowding_requires_funding_warmup_not_just_percentile_presence():
    panel = _base_panel(3)  # funding_rate_percentile_90d is non-null for all 3 rows
    warmup_all_false = pd.Series([False, False, False])
    out = elig.eligible_crowding(panel, warmup_all_false)
    assert not out.any()  # percentile alone (without the 90d elapsed-time proof) is not enough

    warmup_mixed = pd.Series([False, True, True])
    out2 = elig.eligible_crowding(panel, warmup_mixed)
    assert not out2.iloc[0] and out2.iloc[1] and out2.iloc[2]


def test_eligible_rvd_base_requires_own_columns_and_std_warmup():
    panel = _base_panel(3)
    warmup_ok = pd.Series([0.01, 0.01, 0.01])
    out = elig.eligible_rvd_base(panel, warmup_ok)
    assert out.all()

    panel_missing_flow = panel.copy()
    panel_missing_flow.loc[1, "signed_volume"] = np.nan
    out2 = elig.eligible_rvd_base(panel_missing_flow, warmup_ok)
    assert not out2.iloc[1]

    warmup_not_ready = pd.Series([np.nan, 0.01, 0.01])
    out3 = elig.eligible_rvd_base(panel, warmup_not_ready)
    assert not out3.iloc[0] and out3.iloc[1]


def test_eligible_ffr_flow_fallback_only_when_flow_itself_present():
    panel = _base_panel(3)
    panel.loc[1, "signed_volume"] = np.nan  # flow missing, liq feed also down (default False)
    out = elig.eligible_ffr(panel)
    assert out.iloc[0] and not out.iloc[1] and out.iloc[2]


def test_eligible_ffr_liq_feed_available_alone_is_sufficient_even_without_flow():
    panel = _base_panel(2)
    panel.loc[0, "liq_feed_available"] = True
    panel.loc[0, "signed_volume"] = np.nan  # flow missing, but liq feed is up
    out = elig.eligible_ffr(panel)
    assert out.iloc[0]  # liq path alone covers it


def test_eligible_ffr_neither_path_available_is_ineligible():
    panel = _base_panel(1)
    panel.loc[0, "signed_volume"] = np.nan
    panel.loc[0, "liq_feed_available"] = False
    out = elig.eligible_ffr(panel)
    assert not out.iloc[0]


def test_no_mask_reads_a_return_pnl_or_label_column():
    # structural guard: none of the actual DataFrame column lookups in
    # this module (bracket-indexed string literals) may reference an
    # economic-result column name -- scans string literals only, not
    # prose/docstrings (which legitimately discuss what must be avoided).
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(elig))
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    forbidden = {"pnl", "sharpe", "profit_factor", "win_rate", "return_1h_fwd", "mfe", "mae", "label"}
    hit = literals & forbidden
    assert not hit, f"eligibility.py must stay PnL-blind, found forbidden column literal(s): {hit}"
