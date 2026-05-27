#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.logging.setup import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export artifacts to latest")
    parser.add_argument("--component", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    meta_path = Path(f"artifacts/models/{args.component}/active_run_id.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"active_run_id": args.run_id}))


if __name__ == "__main__":
    main()
