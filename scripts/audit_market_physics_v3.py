#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

REQUIRED_FEEDS = {
    "microstructure": ["l2_book_events", "tick_trades", "bbo"],
    "cross_venue": ["binance", "bybit", "okx", "hyperliquid"],
    "leverage": ["open_interest", "funding", "mark_index_premium", "liquidations"],
    "options": ["option_quotes", "option_trades", "option_open_interest"],
    "execution": ["decision_send_ack_fill", "future_markouts"],
    "external": ["stablecoin_flows", "etf_cme", "macro_events", "news_events"],
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit Market Physics / Data V3 feed inventory")
    ap.add_argument("--manifest", required=True, help="CSV with columns feed,present,start,end,notes")
    ap.add_argument("--out", default="reports/market_physics_v3/COVERAGE.json")
    args = ap.parse_args()
    df = pd.read_csv(args.manifest)
    if "feed" not in df or "present" not in df:
        raise SystemExit("manifest must contain feed,present")
    present = {str(r.feed): bool(r.present) for r in df.itertuples()}
    result = {"families": {}, "missing": []}
    for family, feeds in REQUIRED_FEEDS.items():
        status = {feed: bool(present.get(feed, False)) for feed in feeds}
        result["families"][family] = status
        result["missing"].extend([feed for feed, ok in status.items() if not ok])
    result["ready_for_market_physics_research"] = len(result["missing"]) == 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(out)


if __name__ == "__main__":
    main()
