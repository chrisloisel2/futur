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
    OrdersPlanReader,
    ExecutionStateWriter,
    OrderEventsWriter,
    ExecutedFillsWriter,
    ExecutionCostsWriter,
)
from pipeline.execution import ExecutionEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run execution engine")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    engine = ExecutionEngine(cfg.get("execution", cfg))
    if args.mode == "live":
        plan_reader = OrdersPlanReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("orders_plan_collection", "orders_plan_cache"))
        pf_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("portfolio_collection", "portfolio_state_cache"))
        st_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("state_collection", "state_cache"))
        plan_doc = plan_reader.read_latest() or {}
        plan_payload = plan_doc.get("orders_plan", plan_doc) if isinstance(plan_doc, dict) else {}
        plan_orders = []
        for o in plan_payload.get("orders", []):
            plan_orders.append(o)
        plan_obj = type("Plan", (), plan_payload | {"orders": [], "stops": [], "time_stops": [], "run_id": plan_payload.get("run_id", args.run_id)})()
        plan_obj.orders = []
        for o in plan_payload.get("orders", []):
            plan_obj.orders.append(type("O", (), o)())
        plan_obj.stops = []
        plan_obj.time_stops = []
        portfolio_state = pf_reader.fetch_events("portfolio", datetime.utcnow(), datetime.utcnow())
        portfolio_state = portfolio_state.iloc[-1].get("portfolio", {}) if portfolio_state is not None and not portfolio_state.empty else {}
        states = {}
        for sym in args.symbol:
            df = st_reader.fetch_events(sym, datetime.utcnow(), datetime.utcnow())
            if df is not None and not df.empty:
                states[sym] = df.iloc[-1]
        executed, exec_state, order_events, costs = engine.step(plan_obj, portfolio_state, states)
        ExecutionStateWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("execution_state_collection", "execution_state_cache")).write_state(exec_state.__dict__ | {"scope": "portfolio"})
        OrderEventsWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("order_events_collection", "order_events_cache")).write_events({"scope": "portfolio", "event_time": order_events.event_time, "events": [e.__dict__ for e in order_events.events]})
        ExecutedFillsWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("executed_fills_collection", "executed_fills_cache")).write_fills({"scope": "portfolio", "event_time": executed.event_time, "fills": [f.__dict__ for f in executed.fills]})
        ExecutionCostsWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("execution_costs_collection", "execution_costs_cache")).write_costs({"scope": "portfolio", "event_time": executed.event_time, **costs})
    else:
        plan_path = cfg.get("orders_plan_path")
        state_path = cfg.get("state_path")
        if not plan_path:
            return
        df_plan = pd.read_parquet(plan_path)
        dummy_plan = type("Plan", (), {"orders": [], "stops": [], "time_stops": [], "run_id": args.run_id, "risk_state_ref": "portfolio"})()
        for _, row in df_plan.iterrows():
            dummy_plan.orders.append(type("O", (), row.to_dict())())
        states = {}
        if state_path:
            df_state = pd.read_parquet(state_path)
            for sym in df_state["symbol"].unique():
                states[sym] = df_state[df_state["symbol"] == sym].iloc[-1]
        executed, exec_state, order_events, costs = engine.step(dummy_plan, {}, states)
        out_dir = Path("artifacts/execution/executed_fills")
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([f.__dict__ for f in executed.fills]).to_parquet(out_dir / f"fills_{args.run_id}.parquet", index=False)


if __name__ == "__main__":
    main()
