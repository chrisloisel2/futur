import json
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_foundry_v5.planes.derivatives import DerivativesPlane
from alpha_foundry_v5.planes.event_trade import EventTradePlane
from alpha_foundry_v5.planes.replay import iter_merged_records
from alpha_foundry_v5.planes.tensor import build_multimodal_market_tensor


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def test_generic_replay_repairs_physical_inversion_and_merges_dates(tmp_path):
    root = tmp_path / "market"
    p1 = root / "raw/trades/venue=okx/symbol=BTCUSDT/date=2026-08-16/events.jsonl"
    p2 = root / "raw/trades/venue=okx/symbol=BTCUSDT/date=2026-08-17/events.jsonl"
    _write_jsonl(
        p1,
        [
            {"receive_ts_ns": 300, "event_ts_ns": 290, "venue": "okx", "symbol": "BTCUSDT"},
            {"receive_ts_ns": 200, "event_ts_ns": 190, "venue": "okx", "symbol": "BTCUSDT"},
        ],
    )
    _write_jsonl(
        p2,
        [{"receive_ts_ns": 250, "event_ts_ns": 240, "venue": "okx", "symbol": "BTCUSDT"}],
    )
    rows = list(iter_merged_records(str(root), "trades", 100, 400, ["okx"], ["BTCUSDT"]))
    assert [int(row["receive_ts_ns"]) for row in rows] == [200, 250, 300]
    assert all(row["_source_kind"] == "trades" for row in rows)


def test_event_trade_plane_keeps_remove_and_cancel_distinct_and_preserves_modality():
    plane = EventTradePlane(100, ["okx"], ["BTCUSDT"], time_windows_ms=[100, 500], event_windows=[2, 3])
    plane.ingest({"_source_kind": "book_events", "venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": 10, "source_stream": "books", "event_type": "snapshot", "side": "bid"})
    plane.ingest({"_source_kind": "book_events", "venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": 70, "source_stream": "books", "event_type": "remove", "side": "bid"})
    plane.ingest({"_source_kind": "book_events", "venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": 80, "source_stream": "books", "event_type": "cancel", "side": "ask"})
    plane.ingest({"_source_kind": "trades", "venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": 90, "price": 100.0, "qty": 2.0, "aggressor": "buy", "granularity": "individual"})
    plane.ingest({"_source_kind": "trades", "venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": 95, "price": 100.1, "qty": 1.0, "aggressor": "sell", "granularity": "aggregate"})
    plane.advance(100)
    state = plane.state(100, "BTCUSDT")
    assert state["okx__bid_remove_count_100ms"] == 1.0
    assert state["okx__ask_cancel_count_100ms"] == 1.0
    assert state["okx__removal_imbalance_100ms"] == -1.0
    assert state["okx__cancellation_imbalance_100ms"] == 1.0
    assert state["okx__trade_count_100ms"] == 2.0
    assert state["okx__individual_fraction_100ms"] == 0.5
    assert state["okx__aggregate_fraction_100ms"] == 0.5
    assert int(state["okx__trade_available_ts_ns"]) == 95
    assert int(state["okx__book_event_available_ts_ns"]) == 80


def test_event_count_windows_are_clock_invariant_until_new_trade():
    plane = EventTradePlane(100, ["bybit"], ["ETHUSDT"], time_windows_ms=[100], event_windows=[2])
    for ts, px, side in [(10, 100.0, "buy"), (20, 101.0, "sell"), (30, 102.0, "buy")]:
        plane.ingest({"_source_kind": "trades", "venue": "bybit", "symbol": "ETHUSDT", "receive_ts_ns": ts, "price": px, "qty": 1.0, "aggressor": side, "granularity": "individual"})
    plane.advance(100)
    a = plane.state(100, "ETHUSDT")
    plane.advance(200)
    b = plane.state(200, "ETHUSDT")
    assert a["bybit__signed_notional_last2"] == b["bybit__signed_notional_last2"]
    assert a["bybit__impact_bps_last2"] == b["bybit__impact_bps_last2"]


