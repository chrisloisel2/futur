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

from alpha_foundry_v4.readiness import lab_readiness


def _columns(path: str):
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        return list(pd.read_parquet(p).columns)
    if p.suffix.lower() == ".csv":
        return list(pd.read_csv(p, nrows=1).columns)
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text())
        if isinstance(payload, dict) and "columns" in payload:
            return list(payload["columns"])
    raise ValueError("supported inputs: parquet, csv, or json with a columns field")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    args = ap.parse_args()
    print(json.dumps(lab_readiness(_columns(args.input)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
