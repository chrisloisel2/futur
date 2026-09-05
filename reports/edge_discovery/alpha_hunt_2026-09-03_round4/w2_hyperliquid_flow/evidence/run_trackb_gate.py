#!/usr/bin/env python
"""W2 -- Track B: mechanisms that live on Hyperliquid's own tape (T9 / T10 / T11).

Window: 2026-07-18 -> 2026-08-29 (40 calendar days), 12 coins.  That is ~40 independent
L3 episodes, so the preregistration already expected DATA_LIMITED here; the point of this
script is to measure the effect sizes honestly rather than to assert an edge.

 T9  DISLOCATION.  HL publishes `premium` = (mark_px - oracle_px)/oracle_px, where the oracle
     is HL's own multi-venue index.  So the HL premium IS the HL-vs-rest-of-market
     dislocation, natively, with no external price needed.  Tested z-scored on a trailing
     window against the forward change of the premium (the reversion) and against the forward
     HL mark return.  A dislocation trade is TWO legs -> cost 28 bps, stress 56 bps.
 T10 FUNDING DIVERGENCE.  HL hourly funding (ctxs) vs Binance funding_rate (enriched 1h),
     both put on the same 1h grid, difference z-scored, tested against the forward Binance
     return (executable leg) and the forward HL-Binance spread.
 T11 L2 IMBALANCE.  HL top-of-book `imbalance` (bid_depth_usd vs ask_depth_usd), 1-min grid,
     against the forward HL mid return.  capacity_usd_estimate = median top-of-book depth.

Re-executable: .venv/bin/python evidence/run_trackb_gate.py
"""
import os, sys, json, numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import gate

REPO = "/home/qbee/futur"
OUT = os.path.dirname(os.path.abspath(__file__))
con = duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2;")
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "BNB", "LTC", "SUI"]
BMAP = {c: c+"USDT" for c in COINS}
res = []


def add(sub, col, name, cost2leg=False, cap=None, extra=None):
    g = gate(sub, col, name)
    if "gross_bps" in g:
        c, s = (28.0, 56.0) if cost2leg else (14.0, 28.0)
        g["net_bps"] = round(g["gross_bps"]-c, 2)
        g["net_bps_stress28"] = round(g["gross_bps"]-s, 2)
        g["cost_convention"] = "two-leg 28/56 bps" if cost2leg else "one-leg 14/28 bps"
        g["capacity_usd_estimate"] = cap
        g["track"] = "B_hyperliquid_native"
    if extra:
        g.update(extra)
    res.append(g)
    return g


# HL book depth is loaded first: it is the capacity constraint for every HL-executed leg.
DEPTH = con.execute(f"""select median(least(bid_depth_usd, ask_depth_usd)) d
      from read_parquet('{REPO}/data/hyperliquid/l2/date=*/*.parquet')
      where coin in ({','.join(chr(39)+c+chr(39) for c in COINS)})
        and bid_depth_usd > 0 and ask_depth_usd > 0""").fetchone()[0]
HL_DEPTH = int(DEPTH)
print("median HL top-of-book depth USD:", HL_DEPTH)

# ---------------- T9 : HL premium vs its own multi-venue oracle -------------------------
q = f"""select coin, cast(time_ms/3600000 as bigint) hr,
        last(premium order by time_ms) prem, last(mark_px order by time_ms) mark,
        last(funding order by time_ms) fund
      from read_parquet('{REPO}/data/hyperliquid/ctxs/date=*/*.parquet')
      where coin in ({','.join(chr(39)+c+chr(39) for c in COINS)}) group by 1,2 order by 1,2"""
C = con.execute(q).df()
C["ts"] = C.hr*3600000
C["day"] = pd.to_datetime(C.ts, unit="ms", utc=True).dt.strftime("%Y-%m-%d")
C["year"] = C.day.str[:4]
print("ctxs hourly rows:", len(C), C.day.min(), "->", C.day.max())

out = []
for c, g in C.groupby("coin"):
    g = g.sort_values("hr").copy()
    g["prem_bps"] = g.prem*1e4
    mu = g.prem_bps.rolling(168, min_periods=48).mean().shift(1)      # PIT: shift(1)
    sd = g.prem_bps.rolling(168, min_periods=48).std().shift(1)
    g["z"] = (g.prem_bps-mu)/sd.replace(0, np.nan)
    for h in (1, 4, 24):
        g[f"dprem_{h}"] = -(g.prem_bps.shift(-h)-g.prem_bps)           # reversion, signed by trade
        g[f"rmark_{h}"] = (np.log(g.mark.shift(-h)/g.mark))*1e4
    out.append(g)
C = pd.concat(out, ignore_index=True)
C["usr"] = C.coin
for h in (1, 4, 24):
    for zt, tag in ((2.0, "z2"), (1.0, "z1")):
        m = C[np.isfinite(C.z) & (np.abs(C.z) >= zt)].copy()
        m["S"] = -np.sign(m.z)*m[f"rmark_{h}"]        # fade the dislocation on the HL leg
        m["D"] = np.sign(m.z)*m[f"dprem_{h}"]*np.sign(1)   # premium closes back toward mean
        add(m, "S", f"T9 HL_PREMIUM_FADE_{tag}_h{h}h (HL leg only)", cost2leg=True,
            cap=HL_DEPTH, extra={"z_threshold": zt, "horizon_h": h,
                                 "capacity_basis": "median HL top-of-book depth (executed on HL)"})
        add(m, "D", f"T9 HL_PREMIUM_MEANREVERSION_{tag}_h{h}h (spread, not tradable alone)",
            cost2leg=True, cap=HL_DEPTH,
            extra={"z_threshold": zt, "horizon_h": h,
                   "capacity_basis": "median HL top-of-book depth (executed on HL)",
                   "note": "measures whether the dislocation closes at all"})

