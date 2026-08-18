#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.planes.tensor import (
    DEFAULT_SYMBOLS,
    DEFAULT_VENUES,
    build_multimodal_market_tensor,
)
from alpha_foundry_v5.provenance import write_feature_provenance_manifest


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build Alpha Foundry V5 causal multimodal market tensor"
    )
    ap.add_argument("--base-tape", required=True, help="Existing Market Physics causal state tape")
    ap.add_argument(
        "--market-root",
        default="data/market_physics_v3",
        help="Root containing raw/book_events, raw/trades and raw/derivatives",
    )
    ap.add_argument("--venues", default=",".join(DEFAULT_VENUES))
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--chunk-rows", type=int, default=50000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    report = build_multimodal_market_tensor(
        base_tape=args.base_tape,
        market_root=args.market_root,
        out_dir=args.out,
        venues=[x.lower() for x in _csv(args.venues)],
        symbols=[x.upper() for x in _csv(args.symbols)],
        chunk_rows=args.chunk_rows,
    )
    provenance = write_feature_provenance_manifest(args.out, args.base_tape)
    report = dict(report)
    report["feature_provenance_manifest"] = str(Path(args.out) / "FEATURE_PROVENANCE.json")
    report["feature_provenance_digest"] = provenance["manifest_digest"]
    summary_path = Path(args.out) / "SUMMARY.json"
    summary_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(summary_path)
    print(Path(args.out) / "FEATURE_PROVENANCE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
