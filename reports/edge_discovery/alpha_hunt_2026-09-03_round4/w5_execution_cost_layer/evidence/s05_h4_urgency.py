"""W5/s05 - H4: how much worse is execution DURING the events this project's alphas trade?

Two independent event definitions:
  (A) the project's own cascade_dataset (overlaps the probe 2026-07-12..2026-08-27)
  (B) endogenous price shocks measured on the probe's own 30s mid grid (dose-response,
      far larger N, and exactly what an event alpha triggers on)
Declustering: same-symbol / 24h episodes (L1), calendar day (L2), symbol-day (L3).
"""
import duckdb, os, numpy as np, pandas as pd, json
S = os.environ["W5_SCRATCH"]
con = duckdb.connect(); con.execute("PRAGMA threads=8")
p = pd.read_parquet(f"{S}/panel.parquet")
p["ts"] = pd.to_datetime(p.ts, utc=True)
p = p.sort_values(["symbol", "ts"]).reset_index(drop=True)
res = {}

SYMS = sorted(p.symbol.unique())
# ---------------- (A) project cascade events ----------------
ev = con.execute(f"""SELECT symbol, event_time, kind FROM read_parquet('data/events/cascade_dataset.parquet')
                     WHERE event_time >= '2026-07-12' AND event_time < '2026-08-28'""").df()
ev["ts"] = pd.to_datetime(ev.event_time, utc=True)
ev = ev[ev.symbol.isin(SYMS)].sort_values(["symbol", "ts"]).reset_index(drop=True)
# decluster L1: same symbol, keep first event per 24h
keep, last = [], {}
for i, r in ev.iterrows():
    if r.symbol not in last or (r.ts - last[r.symbol]).total_seconds() > 86400:
        keep.append(i); last[r.symbol] = r.ts
evd = ev.loc[keep].reset_index(drop=True)
print(f"[A] cascade events raw={len(ev)} L1-declustered(24h/symbol)={len(evd)} "
      f"L2 days={evd.ts.dt.date.nunique()} L3 symbol-days={evd.groupby([evd.symbol, evd.ts.dt.date]).ngroups}")

rows = []
for _, e in evd.iterrows():
    d = p[p.symbol == e.symbol]
    w = d[(d.ts >= e.ts) & (d.ts < e.ts + pd.Timedelta(minutes=15))]
    b = d[(d.ts >= e.ts - pd.Timedelta(hours=24)) & (d.ts < e.ts - pd.Timedelta(minutes=30))]
    if len(w) < 5 or len(b) < 200: continue
    rows.append(dict(symbol=e.symbol, ts=e.ts, kind=e.kind, day=str(e.ts.date()),
        spr_evt=w.spread_bps.median(), spr_base=b.spread_bps.median(),
        rvol_evt=w.rvol_10m.median(), rvol_base=b.rvol_10m.median(),
        advb_evt=w.adv_buy.mean(), advb_base=b.adv_buy.mean(),
        advs_evt=w.adv_sell.mean(), advs_base=b.adv_sell.mean(),
        fb_evt=w.fill_buy.mean(), fb_base=b.fill_buy.mean(),
        fs_evt=w.fill_sell.mean(), fs_base=b.fill_sell.mean()))
A = pd.DataFrame(rows)
A["spr_mult"] = A.spr_evt / A.spr_base
A["dspr_bps"] = A.spr_evt - A.spr_base
A["dadv_buy"] = A.advb_evt - A.advb_base
print(f"\n[A] usable declustered episodes n={len(A)} over {A.day.nunique()} days, {A.symbol.nunique()} symbols")
print(f"    spread multiplier: median={A.spr_mult.median():.3f} mean={A.spr_mult.mean():.3f} "
      f"p75={A.spr_mult.quantile(.75):.3f} p90={A.spr_mult.quantile(.90):.3f}")
print(f"    extra half-spread cost round-trip = {A.dspr_bps.median():.3f} bps (median dspread, x1 rt)")
print(f"    maker markout(BUY) delta        = {A.dadv_buy.median():.3f} bps")
# day-level block bootstrap on the spread multiplier
rng = np.random.default_rng(7)
days = A.day.unique(); bs = []
for _ in range(4000):
    s = rng.choice(days, len(days), replace=True)
    bs.append(pd.concat([A[A.day == d] for d in s]).spr_mult.median())
