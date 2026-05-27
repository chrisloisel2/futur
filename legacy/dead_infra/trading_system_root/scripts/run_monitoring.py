#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.timeseries_db import MongoBufferReader, DriftReportsWriter, ActionPlansWriter, AlertsWriter, MonitoringStateWriter
from pipeline.monitoring import MonitoringPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run monitoring pipeline")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    pipeline = MonitoringPipeline(cfg.get("monitoring", cfg))
    if args.mode == "live":
        state_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("state_collection", "state_cache"))
        signal_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("signal_collection", "signal_cache"))
        fills_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("fills_collection", "executed_fills_cache"))
        costs_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("costs_collection", "execution_costs_cache"))
        portfolio_reader = MongoBufferReader(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("portfolio_collection", "portfolio_state_cache"))
        now = datetime.utcnow()
        state_df = state_reader.fetch_events(args.symbol[0], now, now) if args.symbol else pd.DataFrame()
        signal_df = signal_reader.fetch_events(args.symbol[0], now, now) if args.symbol else pd.DataFrame()
        fills_df = fills_reader.fetch_events("portfolio", now, now) if hasattr(fills_reader, 'fetch_events') else pd.DataFrame()
        costs_df = costs_reader.fetch_events("portfolio", now, now) if hasattr(costs_reader, 'fetch_events') else pd.DataFrame()
        portfolio_df = portfolio_reader.fetch_events("portfolio", now, now) if hasattr(portfolio_reader, 'fetch_events') else pd.DataFrame()
        outputs = pipeline.step(now, state_df if state_df is not None else pd.DataFrame(), signal_df if signal_df is not None else pd.DataFrame(), fills_df if fills_df is not None else pd.DataFrame(), costs_df if costs_df is not None else pd.DataFrame(), portfolio_df if portfolio_df is not None else pd.DataFrame(), run_id=args.run_id)
        DriftReportsWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("drift_reports_collection", "drift_reports_cache")).write_reports({"scope": "portfolio", "event_time": now, "run_id": args.run_id, "reports": outputs["reports"]})
        ActionPlansWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("action_plans_collection", "action_plans_cache")).write_actions({"scope": "portfolio", "event_time": now, "run_id": args.run_id, "actions": [a.__dict__ for a in outputs["action_plan"].actions]})
        AlertsWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("alerts_collection", "alerts_cache")).write_alerts({"scope": "portfolio", "event_time": now, "alerts": [a.__dict__ for a in outputs["alerts"]]})
        MonitoringStateWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("monitoring_state_collection", "monitoring_state_cache")).write_monitoring_state({"scope": "portfolio", "event_time": now, "run_id": args.run_id, "state": {}})
    else:
        now = datetime.utcnow()
        outputs = pipeline.step(now, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), run_id=args.run_id)
        out_dir = Path("artifacts/monitoring/drift_reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        Path(out_dir / f"drift_{args.run_id}.json").write_text(str(outputs["reports"]))


if __name__ == "__main__":
    main()
