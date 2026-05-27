#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.timeseries_db import (
    MongoBufferReader,
    RiskStateCacheWriter,
    OrdersPlanCacheWriter,
    ScenarioResultsCacheWriter,
)
from infra.storage.object_store import S3ParquetWriter
from pipeline.risk import RiskController


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run risk controller")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    controller = RiskController(cfg.get("risk", cfg))
    if args.mode == "live":
        tp_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("target_positions_collection", "target_positions_cache"))
        pf_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("portfolio_collection", "portfolio_state_cache"))
        bs_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("books_state_collection", "books_state_cache"))
        st_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("state_collection", "state_cache"))
        tp_df = tp_reader.fetch_events("portfolio", datetime.utcnow(), datetime.utcnow())
        portfolio_df = pf_reader.fetch_events("portfolio", datetime.utcnow(), datetime.utcnow())
        books_df = bs_reader.fetch_events("portfolio", datetime.utcnow(), datetime.utcnow())
        states = {}
        for sym in args.symbol:
            df = st_reader.fetch_events(sym, datetime.utcnow(), datetime.utcnow())
            if df is not None and not df.empty:
                states[sym] = df.iloc[-1]
        target_positions = tp_df.iloc[-1].to_dict() if tp_df is not None and not tp_df.empty else {}
        portfolio_state = portfolio_df.iloc[-1].get("portfolio", {}) if portfolio_df is not None and not portfolio_df.empty else {}
        books_state = books_df.iloc[-1].get("books_state", {}) if books_df is not None and not books_df.empty else {}
        # reconstruct TargetPositions-like
        targets_list = []
        for t in target_positions.get("targets", []):
            targets_list.append(t)
        tp_obj = type("TP", (), target_positions | {"targets": []})()
        tp_obj.targets = []
        for t in target_positions.get("targets", []):
            tp_obj.targets.append(type("T", (), t)())
        risk_state, orders_plan = controller.step(tp_obj, portfolio_state, books_state, states, cfg.get("risk", cfg))
        rs_writer = RiskStateCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("risk_state_collection", "risk_state_cache"))
        rs_writer.write_risk_state(risk_state.__dict__ | {"scope": "portfolio"})
        op_writer = OrdersPlanCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("orders_plan_collection", "orders_plan_cache"))
        op_writer.write_orders_plan(orders_plan.__dict__ | {"scope": "portfolio"})
    else:
        tp_path = cfg.get("target_positions_path")
        portfolio_path = cfg.get("portfolio_state_path")
        if tp_path and portfolio_path:
            tp_df = pd.read_parquet(tp_path)
            portfolio_state = pd.read_parquet(portfolio_path).iloc[-1].to_dict()
            targets = []
            for _, row in tp_df.iterrows():
                targets.append(type("T", (), row.to_dict())())
            tp_obj = type("TP", (), {"targets": targets, "run_id": args.run_id, "model_stack": cfg.get("model_stack", "v1"), "feature_set": cfg.get("feature_set", "v1")})()
            risk_state, orders_plan = controller.step(tp_obj, portfolio_state, {}, {}, cfg.get("risk", cfg))
            out_dir = Path("artifacts/risk/risk_state")
            out_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([risk_state.__dict__]).to_parquet(out_dir / f"risk_state_{args.run_id}.parquet", index=False)
            op_dir = Path("artifacts/risk/orders_plan")
            op_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([orders_plan.__dict__]).to_parquet(op_dir / f"orders_plan_{args.run_id}.parquet", index=False)


if __name__ == "__main__":
    main()
