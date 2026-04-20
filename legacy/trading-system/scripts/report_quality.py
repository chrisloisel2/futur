#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate quality gate report")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    out_dir = Path(f"artifacts/quality_gate/{args.run_id}")
    metrics_path = out_dir / "metrics.json"
    flags_path = cfg["gate"]["output_flags_path"]
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    report_lines = ["# Quality Gate Summary", f"Run: {args.run_id}", "", "## Metrics"]
    for k, v in metrics.items():
        report_lines.append(f"- {k}: {v}")
    report = "\n".join(report_lines) + "\n"
    (out_dir / "summary.md").write_text(report)


if __name__ == "__main__":
    main()
