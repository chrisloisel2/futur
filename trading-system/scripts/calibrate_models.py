#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from pipeline.models.edge.calibrator import EdgeCalibrator
from pipeline.models.regime.calibrator import RegimeCalibrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate models")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predictions-path", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    preds = pd.read_parquet(args.predictions_path)
    regime_cal = RegimeCalibrator()
    edge_cal = EdgeCalibrator()
    out_dir = Path(f"artifacts/models/calibration/{args.run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "regime_calibrator.pkl").write_bytes(pickle.dumps(regime_cal))
    (out_dir / "edge_calibrator.pkl").write_bytes(pickle.dumps(edge_cal))


if __name__ == "__main__":
    main()
