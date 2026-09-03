"""M5 — INCREMENTALITY (the decisive test).

(a) M5a: does F&G add anything to LIQ_CASCADE_REPEAT once btc_vol_24h is controlled for?
    Round 3 T1.1 already showed a BTC-vol gate pays (+17.87bps OOS delta). F&G is built
    from volatility + momentum + volume + social + dominance, so it may be laundered vol.
(b) M5b: is M1's "buy extreme fear" BTC result anything more than buying a drawdown?
    Control = trailing 30d BTC return percentile (causal). If F&G's effect vanishes inside
    a drawdown bucket, F&G is a lagging re-description of price, not information.
(c) M5c: how much of F&G is mechanically explained by trailing price/vol? (R^2 audit)
(d) M5d: event-weighted vs episode-weighted estimator gap (methodology audit).
"""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence")
import w4_lib as L

LO, HI = 1/3., 2/3.
OUT = {}

# ---------------------------------------------------------------- M5a
D = pd.read_parquet("/home/qbee/futur/data/events/liq_cascade_dataset.parquet")
D = L.attach_fg(D, "event_time")
D["r_bps"] = D["fwd_4h"] * 1e4
D = D.dropna(subset=["r_bps", "fg_pct365", "btc_vol_24h"])
base = D[(D.n_events_sym_24h >= 2) & (D.is_long_cascade == 1)].copy()

base["_fgb"] = np.select([base.fg_pct365 <= LO, base.fg_pct365 >= HI], ["low_fear", "high_greed"], "mid")
base["_volb"] = pd.qcut(base.btc_vol_24h, 3, labels=["vol_low", "vol_mid", "vol_high"]).astype(str)

day_reg = base[["day", "_fgb"]].drop_duplicates("day").sort_values("day").reset_index(drop=True)
chg = (day_reg["_fgb"] != day_reg["_fgb"].shift(1)) | (day_reg["day"].diff() > pd.Timedelta("1D"))
day_reg["_ep"] = chg.cumsum()
base = base.merge(day_reg[["day", "_ep"]], on="day", how="left")

uncond_spread = None
m5a = {}
for vb in ["vol_low", "vol_mid", "vol_high"]:
    sub = base[base._volb == vb]
    lo = L.run_gate(sub[sub._fgb == "low_fear"], "r_bps", f"M5a_{vb}_lowfear")
    hi = L.run_gate(sub[sub._fgb == "high_greed"], "r_bps", f"M5a_{vb}_highgreed")
    un = L.run_gate(sub, "r_bps", f"M5a_{vb}_uncond")
    m5a[vb] = {"low_fear": lo, "high_greed": hi, "uncond": un,
               "fg_spread_within_vol": None if (lo.get("net_bps") is None or hi.get("net_bps") is None)
                                       else round(lo["net_bps"] - hi["net_bps"], 2)}
lo_all = L.run_gate(base[base._fgb == "low_fear"], "r_bps", "M5a_all_lowfear")
hi_all = L.run_gate(base[base._fgb == "high_greed"], "r_bps", "M5a_all_highgreed")
uncond_spread = round(lo_all["net_bps"] - hi_all["net_bps"], 2)
spreads = [m5a[v]["fg_spread_within_vol"] for v in m5a if m5a[v]["fg_spread_within_vol"] is not None]
m5a["_unconditional_fg_spread"] = uncond_spread
m5a["_mean_within_vol_fg_spread"] = round(float(np.mean(spreads)), 2)
m5a["_retention_frac"] = round(float(np.mean(spreads) / uncond_spread), 3) if uncond_spread else None
# also the reverse: does the VOL gate survive controlling for F&G?
m5a["_reverse_vol_spread_within_fg"] = {}
for fb in ["low_fear", "mid", "high_greed"]:
    sub = base[base._fgb == fb]
    vh = L.run_gate(sub[sub._volb == "vol_high"], "r_bps", f"M5a_rev_{fb}_volhigh")
    vl = L.run_gate(sub[sub._volb == "vol_low"], "r_bps", f"M5a_rev_{fb}_vollow")
    m5a["_reverse_vol_spread_within_fg"][fb] = {
        "vol_high_net": vh.get("net_bps"), "vol_low_net": vl.get("net_bps"),
        "spread": None if (vh.get("net_bps") is None or vl.get("net_bps") is None)
                  else round(vh["net_bps"] - vl["net_bps"], 2)}