def test_derivatives_plane_builds_basis_oi_liquidation_and_explicit_funding_clock():
    plane = DerivativesPlane(["bybit"], ["BTCUSDT"], liquidation_windows_ms=[30000])
    base = {"_source_kind": "derivatives", "venue": "bybit", "symbol": "BTCUSDT"}
    plane.ingest(dict(base, receive_ts_ns=10, event_ts_ns=9, kind="open_interest", value=100.0))
    plane.ingest(dict(base, receive_ts_ns=20, event_ts_ns=19, kind="open_interest", value=110.0))
    plane.ingest(dict(base, receive_ts_ns=30, event_ts_ns=29, kind="mark", value=101.0))
    plane.ingest(dict(base, receive_ts_ns=35, event_ts_ns=34, kind="index", value=100.0))
    plane.ingest(dict(base, receive_ts_ns=40, event_ts_ns=39, kind="funding", value=0.0001, next_funding_ts_ns=1_000_000_040))
    plane.ingest(dict(base, receive_ts_ns=50, event_ts_ns=49, kind="liquidation", value=1000.0, side="long"))
    plane.advance(100)
    state = plane.state(100, "BTCUSDT")
    assert abs(state["bybit__open_interest_change_pct"] - 0.1) < 1e-12
    assert abs(state["bybit__basis_bps"] - 100.0) < 1e-9
    assert state["bybit__liquidation_notional_30000ms"] == 1000.0
    assert state["bybit__long_liquidation_notional_30000ms"] == 1000.0
    assert "bybit__funding_clock_seconds" in state
    assert int(state["bybit__funding_clock_available_ts_ns"]) == 40
    # OI event deltas are one-shot evidence, not stale values forward-filled
    # into every 100ms research row.
    later = plane.state(200, "BTCUSDT")
    assert "bybit__open_interest_change_pct" not in later
    assert "deriv__median_oi_change_pct" not in later


def _synthetic_base_tape(root: Path):
    start = 1_000_000_000
    cadence_ns = 100_000_000
    stop = start + 2 * cadence_ns
    rows = []
    for i, asof in enumerate([start + cadence_ns, start + 2 * cadence_ns]):
        rows.append(
            {
                "asof_ns": asof,
                "symbol": "BTCUSDT",
                "cadence_ms": 100,
                "price_fair_value": 100.0 + i,
                "price_ready": True,
                "okx__price_mid": 100.0 + i,
                "okx__price_dislocation_bps": 0.1,
                "okx__price_receive_age_ms": 10.0,
                "okx__depth_receive_age_ms": 20.0,
                "okx__queue_imbalance_l1": 0.2,
                "okx__bid_depth_5bps": 10.0,
                "okx__buy_notional_10bps": 100000.0,
                "okx__sell_notional_10bps": 100000.0,
                "okx__spread_bps": 1.0,
            }
        )
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(root / "part-00000.parquet", index=False)
    (root / "_SUCCESS").write_text("ok\n")
    (root / "SUMMARY.json").write_text(
        json.dumps(
            {
                "window": {"started_ns": start, "stopped_ns": stop},
                "streaming": {"cadence_ms": 100},
                "venues": ["okx"],
                "symbols": ["BTCUSDT"],
            }
        )
    )
    return start, stop


def _synthetic_raw(root: Path, start: int):
    def path(kind):
        return root / ("raw/%s/venue=okx/symbol=BTCUSDT/date=2026-08-16/events.jsonl" % kind)

    _write_jsonl(
        path("book_events"),
        [
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 10_000_000, "event_ts_ns": start + 9_000_000, "source_stream": "books", "event_type": "snapshot", "side": "bid", "price": 99.0, "qty": 1.0},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 60_000_000, "event_ts_ns": start + 59_000_000, "source_stream": "books", "event_type": "remove", "side": "bid", "price": 99.0, "qty": 0.0},
        ],
    )
    _write_jsonl(
        path("trades"),
        [
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 70_000_000, "event_ts_ns": start + 69_000_000, "price": 100.0, "qty": 1.0, "aggressor": "buy", "granularity": "individual"},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 150_000_000, "event_ts_ns": start + 149_000_000, "price": 101.0, "qty": 1.0, "aggressor": "sell", "granularity": "individual"},
        ],
    )
    next_funding = start + 10_000_000_000
    _write_jsonl(
        path("derivatives"),
        [
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 20_000_000, "event_ts_ns": start + 19_000_000, "kind": "open_interest", "value": 100.0},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 30_000_000, "event_ts_ns": start + 29_000_000, "kind": "funding", "value": 0.0001, "next_funding_ts_ns": next_funding},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 40_000_000, "event_ts_ns": start + 39_000_000, "kind": "mark", "value": 101.0},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 50_000_000, "event_ts_ns": start + 49_000_000, "kind": "index", "value": 100.0},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 80_000_000, "event_ts_ns": start + 79_000_000, "kind": "liquidation", "value": 1000.0, "side": "long", "price": 100.0},
            {"venue": "okx", "symbol": "BTCUSDT", "receive_ts_ns": start + 120_000_000, "event_ts_ns": start + 119_000_000, "kind": "open_interest", "value": 101.0},
        ],
    )


