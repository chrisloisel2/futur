#!/usr/bin/env python
"""W7 round4 — daily & hourly perp return panel (UTC) for BTC, ETH and an alt cross-section."""
import glob, os
import numpy as np, pandas as pd

BASE = "/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance"
OUT = os.path.dirname(os.path.abspath(__file__))

def load(sym, y0=2021):
    fs = sorted(glob.glob(f"{BASE}/symbol={sym}/year=*/perp_5m.parquet"))
    fs = [f for f in fs if int(f.split("year=")[1][:4]) >= y0]
    if not fs: return None
    df = pd.concat([pd.read_parquet(f, columns=["timestamp","close","quote_asset_volume"]) for f in fs])
    return df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")

CORE = ["BTCUSDT","ETHUSDT"]
ALTS = ["SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
        "LTCUSDT","BCHUSDT","ATOMUSDT","FILUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT",
        "MATICUSDT","UNIUSDT","AAVEUSDT","ETCUSDT","TRXUSDT","XLMUSDT","ALGOUSDT","EOSUSDT",
        "SANDUSDT","MANAUSDT","AXSUSDT","GALAUSDT","ICPUSDT","INJUSDT","SUIUSDT","SEIUSDT",
        "TIAUSDT","RUNEUSDT","FTMUSDT","GRTUSDT","CRVUSDT","LDOUSDT","IMXUSDT","STXUSDT"]

daily, hourly, dollarvol = {}, {}, {}
for s in CORE + ALTS:
    df = load(s)
    if df is None or len(df) < 20000:
        print("skip", s); continue
    d = df.close.resample("1D").last()
    h = df.close.resample("1H").last()
    dv = df.quote_asset_volume.resample("1D").sum()
    if d.notna().sum() < 400: print("skip(short)", s); continue
    daily[s] = d; hourly[s] = h; dollarvol[s] = dv
    print(f"{s:12s} {d.index.min().date()} -> {d.index.max().date()} n={d.notna().sum()}")

pd.DataFrame(daily).to_parquet(f"{OUT}/perp_daily_close.parquet")
pd.DataFrame({k: hourly[k] for k in CORE}).to_parquet(f"{OUT}/perp_hourly_close_core.parquet")
pd.DataFrame(dollarvol).to_parquet(f"{OUT}/perp_daily_dollarvol.parquet")
print("saved", len(daily), "symbols")
