#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from infra.config.loader import load_config
from pipeline.research.splitters import Embargo, PurgedKFoldSplitter, WalkForwardSplitter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate research splits")
    parser.add_argument("--config", required=True, help="Path to split config YAML")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", default="walk_forward", choices=["walk_forward", "purged_kfold"])
    parser.add_argument("--output", default="artifacts/splits")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    idx = pd.date_range(args.start, args.end, freq="1min")
    embargo = Embargo.from_minutes(cfg.get("embargo_minutes", 5))
    if args.mode == "walk_forward":
        splitter = WalkForwardSplitter(
            train_window=pd.Timedelta(days=cfg["walk_forward"]["train_days"]),
            test_window=pd.Timedelta(days=cfg["walk_forward"]["test_days"]),
            step=pd.Timedelta(days=cfg["walk_forward"].get("step_days", 1)),
            purge_window=pd.Timedelta(minutes=cfg.get("purge_minutes", 0)),
            embargo=embargo,
        )
    else:
        splitter = PurgedKFoldSplitter(
            n_splits=cfg["purged_kfold"]["n_splits"],
            purge_window=pd.Timedelta(minutes=cfg.get("purge_minutes", 0)),
            embargo=embargo,
        )
    splits = splitter.split(idx)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [s.__dict__ for s in splits]
    (out_dir / f"splits_{args.run_id}.json").write_text(pd.DataFrame(records).to_json(orient="records", indent=2))


if __name__ == "__main__":
    main()