OUT["M5a_fg_incremental_over_btcvol"] = m5a

# ---------------------------------------------------------------- M5b + M5c
btc = pd.read_parquet("/home/qbee/futur/data/enriched/BTCUSDT_1h_enriched.parquet",
                      columns=["datetime", "close"])
btc["ts"] = pd.to_datetime(btc["datetime"], utc=True)
d = btc.set_index("ts")["close"].resample("1D").last().to_frame("close")
d["ret1d"] = d["close"].pct_change()
d["trail_30d"] = d["close"] / d["close"].shift(30) - 1.0            # causal, ends at t
d["trail_vol30"] = d["ret1d"].rolling(30).std()                      # causal
d["dd_365"] = d["close"] / d["close"].rolling(365, min_periods=180).max() - 1.0
d["ret_1d_fwd"] = d["close"].shift(-1) / d["close"] - 1.0
d = d.reset_index().rename(columns={"ts": "day"})
fg = L.load_fg()
m = d.merge(fg[["day", "fg_pct365", "fg_lvl"]], on="day", how="inner")
m = m.dropna(subset=["fg_pct365", "trail_30d", "trail_vol30", "dd_365", "ret_1d_fwd"])
m["symbol"] = "BTCUSDT"; m["_ts"] = m["day"]; m["r_bps"] = m["ret_1d_fwd"] * 1e4

