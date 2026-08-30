#!/usr/bin/env python3
"""
Build daily curve panel per symbol (BTC/ETH):
  date, perp_close, near_contract, near_close, near_expiry, near_dte,
  next_contract, next_close, next_expiry, next_dte, funding_rate_daily_mean,
  premium_daily_mean

Strictly causal: for date D we only use quarterly contracts whose data on D
is real (date <= expiry, from files already truncated by prior work's fix),
and near/next selection uses only date<=D info (no lookahead: near = the
contract with the smallest positive dte on D among contracts trading on D).

Output: evidence/panel_BTCUSDT.parquet, evidence/panel_ETHUSDT.parquet
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path("/home/qbee/futur")
QDIR = ROOT / "data/derivatives_backfill/binance_vision_quarterly"
OUT = ROOT / "reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/evidence"
OUT.mkdir(parents=True, exist_ok=True)

contracts = json.loads((QDIR / "contracts.json").read_text())

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def load_quarterly(symbol):
    """Load all quarterly contracts for symbol -> long df (date, contract, close, expiry, dte)."""
    frames = []
    for c, meta in contracts.items():
        if meta["symbol"] != symbol:
            continue
        f = QDIR / f"{c}_1d.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("UTC").dt.normalize()
        expiry = pd.Timestamp(meta["expiry"], tz="UTC")
        # hard-truncate at date<=expiry (pitfall #3 from w3: trailing stale forward-fill past expiry)
        df = df[df["date"] <= expiry].copy()
        # trim trailing duplicate-close runs (same pitfall)
        df = df.sort_values("date")
        dup_tail = (df["close"].diff() == 0)
        # remove trailing block of exact-duplicate closes at the end (stale ffill)
        keep = len(df)
        while keep > 1 and df["close"].iloc[keep - 1] == df["close"].iloc[keep - 2]:
            keep -= 1
        df = df.iloc[:keep]
        df["contract"] = c
        df["expiry"] = expiry
        df["dte"] = (expiry - df["date"]).dt.days
        df = df[df["dte"] > 0]  # strictly before expiry only
        frames.append(df[["date", "contract", "close", "expiry", "dte"]])
    out = pd.concat(frames, ignore_index=True)
    return out


def load_perp(symbol):
    f = ROOT / f"data/derivatives_backfill/um_klines_1d/{symbol}_1d.parquet"
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["open_time"]).dt.tz_convert("UTC").dt.normalize()
    df = df.rename(columns={"close": "perp_close"})
    return df[["date", "perp_close"]]


def load_funding(symbol):
    f = ROOT / f"data/derivatives_backfill/binance/funding/{symbol}.parquet"
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("UTC").dt.normalize()
    g = df.groupby("date")["funding_rate"].mean().rename("funding_rate_mean").reset_index()
    return g


def load_premium(symbol):
    f = ROOT / f"data/derivatives_backfill/binance_vision_premium/{symbol}_premium_5m.parquet"
    df = pd.read_parquet(f)
    df["date"] = pd.to_datetime(df["ts"]).dt.tz_convert("UTC").dt.normalize()
    g = df.groupby("date")["premium"].mean().rename("premium_mean").reset_index()
    return g


def build(symbol):
    q = load_quarterly(symbol)
    perp = load_perp(symbol)
    fund = load_funding(symbol)
    prem = load_premium(symbol)

    dates = sorted(q["date"].unique())
    rows = []
    for d in dates:
        day = q[q["date"] == d].sort_values("dte")
        if day.empty:
            continue
        near = day.iloc[0]
        nxt = day.iloc[1] if len(day) > 1 else None
        rows.append({
            "date": d,
            "near_contract": near["contract"], "near_close": near["close"],
            "near_expiry": near["expiry"], "near_dte": near["dte"],
            "next_contract": nxt["contract"] if nxt is not None else None,
            "next_close": nxt["close"] if nxt is not None else None,
            "next_expiry": nxt["expiry"] if nxt is not None else None,
            "next_dte": nxt["dte"] if nxt is not None else None,
        })
    panel = pd.DataFrame(rows).merge(perp, on="date", how="left")
    panel = panel.merge(fund, on="date", how="left")
    panel = panel.merge(prem, on="date", how="left")
    panel = panel.sort_values("date").reset_index(drop=True)

    # basis defs
    panel["basis_near_pct"] = (panel["near_close"] / panel["perp_close"] - 1.0) * 100.0
    panel["basis_near_ann"] = panel["basis_near_pct"] * (365.0 / panel["near_dte"])
    has_next = panel["next_close"].notna()
    panel.loc[has_next, "basis_next_pct"] = (panel.loc[has_next, "next_close"] / panel.loc[has_next, "perp_close"] - 1.0) * 100.0
    panel.loc[has_next, "basis_next_ann"] = panel.loc[has_next, "basis_next_pct"] * (365.0 / panel.loc[has_next, "next_dte"])
    # near-next calendar spread (quarterly-to-quarterly): next vs near, pct of near price
    panel.loc[has_next, "cal_spread_pct"] = (panel.loc[has_next, "next_close"] / panel.loc[has_next, "near_close"] - 1.0) * 100.0
    panel.loc[has_next, "cal_dte_diff"] = panel.loc[has_next, "next_dte"] - panel.loc[has_next, "near_dte"]
    panel.loc[has_next, "cal_spread_ann"] = panel.loc[has_next, "cal_spread_pct"] * (365.0 / panel.loc[has_next, "cal_dte_diff"])
    # funding-implied annualized carry (8h funding * 3/day * 365)
    panel["funding_ann_pct"] = panel["funding_rate_mean"] * 3 * 365 * 100.0
    panel["premium_ann_pct"] = panel["premium_mean"] * 3 * 365 * 100.0

    panel.to_parquet(OUT / f"panel_{symbol}.parquet")
    print(symbol, len(panel), panel["date"].min(), panel["date"].max())
    print(" has_next frac:", has_next.mean())
    return panel


for s in SYMBOLS:
    build(s)