ci = np.percentile(bs, [2.5, 97.5])
print(f"    block-bootstrap CI95 (blocks=day, {len(days)} days) on median spread multiplier: [{ci[0]:.3f}, {ci[1]:.3f}]")
res["A_cascade"] = dict(n_raw=int(len(ev)), n_ind_L1=int(len(evd)), n_used=int(len(A)),
    n_ind_L2_days=int(A.day.nunique()), n_ind_L3_symday=int(A.groupby(["symbol","day"]).ngroups),
    spread_mult_median=float(A.spr_mult.median()), spread_mult_ci95=[float(ci[0]), float(ci[1])],
    dspread_bps_median=float(A.dspr_bps.median()), dmarkout_buy_bps=float(A.dadv_buy.median()))

# ---------------- (B) endogenous shock dose-response ----------------
print("\n[B] endogenous shock dose-response (probe 30s grid, PIT: shock uses only past 5min)")
p["shock_q"] = p.groupby("symbol").shock_5m_bps.transform(lambda x: pd.qcut(x, 10, labels=False, duplicates="drop"))
b = p.groupby("shock_q").agg(n=("spread_bps","size"), shock=("shock_5m_bps","median"),
        spread=("spread_bps","median"), rvol=("rvol_10m","median"),
        fill_buy=("fill_buy","mean"), adv_buy=("adv_buy","mean"), adv_sell=("adv_sell","mean")).round(4)
b["spread_vs_d0"] = (b.spread / b.spread.iloc[0]).round(3)
print(b.to_string())
res["B_shock_deciles"] = b.reset_index().to_dict("records")

# extreme tail: the top 1% shock, which is what a cascade alpha actually fires on
for q in [0.99, 0.995, 0.999]:
    thr = p.groupby("symbol").shock_5m_bps.transform(lambda x: x.quantile(q))
    m = p.shock_5m_bps >= thr
    sub, base = p[m], p[~m]
    # decluster to symbol-day means before testing
    sd = sub.groupby(["symbol","date"]).agg(spr=("spread_bps","median"), adv=("adv_buy","mean")).reset_index()
    bd = base.groupby(["symbol","date"]).agg(spr=("spread_bps","median"), adv=("adv_buy","mean")).reset_index()
    j = sd.merge(bd, on=["symbol","date"], suffixes=("_e","_b"))
    d = j.spr_e - j.spr_b
    t = d.mean()/(d.std(ddof=1)/np.sqrt(len(d))) if len(d)>2 else np.nan
    print(f"  top {(1-q)*100:.1f}% shock: spread {sub.spread_bps.median():.3f} vs base {base.spread_bps.median():.3f} bps "
          f"(x{sub.spread_bps.median()/base.spread_bps.median():.2f}) | markout_buy {sub.adv_buy.mean():.2f} vs {base.adv_buy.mean():.2f} | "
          f"n_symday={len(j)} paired t={t:.2f}")
    res[f"B_tail_{q}"] = dict(spread_evt=float(sub.spread_bps.median()), spread_base=float(base.spread_bps.median()),
        mult=float(sub.spread_bps.median()/base.spread_bps.median()), n_ind_symday=int(len(j)), t_declustered=float(t),
        markout_buy_evt=float(sub.adv_buy.mean()), markout_buy_base=float(base.adv_buy.mean()))

# per-symbol version (liquidity tiers matter)
print("\n[B] per-symbol, top 1% shock vs baseline (spread bps):")
ps = []
for s, d in p.groupby("symbol"):
    thr = d.shock_5m_bps.quantile(0.99); e = d[d.shock_5m_bps >= thr]; bb = d[d.shock_5m_bps < thr]
    ps.append(dict(symbol=s, spr_base=bb.spread_bps.median(), spr_evt=e.spread_bps.median(),
                   mult=e.spread_bps.median()/bb.spread_bps.median(),
                   extra_rt_bps=(e.spread_bps.median()-bb.spread_bps.median()),
                   adv_base=bb.adv_buy.mean(), adv_evt=e.adv_buy.mean()))
PS = pd.DataFrame(ps).sort_values("spr_base")
print(PS.round(3).to_string())
res["B_per_symbol"] = PS.round(4).to_dict("records")
PS.to_csv(f"{S}/h4_per_symbol.csv", index=False); A.to_csv(f"{S}/h4_cascade_episodes.csv", index=False)
json.dump(res, open(f"{S}/h4.json","w"), indent=1, default=str)
