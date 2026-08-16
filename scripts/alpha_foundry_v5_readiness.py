from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.quality import audit_point_in_time


def load_frame(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p)
    if p.is_dir():
        parts = sorted(p.glob("part-*.parquet"))
        if not parts:
            raise ValueError("no part-*.parquet under %s" % p)
        return pd.concat([pd.read_parquet(x) for x in parts], ignore_index=True)
    return pd.read_parquet(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Alpha Foundry V5 lab readiness against a real frame")
    parser.add_argument("--data", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    frame = load_frame(args.data)
    registry = LabRegistry()
    pit = audit_point_in_time(frame)
    labs = registry.audit(frame)
    payload = {"rows": int(len(frame)), "columns": int(len(frame.columns)), "pit_clean": bool(pit.clean), "pit": pit.__dict__, "ready_labs": [k for k, v in labs.items() if v["ready"]], "blocked_labs": [k for k, v in labs.items() if not v["ready"]], "labs": labs}
    text = json.dumps(payload, indent=2, sort_keys=True, default=list)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
