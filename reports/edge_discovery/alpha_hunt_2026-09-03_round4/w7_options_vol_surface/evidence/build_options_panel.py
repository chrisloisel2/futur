#!/usr/bin/env python
"""W7 round4 — build causal daily options-surface panel from Deribit BTC trades.

Outputs (small, written to this evidence/ dir):
  panel_daily_btc_options.parquet   daily surface + gamma-proxy features
  hourly_block_flow.parquet         hourly delta-weighted block flow
  strike_notional.parquet           (day, expiry, strike) cumulative traded notional (pin proxy)

All features are computed from trades with ts <= end of that UTC day. No forward info.
"""
import glob, os, sys
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = "/home/qbee/futur"
TRADES = sorted(glob.glob(f"{ROOT}/data/options_backfill/deribit/trades/BTC/*.parquet"))
OUT = os.path.dirname(os.path.abspath(__file__))

COLS = ["ts","price","iv","index_price","direction","amount","expiry","strike","cp","is_block"]

def bs_greeks(S, K, tau, sig):
    """Black-Scholes gamma and delta, r=0. sig in decimals, tau in years.
    Returns (gamma_per_unit_underlying, delta_call). Guarded against tau/sig -> 0."""
    tau = np.maximum(tau, 1.0/(365.0*24.0))          # floor 1 hour
    sig = np.clip(sig, 0.05, 5.0)
    sq = sig*np.sqrt(tau)
    d1 = (np.log(S/K) + 0.5*sig*sig*tau)/sq
    gamma = norm.pdf(d1)/(S*sq)
    return gamma, norm.cdf(d1)

daily_rows = []
hourly_rows = []
inst_day = []          # (day, expiry, strike, cp, net_customer_qty)  for gamma inventory
strike_day = []        # (day, expiry, strike, notional_btc)

for f in TRADES:
    df = pd.read_parquet(f, columns=COLS)
    if len(df) == 0:
        continue
    df = df[(df.iv > 1) & (df.iv < 300)]                 # W6's cleaning rule, reused verbatim
    df = df[df.index_price > 0]
    if len(df) == 0:
        continue
    df["day"] = df.ts.dt.floor("D")
    df["hour"] = df.ts.dt.floor("H")
    S = df.index_price.values; K = df.strike.values
    df["tau"] = (df.expiry - df.ts).dt.total_seconds()/(365.25*86400.0)
    df["tau_d"] = df.tau*365.25
    df["mny"] = np.log(K/S)
    sig = df.iv.values/100.0
    g, dc = bs_greeks(S, K, df.tau.values, sig)
    df["gamma"] = g
    df["delta"] = np.where(df.cp.values == "C", dc, dc-1.0)
    # taker side: buy => customer long => dealer short
    df["cust_sign"] = np.where(df.direction.values == "buy", 1.0, -1.0)
    df["qty"] = df.cust_sign*df.amount
    df["notional_btc"] = df.amount            # 1 contract = 1 BTC on Deribit BTC options

    # ---- term structure & skew (traded-IV medians, per day) ----
    atm = df[df.mny.abs() <= 0.05]
    for name, lo, hi in [("near",2,10),("mid",10,45),("far",45,180)]:
        sub = atm[(atm.tau_d >= lo) & (atm.tau_d < hi)]
        if len(sub):
            gg = sub.groupby("day").iv.median().rename(f"iv_{name}")
            daily_rows.append(gg)
    wing = df[(df.tau_d >= 7) & (df.tau_d <= 60)]
    put_w = wing[(wing.cp == "P") & (wing.mny <= -0.05) & (wing.mny >= -0.20)]
    call_w = wing[(wing.cp == "C") & (wing.mny >= 0.05) & (wing.mny <= 0.20)]
    if len(put_w):  daily_rows.append(put_w.groupby("day").iv.median().rename("iv_put_wing"))
    if len(call_w): daily_rows.append(call_w.groupby("day").iv.median().rename("iv_call_wing"))
    # ---- daily aggregates ----
    daily_rows.append(df.groupby("day").index_price.last().rename("index_px"))
    daily_rows.append(df.groupby("day").amount.sum().rename("vol_btc"))
    daily_rows.append(df.groupby("day").size().rename("n_trades"))
    bl = df[df.is_block]
    if len(bl):
        daily_rows.append(bl.groupby("day").amount.sum().rename("block_vol_btc"))
        daily_rows.append((bl.qty*bl.delta).groupby(bl.day).sum().rename("block_delta_flow"))
    # ---- hourly delta-weighted block flow (M7) ----
    if len(bl):
        h = bl.groupby("hour").apply(lambda x: pd.Series({
            "blk_delta_flow": float((x.qty*x.delta).sum()),
            "blk_gamma_flow": float(-(x.qty*x.gamma*x.index_price).sum()),
            "blk_vol": float(x.amount.sum()), "blk_n": len(x)}))
        hourly_rows.append(h)
    # ---- inventory for gamma proxy ----
    inst_day.append(df.groupby(["day","expiry","strike","cp"], observed=True).qty.sum().reset_index())
    strike_day.append(df.groupby(["day","expiry","strike"], observed=True).notional_btc.sum().reset_index())
    print(f"  {os.path.basename(f)} rows={len(df)}", flush=True)

