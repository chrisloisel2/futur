from __future__ import annotations

from pathlib import Path
from typing import Dict
import numpy as np
import pandas as pd


def build_stablecoin_pit_state(root: str = "data/stablecoins") -> pd.DataFrame:
    """Convert existing daily stablecoin files into a conservative PIT table.

    Historical DefiLlama rows do not carry the exact timestamp at which each
    observation was first published. We therefore apply a conservative T+1 UTC
    availability rule. This is suitable for slow-regime research, not intraday
    event alpha, and the quality is explicitly tagged PIT_AGGREGATED_T1.
    """
    root = Path(root)
    supply_path = root / "supply_daily.parquet"
    price_path = root / "prices_daily.parquet"
    if not supply_path.exists() or not price_path.exists():
        raise FileNotFoundError("stablecoin supply_daily.parquet and prices_daily.parquet are required")

    supply = pd.read_parquet(supply_path).copy()
    prices = pd.read_parquet(price_path).copy()
    supply["date"] = pd.to_datetime(supply["date"], utc=True)
    prices["date"] = pd.to_datetime(prices["date"], utc=True)
    df = supply.merge(prices, on="date", how="left", validate="one_to_one").sort_values("date")
    df["research_available_at"] = df["date"] + pd.Timedelta(days=1)

    for col in ["trio", "all_usd", "usdt", "usdc", "dai"]:
        x = pd.to_numeric(df[col], errors="coerce")
        df[col + "_chg_1d"] = x.pct_change(1, fill_method=None)
        df[col + "_chg_7d"] = x.pct_change(7, fill_method=None)
        df[col + "_chg_30d"] = x.pct_change(30, fill_method=None)
    peg_cols = [c for c in ["p_usdt", "p_usdc", "p_dai"] if c in df]
    if peg_cols:
        peg = df[peg_cols].apply(pd.to_numeric, errors="coerce")
        df["stablecoin_max_abs_depeg"] = (peg - 1.0).abs().max(axis=1)
        df["stablecoin_mean_depeg"] = (peg - 1.0).mean(axis=1)
    df["source_quality"] = "PIT_AGGREGATED_T1"
    return df


def stablecoin_state_asof(table: pd.DataFrame, decision_ts) -> Dict[str, float]:
    ts = pd.Timestamp(decision_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    eligible = table[pd.to_datetime(table["research_available_at"], utc=True) <= ts]
    if eligible.empty:
        return {}
    row = eligible.iloc[-1]
    out = {}
    for key, val in row.items():
        if key in {"date", "research_available_at", "source_quality"}:
            continue
        if isinstance(val, (int, float, np.integer, np.floating)) and np.isfinite(val):
            out["stablecoin__" + key] = float(val)
    return out
