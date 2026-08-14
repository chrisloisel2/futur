"""
tests/unit/test_build_event_feature_panel.py
─────────────────────────────────────────────────────────────────────────────
scripts/build_event_feature_panel.py: the canonical, causal per-(symbol,
timestamp) event feature panel join. Covers the mission's explicit
invariants for this step (section 10/11/14):
  - exact-timestamp joins only, never nearest-future
  - funding is a causal backward-only merge_asof (discrete settlement,
    never a fabricated 5m-repeated observation before its own settlement)
  - the dense 5m grid represents a real gap as an explicit NaN row, not a
    silently-skipped one (residuals.py/labels.py assume row-offset ==
    wall-clock offset)
  - research_available_at (the panel's feature_available_at) is the
    row-wise max of only the sources that actually contributed data
  - the future-mutation invariant: changing a LATER observation of any
    source must never change an EARLIER panel row

Gate:
    python3 -m pytest tests/unit/test_build_event_feature_panel.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import build_event_feature_panel as bep
from data_v2.events.schema import validate_schema


def _write_perp(base: Path, symbol: str, rows: pd.DataFrame) -> None:
    for y, chunk in rows.groupby(rows["timestamp"].dt.year):
        d = base / f"symbol={symbol}" / f"year={y}"
        d.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(d / "perp_5m.parquet", index=False)


def _write_basis(base: Path, symbol: str, rows: pd.DataFrame) -> None:
    for y, chunk in rows.groupby(rows["timestamp"].dt.year):
        d = base / f"symbol={symbol}" / f"year={y}"
        d.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(d / "basis_5m.parquet", index=False)


def _write_flow(base: Path, symbol: str, rows: pd.DataFrame) -> None:
    for y, chunk in rows.groupby(rows["timestamp"].dt.year):
        d = base / f"symbol={symbol}" / f"year={y}"
        d.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(d / "flow.parquet", index=False)


def _perp_rows(idx: pd.DatetimeIndex, close=None) -> pd.DataFrame:
    n = len(idx)
    c = close if close is not None else np.full(n, 100.0)
    return pd.DataFrame({"timestamp": idx, "open": c, "close": c, "volume": np.full(n, 1000.0)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bep, "PERP_DIR", tmp_path / "perp")
    monkeypatch.setattr(bep, "BASIS_DIR", tmp_path / "basis")
    monkeypatch.setattr(bep, "AGG_5M_DIR", tmp_path / "flow")
    monkeypatch.setattr(bep, "load_oi", lambda sym: None)
    monkeypatch.setattr(bep, "load_funding", lambda sym: None)
    return tmp_path


def _btc_eth_close(idx: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    n = len(idx)
    rng = np.random.default_rng(0)
    btc = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.0005, n))), index=idx)
    eth = pd.Series(50.0 * np.exp(np.cumsum(rng.normal(0, 0.0005, n))), index=idx)
    return btc, eth


NOW = pd.Timestamp("2026-08-11", tz="UTC")


# ── exact-match join, never nearest-future ────────────────────────────────


def test_basis_join_is_exact_timestamp_never_nearest(env):
    idx = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    # basis has a row 5 minutes AFTER a grid timestamp that has no exact
    # basis row of its own -- a nearest/asof join could wrongly attach it
    # to the earlier grid bar; an exact join must leave that bar NaN.
    missing_ts = idx[5]
    basis_ts = idx[6]  # the only basis row provided, one grid step later
    basis_rows = pd.DataFrame({
        "timestamp": [basis_ts], "perp_spot_basis": [0.01], "basis_z_1d": [1.0], "basis_z_7d": [1.0],
    })
    _write_basis(env / "basis", "FOOUSDT", basis_rows)

    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)

    row_missing = panel[panel["timestamp"] == missing_ts].iloc[0]
    row_present = panel[panel["timestamp"] == basis_ts].iloc[0]
    assert pd.isna(row_missing["basis"])  # never pulled from the neighbouring bar
    assert row_present["basis"] == pytest.approx(0.01)


def test_oi_join_is_exact_timestamp_never_nearest(env):
    idx = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    oi_rows = pd.DataFrame({"create_time": [idx[10]], "sum_open_interest": [500.0]})

    btc, eth = _btc_eth_close(idx)
    orig = bep.load_oi
    bep.load_oi = lambda sym: oi_rows
    try:
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_oi = orig

    assert pd.isna(panel.loc[panel["timestamp"] == idx[9], "oi"].iloc[0])
    assert pd.isna(panel.loc[panel["timestamp"] == idx[11], "oi"].iloc[0])
    assert panel.loc[panel["timestamp"] == idx[10], "oi"].iloc[0] == pytest.approx(500.0)


# ── dense grid: a real gap is an explicit NaN row, never silently skipped ──


def test_grid_is_dense_a_missing_perp_bar_is_a_nan_row_not_a_skipped_one(env):
    idx_full = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    idx_with_gap = idx_full.delete(10)  # drop one real bar in the middle
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx_with_gap))

    btc, eth = _btc_eth_close(idx_full)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)

    assert len(panel) == len(idx_full)  # the gap bar still occupies a row
    gap_row = panel[panel["timestamp"] == idx_full[10]].iloc[0]
    assert pd.isna(gap_row["close"])
    assert pd.isna(gap_row["research_available_at"])  # nothing contributed at that row


def test_oi_delta_pct_1h_is_nan_across_a_gap_not_a_false_baseline(env):
    idx = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    oi_idx = idx.delete(15)  # gap exactly 12 bars before row 27 (15+12=27)
    oi_rows = pd.DataFrame({"create_time": oi_idx, "sum_open_interest": np.full(len(oi_idx), 100.0)})
    orig = bep.load_oi
    bep.load_oi = lambda sym: oi_rows
    try:
        btc, eth = _btc_eth_close(idx)
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_oi = orig

    row27 = panel[panel["timestamp"] == idx[27]].iloc[0]
    assert pd.isna(row27["oi_delta_pct_1h"])  # baseline 12 bars back (idx[15]) is a real gap


# ── funding: causal discrete settlement, never a forward/nearest join ─────


def test_funding_forward_filled_only_after_its_own_settlement(env):
    idx = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    settle_ts = idx[10]
    funding_rows = pd.DataFrame({"timestamp": [settle_ts], "funding_rate": [0.0005], "mark_price": [100.0]})
    orig = bep.load_funding
    bep.load_funding = lambda sym: funding_rows
    try:
        btc, eth = _btc_eth_close(idx)
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_funding = orig

    before = panel[panel["timestamp"] < settle_ts]
    at_and_after = panel[panel["timestamp"] >= settle_ts]
    assert before["funding_rate"].isna().all()  # nothing before the real settlement
    assert np.allclose(at_and_after["funding_rate"].to_numpy(), 0.0005)
    at_row = panel[panel["timestamp"] == settle_ts].iloc[0]
    assert at_row["funding_is_settlement"] == True  # noqa: E712
    next_row = panel[panel["timestamp"] == idx[11]].iloc[0]
    assert next_row["funding_is_settlement"] == False  # noqa: E712
    assert next_row["time_since_last_funding"] == pd.Timedelta(minutes=5)


def test_funding_never_leaks_a_future_settlement_backward(env):
    idx = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    future_settle = idx[25]
    funding_rows = pd.DataFrame({"timestamp": [future_settle], "funding_rate": [0.01], "mark_price": [100.0]})
    orig = bep.load_funding
    bep.load_funding = lambda sym: funding_rows
    try:
        btc, eth = _btc_eth_close(idx)
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_funding = orig

    early_row = panel[panel["timestamp"] == idx[0]].iloc[0]
    assert pd.isna(early_row["funding_rate"])  # the 0.01 settlement must never appear this early


def test_funding_settlement_jitter_floors_to_its_own_started_bar(env):
    """A settlement posted a few ms after the canonical mark (e.g.
    16:00:00.003, the real observed pattern) belongs to the 5m bar that
    had already started -- never floored/matched into a LATER bar."""
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    jittered = idx[5] + pd.Timedelta(milliseconds=3)
    funding_rows = pd.DataFrame({"timestamp": [jittered], "funding_rate": [0.002], "mark_price": [100.0]})
    orig = bep.load_funding
    bep.load_funding = lambda sym: funding_rows
    try:
        btc, eth = _btc_eth_close(idx)
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_funding = orig

    row = panel[panel["timestamp"] == idx[5]].iloc[0]
    assert row["funding_is_settlement"] == True  # noqa: E712
    assert row["funding_rate"] == pytest.approx(0.002)
    assert row["time_since_last_funding"] >= pd.Timedelta(0)  # never negative despite the ms jitter


# ── funding_rate_percentile_90d: settlement-native, not bar-repeated ──────
# (mission section 11 / external review 2026-08-14)


def test_settlement_percentile_rank_basic_correctness():
    times = pd.date_range("2024-01-01", periods=5, freq="8h", tz="UTC")
    abs_rate = pd.Series([0.01, 0.05, 0.03, 0.02, 0.09], index=times)
    ranks = bep._settlement_percentile_rank(abs_rate)
    assert pd.isna(ranks.iloc[0])  # nothing prior yet
    assert ranks.iloc[1] == pytest.approx(1.0)   # 0.05 > only prior value 0.01 -> rank 1.0
    assert ranks.iloc[2] == pytest.approx(0.5)   # 0.03 vs {0.01, 0.05} -> 1/2 <= it
    assert ranks.iloc[3] == pytest.approx(1 / 3)  # 0.02 vs {0.01, 0.05, 0.03} -> only 0.01 <= it


def test_settlement_percentile_rank_excludes_current_from_its_own_window():
    times = pd.date_range("2024-01-01", periods=3, freq="8h", tz="UTC")
    abs_rate = pd.Series([0.01, 0.01, 100.0], index=times)  # a huge outlier last
    ranks = bep._settlement_percentile_rank(abs_rate)
    # the outlier's own value must never appear in ITS OWN reference window
    assert ranks.iloc[2] == pytest.approx(1.0)  # both priors (0.01, 0.01) <= 100.0
    assert ranks.iloc[1] == pytest.approx(1.0)  # only prior is 0.01 <= 0.01


def test_settlement_percentile_rank_respects_the_90_day_window_boundary():
    times = pd.DatetimeIndex([
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=91),  # outside the 90d window
        pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=95),
    ])
    abs_rate = pd.Series([0.5, 0.01, 0.02], index=times)
    ranks = bep._settlement_percentile_rank(abs_rate)
    # the huge 0.5 from day 0 is > 90d before day 95 -- must be excluded from its window
    assert ranks.iloc[2] == pytest.approx(1.0)  # only 0.01 (day 91, within 90d of day 95) <= 0.02


def test_funding_rate_percentile_90d_forward_filled_like_funding_rate(env):
    idx = pd.date_range("2024-01-01", periods=30, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    funding_rows = pd.DataFrame({
        "timestamp": [idx[5], idx[20]], "funding_rate": [0.0001, 0.0002], "mark_price": [100.0, 101.0],
    })
    orig = bep.load_funding
    bep.load_funding = lambda sym: funding_rows
    try:
        btc, eth = _btc_eth_close(idx)
        panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    finally:
        bep.load_funding = orig

    before_first = panel[panel["timestamp"] < idx[5]]
    assert before_first["funding_rate_percentile_90d"].isna().all()
    at_first = panel[panel["timestamp"] == idx[5]].iloc[0]
    assert pd.isna(at_first["funding_rate_percentile_90d"])  # nothing prior to rank the first settlement against
    at_second = panel[panel["timestamp"] == idx[20]].iloc[0]
    assert at_second["funding_rate_percentile_90d"] == pytest.approx(1.0)  # 0.0002 > the only prior, 0.0001
    between = panel[(panel["timestamp"] > idx[5]) & (panel["timestamp"] < idx[20])]
    # forward-filled from the first settlement's own (NaN, nothing prior) rank
    assert between["funding_rate_percentile_90d"].isna().all()


# ── research_available_at (feature_available_at): row-wise max of only
# the sources that actually contributed ────────────────────────────────


def test_research_available_at_is_nan_when_nothing_contributed(env):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    gap_idx = idx.delete(4)
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(gap_idx))
    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    gap_row = panel[panel["timestamp"] == idx[4]].iloc[0]
    assert pd.isna(gap_row["research_available_at"])


def test_research_available_at_reflects_a_source_that_arrives_after_perps_own_bar_close(env):
    """basis's own research_available_at uses the same bar-close+margin
    profile as perp -- both real here, so research_available_at must be
    >= the row's own timestamp (never claims same-instant knowledge of a
    bar's close)."""
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    basis_rows = pd.DataFrame({
        "timestamp": idx, "perp_spot_basis": np.full(len(idx), 0.001),
        "basis_z_1d": np.full(len(idx), 0.0), "basis_z_7d": np.full(len(idx), 0.0),
    })
    _write_basis(env / "basis", "FOOUSDT", basis_rows)
    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    assert (panel["research_available_at"] >= panel["timestamp"]).all()


