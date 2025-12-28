#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from pipeline.quality.gate import QualityGate
from pipeline.quality.checks import (
    BookSanityCheck,
    ClockSkewCheck,
    CrossSourceConsistencyCheck,
    DuplicateCheck,
    HaltDetectionCheck,
    MissingnessCheck,
    MicrostructureToxicityCheck,
    OutlierCheck,
    SchemaValidationCheck,
    SequenceGapCheck,
    StalenessCheck,
    TimeTravelCheck,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run quality gate batch")
    parser.add_argument("--config", required=True)
    parser.add_argument("--raw-path", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def build_checks(cfg) -> list:
    thresholds = cfg["checks"]
    return [
        SchemaValidationCheck(thresholds.get("required_fields", ["event_time", "recv_time", "event_time_aligned", "symbol", "venue", "source", "event_type"])),
        MissingnessCheck(thresholds.get("required_fields", [])),
        ClockSkewCheck(thresholds.get("max_skew_ms", 2000)),
        StalenessCheck(thresholds.get("staleness_ms", 30_000)),
        DuplicateCheck(),
        SequenceGapCheck(thresholds.get("seq_tolerance", 1)),
        TimeTravelCheck(),
        OutlierCheck(thresholds.get("outlier_z", 6.0), thresholds.get("outlier_window", 50)),
        BookSanityCheck(thresholds.get("max_spread_bps", 200.0), thresholds.get("min_depth", 1)),
        MicrostructureToxicityCheck(thresholds.get("toxic_spread_bps", 400.0)),
        CrossSourceConsistencyCheck(thresholds.get("cross_source_bps", 50.0)),
        HaltDetectionCheck(thresholds.get("halt_no_trade_s", 300)),
    ]


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    gate_cfg = cfg["gate"]
    df_raw = pd.read_parquet(args.raw_path)
    checks = build_checks(cfg)
    gate = QualityGate(
        checks=checks,
        mode=gate_cfg.get("mode", "batch"),
        watermark_ms=gate_cfg.get("watermark_ms", 5000),
        run_id=args.run_id,
        output_clean_path=gate_cfg["output_clean_path"],
        output_flags_path=gate_cfg["output_flags_path"],
        quarantine_path=gate_cfg.get("quarantine_path"),
        check_version=gate_cfg.get("check_version", 1),
    )
    gate.run_batch(df_raw)


if __name__ == "__main__":
    main()
