#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.collectors.qualification import promote_manifest, qualify_venue


def main() -> int:
    ap = argparse.ArgumentParser(description="Qualify one Market Physics V3 venue from live evidence")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--root", default="data/market_physics_v3")
    ap.add_argument("--health-dir", default="reports/market_physics_v3/health")
    ap.add_argument("--out", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--promote-manifest", action="store_true")
    ap.add_argument("--min-messages", type=int, default=100)
    ap.add_argument("--min-events", type=int, default=100)
    ap.add_argument("--max-idle-ms", type=float, default=5000.0)
    args = ap.parse_args()

    venue = args.venue.lower().strip()
    report = qualify_venue(
        venue=venue,
        root=args.root,
        health_dir=args.health_dir,
        min_messages=args.min_messages,
        min_events=args.min_events,
        max_idle_ms=args.max_idle_ms,
    )
    promoted = False
    if args.promote_manifest:
        if not args.manifest:
            raise SystemExit("--promote-manifest requires --manifest")
        promoted = promote_manifest(report, args.manifest)
    report["manifest_promoted"] = bool(promoted)

    out = Path(args.out or ("reports/market_physics_v3/qualification/%s.json" % venue))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))

    print(out)
    print("qualified", report["qualified"])
    print("status", report["status"])
    print("reasons", ",".join(report["reasons"]) if report["reasons"] else "NONE")
    print("manifest_promoted", promoted)
    return 0 if report["qualified"] else 2


if __name__ == "__main__":
    sys.exit(main())
