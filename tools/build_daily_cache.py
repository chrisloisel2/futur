#!/usr/bin/env python3
"""
build_daily_cache.py -- agrege les sources brutes en panels QUOTIDIENS caches.

Trois sources, aucune n'etait branchee correctement dans v3 :

  um_klines_1d          787 symboles, 2020-01 -> 2026-06-30, avec taker_buy REEL
  binance_vision_metrics 312 symboles, 5 min, 2021-12 -> 2026-09 : OI + 4 ratios L/S
  binance/funding        329 symboles, 8h, 2020-01 -> 2026-08

v3 pointait sur data/enriched (50 symboles) et sur open_interest_hist (30 jours
glissants d'API, donc zero chevauchement avec la fenetre de backtest).

Sortie : data/_cache/panel_daily.npz + panel_daily_meta.json
"""
import glob, json, os, sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/qbee/futur")
OUT = ROOT / "data" / "_cache"
OUT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ klines 1d
print("=== um_klines_1d ===", flush=True)
kpaths = sorted(glob.glob(str(ROOT / "data/derivatives_backfill/um_klines_1d/*_1d.parquet")))
KF = ["open", "high", "low", "close", "quote_volume", "count", "taker_buy_quote_volume"]
kacc = {k: {} for k in KF}
for i, p in enumerate(kpaths):
    sym = os.path.basename(p).replace("_1d.parquet", "")
    try:
        d = pd.read_parquet(p)
    except Exception:
        continue
    if len(d) < 60 or "open_time" not in d.columns:
        continue
    t = pd.to_datetime(d["open_time"], utc=True).dt.normalize()
    d = d.assign(_t=t).drop_duplicates("_t", keep="last").set_index("_t").sort_index()
    for k in KF:
        if k in d.columns:
            kacc[k][sym] = pd.to_numeric(d[k], errors="coerce")
    if (i + 1) % 200 == 0:
        print(f"  {i+1}/{len(kpaths)}", flush=True)

PX = {k: pd.DataFrame(v).sort_index() for k, v in kacc.items() if v}
idx = PX["close"].index
syms = list(PX["close"].columns)
print(f"  panel {len(idx)}j x {len(syms)} symboles  [{idx.min().date()} -> {idx.max().date()}]")

# ------------------------------------------------------------- vision metrics
print("=== binance_vision_metrics (OI + ratios L/S) ===", flush=True)
MCOLS = {
    "oi":        "sum_open_interest",
    "oi_usd":    "sum_open_interest_value",
    "tt_acct":   "count_toptrader_long_short_ratio",
    "tt_pos":    "sum_toptrader_long_short_ratio",
    "glob_acct": "count_long_short_ratio",
    "taker_ls":  "sum_taker_long_short_vol_ratio",
}
mpaths = sorted(glob.glob(str(ROOT / "data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet")))
macc = {k: {} for k in MCOLS}
macc["oi_intraday_chg"] = {}
for i, p in enumerate(mpaths):
    sym = os.path.basename(p).replace("_metrics_5m.parquet", "")
    if sym not in PX["close"].columns:
        continue
    try:
        d = pd.read_parquet(p, columns=["create_time"] + list(MCOLS.values()))
    except Exception:
        continue
    if len(d) < 500:
        continue
    d = d.set_index(pd.to_datetime(d["create_time"], utc=True)).sort_index()
    day = d.resample("1D")
    for k, col in MCOLS.items():
        s = day[col].last() if k in ("oi", "oi_usd") else day[col].mean()
        macc[k][sym] = pd.to_numeric(s, errors="coerce")
    f, l = day["sum_open_interest"].first(), day["sum_open_interest"].last()
    macc["oi_intraday_chg"][sym] = (l / f - 1.0)
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(mpaths)}", flush=True)

MET = {k: pd.DataFrame(v).sort_index().reindex(idx) for k, v in macc.items() if v}
for k, v in MET.items():
    print(f"  {k:<16} {int(v.notna().sum().sum()):>9} points, {int(v.notna().any().sum()):>3} symboles")

# -------------------------------------------------------------------- funding
print("=== funding ===", flush=True)
fpaths = sorted(glob.glob(str(ROOT / "data/derivatives_backfill/binance/funding/*.parquet")))
facc, macc2 = {}, {}
for p in fpaths:
    sym = os.path.basename(p).replace(".parquet", "")
    if sym not in PX["close"].columns:
        continue
    try:
        d = pd.read_parquet(p)
    except Exception:
        continue
    if "timestamp" not in d.columns or "funding_rate" not in d.columns or len(d) < 100:
        continue
    d = d.set_index(pd.to_datetime(d["timestamp"], utc=True)).sort_index()
    facc[sym] = pd.to_numeric(d["funding_rate"], errors="coerce").resample("1D").sum(min_count=1)
    if "mark_price" in d.columns:
        macc2[sym] = pd.to_numeric(d["mark_price"], errors="coerce").resample("1D").last()

FUND = pd.DataFrame(facc).sort_index().reindex(idx)
MARK = pd.DataFrame(macc2).sort_index().reindex(idx) if macc2 else None
print(f"  funding {int(FUND.notna().sum().sum())} points, {int(FUND.notna().any().sum())} symboles")

# ---------------------------------------------------------------------- ecrit
store = {}
for k, v in PX.items():
    store[f"px_{k}"] = v.reindex(columns=syms).to_numpy(dtype=np.float32)
for k, v in MET.items():
    store[f"met_{k}"] = v.reindex(columns=syms).to_numpy(dtype=np.float32)
store["fund"] = FUND.reindex(columns=syms).to_numpy(dtype=np.float32)
if MARK is not None:
    store["mark"] = MARK.reindex(columns=syms).to_numpy(dtype=np.float32)

np.savez_compressed(OUT / "panel_daily.npz", **store)
json.dump({"index": [str(x) for x in idx], "symbols": syms,
           "fields": sorted(store.keys())},
          open(OUT / "panel_daily_meta.json", "w"))
print(f"\n-> {OUT/'panel_daily.npz'}  ({(OUT/'panel_daily.npz').stat().st_size/1e6:.0f} Mo)")
print(f"   champs : {sorted(store.keys())}")
