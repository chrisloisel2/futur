#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v4.trade_tape import build_trade_tape, iter_merged_trades, write_trade_tape
from market_physics_v3.state_tape import DEFAULT_SYMBOLS, DEFAULT_VENUES, concurrent_health_window


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market_physics_v3")
    ap.add_argument("--health-dir", default="reports/market_physics_v3/health")
    ap.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--chunk-rows", type=int, default=50000)
    ap.add_argument("--out-root", default="data/alpha_foundry_v4/trade_tape")
    args = ap.parse_args()

    venues = [v.lower() for v in _csv(args.venues)]
    symbols = [s.upper() for s in _csv(args.symbols)]
    window = concurrent_health_window(args.health_dir, venues=venues)
    start_ns = int(window["started_ns"])
    stop_ns = int(window["stopped_ns"])
    run = "run=%s-%s" % (start_ns, stop_ns)
    out_dir = Path(args.out_root) / run / ("cadence=%sms" % int(args.cadence_ms))

    print("[trade-tape-v4] window start=%s stop=%s duration_s=%.3f" % (start_ns, stop_ns, float(window["duration_s"])), flush=True)
    events = iter_merged_trades(args.root, start_ns, stop_ns, venues=venues, symbols=symbols)
    rows = build_trade_tape(events, start_ns, stop_ns, cadence_ms=args.cadence_ms, venues=venues, symbols=symbols)
    summary = write_trade_tape(rows, str(out_dir), chunk_rows=args.chunk_rows)
    summary.update({"started_ns": start_ns, "stopped_ns": stop_ns, "duration_s": float(window["duration_s"]), "start_skew_ms": float(window["start_skew_ms"]), "venues": venues, "symbols": symbols, "cadence_ms": int(args.cadence_ms)})
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(out_dir)


if __name__ == "__main__":
    main()
