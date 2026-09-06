#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# When executed as `python scripts/<name>.py`, Python puts `scripts/` rather
# than the repository root on sys.path.  Bootstrap the repo explicitly before
# importing the research package so CLI behaviour matches pytest behaviour.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from market_physics_v3.external import build_stablecoin_pit_state


def _update_manifest(path: Path) -> None:
    if not path.exists():
        return
    df = pd.read_csv(path).fillna("")
    if "feed" not in df.columns or "status" not in df.columns:
        raise ValueError("manifest must contain feed,status")
    mask = df["feed"].astype(str) == "stablecoin_flows"
    if not mask.any():
        raise ValueError("manifest has no stablecoin_flows row")
    df.loc[mask, "status"] = "PIT_AGGREGATED"
    if "notes" in df.columns:
        df.loc[mask, "notes"] = "bridged from data/stablecoins with conservative T+1 UTC availability"
    df.to_csv(path, index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build slow PIT external context for Market Physics V3")
    ap.add_argument("--stablecoin-root", default="data/stablecoins")
    ap.add_argument("--out", default="data/market_physics_v3/context/stablecoin_daily.parquet")
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()

    table = build_stablecoin_pit_state(args.stablecoin_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out, index=False)
    if args.manifest:
        _update_manifest(Path(args.manifest))
    print("wrote", out)
    print("rows", len(table))
    print("date_start", table["date"].min())
    print("date_end", table["date"].max())
    print("available_start", table["research_available_at"].min())
    print("available_end", table["research_available_at"].max())
    print("source_quality", table["source_quality"].iloc[-1] if len(table) else "EMPTY")


if __name__ == "__main__":
    main()
