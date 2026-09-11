"""market_state_schema: mandatory fields, hashes, append-only writer readable through tape_io."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import market_state_schema as S  # noqa: E402
from data_lake.collectors.tape_io import iter_records  # noqa: E402


def test_schemas_have_every_mandatory_field():
    assert set(S.SYMBOL_LIFECYCLE_EVENT) >= {"venue", "symbol", "base_asset", "quote_asset", "market_type", "old_status", "new_status", "detected_at_local", "exchange_ts", "onboard_date", "first_seen_at", "raw_hash", "source"}
    assert set(S.MARKET_STATE_SNAPSHOT) >= {"venue", "symbol", "ts_exchange", "ts_local", "source", "bid", "ask", "mid", "spread_bps", "depth_10bps_bid_usd", "depth_10bps_ask_usd", "depth_25bps_bid_usd", "depth_25bps_ask_usd", "depth_50bps_bid_usd", "depth_50bps_ask_usd", "imbalance_10bps", "mark_price", "index_price", "funding_rate", "open_interest", "volume_1m", "trade_count_1m", "latency_ms", "raw_hash"}
    assert set(S.TRIGGERED_WINDOW_MANIFEST) >= {"trigger_id", "trigger_type", "venue", "symbol", "start_ts", "end_ts", "reason", "prereg_link", "files_written", "row_counts", "sha256", "completeness_score", "missing_fields", "no_alpha_test"}


def test_validate_refuses_missing_and_unset_fields():
    with pytest.raises(S.SchemaError):
        S.validate("market_state_snapshot", {"venue": "binance", "symbol": "X"})
    rec = S.make_snapshot("binance", "XUSDT", "test", bid=1.0, ask=1.001)
    assert rec["mid"] == pytest.approx(1.0005) and rec["spread_bps"] == pytest.approx(10.0, rel=1e-3) and len(rec["raw_hash"]) == 64
    with pytest.raises(S.SchemaError):
        S.make_manifest("t", "not_a_type", "binance", "X", "s", None, "r", [], {}, {}, 0.0, [])
    m = S.make_manifest("t1", "manual", "binance", "XUSDT", "2026-09-11T00:00:00", None, "r", [], {}, {}, 0.0, [])
    assert m["no_alpha_test"] is True


def test_completeness_counts_non_null_fields():
    rows = [{"a": 1, "b": None}, {"a": 2, "b": None}]
    c = S.completeness(rows, ["a", "b"])
    assert c["score"] == 0.5 and c["missing_fields"] == ["b"] and c["n_rows"] == 2


def test_append_only_writer_and_gz_partial_read(tmp_path):
    w = S.AppendOnlyJsonl(tmp_path, gz=True)
    ev = S.make_lifecycle_event("binance_um", "XUSDT", "born", new_status="TRADING", market_type="perp", raw={"x": 1})
    w.write("symbol_lifecycle_event", ev); w.close()
    w2 = S.AppendOnlyJsonl(tmp_path, gz=True); w2.write("symbol_lifecycle_event", ev, venue="binance_um"); w2.close()   # second membre gzip concatene
    files = list(tmp_path.rglob("*.jsonl.gz")); assert len(files) == 1
    rows = list(iter_records(files[0])); assert len(rows) == 2 and rows[0]["event_kind"] == "born"
