#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.timeseries_db import (
    AllocCacheReader,
    TargetPositionsCacheWriter,
    AllocatorDecisionCacheWriter,
    BooksStateCacheWriter,
    MongoBufferReader,
)
from pipeline.books import MultiBookAlphaEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multi-book alpha")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    engine = MultiBookAlphaEngine(cfg.get("books", {}))
    clusters = cfg.get("clusters", {}).get("clusters", {})
    if args.mode == "live":
        alloc_reader = AllocCacheReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("alloc_collection", "alloc_cache"))
        signal_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("signal_collection", "signal_cache"))
        state_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("state_collection", "state_cache"))
        alloc_doc = alloc_reader.fetch_latest_alloc("portfolio") if hasattr(alloc_reader, "fetch_latest_alloc") else None
        alloc = alloc_doc.get("alloc", alloc_doc) if alloc_doc else {}
        signals = {}
        states = {}
        for sym in args.symbol:
            sig_df = signal_reader.fetch_events(sym, datetime.utcnow(), datetime.utcnow())
            if sig_df is not None and not sig_df.empty:
                signals[sym] = sig_df.iloc[-1].to_dict()
            st_df = state_reader.fetch_events(sym, datetime.utcnow(), datetime.utcnow())
            if st_df is not None and not st_df.empty:
                states[sym] = st_df.iloc[-1]
        tgt_positions, books_state, alloc_decision = engine.step(
            states,
            signals,
            alloc,
            {},
            {},
            None,
            cfg.get("books_budgets", cfg.get("books", {})),
            clusters,
            args.run_id,
            cfg.get("model_stack", "v1"),
            cfg.get("feature_set", "v1"),
        )
        tp_writer = TargetPositionsCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("target_positions_collection", "target_positions_cache"))
        tp_writer.write_target_positions({"scope": "portfolio", "event_time": datetime.utcnow(), "targets": [t.__dict__ for t in tgt_positions.targets]})
        dec_writer = AllocatorDecisionCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("allocator_decision_collection", "allocator_decision_cache"))
        dec_writer.write_allocator_decision({"scope": "portfolio", "event_time": datetime.utcnow(), **alloc_decision})
        bs_writer = BooksStateCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("books_state_collection", "books_state_cache"))
        bs_writer.write_books_state({"scope": "portfolio", "event_time": datetime.utcnow(), **books_state.__dict__})
    else:
        # batch mode stub: read parquet if provided
        tgt_positions, books_state, alloc_decision = engine.step({}, {}, {}, {}, {}, None, cfg.get("books_budgets", cfg.get("books", {})), clusters, args.run_id, cfg.get("model_stack", "v1"), cfg.get("feature_set", "v1"))
        out_dir = Path("artifacts/books/target_positions")
        out_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for t in tgt_positions.targets:
            rec = t.__dict__.copy()
            rec["run_id"] = args.run_id
            records.append(rec)
        if records:
            df = pd.DataFrame(records)
            df.to_parquet(out_dir / f"targets_{args.run_id}.parquet", index=False)


if __name__ == "__main__":
    main()