# ---------------- T10 : HL funding vs Binance funding -----------------------------------
rows = []
for c in COINS:
    p = f"{REPO}/data/enriched/{BMAP[c]}_1h_enriched.parquet"
    if not os.path.exists(p):
        print("  no enriched for", c); continue
    cols = {r[0] for r in con.execute(
        f"describe select * from read_parquet('{p}')").fetchall()}
    if "funding_rate" not in cols:
        print("  enriched has no funding_rate for", c); continue
    b = con.execute(f"""select cast(epoch_ms(datetime)/3600000 as bigint) hr, close,
                        funding_rate, quote_asset_volume qv from read_parquet('{p}')
                        where datetime >= to_timestamp(1784332800)""").df()
    b["coin"] = c
    rows.append(b)
B = pd.concat(rows, ignore_index=True)
M = C.merge(B, on=["coin", "hr"], how="inner")
print("T10 merged rows:", len(M), M.coin.nunique(), "coins")
o = []
for c, g in M.groupby("coin"):
    g = g.sort_values("hr").copy()
    # HL funding is hourly, Binance is 8h -> put both on a per-hour basis
    g["fdiff"] = (g.fund - g.funding_rate/8.0)*1e4
    mu = g.fdiff.rolling(168, min_periods=48).mean().shift(1)
    sd = g.fdiff.rolling(168, min_periods=48).std().shift(1)
    g["zf"] = (g.fdiff-mu)/sd.replace(0, np.nan)
    for h in (4, 24):
        g[f"rb_{h}"] = np.log(g.close.shift(-h)/g.close)*1e4
    o.append(g)
M = pd.concat(o, ignore_index=True)
M["usr"] = M.coin
for h in (4, 24):
    for zt, tag in ((2.0, "z2"), (1.0, "z1")):
        m = M[np.isfinite(M.zf) & (np.abs(M.zf) >= zt)].copy()
        # HL funding rich vs Binance => HL longs crowded => fade on the Binance leg
        m["S"] = -np.sign(m.zf)*m[f"rb_{h}"]
        cap = m.qv.values*h*0.005          # 0.5% of Binance quote volume over the holding window
        cap = cap[np.isfinite(cap) & (cap > 0)]
        add(m, "S", f"T10 HL_VS_BINANCE_FUNDING_DIVERGENCE_{tag}_h{h}h (Binance leg)",
            cost2leg=False, cap=int(np.median(cap)) if len(cap) else None,
            extra={"z_threshold": zt, "horizon_h": h,
                   "capacity_basis": "0.5% of Binance quote volume over the holding window"})

# ---------------- T11 : HL top-of-book imbalance ----------------------------------------
q = f"""select coin, cast(time_ms/60000 as bigint) mn,
        last(mid order by time_ms) mid, last(imbalance order by time_ms) imb,
        last(bid_depth_usd order by time_ms) bd, last(ask_depth_usd order by time_ms) ad,
        last(spread_bps order by time_ms) spr
      from read_parquet('{REPO}/data/hyperliquid/l2/date=*/*.parquet')
      where coin in ({','.join(chr(39)+c+chr(39) for c in COINS)}) group by 1,2 order by 1,2"""
L = con.execute(q).df()
L["ts"] = L.mn*60000
L["day"] = pd.to_datetime(L.ts, unit="ms", utc=True).dt.strftime("%Y-%m-%d")
L["year"] = L.day.str[:4]
print("l2 minute rows:", len(L), L.day.min(), "->", L.day.max())
o = []
for c, g in L.groupby("coin"):
    g = g.sort_values("mn").copy()
    for h in (5, 15, 60):
        g[f"r{h}"] = np.log(g.mid.shift(-h)/g.mid)*1e4
    o.append(g)
L = pd.concat(o, ignore_index=True)
L["usr"] = L.coin
cap_med = int(np.nanmedian(np.minimum(L.bd, L.ad)))
for h in (5, 15, 60):
    for thr, tag in ((0.0, "sign"), (0.5, "strong")):
        m = L[np.abs(L.imb) >= thr].copy() if thr > 0 else L.copy()
        m["S"] = np.sign(m.imb)*m[f"r{h}"]
        add(m, "S", f"T11 HL_L2_IMBALANCE_{tag}_h{h}min", cost2leg=False, cap=cap_med,
            extra={"imbalance_threshold": thr, "horizon_min": h,
                   "median_top_of_book_depth_usd": cap_med})

json.dump({"mechanisms": res,
           "window": {"start": str(C.day.min()), "end": str(C.day.max()),
                      "n_calendar_days": int(C.day.nunique()), "n_coins": len(COINS)}},
          open(f"{OUT}/trackb_gate_results.json", "w"), indent=1, default=str)
pd.set_option("display.width", 250)
df = pd.DataFrame(res)
print(df[["mechanism", "n_raw", "n_independent_L2_coin_day", "n_independent_L3_day",
          "gross_bps", "net_bps", "net_bps_stress28", "t_stat_declustered_L3day",
          "bootstrap_ci95", "eta_forward_confirmation_years",
          "capacity_usd_estimate"]].to_string(index=False))
