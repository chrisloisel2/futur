#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.state_tape import DEFAULT_SYMBOLS, DEFAULT_VENUES, concurrent_health_window
from market_physics_v3.state_tape_stream import build_streaming_state_tape, iter_merged_book_events


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market_physics_v3")
    ap.add_argument("--health-dir", default="reports/market_physics_v3/health")
    ap.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--max-start-skew-ms", type=float, default=5000.0)
    ap.add_argument("--max-receive-age-ms", type=float, default=1500.0)
    ap.add_argument("--max-transport-lag-ms", type=float, default=5000.0)
    ap.add_argument("--max-sync-span-ms", type=float, default=1000.0)
    ap.add_argument("--chunk-rows", type=int, default=50000)
    ap.add_argument("--out-root", default="data/market_physics_v3/state_tape_stream")
    args = ap.parse_args()

    venues = [x.lower() for x in _csv(args.venues)]
    symbols = [x.upper() for x in _csv(args.symbols)]
    window = concurrent_health_window(
        args.health_dir, venues=venues, max_start_skew_ms=args.max_start_skew_ms
    )
    run_id = "%s-%s" % (window["started_ns"], window["stopped_ns"])
    out_dir = Path(args.out_root) / ("run=" + run_id) / ("cadence=%sms" % args.cadence_ms)

    events = iter_merged_book_events(
        args.root,
        int(window["started_ns"]),
        int(window["stopped_ns"]),
        venues=venues,
        symbols=symbols,
    )
    result = build_streaming_state_tape(
        events,
        int(window["started_ns"]),
        int(window["stopped_ns"]),
        args.cadence_ms,
        str(out_dir),
        venues=venues,
        symbols=symbols,
        max_receive_age_ms=args.max_receive_age_ms,
        max_transport_lag_ms=args.max_transport_lag_ms,
        max_sync_span_ms=args.max_sync_span_ms,
        chunk_rows=args.chunk_rows,
    )
    report = {
        "run_id": run_id,
        "window": {
            "started_ns": int(window["started_ns"]),
            "stopped_ns": int(window["stopped_ns"]),
            "duration_s": float(window["duration_s"]),
            "start_skew_ms": float(window["start_skew_ms"]),
        },
        "venues": venues,
        "symbols": symbols,
        "gates": {
            "max_receive_age_ms": args.max_receive_age_ms,
            "max_transport_lag_ms": args.max_transport_lag_ms,
            "max_sync_span_ms": args.max_sync_span_ms,
        },
        "streaming": result,
    }
    summary = out_dir / "SUMMARY.json"
    summary.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    print(summary)


if __name__ == "__main__":
    main()
