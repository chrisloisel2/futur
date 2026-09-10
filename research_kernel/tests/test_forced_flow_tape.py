"""P2B — a liquidation record must say when it happened at the exchange, when we saw
it, and what the book said BEFORE it (never after). Runs only if partitions exist."""
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PART = ROOT / "data_lake" / "events" / "liquidations"
REQUIRED = {"venue", "symbol", "event_ts_exchange", "event_ts_exchange_ns", "recv_ts_local", "recv_ts_local_ns", "latency_ms",
            "side", "liquidated_position", "price", "qty", "notional_usd", "spread_before_bps", "book_imbalance_before",
            "mark_price", "index_price", "funding_rate", "open_interest", "depth_10bps_before"}


def _rows(limit=5000):
    import sys
    sys.path.insert(0, str(ROOT))
    from data_lake.collectors.tape_io import read_tape           # tolere la partition en cours d'ecriture
    return read_tape(PART, limit=limit) if PART.exists() else []


def test_records_carry_both_timestamps_and_enrichment_fields():
    for r in _rows():
        assert REQUIRED <= set(r), REQUIRED - set(r)
        assert r["recv_ts_local_ns"] >= r["event_ts_exchange_ns"] - 5_000_000_000, "received before it happened (clock?)"


def test_enrichment_is_strictly_before_the_event():
    for r in _rows():
        for k in ("bbo_age_ms", "mark_age_ms"):
            if r.get(k) is not None:
                assert r[k] >= 0, f"{k} = {r[k]} : the 'before' state was taken after the liquidation"


def test_no_return_is_filled_by_the_collector():
    for r in _rows():
        assert r["return_5s_bps"] is None and r["return_30s_bps"] is None and r["return_5m_bps"] is None
