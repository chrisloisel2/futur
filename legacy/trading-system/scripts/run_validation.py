#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from infra.config.loader import load_config
from pipeline.research.validation import ValidationConfig, ValidationSuite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run validation suite")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    trades = pd.read_parquet(cfg["data"]["trades_path"])
    equity = pd.read_parquet(cfg["data"]["equity_path"])
    features = pd.read_parquet(cfg["data"].get("features_path", cfg["data"]["trades_path"]))
    labels = pd.read_parquet(cfg["data"].get("labels_path", cfg["data"]["trades_path"]))
    suite = ValidationSuite(ValidationConfig(**cfg["validation"]))
    suite.run(features, labels, trades, equity, run_id=args.run_id, output_dir=Path(cfg["validation"].get("report_path", "artifacts/validation")))


if __name__ == "__main__":
    main()
