#!/usr/bin/env python3
"""Resolve and freeze an explicit, immutable feature set for one lab against one dataset's
real column list. A hypothesis's feature_set_id must reference the id written here -- the
selection is computed once, then never recomputed at discovery/confirmation time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.feature_sets import FeatureSet, resolve_feature_columns, write_feature_set
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.support_io import parquet_union_schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Tensor directory or single parquet/csv file")
    ap.add_argument("--lab", required=True)
    ap.add_argument("--feature-set-id", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    registry = LabRegistry()
    spec = registry.spec(args.lab)

    p = Path(args.data)
    if p.is_file():
        import pandas as pd

        all_columns = tuple(pd.read_parquet(p).columns) if p.suffix.lower() != ".csv" else tuple(pd.read_csv(p, nrows=1).columns)
    else:
        _parts, all_columns, _by_part = parquet_union_schema(str(p))

    columns = resolve_feature_columns(spec, all_columns)
    feature_set = FeatureSet(feature_set_id=args.feature_set_id, lab_id=args.lab, columns=columns)
    write_feature_set(feature_set, args.out)
    print(json.dumps({"feature_set_id": feature_set.feature_set_id, "lab_id": feature_set.lab_id, "n_columns": len(columns), "digest": feature_set.digest, "out": args.out}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
