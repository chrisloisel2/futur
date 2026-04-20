#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.meta_store import MetaStore
from infra.storage.timeseries_db import AllocCacheWriter, MetaStateCacheWriter, SignalCacheReader, MongoBufferReader
from pipeline.meta_control import MetaController
from domain.risk.budgets import RiskBudgets
from domain.risk.scenarios import ScenarioState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run meta control")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    controller = MetaController(cfg.get("meta_control", {}))
    meta_store = MetaStore(cfg.get("s3_prefix", "s3://my-bucket"), cfg.get("mongo_uri"), cfg.get("mongo_db"))
    budgets = RiskBudgets(**cfg.get("risk_budgets", {}))
    scenario = ScenarioState(cfg.get("scenario_flags", {}))
    clusters_cfg = cfg.get("clusters", {})
    if args.mode == "live":
        signal_reader = SignalCacheReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("mongo_collection", "signal_cache"))
        state_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("state_collection", "state_cache"))
        signals = {}
        states = {}
        for sym in args.symbol:
            sig_doc = signal_reader.fetch_latest_signal(sym)
            if sig_doc:
                signals[sym] = sig_doc.get("signal", sig_doc)
            state_doc = state_reader.fetch_events(sym, datetime.utcnow(), datetime.utcnow())
            if state_doc is not None and not state_doc.empty:
                states[sym] = state_doc.iloc[-1]
        telemetry = {
            "perf_snapshot": meta_store.load_perf_snapshot(args.symbol),
            "drift_snapshot": meta_store.load_drift_snapshot(args.symbol),
        }
        alloc, meta_state = controller.step(states, signals, {}, telemetry, {}, budgets, scenario, clusters_cfg.get("clusters", {}), args.run_id, cfg.get("model_stack", "v1"), cfg.get("feature_set", "v1"))
        alloc_dict = alloc.__dict__.copy()
        alloc_dict["event_time"] = datetime.utcnow()
        alloc_cache = AllocCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("alloc_collection", "alloc_cache"))
        alloc_cache.write_alloc(alloc_dict)
        meta_cache = MetaStateCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("meta_state_collection", "meta_state_cache"))
        meta_cache.write_meta_state(meta_state.__dict__ | {"event_time": datetime.utcnow(), "scope": "portfolio"})
    else:
        # batch: load parquet signals/states is simplified placeholder
        signals = {}
        states = {}
        telemetry = {"perf_snapshot": meta_store.load_perf_snapshot(args.symbol), "drift_snapshot": meta_store.load_drift_snapshot(args.symbol)}
        alloc, meta_state = controller.step(states, signals, {}, telemetry, {}, budgets, scenario, clusters_cfg.get("clusters", {}), args.run_id, cfg.get("model_stack", "v1"), cfg.get("feature_set", "v1"))
        out_dir = Path("artifacts/meta_control/allocations")
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([alloc.__dict__]).to_parquet(out_dir / f"alloc_{args.run_id}.parquet", index=False)


if __name__ == "__main__":
    main()
