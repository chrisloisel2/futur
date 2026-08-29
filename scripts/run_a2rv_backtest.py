#!/usr/bin/env python3
"""A2-RV-v1 backtest runner, per docs/A2RV_PREREGISTRATION.md.

--tape must be the confirm_24h_collect window once it's done collecting (or, for a
code-correctness smoke test only, an older tape -- but that is NOT the preregistered
evaluation and must be labeled as a smoke test wherever it's reported, never as a
result). Thresholds are always frozen from the DEV_PILOT tape, never from --tape.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.a2rv_execution import build_trades, freeze_thresholds, summarize_trades
from market_physics_v3.phase5_audit import load_parquet_dataset

PRIMARY_SYMBOLS = ("BTCUSDT", "ETHUSDT")
SUPPORT_SYMBOLS = ("SOLUSDT",)
VENUES = ("binance", "bybit", "okx", "hyperliquid")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--dev-pilot-tape", required=True, help="thresholds are frozen here, never recomputed on --tape")
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--out", default="reports/market_physics_v3/a2rv_backtest")
    ap.add_argument("--smoke-test", action="store_true", help="mark this run's SUMMARY.json as a code-correctness check, not the preregistered evaluation")
    args = ap.parse_args()

    print(f"[a2rv] loading {args.tape}", flush=True)
    frame = load_parquet_dataset(args.tape)
    print(f"[a2rv] loaded rows={len(frame)} columns={len(frame.columns)}", flush=True)

    results: dict[str, dict] = {}
    thresholds_by_symbol: dict[str, dict] = {}
    all_trades = []
    for symbol in (*PRIMARY_SYMBOLS, *SUPPORT_SYMBOLS):
        thresholds = {}
        thresholds_by_symbol[symbol] = {}
        for venue in VENUES:
            th = freeze_thresholds(args.dev_pilot_tape, symbol, venue)
            thresholds[venue] = th
            thresholds_by_symbol[symbol][venue] = {"lo": th.lo, "hi": th.hi}
        trades = build_trades(frame, symbol, thresholds, VENUES, cadence_ms=args.cadence_ms)
        print(f"[a2rv] {symbol} n_trades={len(trades)}", flush=True)
        if not trades:
            results[symbol] = {"n_trades": 0}
            continue
        all_trades.extend(trades)
        summary = summarize_trades(trades)
        summary["role"] = "PRIMARY" if symbol in PRIMARY_SYMBOLS else "SUPPORT"
        results[symbol] = summary
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)

    if all_trades:
        results["COMBINED"] = summarize_trades(all_trades)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "smoke_test_only": bool(args.smoke_test),
        "tape": str(args.tape),
        "dev_pilot_tape": str(args.dev_pilot_tape),
        "venues": list(VENUES),
        "horizon_ms": 2000,
        "entry_thresholds_frozen_from_dev_pilot": thresholds_by_symbol,
        "results": results,
    }
    out_path = out_dir / "SUMMARY.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"[a2rv] wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
