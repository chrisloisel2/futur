#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.modality import audit_modality_matrix


def main() -> int:
    p = argparse.ArgumentParser(description="Audit venue x symbol x modality readiness from latest smoke windows")
    p.add_argument("--root", default="data/market_physics_v3")
    p.add_argument("--health-dir", default="reports/market_physics_v3/health")
    p.add_argument("--venues", default="binance,bybit,okx,hyperliquid")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--fresh-max-lag-ms", type=float, default=5000.0)
    p.add_argument("--out", default="reports/market_physics_v3/MODALITY_MATRIX.json")
    args = p.parse_args()

    venues = [x.strip().lower() for x in args.venues.split(",") if x.strip()]
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    report = audit_modality_matrix(
        root=args.root,
        health_dir=args.health_dir,
        venues=venues,
        symbols=symbols,
        fresh_max_lag_ms=args.fresh_max_lag_ms,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(out)
    print("ready_for_synchronized_books", report["summary"]["ready_for_synchronized_books"])
    for item in report["summary"]["blocking_cells"]:
        print("BLOCK", item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
