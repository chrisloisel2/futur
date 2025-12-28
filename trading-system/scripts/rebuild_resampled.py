#!/usr/bin/env python3
from __future__ import annotations

import argparse

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from pipeline.data.resampling import BarBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild resampled bars from raw")
    parser.add_argument("--config", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--freq", default="1min")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    df = pd.read_parquet(args.path)
    builder = BarBuilder(freq=args.freq)
    ohlcv = builder.build_ohlcv(df, freq=args.freq)
    out_path = cfg["data_layer"]["resampling"].get("output_path", "data/cache/ohlcv.parquet")
    ohlcv.to_parquet(out_path, index=False)


if __name__ == "__main__":
    main()
