#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.object_store import S3ParquetWriter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export local raw parquet to S3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--prefix", required=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    writer = S3ParquetWriter()
    df = pd.read_parquet(args.path)
    prefix = args.prefix or f"{cfg['data_layer']['s3_lake']['prefix']}/data/raw/export"
    writer.write(df, prefix, partition_cols=["dt", "symbol", "venue", "source"])


if __name__ == "__main__":
    main()
