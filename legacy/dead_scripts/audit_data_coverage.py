#!/usr/bin/env python3
"""Audit raw and feature parquet coverage for max-public datasets."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipeline.audit import audit_parquet_tree, write_audit_report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit data coverage and quality")
    parser.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    parser.add_argument("--features", default=str(ROOT / "data" / "training"))
    parser.add_argument("--write", default=str(ROOT / "reports" / "data_quality" / "max_public.json"))
    parser.add_argument("--max-files", type=int)
    args = parser.parse_args(argv)

    report = {
        "raw": audit_parquet_tree(Path(args.raw), max_files=args.max_files),
        "features": audit_parquet_tree(Path(args.features), max_files=args.max_files),
    }
    write_audit_report(report, Path(args.write))
    print(json.dumps({"report": args.write}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
