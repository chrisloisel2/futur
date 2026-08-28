from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.data_planes import build_cross_asset_plane, build_derivatives_plane, build_event_microstructure_plane, build_wallet_plane, merge_planes


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Alpha Foundry V5 causal data planes on top of an existing Market Physics tape")
    parser.add_argument("--base-tape", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--planes", default="event,derivatives,wallet,cross_asset")
    parser.add_argument("--venues", default="binance,bybit,okx,hyperliquid")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--chunk-rows", type=int, default=50000)
    args = parser.parse_args()

    root = Path(args.out_root)
    root.mkdir(parents=True, exist_ok=True)
    planes = [x.strip() for x in args.planes.split(",") if x.strip()]
    venues = [x.strip().lower() for x in args.venues.split(",") if x.strip()]
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    built = []
    summaries = {}

    if "event" in planes:
        path = root / "event_microstructure"
        summaries["event"] = build_event_microstructure_plane(args.base_tape, args.raw_root, str(path), venues, symbols, chunk_rows=args.chunk_rows)
        built.append(str(path))
    if "derivatives" in planes:
        path = root / "derivatives"
        summaries["derivatives"] = build_derivatives_plane(args.base_tape, args.raw_root, str(path), venues, symbols, chunk_rows=args.chunk_rows)
        built.append(str(path))
    if "wallet" in planes:
        path = root / "wallet"
        summaries["wallet"] = build_wallet_plane(args.base_tape, args.raw_root, str(path), symbols, chunk_rows=args.chunk_rows)
        built.append(str(path))
    if "cross_asset" in planes:
        path = root / "cross_asset"
        summaries["cross_asset"] = build_cross_asset_plane(args.base_tape, str(path), chunk_rows=args.chunk_rows)
        built.append(str(path))

    enriched = root / "enriched"
    summaries["enriched"] = merge_planes(args.base_tape, built, str(enriched))
    print(json.dumps({"out_root": str(root), "enriched": str(enriched), "summaries": summaries}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
