#!/usr/bin/env python3
"""Build the A13-H (Hourly Residual Relative Value) base panel.

Preregistration note: this script defines the panel construction (universe
membership, market factor, rolling beta, residual) BEFORE any H1/H2/H3
hypothesis is evaluated against it. See docs/A13H_PREREGISTRATION.md for the
frozen hypothesis definitions this panel feeds -- do not add columns here in
response to a backtest result.

Inputs (all real, already-collected data, read cross-worktree from
/home/qbee/futur-data-v2 where the materialized parquet lives):
  - data_v2/instruments/instrument_master.parquet: 312-symbol PIT universe
    with listing_ts/delisting_ts, reconciled from exchangeInfo/klines/
    funding/OI (data_v2/instruments/build_instrument_master.py).
  - data_v2/normalized/perp_ohlcv/venue=binance/symbol=*/year=*/*.parquet:
    5min OHLCV for all 312 instrument_master symbols (verified 312/312
    coverage), resampled here to 1h bars.

Universe eligibility U_t (all three required):
  1. listing_ts + MIN_AGE_DAYS <= t < delisting_ts (or delisting_ts is NaT)
  2. actual 1h bar exists at t (no fabricated fill across real data gaps)
  3. causal trailing 30d (720h) mean daily quote volume >= MIN_LIQUIDITY_USD
     (matches instrument_master's own curation floor, applied here per-
     timestamp rather than as a one-time historical filter -- a symbol that
     was liquid once is not liquid at every t of its life)

Market factor r_market,t: equal-weighted mean of r_i,t across i in U_t at
that same t. Simple by design (not cap/volume-weighted) -- documented choice,
not an oversight.

Rolling beta beta_i,t: Cov(r_i, r_market) / Var(r_market) over the 720h
window ENDING at t-1 (shifted, strictly causal -- beta_i,t must not see
r_i,t or r_market,t). Requires the full 720h of prior history for both legs;
symbols get a residual only once they clear MIN_AGE_DAYS *and* have 720h of
subsequent trailing history, i.e. ~120 days of total history in practice.

Residual: epsilon_i,t = r_i,t - beta_i,t * r_market,t.
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_INSTRUMENT_MASTER = "/home/qbee/futur-data-v2/data_v2/instruments/instrument_master.parquet"
DEFAULT_OHLCV_ROOT = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance"
MIN_AGE_DAYS = 90
LIQUIDITY_WINDOW_H = 720
MIN_LIQUIDITY_USD_DAILY = 5_000_000.0
BETA_WINDOW_H = 720


def _load_symbol_hourly(symbol: str, ohlcv_root: str) -> pd.DataFrame | None:
    files = sorted(glob.glob(f"{ohlcv_root}/symbol={symbol}/year=*/*.parquet"))
    if not files:
        return None
    frame = pd.concat([pd.read_parquet(f, columns=["timestamp", "close", "quote_asset_volume"]) for f in files], ignore_index=True)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp")
    frame = frame.set_index("timestamp")
    hourly_close = frame["close"].resample("1h").last()
    hourly_quote_volume = frame["quote_asset_volume"].resample("1h").sum()
    out = pd.DataFrame({"close": hourly_close, "quote_volume": hourly_quote_volume})
    out = out.dropna(subset=["close"])
    return out


def rolling_causal_beta(ret: pd.DataFrame, market_ret: pd.Series, window: int) -> pd.DataFrame:
    """beta_i,t = Cov(r_i, r_market)/Var(r_market) over the `window` bars strictly
    before t (t excluded). Factored out of main() so this specific causality
    contract -- the one property a rolling-window bug would most easily violate
    silently -- is directly unit-testable."""
    market_shifted = market_ret.shift(1)
    ret_shifted = ret.shift(1)
    market_var = market_shifted.rolling(window, min_periods=window).var()
    beta = pd.DataFrame(index=ret.index, columns=ret.columns, dtype=float)
    for symbol in ret.columns:
        cov = ret_shifted[symbol].rolling(window, min_periods=window).cov(market_shifted)
        beta[symbol] = cov / market_var
    return beta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument-master", default=DEFAULT_INSTRUMENT_MASTER)
    ap.add_argument("--ohlcv-root", default=DEFAULT_OHLCV_ROOT)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    im = pd.read_parquet(args.instrument_master)
    im = im[im["venue"] == "binance"].set_index("symbol")
    symbols = sorted(im.index.unique())
    print(f"[a13h-panel] instrument_master symbols={len(symbols)}", flush=True)

    per_symbol: dict[str, pd.DataFrame] = {}
    for i, symbol in enumerate(symbols):
        frame = _load_symbol_hourly(symbol, args.ohlcv_root)
        if frame is None or len(frame) < MIN_AGE_DAYS * 24:
            continue
        per_symbol[symbol] = frame
        if (i + 1) % 50 == 0:
            print(f"[a13h-panel] loaded {i + 1}/{len(symbols)} symbols", flush=True)
    print(f"[a13h-panel] symbols with usable hourly history: {len(per_symbol)}", flush=True)

    full_index = sorted(set().union(*(frame.index for frame in per_symbol.values())))
    full_index = pd.DatetimeIndex(full_index)
    print(f"[a13h-panel] global hourly grid: {full_index.min()} .. {full_index.max()} ({len(full_index)} hours)", flush=True)

    close = pd.DataFrame({s: f["close"].reindex(full_index) for s, f in per_symbol.items()})
    quote_volume = pd.DataFrame({s: f["quote_volume"].reindex(full_index) for s, f in per_symbol.items()})
    ret = np.log(close).diff()

    listing_ts = im["listing_ts"].reindex(list(per_symbol.keys()))
    delisting_ts = im["delisting_ts"].reindex(list(per_symbol.keys()))
    min_eligible_ts = listing_ts + pd.Timedelta(days=MIN_AGE_DAYS)

    age_ok = pd.DataFrame({s: full_index >= min_eligible_ts[s] for s in per_symbol}, index=full_index)
    not_delisted = pd.DataFrame(
        {s: (full_index < delisting_ts[s]) if pd.notna(delisting_ts[s]) else True for s in per_symbol},
        index=full_index,
    )
    has_bar = close.notna()
    trailing_daily_quote_volume = quote_volume.rolling(LIQUIDITY_WINDOW_H, min_periods=LIQUIDITY_WINDOW_H).mean() * 24.0
    liquid_ok = trailing_daily_quote_volume >= MIN_LIQUIDITY_USD_DAILY

    eligible = age_ok & not_delisted & has_bar & liquid_ok
    print(f"[a13h-panel] eligible (symbol, hour) cells: {int(eligible.to_numpy().sum())}", flush=True)

    universe_size = eligible.sum(axis=1)
    market_ret = ret.where(eligible).mean(axis=1)

    beta = rolling_causal_beta(ret, market_ret, BETA_WINDOW_H)
    residual = ret - beta.mul(market_ret, axis=0)

    frames = []
    for symbol in per_symbol:
        sub = pd.DataFrame({
            "asof_ns": (full_index.view("int64")),
            "symbol": symbol,
            "close": close[symbol].to_numpy(),
            "ret_1h": ret[symbol].to_numpy(),
            "eligible": eligible[symbol].to_numpy(),
            "market_ret_1h": market_ret.to_numpy(),
            "universe_size": universe_size.to_numpy(),
            "beta_720h": beta[symbol].to_numpy(),
            "residual": residual[symbol].to_numpy(),
            "trailing_daily_quote_volume_usd": trailing_daily_quote_volume[symbol].to_numpy(),
        })
        sub = sub[sub["eligible"] & sub["beta_720h"].notna() & sub["residual"].notna()]
        if len(sub):
            frames.append(sub)

    panel = pd.concat(frames, ignore_index=True).sort_values(["asof_ns", "symbol"]).reset_index(drop=True)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out_dir / "part-00000.parquet", index=False)
    summary = {
        "rows": len(panel),
        "symbols_with_residual": int(panel["symbol"].nunique()),
        "start_ns": int(panel["asof_ns"].min()) if len(panel) else None,
        "stop_ns": int(panel["asof_ns"].max()) if len(panel) else None,
        "min_age_days": MIN_AGE_DAYS,
        "liquidity_window_h": LIQUIDITY_WINDOW_H,
        "min_liquidity_usd_daily": MIN_LIQUIDITY_USD_DAILY,
        "beta_window_h": BETA_WINDOW_H,
        "market_factor": "equal_weighted_mean_return_of_eligible_universe",
    }
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "_SUCCESS").write_text("ok\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
