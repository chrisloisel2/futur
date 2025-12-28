#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from infra.storage.feature_store import FeatureStore
from infra.storage.model_store import ModelStore
from infra.storage.object_store import S3ParquetWriter
from infra.storage.timeseries_db import SignalCacheWriter
from pipeline.decision.logic import DecisionLogic
from pipeline.decision.risk_aware_filters import RiskAwareFilters
from pipeline.decision.signal_builder import SignalBuilder
from pipeline.models.edge.forecaster import EdgeForecasterModel
from pipeline.models.gating.rules import GatingRules
from pipeline.models.regime.classifier import RegimeClassifierModel
from pipeline.models.comparator.novelty import ood_novelty_l2
from pipeline.models.comparator.disagreement import disagreement_score


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run inference")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["live", "batch"], required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=False)
    parser.add_argument("--end", required=False)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    model_store = ModelStore(cfg["s3_prefix"] if "s3_prefix" in cfg else "s3://my-bucket")
    gating_rules = GatingRules(cfg.get("gating", {}))
    regimes = cfg.get("regimes", ["impulse", "reversal", "breakout", "squeeze", "calm", "chop"])
    regime_model = RegimeClassifierModel(regimes)
    edge_model = EdgeForecasterModel()
    builder = SignalBuilder(threshold_bps=cfg.get("edge", {}).get("direction_bps", 5.0))
    logic = DecisionLogic()
    filters = RiskAwareFilters(cfg.get("risk_filters", {}))

    if args.mode == "batch":
        store = FeatureStore(cfg["s3_prefix"])
        df_state = store.load_state(args.symbol, args.start, args.end, feature_set=cfg.get("feature_set", "v1"))
    else:
        df_state = pd.DataFrame(
            {
                "event_time": [datetime.utcnow()],
                "symbol": [args.symbol],
                "feature_set": [cfg.get("feature_set", "v1")],
                "quality_flags": [0],
            }
        )
    if df_state.empty:
        return
    writer = S3ParquetWriter()
    cache_writer = SignalCacheWriter(cfg.get("mongo_uri", "mongodb://localhost:27017"), cfg.get("mongo_db", "market"), cfg.get("mongo_collection", "signal_cache"))
    signals = []
    for _, row in df_state.iterrows():
        gating = gating_rules.apply(row)
        regime_out = regime_model.predict(pd.DataFrame([row]))
        regime_probs = {k: float(regime_out.iloc[0][k]) for k in regimes}
        edge_out = edge_model.predict(pd.DataFrame([row])).iloc[0].to_dict()
        comp_out = {
            "novelty_score": ood_novelty_l2(pd.DataFrame([row]), {}, {}),
            "disagreement_score": disagreement_score(regime_probs, {"q50": edge_out.get("q50", 0.0)}),
        }
        signal = builder.build(row, gating.__dict__, {"regime_probs": regime_probs, "regime_entropy": float(regime_out["entropy"].iloc[0])}, edge_out, comp_out, args.run_id, cfg.get("model_version", "v1"))
        signal = filters.apply(signal, row)
        signal = logic.apply(signal)
        sig_dict = signal.to_dict()
        sig_dict["reasons"] = json.dumps(signal.reasons)
        signals.append(sig_dict)
        cache_writer.write_signal(sig_dict)
    out_df = pd.DataFrame(signals)
    dt = pd.to_datetime(out_df["event_time"]).dt.strftime("%Y-%m-%d")
    out_df["dt"] = dt
    writer.write(out_df, f"{cfg.get('s3_prefix', 's3://my-bucket')}/data/signals", partition_cols=["dt", "symbol", "model_stack", "feature_set"])


if __name__ == "__main__":
    main()
