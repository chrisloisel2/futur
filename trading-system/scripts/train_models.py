#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common.logging.setup import configure_logging
from infra.config.loader import load_config
from pipeline.models.edge.forecaster import EdgeForecasterModel
from pipeline.models.regime.classifier import RegimeClassifierModel
from pipeline.models.base import save_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train regime + edge models")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--state-path", required=True, help="Parquet with state features (+ labels columns)")
    parser.add_argument("--output-root", default="artifacts/models", help="Root dir for artifacts")
    parser.add_argument("--regime-label-col", default="regime", help="Column name for regime labels")
    parser.add_argument("--edge-return-col", default="return_fwd", help="Column for forward return")
    parser.add_argument("--edge-phit-col", default="tp_hit", help="Column for TP hit flag")
    parser.add_argument("--edge-rv-col", default="rv_fwd_mean", help="Column for forward realized vol/rv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    cfg = load_config(args.config)
    df = pd.read_parquet(args.state_path)
    df = df.sort_values("event_time") if "event_time" in df.columns else df

    regimes = cfg["regimes"] if "regimes" in cfg else ["impulse", "reversal", "breakout", "squeeze", "calm", "chop"]
    regime_label_col = args.regime_label_col
    if regime_label_col not in df.columns:
        raise ValueError(f"Missing regime labels column {regime_label_col} in {args.state_path}")

    # split train/val (simple time split 80/20)
    split_idx = int(len(df) * 0.8)
    df_train = df.iloc[:split_idx]
    df_val = df.iloc[split_idx:]

    regime_model = RegimeClassifierModel(regimes)
    regime_model.fit(df_train, df_train[regime_label_col])
    regime_val = regime_model.predict(df_val)

    # Edge labels
    if args.edge_return_col not in df.columns:
        raise ValueError(f"Missing edge return column {args.edge_return_col}")
    labels_cols = [c for c in [args.edge_return_col, args.edge_phit_col, args.edge_rv_col] if c in df.columns]
    labels_df = df_train[labels_cols].rename(columns={args.edge_return_col: "return_fwd", args.edge_phit_col: "tp_hit", args.edge_rv_col: "rv_fwd_mean"})

    edge_model = EdgeForecasterModel()
    edge_model.fit(df_train, labels_df)
    edge_val = edge_model.predict(df_val)

    out_root = Path(args.output_root)
    for comp, model in [("regime", regime_model), ("edge", edge_model)]:
        target = out_root / comp / args.run_id
        target.mkdir(parents=True, exist_ok=True)
        model.save(str(target / "model.pkl"))
        save_metadata(
            target / "metadata.json",
            {
                "run_id": args.run_id,
                "component": comp,
                "train_rows": len(df_train),
                "val_rows": len(df_val),
                "feature_cols": model.feature_cols if hasattr(model, "feature_cols") else None,
            },
        )


if __name__ == "__main__":
    main()