# ── future-mutation invariant (mission section 14) ─────────────────────


def test_future_mutation_of_any_source_never_changes_an_earlier_panel_row(env):
    idx = pd.date_range("2024-01-01", periods=40, freq="5min", tz="UTC")
    perp_rows = _perp_rows(idx)
    _write_perp(env / "perp", "FOOUSDT", perp_rows)
    basis_rows = pd.DataFrame({
        "timestamp": idx, "perp_spot_basis": np.linspace(0.0, 0.01, len(idx)),
        "basis_z_1d": np.zeros(len(idx)), "basis_z_7d": np.zeros(len(idx)),
    })
    _write_basis(env / "basis", "FOOUSDT", basis_rows)
    flow_rows = pd.DataFrame({
        "timestamp": idx, "aggressive_buy_usd": np.full(len(idx), 1000.0),
        "aggressive_sell_usd": np.full(len(idx), 900.0),
        "signed_volume": np.full(len(idx), 100.0), "CVD": np.cumsum(np.full(len(idx), 100.0)),
    })
    _write_flow(env / "flow", "FOOUSDT", flow_rows)
    oi_rows = pd.DataFrame({"create_time": idx, "sum_open_interest": np.linspace(100.0, 200.0, len(idx))})
    funding_rows = pd.DataFrame({
        "timestamp": [idx[3], idx[15], idx[27]], "funding_rate": [0.0001, 0.0002, 0.0003],
        "mark_price": [100.0, 101.0, 102.0],
    })
    bep.load_oi = lambda sym: oi_rows
    bep.load_funding = lambda sym: funding_rows
    try:
        btc, eth = _btc_eth_close(idx)
        baseline = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)

        cutoff = idx[20]

        # mutate every source strictly AFTER cutoff
        perp_mut = perp_rows.copy()
        perp_mut.loc[perp_mut["timestamp"] > cutoff, ["open", "close"]] = 999.0
        _write_perp(env / "perp", "FOOUSDT", perp_mut)
        basis_mut = basis_rows.copy()
        basis_mut.loc[basis_mut["timestamp"] > cutoff, "perp_spot_basis"] = 999.0
        _write_basis(env / "basis", "FOOUSDT", basis_mut)
        flow_mut = flow_rows.copy()
        flow_mut.loc[flow_mut["timestamp"] > cutoff, "signed_volume"] = 999.0
        _write_flow(env / "flow", "FOOUSDT", flow_mut)
        oi_mut = oi_rows.copy()
        oi_mut.loc[oi_mut["create_time"] > cutoff, "sum_open_interest"] = 999.0
        bep.load_oi = lambda sym: oi_mut
        funding_mut = pd.DataFrame({
            "timestamp": [idx[3], idx[15], idx[35]], "funding_rate": [0.0001, 0.0002, 0.9999],
            "mark_price": [100.0, 101.0, 999.0],
        })
        bep.load_funding = lambda sym: funding_mut
        btc_mut, eth_mut = btc.copy(), eth.copy()
        btc_mut.loc[btc_mut.index > cutoff] = 999.0
        eth_mut.loc[eth_mut.index > cutoff] = 999.0

        mutated = bep.build_symbol_panel("FOOUSDT", btc_close=btc_mut, eth_close=eth_mut, now=NOW)
    finally:
        bep.load_oi = lambda sym: None
        bep.load_funding = lambda sym: None

    early_baseline = baseline[baseline["timestamp"] <= cutoff].reset_index(drop=True)
    early_mutated = mutated[mutated["timestamp"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(early_baseline, early_mutated)


# ── liquidation feed: documented absence, never a fabricated 0 ─────────


def test_liq_feed_available_is_always_false_documented_gap(env):
    idx = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    assert (panel["liq_feed_available"] == False).all()  # noqa: E712


# ── schema conformance end to end ───────────────────────────────────────


def test_built_panel_conforms_to_the_event_scanner_schema(env):
    idx = pd.date_range("2024-01-01", periods=50, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)
    validate_schema(panel)  # must not raise


def test_residual_return_1h_requires_the_full_60_day_warmup_not_20_bars(env):
    """Bug found 2026-08-14: compute_residual_returns' own default
    min_periods (BETA_MIN_PERIODS=20, documented in residuals.py as a
    test-only shortcut) was silently inherited by this production call
    site, so residual_return_1h started populating after ~100 minutes
    instead of a full 60-day warmup -- contradicting the mission's
    "full warmup required, never a premature value" rule. The panel
    builder must pass an explicit full-window min_periods."""
    n = 60 * 288 + 500  # a bit more than 60 days of 5m bars
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    _write_perp(env / "perp", "FOOUSDT", _perp_rows(idx))
    btc, eth = _btc_eth_close(idx)
    panel = bep.build_symbol_panel("FOOUSDT", btc_close=btc, eth_close=eth, now=NOW)

    warmup_bars = 60 * 288
    early = panel.iloc[: int(warmup_bars * 0.9)]
    assert early["residual_return_1h"].isna().all()  # no beta computed yet -- genuinely unavailable, not fabricated
    later = panel.iloc[warmup_bars:]
    assert later["residual_return_1h"].notna().any()  # warmup eventually completes and residuals populate


def test_no_perp_data_returns_none(env):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    btc, eth = _btc_eth_close(idx)
    assert bep.build_symbol_panel("NOPERPUSDT", btc_close=btc, eth_close=eth, now=NOW) is None