def test_multimodal_tensor_joins_planes_and_proves_source_clocks(tmp_path):
    base = tmp_path / "base"
    market = tmp_path / "market"
    out = tmp_path / "tensor"
    start, _stop = _synthetic_base_tape(base)
    _synthetic_raw(market, start)
    report = build_multimodal_market_tensor(str(base), str(market), str(out), venues=["okx"], symbols=["BTCUSDT"], chunk_rows=1)
    assert report["rows"] == 2
    assert report["raw_records_consumed"]["book_events"] == 2
    assert report["raw_records_consumed"]["trades"] == 2
    assert report["raw_records_consumed"]["derivatives"] == 6
    frame = pd.concat([pd.read_parquet(p) for p in sorted(out.glob("part-*.parquet"))], ignore_index=True)
    assert "okx__bid_remove_intensity_100ms" in frame
    assert "okx__signed_notional_100ms" in frame
    assert "okx__absorption_100ms" in frame
    assert "okx__open_interest" in frame
    assert "okx__basis_bps" in frame
    assert "okx__liquidation_notional_30000ms" in frame
    clocks = [c for c in frame.columns if c.endswith("_available_ts_ns")]
    assert clocks
    for c in clocks:
        valid = frame[c].notna()
        assert (frame.loc[valid, c].astype("int64") <= frame.loc[valid, "asof_ns"].astype("int64")).all()
    assert (out / "_SUCCESS").is_file()
    assert (out / "AVAILABILITY_CONTRACT.json").is_file()


def test_tensor_columns_unlock_event_leverage_labs_but_do_not_fake_spot_basis(tmp_path):
    from alpha_foundry_v5.labs.registry import LabRegistry

    base = tmp_path / "base"
    market = tmp_path / "market"
    out = tmp_path / "tensor"
    start, _stop = _synthetic_base_tape(base)
    _synthetic_raw(market, start)
    build_multimodal_market_tensor(str(base), str(market), str(out), venues=["okx"], symbols=["BTCUSDT"], chunk_rows=100)
    small = pd.concat([pd.read_parquet(p) for p in sorted(out.glob("part-*.parquet"))], ignore_index=True)
    # Readiness has minimum activity counts. Replicate a fully populated causal
    # row to exercise the contract, not statistical validity.
    seed = small.iloc[-1].to_dict()
    rows = []
    for i in range(150):
        row = dict(seed)
        row["asof_ns"] = int(seed["asof_ns"]) + i + 1
        # Keep activity gates non-zero.
        row["okx__remove_count_100ms"] = 1.0
        row["okx__trade_count_100ms"] = 1.0
        row["okx__signed_notional_100ms"] = 10.0
        row["okx__impact_bps_100ms"] = 0.1
        row["okx__liquidation_total_usd_30000ms"] = 1000.0
        row["okx__open_interest_change_pct"] = 0.01
        rows.append(row)
    audit = LabRegistry().audit(pd.DataFrame(rows))
    for lab_id in ("A3", "A4", "A5", "A7", "A8", "A10"):
        assert audit[lab_id]["ready"] is True, (lab_id, audit[lab_id])
    # A9 now requires executable perp-vs-spot basis. Mark/index basis is not
    # silently promoted to a spot arbitrage signal.
    assert audit["A9"]["ready"] is False
