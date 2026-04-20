#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from pipeline.data.ingestion import (
    BinanceFuturesConnector,
    BinanceSpotConnector,
    CrossVenueConnector,
    DeribitConnector,
    IngestionConfig,
    IngestionOrchestrator,
    MacroConnector,
    OKXConnector,
    BybitConnector,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live ingestion")
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    dl = cfg["data_layer"]
    ingest_cfg = IngestionConfig(
        mongo_uri=dl["mongo_buffer"]["uri"],
        mongo_db=dl["mongo_buffer"]["db"],
        mongo_collection=dl["mongo_buffer"]["collection"],
        s3_prefix=dl["s3_lake"]["prefix"],
        ingest_run_id=dl.get("ingest_run_id", datetime.utcnow().strftime("%Y%m%d_%H%M%S_ingest")),
        ttl_seconds=dl["mongo_buffer"].get("ttl_seconds", 3600),
        payload_version=dl.get("payload_version", 1),
    )
    connectors = [
        BinanceSpotConnector(),
        BinanceFuturesConnector(),
        BybitConnector(),
        OKXConnector(),
        DeribitConnector(),
        MacroConnector(),
        CrossVenueConnector(),
    ]
    orchestrator = IngestionOrchestrator(connectors, ingest_cfg)
    orchestrator.run_live(args.symbol, start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end))


if __name__ == "__main__":
    main()
