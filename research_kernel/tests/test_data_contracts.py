from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research_kernel.data_contracts import (
    CONTRACTS,
    DataContractError,
    Manifest,
    assert_no_lookahead,
    check_frame,
    latency_gate,
    schema_hash,
)


def good(n=100):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5T", tz="UTC"),
            "symbol": "BTCUSDT",
            "value": np.arange(n, dtype=float),
            "source_latency_ms": 120.0,
        }
    )


def manifest(**over):
    payload = dict(
        dataset_id="demo",
        source="test",
        created_at="2026-09-10",
        start="2026-01-01",
        end="2026-01-02",
        point_in_time=True,
        latency_assumption_ms=200.0,
    )
    payload.update(over)
    return Manifest(**payload)


def test_a_clean_frame_passes():
    rep = check_frame(good(), "demo", manifest=manifest())
    assert rep.passed, rep.violations


def test_a_placeholder_column_is_a_violation():
    df = good()
    df["taker_buy_volume"] = 0.0
    rep = check_frame(df, "demo", manifest=manifest())
    assert any("placeholder" in v for v in rep.violations)


def test_a_constant_column_is_a_violation():
    df = good()
    df["oi"] = 7.0
    rep = check_frame(df, "demo", manifest=manifest())
    assert any("constant" in v for v in rep.violations)


def test_meta_columns_may_be_constant():
    rep = check_frame(good(), "demo", manifest=manifest())
    assert not any("source_latency_ms" in v for v in rep.violations)


def test_duplicates_are_a_violation():
    df = pd.concat([good(10), good(10)]).sort_values("timestamp")
    rep = check_frame(df, "demo", manifest=manifest())
    assert any("duplicated" in v for v in rep.violations)


def test_non_monotonic_timestamps_are_a_violation():
    df = good(10)
    df.loc[5, "timestamp"] = pd.Timestamp("2020-01-01", tz="UTC")
    rep = check_frame(df, "demo", manifest=manifest())
    assert any("monotonic" in v for v in rep.violations)


def test_a_survivorship_universe_is_a_violation():
    rep = check_frame(good(), "demo", manifest=manifest(point_in_time=False))
    assert any("point-in-time" in v for v in rep.violations)


def test_an_unknown_availability_delay_is_a_violation():
    df = good().drop(columns=["source_latency_ms"])
    rep = check_frame(df, "demo", manifest=manifest(latency_assumption_ms=None))
    assert any("availability delay is unknown" in v for v in rep.violations)


def test_an_assumed_latency_is_a_warning_not_a_measurement():
    df = good().drop(columns=["source_latency_ms"])
    rep = check_frame(df, "demo", manifest=manifest())
    assert rep.passed
    assert any("assumption" in w for w in rep.warnings)


def test_measured_latency_beats_the_assumption():
    df = good(20)
    df["event_ts_ns"] = df["timestamp"].astype("int64")
    df["receive_ts_ns"] = df["event_ts_ns"] + 250_000_000
    rep = check_frame(df, "demo", manifest=manifest())
    assert rep.measured_latency_ms == pytest.approx(250.0)


def test_data_received_before_it_happened_is_a_violation():
    df = good(20)
    df["event_ts_ns"] = df["timestamp"].astype("int64")
    df["receive_ts_ns"] = df["event_ts_ns"] - 1_000_000
    rep = check_frame(df, "demo", manifest=manifest())
    assert any("before they happened" in v for v in rep.violations)


def test_an_undocumented_gap_is_a_violation():
    df = good(20)
    df.loc[10:, "timestamp"] = df.loc[10:, "timestamp"] + pd.Timedelta(days=3)
    rep = check_frame(df, "demo", manifest=manifest(), max_gap_seconds=600)
    assert any("undocumented" in v for v in rep.violations)


def test_a_declared_gap_is_a_warning():
    df = good(20)
    df.loc[10:, "timestamp"] = df.loc[10:, "timestamp"] + pd.Timedelta(days=3)
    m = manifest(known_gaps=[["2026-01-01", "2026-01-04"]])
    rep = check_frame(df, "demo", manifest=m, max_gap_seconds=600)
    assert rep.passed
    assert any("known gaps" in w for w in rep.warnings)


def test_an_empty_dataset_is_a_violation():
    rep = check_frame(good().head(0), "demo")
    assert any("empty" in v for v in rep.violations)


def test_a_missing_contract_column_is_a_violation():
    rep = check_frame(good(), "demo", contract="orderbook_l1", manifest=manifest())
    assert any("missing contract columns" in v for v in rep.violations)


def test_the_latency_gate_is_the_horizon_over_four():
    assert latency_gate(100.0, 15_000.0) is None
    assert "exceeds the budget" in latency_gate(20_000.0, 15_000.0)
    assert latency_gate(None, 15_000.0) == "latency unknown"


def test_lookahead_is_refused():
    df = pd.DataFrame(
        {
            "decision_ts": pd.date_range("2026-01-01", periods=3, freq="1D", tz="UTC"),
            "available_at": pd.date_range("2026-01-02", periods=3, freq="1D", tz="UTC"),
        }
    )
    with pytest.raises(DataContractError, match="published after"):
        assert_no_lookahead(df)


def test_contracts_are_declared():
    assert set(CONTRACTS) == {"bar", "trade", "orderbook_l1", "orderbook_l2", "positioning"}
    assert schema_hash(["b", "a"]) == schema_hash(["a", "b"])


def test_a_manifest_round_trips(tmp_path):
    p = manifest().write(tmp_path / "m.json")
    assert Manifest.read(p).dataset_id == "demo"
