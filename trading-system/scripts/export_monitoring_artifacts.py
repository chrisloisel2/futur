#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common.logging.setup import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export monitoring artifacts")
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    out = Path("artifacts/monitoring") / f"bundle_{args.run_id}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"bundle for {args.run_id}\\n")


if __name__ == "__main__":
    main()
