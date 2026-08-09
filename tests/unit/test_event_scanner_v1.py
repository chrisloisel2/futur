"""
tests/unit/test_event_scanner_v1.py
─────────────────────────────────────────────────────────────────────────────
Event Scanner V1 (reports/EVENT_SCANNER_V1_PROTOCOL.md) mechanics, on
SYNTHETIC data only -- this project's real Data V2 corpus is not
DATA_V2_READY yet (see the protocol's "Order of operations"), so these
tests prove the detector/label/scanner CODE is correct, not that any
family has edge. Do not read these numbers as findings.

Includes the mutation tests requested in the 2026-08-10 pre-unblinding
review (round 3): editing the current bar must never move its own
threshold, editing a future bar must never move a past detection, editing
the market before an event's research_available_at must never move its
label, +shock/-shock must fade in opposite directions, and overlapping 1h
returns must be structurally impossible in the label builder.

Gate:
    python3 -m pytest tests/unit/test_event_scanner_v1.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.events.costs import compute_event_cost
from data_v2.events.detectors import (
    _apply_cooldown, _rolling_std, _trailing_percentile_rank, detect_crowding, detect_deleveraging,
    detect_forced_flow_reversal, detect_relative_value_dislocation,
)
from data_v2.events.labels import label_events, label_events_multi_symbol
from data_v2.events.residuals import compute_residual_returns
from data_v2.events.scanner import PRIMARY_CLASSIFICATION_HORIZON, build_family_report, _classify
from data_v2.events.schema import REQUIRED_COLUMNS, validate_schema

BAR_SECONDS = 300


def _research_available_at(idx: pd.DatetimeIndex) -> pd.Series:
    # close_time (open + 5m) + 5s ingestion margin, matching
    # data_v2.temporal.available_at's defaults. With this margin, bar t's
    # research_available_at (= open(t+1) + 5s) lands AFTER open(t+1), so
    # the first bar whose own open >= it is t+2, not t+1 -- correct
    # (entry can't happen at open(t+1) if the info wasn't available until
    # 5s after that), but makes hand-computed index arithmetic in tests
    # fiddly. Tests that need an exact "entry_idx == event_idx+1" use
    # _research_available_at_exact_close instead.
    return pd.Series(idx + pd.Timedelta(seconds=BAR_SECONDS + 5), index=idx)


def _research_available_at_exact_close(idx: pd.DatetimeIndex) -> pd.Series:
    # zero margin: bar t's research_available_at == open(t+1) exactly, so
    # entry_idx == event_idx+1 precisely -- used where tests need clean,
    # hand-verifiable bar-count arithmetic (non-overlapping-sum tests).
    return pd.Series(idx + pd.Timedelta(seconds=BAR_SECONDS), index=idx)


def _baseline_frame(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": idx,
        "research_available_at": _research_available_at(idx).to_numpy(),
        "symbol": "FOOUSDT",
        "close": 100 + np.cumsum(rng.normal(0, 0.05, n)),
        "oi": 1_000_000 + rng.normal(0, 1000, n).cumsum(),
        "oi_delta_pct_1h": rng.normal(0, 0.005, n),
        "aggressive_buy_usd": rng.uniform(1000, 5000, n),
        "aggressive_sell_usd": rng.uniform(1000, 5000, n),
        "signed_volume": rng.normal(0, 1000, n),
        "CVD": rng.normal(0, 1000, n).cumsum(),
        "funding_rate": rng.normal(0, 0.0001, n),
        "basis": rng.normal(0, 0.0005, n),
        "basis_z_1d": rng.normal(0, 0.5, n),
        "basis_z_7d": rng.normal(0, 0.5, n),
        "residual_logret_5m": rng.normal(0, 0.0005, n),
        "residual_return_15m": rng.normal(0, 0.001, n),
        "residual_return_1h": rng.normal(0, 0.002, n),
        "volume": rng.uniform(1e5, 5e5, n),
    })


# ── schema ──────────────────────────────────────────────────────────────


def test_schema_validates_required_columns():
    df = _baseline_frame(50)
    validate_schema(df)  # must not raise


def test_schema_rejects_missing_column():
    df = _baseline_frame(50).drop(columns=["oi"])
    with pytest.raises(ValueError):
        validate_schema(df)


def test_schema_requires_research_available_at():
    df = _baseline_frame(50).drop(columns=["research_available_at"])
    with pytest.raises(ValueError):
        validate_schema(df)


# ── detector helpers: causality + mutation tests ──────────────────────────


def test_trailing_percentile_rank_excludes_current_bar_from_its_own_threshold():
    # a spike at position 3 must NOT inflate its own rank via self-inclusion:
    # its rank is computed only against [t-3, t-2, t-1] = [1.0, 2.0, 3.0].
    s = pd.Series([1.0, 2.0, 3.0, 100.0, 4.0])
    ranks = _trailing_percentile_rank(s, window_bars=3)
    assert ranks.iloc[3] == pytest.approx(1.0)  # 100.0 > all of [1,2,3]
    # bar 4 (value 4.0) ranked against [2.0, 3.0, 100.0] -- NOT the max
    assert ranks.iloc[4] < 1.0


def test_mutation_editing_current_bar_never_changes_its_own_threshold():
    """Mutation test: the THRESHOLD a bar is compared against (its trailing
    std / the set of historical values its rank is computed from) must be
    a pure function of t-1..t-window -- never of the bar's own value. Note
    this is NOT "the rank stays the same": the rank of bar 30 necessarily
    changes when bar 30's own value changes (rank = current vs history,
    and current changed) -- that's correct, not a bug. What must NOT
    change is (a) the std computed FOR bar 30's own comparison, and (b)
    anything at bars strictly BEFORE 30, which never see bar 30 at all."""
    base = pd.Series(np.random.default_rng(0).normal(0, 1, 50))
    rank_a = _trailing_percentile_rank(base, window_bars=10)
    std_a = _rolling_std(base, window_bars=10)

    mutated = base.copy()
    mutated.iloc[30] = 9999.0  # wildly change bar 30's own value
    rank_b = _trailing_percentile_rank(mutated, window_bars=10)
    std_b = _rolling_std(mutated, window_bars=10)

    # bar 30's own threshold (std of bars 20..29) is unaffected by bar 30's
    # own value -- the causality guarantee _rolling_std exists to provide.
    assert std_a.iloc[30] == pytest.approx(std_b.iloc[30])
    # every bar strictly BEFORE 30 is completely untouched (its window
    # never reaches bar 30 at all -- true by construction, asserted as a
    # sanity check).
    pd.testing.assert_series_equal(rank_a.iloc[:30], rank_b.iloc[:30])
    pd.testing.assert_series_equal(std_a.iloc[:30], std_b.iloc[:30])
    # by contrast, bar 30's rank DOES (correctly) change: it now measures
    # 9999.0 against the same unchanged history.
    assert rank_a.iloc[30] != pytest.approx(rank_b.iloc[30])
    assert rank_b.iloc[30] == pytest.approx(1.0)  # 9999.0 exceeds all of its (unchanged) history


def test_mutation_editing_a_future_bar_never_changes_past_detection():
    df_a = _baseline_frame(500, seed=1)
    trigger_idx = 400
    df_a.loc[trigger_idx, "residual_return_1h"] = -0.05
    df_a.loc[trigger_idx, "oi_delta_pct_1h"] = -0.10
    df_a.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000
    df_a.loc[trigger_idx, "volume"] = 1e8

    result_a = detect_deleveraging(df_a, symbol="FOOUSDT")

    df_b = df_a.copy()
    df_b.loc[trigger_idx + 50, "residual_return_1h"] = 50.0  # huge, unrelated FUTURE mutation
    df_b.loc[trigger_idx + 50, "volume"] = 1e12
    result_b = detect_deleveraging(df_b, symbol="FOOUSDT")

    # detection at/before trigger_idx must be byte-identical regardless of
    # what happens 50 bars later
    early_a = result_a.events[result_a.events["timestamp"] <= df_a.loc[trigger_idx, "timestamp"]]
    early_b = result_b.events[result_b.events["timestamp"] <= df_a.loc[trigger_idx, "timestamp"]]
    pd.testing.assert_frame_equal(early_a.reset_index(drop=True), early_b.reset_index(drop=True))


def test_apply_cooldown_keeps_only_first_fire_in_window():
    mask = pd.Series([False, True, True, True, False, True, False])
    out = _apply_cooldown(mask, cooldown_bars=3)
    assert out.tolist() == [False, True, False, False, False, True, False]


# ── residuals ───────────────────────────────────────────────────────────


def test_residual_returns_btc_eth_are_raw_returns():
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(0)
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, n))), index=idx)
    eth = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.0012, n))), index=idx)
    out = compute_residual_returns({"BTCUSDT": btc, "ETHUSDT": eth}, window_bars=50, min_periods=5)
    np.testing.assert_allclose(
        out["BTCUSDT"]["residual_logret_5m"].to_numpy(),
        np.log(btc / btc.shift(1)).to_numpy(),
        equal_nan=True,
    )


def test_residual_returns_altcoin_is_hedged_not_raw():
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(1)
    btc_ret = rng.normal(0, 0.001, n)
    btc = pd.Series(100 * np.exp(np.cumsum(btc_ret)), index=idx)
    eth = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.0012, n))), index=idx)
    # ALT is literally 1.0x BTC + noise -- residual should be small, close~raw is not
    alt = pd.Series(10 * np.exp(np.cumsum(btc_ret + rng.normal(0, 0.0001, n))), index=idx)

    out = compute_residual_returns({"BTCUSDT": btc, "ETHUSDT": eth, "ALTUSDT": alt}, window_bars=100, min_periods=20)
    residual = out["ALTUSDT"]["residual_logret_5m"].dropna()
    raw_ret = np.log(alt / alt.shift(1)).dropna()
    # once betas have warmed up, residual should be MUCH smaller than raw return
    assert residual.abs().mean() < raw_ret.reindex(residual.index).abs().mean() * 0.5


def test_residual_returns_causal_no_future_leakage():
    """Mutation test: a shock injected far in the FUTURE must not change
    beta (and therefore residual) at any bar before it."""
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(2)
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, n))), index=idx)
    eth = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.0012, n))), index=idx)
    alt = pd.Series(10 * np.exp(np.cumsum(rng.normal(0, 0.001, n))), index=idx)

    out_a = compute_residual_returns({"BTCUSDT": btc, "ETHUSDT": eth, "ALTUSDT": alt}, window_bars=100, min_periods=20)

    alt_mut = alt.copy()
    alt_mut.iloc[350:] *= 5.0  # huge shock, far after bar 200
    out_b = compute_residual_returns({"BTCUSDT": btc, "ETHUSDT": eth, "ALTUSDT": alt_mut}, window_bars=100, min_periods=20)

    pd.testing.assert_series_equal(
        out_a["ALTUSDT"]["residual_return_1h"].iloc[:200],
        out_b["ALTUSDT"]["residual_return_1h"].iloc[:200],
    )


# ── DELEVERAGING ────────────────────────────────────────────────────────


def test_detect_deleveraging_fires_on_synthetic_extreme():
    df = _baseline_frame(500, seed=1)
    trigger_idx = 400
    df.loc[trigger_idx, "residual_return_1h"] = -0.05  # big down shock
    df.loc[trigger_idx, "oi_delta_pct_1h"] = -0.10
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000
    df.loc[trigger_idx, "volume"] = 1e8

    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert result.family == "DELEVERAGING"
    assert len(result.events) >= 1
    assert result.events["timestamp"].iloc[0] == df.loc[trigger_idx, "timestamp"]
    assert result.events["research_available_at"].iloc[0] == df.loc[trigger_idx, "research_available_at"]


def test_detect_deleveraging_silent_on_pure_noise():
    df = _baseline_frame(500, seed=2)
    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert len(result.events) == 0  # no synthetic shock injected -- should not fire


def test_detect_deleveraging_requires_all_four_conditions():
    df = _baseline_frame(500, seed=3)
    trigger_idx = 400
    # only the price shock, nothing else -- must NOT fire alone
    df.loc[trigger_idx, "residual_return_1h"] = -0.05
    result = detect_deleveraging(df, symbol="FOOUSDT")
    assert len(result.events) == 0


# ── CROWDING ────────────────────────────────────────────────────────────


def test_detect_crowding_fires_when_flow_confirms_crowded_side():
    df = _baseline_frame(2000, seed=4)
    trigger_idx = 1800
    df.loc[trigger_idx, "funding_rate"] = 0.01  # extreme positive (longs pay)
    df.loc[trigger_idx, "basis_z_1d"] = 3.0
    df.loc[trigger_idx, "oi_delta_pct_1h"] = 0.05
    df.loc[trigger_idx, "aggressive_buy_usd"] = 1_000_000  # buying INTO the crowded long side
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1000

    result = detect_crowding(df, symbol="FOOUSDT")
    assert len(result.events) >= 1
    assert result.events["crowded_side"].iloc[0] == "long"


def test_detect_crowding_silent_when_flow_fades_not_confirms():
    df = _baseline_frame(2000, seed=5)
    trigger_idx = 1800
    df.loc[trigger_idx, "funding_rate"] = 0.01
    df.loc[trigger_idx, "basis_z_1d"] = 3.0
    df.loc[trigger_idx, "oi_delta_pct_1h"] = 0.05
    df.loc[trigger_idx, "aggressive_buy_usd"] = 1000  # SELLING into a crowded long -> fading, not confirming
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000

    result = detect_crowding(df, symbol="FOOUSDT")
    assert len(result.events) == 0


def test_detect_crowding_captures_short_crowded_side():
    df = _baseline_frame(2000, seed=7)
    trigger_idx = 1800
    df.loc[trigger_idx, "funding_rate"] = -0.01  # extreme negative -> shorts crowded
    df.loc[trigger_idx, "basis_z_1d"] = -3.0
    df.loc[trigger_idx, "oi_delta_pct_1h"] = 0.05
    df.loc[trigger_idx, "aggressive_sell_usd"] = 1_000_000  # selling INTO the crowded short side
    df.loc[trigger_idx, "aggressive_buy_usd"] = 1000

    result = detect_crowding(df, symbol="FOOUSDT")
    assert len(result.events) >= 1
    assert result.events["crowded_side"].iloc[0] == "short"


# ── RELATIVE_VALUE_DISLOCATION ──────────────────────────────────────────


def test_detect_relative_value_dislocation_needs_cross_sectional_extremity():
    # 20 symbols, not 5: with too few symbols, one outlier inflates its own
    # cross-sectional std enough to cap its own z-score below any realistic
    # threshold -- 20 symbols is closer to the real ~300-symbol universe's
    # dilution.
    n = 1000
    panel = {f"SYM{i}USDT": _baseline_frame(n, seed=10 + i) for i in range(20)}
    trigger_idx = 900
    ts = panel["SYM0USDT"].loc[trigger_idx, "timestamp"]
    for sym, df in panel.items():
        df.loc[df["timestamp"] == ts, "residual_return_1h"] = 0.0
        df.loc[df["timestamp"] == ts, "basis_z_1d"] = 0.0
        df.loc[df["timestamp"] == ts, "signed_volume"] = 0.0
    # SYM0 alone dislocates: big positive residual, positive relative basis/flow
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "residual_return_1h"] = 0.05
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "basis_z_1d"] = 3.0
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "signed_volume"] = 1_000_000

    result = detect_relative_value_dislocation(panel)
    fired = result.events[result.events["symbol"] == "SYM0USDT"]
    assert len(fired) >= 1
    assert fired["trigger_residual_sign"].iloc[0] == pytest.approx(1.0)


def test_detect_relative_value_dislocation_captures_negative_sign():
    n = 1000
    panel = {f"SYM{i}USDT": _baseline_frame(n, seed=30 + i) for i in range(20)}
    trigger_idx = 900
    ts = panel["SYM0USDT"].loc[trigger_idx, "timestamp"]
    for sym, df in panel.items():
        df.loc[df["timestamp"] == ts, "residual_return_1h"] = 0.0
        df.loc[df["timestamp"] == ts, "basis_z_1d"] = 0.0
        df.loc[df["timestamp"] == ts, "signed_volume"] = 0.0
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "residual_return_1h"] = -0.05
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "basis_z_1d"] = -3.0
    panel["SYM0USDT"].loc[panel["SYM0USDT"]["timestamp"] == ts, "signed_volume"] = -1_000_000

    result = detect_relative_value_dislocation(panel)
    fired = result.events[result.events["symbol"] == "SYM0USDT"]
    assert len(fired) >= 1
    assert fired["trigger_residual_sign"].iloc[0] == pytest.approx(-1.0)


# ── FORCED_FLOW_REVERSAL ────────────────────────────────────────────────


def test_detect_forced_flow_reversal_fires_on_liquidation_and_oi_collapse():
    df = _baseline_frame(500, seed=6)
    df["liq_long_usd_5m"] = 0.0
    df["liq_short_usd_5m"] = 0.0
    trigger_idx = 400
    df.loc[trigger_idx, "liq_long_usd_5m"] = 5_000_000
    df.loc[trigger_idx, "oi_delta_pct_1h"] = -0.15
    df.loc[trigger_idx, "residual_return_15m"] = -0.06

    result = detect_forced_flow_reversal(df, symbol="FOOUSDT")
    assert len(result.events) >= 1
    assert result.events["trigger_residual_sign"].iloc[0] == pytest.approx(-1.0)


# ── labels: non-overlapping increments + research_available_at entry ──────


def test_label_events_sums_nonoverlapping_5m_increments():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    log_ret = np.zeros(n)
    event_idx = 50
    # entry_idx == event_idx+1 (research_available_at excludes the
    # triggering bar's own move) -- put a known log-return on each of the
    # next 12 bars after entry so 1h = sum of exactly those 12.
    entry_idx = event_idx + 1
    log_ret[entry_idx : entry_idx + 12] = 0.001  # 12 bars of +0.1% log-return each
    frame = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at_exact_close(idx).to_numpy(),
        "residual_logret_5m": log_ret, "close": np.full(n, 100.0),
    })
    events = pd.DataFrame({
        "timestamp": [idx[event_idx]], "research_available_at": [frame.loc[event_idx, "research_available_at"]],
        "symbol": ["FOOUSDT"],
    })

    labelled = label_events(events, frame, family="DELEVERAGING")
    expected_1h = np.expm1(0.001 * 12)
    assert labelled["residual_ret_1h"].iloc[0] == pytest.approx(expected_1h, abs=1e-9)


def test_label_events_entry_excludes_triggering_bars_own_move():
    """The exact bug from review: a move that PRODUCED the trigger must not
    also count as forward performance."""
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    log_ret = np.zeros(n)
    event_idx = 50
    log_ret[event_idx] = 0.05  # the huge move that (hypothetically) triggered the event
    frame = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at(idx).to_numpy(),
        "residual_logret_5m": log_ret, "close": np.full(n, 100.0),
    })
    events = pd.DataFrame({
        "timestamp": [idx[event_idx]], "research_available_at": [frame.loc[event_idx, "research_available_at"]],
        "symbol": ["FOOUSDT"],
    })

    labelled = label_events(events, frame, family="DELEVERAGING")
    # the triggering bar's own 5% move must NOT appear in any forward label
    assert labelled["residual_ret_15m"].iloc[0] == pytest.approx(0.0, abs=1e-9)


def test_mutation_editing_market_before_research_available_at_never_changes_label():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(0)
    log_ret_a = rng.normal(0, 0.001, n)
    event_idx = 50
    frame_a = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at(idx).to_numpy(),
        "residual_logret_5m": log_ret_a, "close": np.full(n, 100.0),
    })
    events = pd.DataFrame({
        "timestamp": [idx[event_idx]], "research_available_at": [frame_a.loc[event_idx, "research_available_at"]],
        "symbol": ["FOOUSDT"],
    })
    labelled_a = label_events(events, frame_a, family="DELEVERAGING")

    frame_b = frame_a.copy()
    frame_b.loc[:event_idx, "residual_logret_5m"] = 999.0  # mutate everything BEFORE (and including) the trigger
    labelled_b = label_events(events, frame_b, family="DELEVERAGING")

    for h in ("15m", "1h", "4h", "8h"):
        assert labelled_a[f"residual_ret_{h}"].iloc[0] == pytest.approx(labelled_b[f"residual_ret_{h}"].iloc[0])


def test_1h_overlapping_returns_impossible_in_label_builder():
    """Structural mutation test: 1h must be exactly 12 non-overlapping 5m
    increments (entry_idx : entry_idx+12), never a rolling/overlapping
    window. Verified by checking that changing a bar OUTSIDE that exact
    12-bar span never moves the 1h label."""
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    log_ret = np.zeros(n)
    event_idx = 100
    entry_idx = event_idx + 1
    frame = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at_exact_close(idx).to_numpy(),
        "residual_logret_5m": log_ret, "close": np.full(n, 100.0),
    })
    events = pd.DataFrame({
        "timestamp": [idx[event_idx]], "research_available_at": [frame.loc[event_idx, "research_available_at"]],
        "symbol": ["FOOUSDT"],
    })

    baseline = label_events(events, frame, family="DELEVERAGING")["residual_ret_1h"].iloc[0]

    # a move exactly ONE bar past the 12-bar 1h window (entry_idx+12) must
    # NOT affect the 1h label -- if the builder were overlapping (e.g.
    # rolling 1h sampled every 5m), it would.
    frame_edge = frame.copy()
    frame_edge.loc[entry_idx + 12, "residual_logret_5m"] = 0.05
    edge_ret = label_events(events, frame_edge, family="DELEVERAGING")["residual_ret_1h"].iloc[0]
    assert edge_ret == pytest.approx(baseline)

    # a move at entry_idx+11 (the LAST bar inside the window) DOES affect it
    frame_inside = frame.copy()
    frame_inside.loc[entry_idx + 11, "residual_logret_5m"] = 0.05
    inside_ret = label_events(events, frame_inside, family="DELEVERAGING")["residual_ret_1h"].iloc[0]
    assert inside_ret != pytest.approx(baseline)


def test_label_events_multi_symbol_groups_correctly():
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    ra = _research_available_at_exact_close(idx).to_numpy()
    log_ret_a = np.zeros(n)
    log_ret_a[63] = 0.02  # bar 63 = entry_idx (62+1) for event at 62
    panel = {
        "AUSDT": pd.DataFrame({"timestamp": idx, "research_available_at": ra, "residual_logret_5m": log_ret_a, "close": np.full(n, 100.0)}),
        "BUSDT": pd.DataFrame({"timestamp": idx, "research_available_at": ra, "residual_logret_5m": np.zeros(n), "close": np.full(n, 100.0)}),
    }
    events = pd.DataFrame({
        "timestamp": [idx[62], idx[62]], "research_available_at": [ra[62], ra[62]], "symbol": ["AUSDT", "BUSDT"],
    })

    labelled = label_events_multi_symbol(events, panel, family="DELEVERAGING")
    a_ret = labelled.loc[labelled["symbol"] == "AUSDT", "residual_ret_15m"].iloc[0]
    b_ret = labelled.loc[labelled["symbol"] == "BUSDT", "residual_ret_15m"].iloc[0]
    assert a_ret == pytest.approx(np.expm1(0.02), abs=1e-9)
    assert b_ret == pytest.approx(0.0, abs=1e-9)


# ── direction: conditional per event, mutation test for +/- shock symmetry ─


def _shock_frame_and_event(shock_log_ret: float, family: str, seed: int = 0):
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    event_idx = 50
    entry_idx = event_idx + 1
    log_ret = np.zeros(n)
    log_ret[entry_idx] = shock_log_ret  # the "market move after entry" being faded
    frame = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at_exact_close(idx).to_numpy(),
        "residual_logret_5m": log_ret, "close": np.full(n, 100.0),
    })
    row = {"timestamp": idx[event_idx], "research_available_at": frame.loc[event_idx, "research_available_at"], "symbol": "FOOUSDT"}
    if family == "CROWDING":
        row["crowded_side"] = "long"
    elif family in ("RELATIVE_VALUE_DISLOCATION", "FORCED_FLOW_REVERSAL"):
        row["trigger_residual_sign"] = 1.0
    return frame, pd.DataFrame([row])


@pytest.mark.parametrize("family", ["CROWDING", "RELATIVE_VALUE_DISLOCATION", "FORCED_FLOW_REVERSAL"])
def test_mutation_positive_and_negative_shock_fade_in_opposite_directions(family):
    frame_pos, events_pos = _shock_frame_and_event(+0.01, family)
    frame_neg, events_neg = _shock_frame_and_event(-0.01, family)

    ret_pos = label_events(events_pos, frame_pos, family=family)["residual_ret_15m"].iloc[0]
    ret_neg = label_events(events_neg, frame_neg, family=family)["residual_ret_15m"].iloc[0]

    # SAME crowded_side/trigger_residual_sign (both fixtures fixed to the
    # same captured trigger info) means the SAME direction is applied in
    # both cases -- a positive market move and a negative one must produce
    # opposite-signed labelled returns under one fixed direction.
    assert np.sign(ret_pos) != np.sign(ret_neg)
    assert np.sign(ret_pos) == -np.sign(ret_neg)


def test_crowding_direction_flips_with_crowded_side():
    frame, events_long = _shock_frame_and_event(+0.01, "CROWDING")
    events_short = events_long.copy()
    events_short["crowded_side"] = "short"

    ret_long_crowded = label_events(events_long, frame, family="CROWDING")["residual_ret_15m"].iloc[0]
    ret_short_crowded = label_events(events_short, frame, family="CROWDING")["residual_ret_15m"].iloc[0]
    assert np.sign(ret_long_crowded) == -np.sign(ret_short_crowded)


def test_relative_value_direction_flips_with_trigger_sign():
    frame, events_pos_trigger = _shock_frame_and_event(+0.01, "RELATIVE_VALUE_DISLOCATION")
    events_neg_trigger = events_pos_trigger.copy()
    events_neg_trigger["trigger_residual_sign"] = -1.0

    ret_a = label_events(events_pos_trigger, frame, family="RELATIVE_VALUE_DISLOCATION")["residual_ret_15m"].iloc[0]
    ret_b = label_events(events_neg_trigger, frame, family="RELATIVE_VALUE_DISLOCATION")["residual_ret_15m"].iloc[0]
    assert np.sign(ret_a) == -np.sign(ret_b)


def test_deleveraging_direction_is_always_long_regardless_of_captured_fields():
    frame, events = _shock_frame_and_event(+0.01, "DELEVERAGING")
    labelled = label_events(events, frame, family="DELEVERAGING")
    assert labelled["direction"].iloc[0] == 1


# ── cost model ──────────────────────────────────────────────────────────


def test_compute_event_cost_scales_with_tick_size_and_price():
    cost_x1_a, cost_x2_a = compute_event_cost(entry_price=100.0, tick_size=0.1, taker_fee_rate=0.0005)
    cost_x1_b, cost_x2_b = compute_event_cost(entry_price=100.0, tick_size=1.0, taker_fee_rate=0.0005)
    assert cost_x1_b > cost_x1_a  # coarser tick -> higher cost at the same price
    assert cost_x2_a == pytest.approx(cost_x1_a * 2)


def test_compute_event_cost_nan_on_invalid_inputs():
    c1, c2 = compute_event_cost(entry_price=0.0, tick_size=0.1)
    assert np.isnan(c1) and np.isnan(c2)


def test_label_events_populates_per_event_cost_when_tick_size_given():
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    frame = pd.DataFrame({
        "timestamp": idx, "research_available_at": _research_available_at(idx).to_numpy(),
        "residual_logret_5m": np.zeros(n), "close": np.full(n, 100.0),
    })
    events = pd.DataFrame({"timestamp": [idx[10]], "research_available_at": [frame.loc[10, "research_available_at"]], "symbol": ["FOOUSDT"]})

    labelled = label_events(events, frame, family="DELEVERAGING", tick_size=0.1)
    assert labelled["event_cost_x1"].iloc[0] > 0
    assert not np.isnan(labelled["event_cost_x1"].iloc[0])

    labelled_no_tick = label_events(events, frame, family="DELEVERAGING")
    assert np.isnan(labelled_no_tick["event_cost_x1"].iloc[0])  # never a silent flat default


# ── scanner: primary horizon + per-event cost ─────────────────────────────


def _fake_labelled_events(n: int, mean_ret_1h: float, seed: int = 0, years=(2024,), cost_x1: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = rng.choice(pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D"), size=n)
    df = pd.DataFrame({"timestamp": pd.to_datetime(dates, utc=True), "symbol": "FOOUSDT"})
    for h in ("15m", "1h", "4h", "8h"):
        df[f"residual_ret_{h}"] = rng.normal(mean_ret_1h, 0.01, n)
        df[f"MFE_{h}"] = df[f"residual_ret_{h}"].abs()
        df[f"MAE_{h}"] = -df[f"residual_ret_{h}"].abs()
    df["event_cost_x1"] = cost_x1
    df["event_cost_x2"] = cost_x1 * 2
    return df


def test_primary_classification_horizon_is_1h():
    assert PRIMARY_CLASSIFICATION_HORIZON == "1h"


def test_scanner_uses_per_event_cost_not_flat_constant():
    # two events with identical gross return but DIFFERENT per-event cost
    # must classify differently -- proves cost isn't a shared flat constant.
    cheap = _fake_labelled_events(500, mean_ret_1h=0.006, seed=1, years=(2022, 2023, 2024, 2025), cost_x1=0.0005)
    expensive = cheap.copy()
    expensive["event_cost_x1"] = 0.05  # absurdly high per-event cost
    expensive["event_cost_x2"] = 0.10

    report_cheap = build_family_report(cheap, family="DELEVERAGING")
    report_expensive = build_family_report(expensive, family="DELEVERAGING")
    assert report_cheap.by_horizon["1h"].net_expectancy_cost_x1 > report_expensive.by_horizon["1h"].net_expectancy_cost_x1
    assert report_expensive.classification in ("KILL", "WEAK")


def test_scanner_reports_stress_cost_separately_from_primary():
    events = _fake_labelled_events(200, mean_ret_1h=0.01, seed=2, cost_x1=0.0005)
    report = build_family_report(events, family="DELEVERAGING")
    stats = report.by_horizon["1h"]
    assert stats.net_expectancy_stress_cost_x1 != stats.net_expectancy_cost_x1
    assert stats.net_expectancy_stress_cost_x1 == pytest.approx(stats.gross_expectancy - 0.0030)


def test_scanner_classifies_strong_positive_edge_as_candidate():
    events = _fake_labelled_events(500, mean_ret_1h=0.02, seed=1, years=(2022, 2023, 2024, 2025), cost_x1=0.0005)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "CANDIDATE"


def test_scanner_classifies_negative_expectancy_as_kill():
    events = _fake_labelled_events(500, mean_ret_1h=-0.01, seed=2)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"


def test_scanner_classifies_small_n_as_kill_regardless_of_expectancy():
    events = _fake_labelled_events(30, mean_ret_1h=0.05, seed=3)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert "N=" in report.classification_reason or "pooled N" in report.classification_reason


def test_scanner_kills_on_sign_flip_across_years():
    pos_years = _fake_labelled_events(200, mean_ret_1h=0.05, seed=5, years=(2022,), cost_x1=0.0005)
    neg_years = _fake_labelled_events(200, mean_ret_1h=-0.01, seed=6, years=(2023,), cost_x1=0.0005)
    events = pd.concat([pos_years, neg_years], ignore_index=True)
    report = build_family_report(events, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert "sign" in report.classification_reason.lower()


def test_no_events_reports_kill_not_a_crash():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "residual_ret_15m", "residual_ret_1h",
                                   "residual_ret_4h", "residual_ret_8h", "MFE_15m", "MFE_1h",
                                   "MFE_4h", "MFE_8h", "MAE_15m", "MAE_1h", "MAE_4h", "MAE_8h"])
    report = build_family_report(empty, family="DELEVERAGING")
    assert report.classification == "KILL"
    assert report.n_total == 0
