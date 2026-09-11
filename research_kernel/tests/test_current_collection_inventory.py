"""Inventory never fails on an absent tape, and counts what a synthetic tape contains."""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import current_collection_inventory as I  # noqa: E402


def test_absent_root_is_best_effort(tmp_path):
    inv = I.scan(tmp_path / "nope"); assert inv["present"] is False and inv["windows"]["count"] == 0
    h = I.health(inv); assert h[0]["level"] == "critical"
    paths = I.write(inv, tmp_path / "out"); assert all(p.exists() for p in paths.values())


def test_synthetic_tape_is_counted(tmp_path):
    root = tmp_path / "ms"; (root / "exchange_info" / "venue=binance_um").mkdir(parents=True)
    (root / "exchange_info" / "venue=binance_um" / "snapshot_1.json").write_text(json.dumps({"captured_at_local": "2026-09-11T00:00:00+00:00", "n_symbols": 3}))
    (root / "exchange_info" / "venue=binance_um" / "changes.jsonl").write_text(json.dumps({"type": "status_changed"}) + "\n")
    d = root / "symbol_lifecycle_event" / "venue=binance_um" / "date=2026-09-11"; d.mkdir(parents=True)
    (d / "symbol_lifecycle_event.jsonl").write_text("\n".join(json.dumps(x) for x in [{"event_kind": "born", "venue": "binance_um", "symbol": "NEWUSDT", "new_status": "PENDING_TRADING", "detected_at_local": "2026-09-11T01:00:00+00:00", "detail": {}},
                                                                                    {"event_kind": "status_change", "venue": "binance_um", "symbol": "NEWUSDT", "old_status": "PENDING_TRADING", "new_status": "TRADING", "detected_at_local": "2026-09-11T02:00:00+00:00", "detail": {}}]) + "\n")
    s = root / "market_state_snapshot" / "venue=binance" / "date=2026-09-11"; s.mkdir(parents=True)
    with gzip.open(s / "watch_premium_index.jsonl.gz", "wt") as f:
        f.write(json.dumps({"symbol": "A", "source": "watch_premium_index", "ts_local": "2026-09-11T00:00:01+00:00"}) + "\n" + json.dumps({"symbol": "B", "source": "watch_premium_index", "ts_local": "2026-09-11T00:00:02+00:00"}) + "\n")
    w = root / "windows" / "new_perp_listing_NEWUSDT_x"; w.mkdir(parents=True)
    (w / "manifest.json").write_text(json.dumps({"trigger_id": "new_perp_listing_NEWUSDT_x", "trigger_type": "new_perp_listing", "symbol": "NEWUSDT", "start_ts": "2026-09-11T01:30:00+00:00", "end_ts": "2026-09-11T08:00:00+00:00", "final": True,
                                                 "row_counts": {"snapshots": 5}, "completeness_score": 0.9, "missing_fields": ["depth_50bps_bid_usd"], "first_timestamps": {"first_orderbook_ts": "x", "first_trade_ts": "x", "first_mark_ts": "x", "first_index_ts": None, "first_oi_ts": "x"},
                                                 "message_counts": {"ws_reconnects": 1}, "sha256": {"a": "b"}, "latency_ms_median": 100}))
    (root / "triggers").mkdir(); (root / "triggers" / "triggers.jsonl").write_text(json.dumps({"trigger_type": "new_perp_listing", "fired": True, "pid": 1, "decision": "fire", "decided_at_local": "2026-09-11T01:30:00+00:00"}) + "\n" + json.dumps({"trigger_type": "liquidation_burst", "fired": False, "decision": "cooldown (1 s < 2 s)", "decided_at_local": "2026-09-11T01:31:00+00:00"}) + "\n")
    (root / "watch.log").write_text(json.dumps({"ts": "2026-09-11T01:00:00+00:00", "heartbeat": True, "rss_mb": 200, "um_errors": 0}) + "\n" + json.dumps({"ts": "2026-09-11T02:00:00+00:00", "heartbeat": True, "rss_mb": 250, "um_errors": 2}) + "\n")
    inv = I.scan(root)
    assert inv["exchange_info"]["binance_um"]["snapshots"] == 1 and inv["lifecycle"]["events"] == 2 and inv["lifecycle"]["by_kind"] == {"born": 1, "status_change": 1}
    assert inv["market_state_snapshot"]["watch_premium_index"]["rows"] == 2 and inv["market_state_snapshot"]["watch_premium_index"]["symbols"] == 2
    assert inv["windows"]["count"] == 1 and inv["windows"]["final"] == 1 and inv["windows"]["first_fields_missing_in_final"] == {"first_index_ts": 1} and inv["windows"]["items"][0]["duration_s"] == 23400
    assert inv["triggers"]["fired"] == 1 and inv["triggers"]["skipped_by_reason"] == {"cooldown": 1}
    assert inv["watch_log"]["rss_mb_last"] == 250 and inv["watch_log"]["errors_last"] == {"um_errors": 2}
    assert inv["no_alpha_test"] is True and any(x["check"] == "first_* timestamps in final captures" and x["level"] == "warn" for x in I.health(inv))
