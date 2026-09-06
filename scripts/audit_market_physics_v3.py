#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_physics_v3.coverage import audit_feed_status


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit Market Physics / Data V3 feed inventory")
    ap.add_argument("--manifest", required=True, help="CSV with columns feed,status,start,end,notes")
    ap.add_argument("--out", default="reports/market_physics_v3/COVERAGE.json")
    args = ap.parse_args()
    df = pd.read_csv(args.manifest).fillna("")
    if "feed" not in df or "status" not in df:
        raise SystemExit("manifest must contain feed,status")
    status = {str(r.feed): r.status for r in df.itertuples()}
    result = audit_feed_status(status)
    result["manifest_rows"] = int(len(df))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(out)


if __name__ == "__main__":
    main()
