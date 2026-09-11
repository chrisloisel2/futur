"""Provider request windows: launch -30 min -> +6 h, P0 only where the free archive has a hole."""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.indices import provider_request_windows as P  # noqa: E402


def test_windows_and_priorities(tmp_path):
    rows = P.rows_from_matrix([{"symbol": "AAAUSDT", "event_id": "e1", "launch_ts": "2025-05-02T08:30:00+00:00", "vision_available": "aggTrades;bookDepth;markPriceKlines;indexPriceKlines;premiumIndexKlines;metrics;fundingRate"},
                               {"symbol": "BBBUSDT", "event_id": "e2", "launch_ts": "2025-05-02T08:30:00+00:00", "vision_available": "aggTrades;markPriceKlines;metrics;fundingRate"}])
    a, b = rows
    assert a["start_ts"] == "2025-05-02T08:00:00+00:00" and a["end_ts"] == "2025-05-02T14:30:00+00:00" and a["priority"] == "P1" and a["needs_paid_provider"] is False
    assert b["priority"] == "P0" and b["needs_paid_provider"] is True and "missing_l2" in b["reason"] and "missing_mark_index" in b["reason"]
    s = P.write(rows, tmp_path); assert s["P0_needs_paid"] == 1 and s["hours_requested_P0"] == 6.5
    with open(tmp_path / "H2_PROVIDER_REQUEST_WINDOWS.csv") as f:
        r = list(csv.DictReader(f)); assert set(P.COLUMNS) <= set(r[0].keys()) and len(r) == 2