# M5c: how mechanically is F&G explained by trailing price/vol?
X = np.column_stack([np.ones(len(m)), m.trail_30d, m.trail_vol30, m.dd_365])
y = m.fg_pct365.values
beta, *_ = np.linalg.lstsq(X, y, rcond=None)
r2 = 1 - ((y - X @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum()
OUT["M5c_fg_explained_by_trailing_price"] = {
    "r2_fg_pct365_on_trail30d_vol30d_dd365": round(float(r2), 3),
    "corr_fg_vs_trail30d": round(float(np.corrcoef(m.fg_pct365, m.trail_30d)[0, 1]), 3),
    "corr_fg_vs_dd365": round(float(np.corrcoef(m.fg_pct365, m.dd_365)[0, 1]), 3),
    "corr_fg_vs_trailvol30": round(float(np.corrcoef(m.fg_pct365, m.trail_vol30)[0, 1]), 3),
    "n": int(len(m))}

# M5b: F&G fear effect inside drawdown buckets
m["_ddb"] = pd.qcut(m.dd_365, 3, labels=["dd_deep", "dd_mid", "dd_shallow"]).astype(str)
m["_fgb"] = np.select([m.fg_pct365 <= 0.20, m.fg_pct365 >= 0.80], ["fear", "greed"], "mid")
chg = (m["_fgb"] != m["_fgb"].shift(1))
m["_ep"] = chg.cumsum()
m5b = {}
for db in ["dd_deep", "dd_mid", "dd_shallow"]:
    sub = m[m._ddb == db]
    f = L.run_gate(sub[sub._fgb == "fear"], "r_bps", f"M5b_{db}_fear")
    g = L.run_gate(sub[sub._fgb == "greed"], "r_bps", f"M5b_{db}_greed")
    u = L.run_gate(sub, "r_bps", f"M5b_{db}_uncond")
    m5b[db] = {"fear_net": f.get("net_bps"), "greed_net": g.get("net_bps"),
               "uncond_net": u.get("net_bps"), "fear_L3": f.get("n_independent_L3"),
               "greed_L3": g.get("n_independent_L3"),
               "spread": None if (f.get("net_bps") is None or g.get("net_bps") is None)
                         else round(f["net_bps"] - g["net_bps"], 2)}
fa = L.run_gate(m[m._fgb == "fear"], "r_bps", "M5b_all_fear")
ga = L.run_gate(m[m._fgb == "greed"], "r_bps", "M5b_all_greed")
m5b["_unconditional_spread"] = round(fa["net_bps"] - ga["net_bps"], 2)
sp = [m5b[k]["spread"] for k in ["dd_deep", "dd_mid", "dd_shallow"] if m5b[k]["spread"] is not None]
m5b["_mean_within_dd_spread"] = round(float(np.mean(sp)), 2)
m5b["_retention_frac"] = round(float(np.mean(sp) / m5b["_unconditional_spread"]), 3)
# reverse: does trailing-drawdown alone reproduce the effect WITHOUT F&G?
dd_only_deep = L.run_gate(m[m._ddb == "dd_deep"], "r_bps", "M5b_ddonly_deep")
dd_only_shal = L.run_gate(m[m._ddb == "dd_shallow"], "r_bps", "M5b_ddonly_shallow")
m5b["_drawdown_only_spread_deep_minus_shallow"] = round(
    dd_only_deep["net_bps"] - dd_only_shal["net_bps"], 2)
m5b["_drawdown_only_deep"] = {k: dd_only_deep[k] for k in
    ("n_raw", "n_independent_L3", "net_bps", "t_stat_declustered", "eta_forward_confirmation_years")}
OUT["M5b_fg_incremental_over_drawdown"] = m5b

# ---------------------------------------------------------------- M5d
ew = float(base["r_bps"].mean()) - L.COST
epw = float(base.groupby("_ep")["r_bps"].mean().mean()) - L.COST
OUT["M5d_estimator_audit"] = {
    "event_weighted_net_bps": round(ew, 2),
    "episode_weighted_net_bps": round(epw, 2),
    "gap_bps": round(ew - epw, 2),
    "n_events": int(len(base)), "n_episodes": int(base["_ep"].nunique()),
    "note": "event-weighted means over-weight long clustered episodes; the episode-weighted "
            "figure is the declustered estimator and is the one the gate uses"}

json.dump(OUT, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m5_results.json", "w"), indent=1, default=str)

print("=== M5a: F&G spread WITHIN btc_vol buckets (LIQ_CASCADE_REPEAT base) ===")
for vb in ["vol_low", "vol_mid", "vol_high"]:
    v = m5a[vb]
    print(f"  {vb:9s} lowfear_net={v['low_fear'].get('net_bps')!s:>8s} highgreed_net={v['high_greed'].get('net_bps')!s:>8s} "
          f"spread={v['fg_spread_within_vol']!s:>8s}  (L3 lo={v['low_fear'].get('n_independent_L3')}, hi={v['high_greed'].get('n_independent_L3')})")
print(f"  unconditional F&G spread = {m5a['_unconditional_fg_spread']}, mean within-vol = {m5a['_mean_within_vol_fg_spread']}, RETENTION = {m5a['_retention_frac']}")
print("  reverse (vol spread within F&G buckets):", json.dumps(m5a["_reverse_vol_spread_within_fg"]))
print()
print("=== M5c: F&G is mechanically price ===", json.dumps(OUT["M5c_fg_explained_by_trailing_price"]))
print()
print("=== M5b: BTC fear effect WITHIN drawdown buckets ===")
for k in ["dd_deep", "dd_mid", "dd_shallow"]:
    print(f"  {k:11s} {m5b[k]}")
print(f"  unconditional spread={m5b['_unconditional_spread']}, mean within-dd={m5b['_mean_within_dd_spread']}, RETENTION={m5b['_retention_frac']}")
print(f"  drawdown-ONLY spread (no F&G at all) = {m5b['_drawdown_only_spread_deep_minus_shallow']}  deep arm: {m5b['_drawdown_only_deep']}")
print()
print("=== M5d estimator audit ===", json.dumps(OUT["M5d_estimator_audit"]))
