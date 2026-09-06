#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.state_tape import (
    DEFAULT_SYMBOLS,
    DEFAULT_VENUES,
    build_state_tape,
    concurrent_health_window,
    load_book_events,
    state_tape_summary,
)


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market_physics_v3")
    ap.add_argument("--health-dir", default="reports/market_physics_v3/health")
    ap.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--cadences-ms", default="100,250,500,1000")
    ap.add_argument("--max-start-skew-ms", type=float, default=5000.0)
    ap.add_argument("--max-receive-age-ms", type=float, default=1500.0)
    ap.add_argument("--max-transport-lag-ms", type=float, default=5000.0)
    ap.add_argument("--max-sync-span-ms", type=float, default=1000.0)
    ap.add_argument("--out-root", default="data/market_physics_v3/state_tape")
    args = ap.parse_args()

    venues = [x.lower() for x in _csv(args.venues)]
    symbols = [x.upper() for x in _csv(args.symbols)]
    cadences = [int(x) for x in _csv(args.cadences_ms)]

    window = concurrent_health_window(
        args.health_dir, venues=venues, max_start_skew_ms=args.max_start_skew_ms
    )
    events = load_book_events(
        args.root,
        int(window["started_ns"]),
        int(window["stopped_ns"]),
        venues=venues,
        symbols=symbols,
    )
    if not events:
        raise SystemExit("no book events found inside the concurrent health window")

    run_id = "%s-%s" % (window["started_ns"], window["stopped_ns"])
    out_dir = Path(args.out_root) / ("run=" + run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}

    for cadence_ms in cadences:
        frame = build_state_tape(
            events,
            int(window["started_ns"]),
            int(window["stopped_ns"]),
            cadence_ms,
            venues=venues,
            symbols=symbols,
            max_receive_age_ms=args.max_receive_age_ms,
            max_transport_lag_ms=args.max_transport_lag_ms,
            max_sync_span_ms=args.max_sync_span_ms,
        )
        path = out_dir / ("state_%sms.parquet" % cadence_ms)
        frame.to_parquet(path, index=False)
        summaries[str(cadence_ms)] = state_tape_summary(frame, window, cadence_ms)
        print("%sms %s rows=%s ready_fraction=%.6f" % (
            cadence_ms,
            path,
            summaries[str(cadence_ms)]["rows"],
            summaries[str(cadence_ms)]["ready_fraction"],
        ))

    report = {
        "run_id": run_id,
        "venues": venues,
        "symbols": symbols,
        "book_events_loaded": len(events),
        "window": {
            "started_ns": int(window["started_ns"]),
            "stopped_ns": int(window["stopped_ns"]),
            "duration_s": float(window["duration_s"]),
            "start_skew_ms": float(window["start_skew_ms"]),
        },
        "gates": {
            "max_receive_age_ms": args.max_receive_age_ms,
            "max_transport_lag_ms": args.max_transport_lag_ms,
            "max_sync_span_ms": args.max_sync_span_ms,
        },
        "cadences": summaries,
    }
    summary_path = out_dir / "SUMMARY.json"
    summary_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(summary_path)


if __name__ == "__main__":
    main()
