#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from infra.config.loader import load_config
from pipeline.research.labeling import EventDrivenLabeler, LabelingConfig
from infra.storage.object_store import S3ParquetReader, S3ParquetWriter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build event-driven labels")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", default="offline")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    reader = S3ParquetReader()
    writer = S3ParquetWriter()
    features_prefix = cfg["s3"]["clean_prefix"]
    df = reader.read(features_prefix, filters={"symbol": args.symbol})
    df["event_time"] = pd.to_datetime(df["event_time"])
    df = df[(df["event_time"] >= pd.to_datetime(args.start)) & (df["event_time"] <= pd.to_datetime(args.end))]
    labeler = EventDrivenLabeler(LabelingConfig(**cfg["labeling"]))
    labels = labeler.label(df)
    labels["run_id"] = args.run_id
    out_dir = Path(cfg["artifacts"]["labels_dir"]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"labels_{args.symbol}_{args.run_id}.parquet"
    labels.to_parquet(out_path, index=False)
    if cfg["artifacts"].get("s3_prefix"):
        writer.write(labels, cfg["artifacts"]["s3_prefix"], partition_cols=["symbol", "label_set"])


if __name__ == "__main__":
    main()
