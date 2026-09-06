#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.provenance import write_feature_provenance_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal feature-to-source provenance for an Alpha Foundry V5 tensor")
    parser.add_argument("--tensor", required=True)
    parser.add_argument("--base-tape", required=True)
    args = parser.parse_args()
    payload = write_feature_provenance_manifest(args.tensor, args.base_tape)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(Path(args.tensor) / "FEATURE_PROVENANCE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
