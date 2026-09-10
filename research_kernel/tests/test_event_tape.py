"""P2A — the official event tape is data, and data here must carry its provenance:
source, exchange timestamp (or an explicit null), local timestamp, raw hash, unique id."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
REQUIRED = {"event_id", "source", "event_type", "asset", "symbol", "market_type", "publication_ts_exchange",
            "first_seen_ts_local", "trading_start_ts", "raw_url", "raw_title", "raw_body_hash", "latency_ms", "backfilled"}
SOURCES = {"binance", "okx", "bybit", "coinbase", "hyperliquid"}


def _rows():
    if not TAPE.exists():
        return []
    return [json.loads(l) for l in TAPE.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_every_record_carries_provenance():
    rows = _rows()
    if not rows:
        return
    for r in rows:
        assert REQUIRED <= set(r), f"missing fields in {r.get('event_id')}: {REQUIRED - set(r)}"
        assert r["source"] in SOURCES
        assert r["first_seen_ts_local"] and r["raw_body_hash"] and r["raw_url"] and r["raw_title"]


def test_event_ids_are_unique():
    ids = [r["event_id"] for r in _rows()]
    assert len(ids) == len(set(ids))


def test_historical_records_have_exchange_timestamp_and_backfill_flag_is_honest():
    for r in _rows():
        if r["raw_url"].startswith("snapshot://"):
            assert r["publication_ts_exchange"] is None
        else:
            assert r["publication_ts_exchange"], r["raw_url"]
        if r["backfilled"]:
            assert r["latency_ms"] is None, "a backfilled record cannot claim a latency"
