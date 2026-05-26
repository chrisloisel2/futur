#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fetch_2018_bear_data.py
=================================
Telecharge 2017-2018 BTCUSDT/ETHUSDT 1m depuis Binance (API publique)
et calcule les features necessaires au walk-forward.

2018 BTC : -84% (de $17k a $3.1k) — donne au modele une exposition bear
pour le fold 2022. Sans ces donnees, le modele n'a jamais vu de crash.

Usage :
  python3 scripts/fetch_2018_bear_data.py
  python3 scripts/fetch_2018_bear_data.py --symbols BTCUSDT --years 2018
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import requests

ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data_out" / "result"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_URL = "https://api.binance.com/api/v3/klines"
_SESSION    = requests.Session()
_SESSION.headers["User-Agent"] = "futur-bear-fetcher/1.0"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
YEARS   = [2017, 2018]


# ─────────────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_year_1m(symbol: str, year: int) -> pd.DataFrame:
    """Fetch all 1m bars for a given symbol and year from Binance."""
    start_ts = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    end_ts   = int(pd.Timestamp(f"{year+1}-01-01", tz="UTC").timestamp() * 1000)

    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_volume","n_trades",
            "taker_buy_base","taker_buy_quote","_x"]

    all_rows = []
    cur = start_ts
    n_req = 0

    while cur < end_ts:
        for attempt in range(5):
            try:
                r = _SESSION.get(BINANCE_URL, params={
                    "symbol": symbol, "interval": "1m",
                    "startTime": cur, "endTime": end_ts,
                    "limit": 1000,
                }, timeout=15)
                r.raise_for_status()
                rows = r.json()
                break
            except Exception as e:
                if attempt == 4:
                    print(f"    ERROR: {e}")
                    rows = []
                time.sleep(2 ** attempt)

        if not rows:
            break
        all_rows.extend(rows)
        cur = rows[-1][6] + 1  # close_time of last bar + 1ms
        n_req += 1
        if n_req % 100 == 0:
            print(f"    {symbol} {year}: {len(all_rows):,} bars ({n_req} req)…")
        time.sleep(0.05)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open","high","low","close","volume","quote_volume",
              "taker_buy_base","taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["n_trades"] = pd.to_numeric(df["n_trades"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df = df[df["timestamp"].dt.year == year]

    print(f"    {symbol} {year}: {len(df):,} bars fetched")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators (pandas — no talib required)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.clip(lower=1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _stoch_rsi(close: pd.Series, period: int = 14, smooth_k: int = 3) -> pd.Series:
    rsi_vals = _rsi(close, period)
    rsi_min  = rsi_vals.rolling(period, min_periods=1).min()
    rsi_max  = rsi_vals.rolling(period, min_periods=1).max()
    stoch    = (rsi_vals - rsi_min) / (rsi_max - rsi_min).clip(lower=1e-9) * 100.0
    return stoch.rolling(smooth_k, min_periods=1).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up   = high.diff()
    down = (-low.diff())
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr       = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_s     = tr.ewm(com=period - 1, adjust=False).mean()
    plus_di   = 100.0 * pd.Series(plus_dm, index=close.index).ewm(com=period - 1, adjust=False).mean() / atr_s.clip(lower=1e-9)
    minus_di  = 100.0 * pd.Series(minus_dm, index=close.index).ewm(com=period - 1, adjust=False).mean() / atr_s.clip(lower=1e-9)
    dx        = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-9)
    return dx.ewm(com=period - 1, adjust=False).mean()


def _squeeze_mom(close: pd.Series, high: pd.Series, low: pd.Series,
                 length: int = 20) -> pd.Series:
    bb_mid = close.rolling(length, min_periods=1).mean()
    bb_std = close.rolling(length, min_periods=1).std().fillna(0)
    kc_atr = _atr(high, low, close, length)
    kc_mid = bb_mid
    # TTM Squeeze momentum = linear regression of (close - midpoint)
    hh     = high.rolling(length, min_periods=1).max()
    ll     = low.rolling(length, min_periods=1).min()
    delta  = close - (hh + ll) / 2.0 - bb_mid
    # Approximate linear reg slope with ewm diff
    return delta.ewm(span=length, adjust=False).mean()


