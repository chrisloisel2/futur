#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAFE_MODALITY_FEEDS = {
    "l2_book_events": "l2_book_events",
    "bbo": "bbo",
    "funding": "funding",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="reports/market_physics_v3/MODALITY_MATRIX.json")
    ap.add_argument("--manifest", default="reports/market_physics_v3/feed_manifest.csv")
    args = ap.parse_args()

    matrix = json.loads(Path(args.matrix).read_text())
    suggestions = matrix.get("summary", {}).get("manifest_status_suggestions", {})
    manifest_path = Path(args.manifest)
    frame = pd.read_csv(manifest_path).fillna("")
    if "feed" not in frame.columns or "status" not in frame.columns:
        raise SystemExit("manifest must contain feed,status columns")

    changed = []
    for feed, suggestion_key in SAFE_MODALITY_FEEDS.items():
        if suggestions.get(suggestion_key) != "EVENT_LEVEL":
            continue
        mask = frame["feed"].astype(str) == feed
        if not mask.any():
            continue
        old = str(frame.loc[mask, "status"].iloc[0])
        if old == "EVENT_LEVEL":
            continue
        frame.loc[mask, "status"] = "EVENT_LEVEL"
        note = "promoted from Phase-3 venue x symbol x modality matrix"
        if "notes" in frame.columns:
            frame.loc[mask, "notes"] = note
        changed.append({"feed": feed, "from": old, "to": "EVENT_LEVEL"})

    # Deliberate non-promotions: tick_trades remains blocked if any cell lacks
    # individual trade granularity; open_interest remains PARTIAL; combined
    # mark_index_premium is not promoted from mark+index evidence without an
    # explicit premium feed.
    frame.to_csv(manifest_path, index=False)
    print(json.dumps({
        "changed": changed,
        "tick_trades": suggestions.get("tick_trades"),
        "open_interest": suggestions.get("open_interest"),
        "mark": suggestions.get("mark"),
        "index": suggestions.get("index"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
