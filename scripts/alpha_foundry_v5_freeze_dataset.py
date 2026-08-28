from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.contracts import TimeWindow
from alpha_foundry_v5.manifest import DatasetManifest, fingerprint_partitions, write_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Create immutable SHA-256 dataset manifest for Alpha Foundry V5")
    parser.add_argument("--name", required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument("--start-ns", required=True, type=int)
    parser.add_argument("--stop-ns", required=True, type=int)
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--partition-glob", action="append", required=True)
    parser.add_argument("--row-count", type=int, default=0)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    paths = []
    for pattern in args.partition_glob:
        paths.extend(glob.glob(pattern))
    manifest = DatasetManifest(schema_version=args.schema_version, dataset_name=args.name, window=TimeWindow(args.start_ns, args.stop_ns), domains=tuple(args.domain), sources=tuple(args.source), partitions=fingerprint_partitions(paths), row_count=int(args.row_count), code_commit=args.code_commit, pit_policy="availability_ts<=asof_ts; no future fill", clock_policy="exchange event time + local receive time; receive time defines information availability")
    write_manifest(manifest, args.out)
    print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
