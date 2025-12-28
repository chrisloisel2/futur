#!/usr/bin/env python3
from __future__ import annotations

import argparse

from infra.config.loader import load_config
from infra.storage.object_store import S3ParquetReader
from common.logging.setup import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download raw data from S3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--start", required=False)
    parser.add_argument("--end", required=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    prefix = f"{cfg['data_layer']['s3_lake']['prefix']}/data/raw/{args.source}"
    reader = S3ParquetReader()
    df = reader.read(prefix, filters={"symbol": args.symbol})
    logger.info({"msg": "downloaded", "rows": len(df)})


if __name__ == "__main__":
    main()