def compute_indicators_1m(df: pd.DataFrame) -> pd.DataFrame:
    """Add all computable 1m technical indicators to df (in place)."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    df["rsi_14"]     = _rsi(c, 14)
    df["rsi_60"]     = _rsi(c, 60)
    df["atr_14"]     = _atr(h, l, c, 14)
    df["atr_pct_14"] = df["atr_14"] / c.clip(lower=1e-9)
    df["atr_240"]    = _atr(h, l, c, 240)

    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    macd  = ema12 - ema26
    df["macd_line"] = macd
    df["macd_hist"] = macd - _ema(macd, 9)

    df["adx_14"]    = _adx(h, l, c, 14)
    df["stoch_rsi_k"] = _stoch_rsi(c, 14, 3)
    df["squeeze_mom"] = _squeeze_mom(c, h, l, 20)

    for span in [8, 21, 55, 144]:
        ema = _ema(c, span)
        df[f"ema_dist_{span}"] = (c / ema.clip(lower=1e-9)) - 1.0

    v_mean_60  = v.rolling(60, min_periods=1).mean()
    v_std_60   = v.rolling(60, min_periods=1).std().clip(lower=1e-9)
    v_mean_240 = v.rolling(240, min_periods=1).mean()
    v_std_240  = v.rolling(240, min_periods=1).std().clip(lower=1e-9)
    df["volume_z_60m"]  = (v - v_mean_60)  / v_std_60
    df["volume_z_240m"] = (v - v_mean_240) / v_std_240

    # Macro fields not available for 2017-2018 (perpetuals didn't exist)
    for col in ("funding_rate","oi_sum","oi_value_sum","oi_chg_60m","oi_chg_240m",
                "top_trader_lsr","lsr_z_1d","funding_z_7d","funding_z_30d",
                "funding_extreme","fear_greed","fred_vixcls"):
        df[col] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Build and save
# ─────────────────────────────────────────────────────────────────────────────

def build_and_save(symbol: str, year: int) -> None:
    out_path = DATA_DIR / f"{year}_{symbol}_features.parquet"
    if out_path.exists():
        print(f"  {out_path.name} already exists — skipping")
        return

    print(f"\n  Downloading {symbol} {year} from Binance…")
    df = fetch_year_1m(symbol, year)
    if df.empty:
        print(f"  No data for {symbol} {year}")
        return

    print(f"  Computing indicators…")
    df = compute_indicators_1m(df)

    # Keep columns compatible with walk_forward_v5.py
    keep_cols = [
        "timestamp","open","high","low","close","volume","quote_volume","n_trades",
        "rsi_14","rsi_60","atr_14","atr_pct_14","atr_240",
        "macd_line","macd_hist","adx_14","stoch_rsi_k","squeeze_mom",
        "ema_dist_8","ema_dist_21","ema_dist_55","ema_dist_144",
        "volume_z_60m","volume_z_240m",
        "funding_rate","oi_sum","oi_value_sum","oi_chg_60m","oi_chg_240m",
        "top_trader_lsr","lsr_z_1d","funding_z_7d","funding_z_30d",
        "funding_extreme","fear_greed","fred_vixcls",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]

    df.to_parquet(out_path, index=False)
    sz = out_path.stat().st_size / 1e6
    print(f"  Saved → {out_path.name}  ({len(df):,} bars, {sz:.0f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch 2017-2018 bear data")
    parser.add_argument("--symbols", type=str, default=",".join(SYMBOLS))
    parser.add_argument("--years",   type=str, default=",".join(map(str, YEARS)))
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    years   = [int(y) for y in args.years.split(",")]

    print("=" * 60)
    print("FETCH 2017-2018 BEAR DATA")
    print("=" * 60)
    print(f"  Symbols : {symbols}")
    print(f"  Years   : {years}")
    print()

    for year in years:
        for sym in symbols:
            build_and_save(sym, year)

    print("\nDone. Update walk_forward_v5.py : all_years starts from 2017")


if __name__ == "__main__":
    main()