# assemble: series with the same name come from different files (disjoint dates) -> stack
# them along the index first, THEN join across names. Concatenating axis=1 directly would
# create one duplicate column per monthly file.
by_name = {}
for s_ in daily_rows:
    by_name.setdefault(s_.name, []).append(s_)
daily = pd.concat({k: pd.concat(v).sort_index() for k, v in by_name.items()}, axis=1).sort_index()
inst = pd.concat(inst_day, ignore_index=True)
inst = inst.groupby(["day","expiry","strike","cp"], as_index=False).qty.sum()
strikes = pd.concat(strike_day, ignore_index=True)
strikes = strikes.groupby(["day","expiry","strike"], as_index=False).notional_btc.sum()

# ================= dealer gamma inventory (causal running position) =================
# open positions carried forward, expired positions dropped, gamma re-marked daily at index px
days = pd.DatetimeIndex(sorted(daily.index))
inst = inst.sort_values("day")
pos = {}                     # (expiry, strike, cp) -> net customer qty
grouped = dict(list(inst.groupby("day")))
rows = []
for d in days:
    if d in grouped:
        for r in grouped[d].itertuples():
            k = (r.expiry, r.strike, r.cp)
            pos[k] = pos.get(k, 0.0) + r.qty
    # drop expired
    for k in [k for k in pos if k[0] <= d]:
        del pos[k]
    if not pos:
        rows.append((d, np.nan, np.nan, np.nan)); continue
    S = daily.at[d, "index_px"]
    if not np.isfinite(S):
        rows.append((d, np.nan, np.nan, np.nan)); continue
    arr = np.array([(k[0].value, k[1], 1.0 if k[2]=="C" else 0.0, v) for k, v in pos.items()])
    tau = (arr[:,0] - d.value)/1e9/(365.25*86400.0)
    Kk = arr[:,1]; q = arr[:,3]
    ivd = daily.at[d, "iv_mid"] if "iv_mid" in daily.columns else np.nan
    if not np.isfinite(ivd):
        ivd = daily.at[d, "iv_near"] if "iv_near" in daily.columns else np.nan
    if not np.isfinite(ivd):
        ivd = 50.0
    gm, dc = bs_greeks(S, Kk, tau, np.full_like(Kk, ivd/100.0))
    dl = np.where(arr[:,2] == 1.0, dc, dc-1.0)
    # dealer = -customer
    dealer_gamma = float(-(q*gm).sum()*S*S/100.0)     # $ gamma per 1% move, index units
    dealer_delta = float(-(q*dl).sum())
    rows.append((d, dealer_gamma, dealer_delta, float(np.abs(q).sum())))

gam = pd.DataFrame(rows, columns=["day","dealer_gamma","dealer_delta","open_interest_proxy"]).set_index("day")
daily = daily.join(gam)

daily.to_parquet(f"{OUT}/panel_daily_btc_options.parquet")
if hourly_rows:
    pd.concat(hourly_rows).sort_index().to_parquet(f"{OUT}/hourly_block_flow.parquet")
strikes.to_parquet(f"{OUT}/strike_notional.parquet")
print("daily panel", daily.shape, daily.index.min(), daily.index.max())
print(daily.tail(3).to_string())
