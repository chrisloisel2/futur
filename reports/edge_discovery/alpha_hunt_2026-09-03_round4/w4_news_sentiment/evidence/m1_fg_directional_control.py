"""M1 — NEGATIVE CONTROL: F&G as a directional signal on BTC.
Preregistered hypothesis: DEAD. This is the most publicly backtested crypto rule alive.
If this reports an edge, the pipeline is broken and every other result is suspect.
"""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence")
import w4_lib as L

btc = pd.read_parquet("/home/qbee/futur/data/enriched/BTCUSDT_1h_enriched.parquet",
                      columns=["datetime", "close"])
btc["ts"] = pd.to_datetime(btc["datetime"], utc=True)
d = btc.set_index("ts")["close"].resample("1D").last().to_frame("close")
d["ret_1d_fwd"] = d["close"].shift(-1) / d["close"] - 1.0
d["ret_7d_fwd"] = d["close"].shift(-7) / d["close"] - 1.0
d = d.reset_index().rename(columns={"ts": "day"})

fg = L.load_fg()
m = d.merge(fg[["day", "fg_pct365", "fg_lvl", "fg_chg_7d"]], on="day", how="inner").dropna(subset=["fg_pct365"])
m["symbol"] = "BTCUSDT"
m["_ts"] = m["day"]

res = {}
for hz, col, days in [("1d", "ret_1d_fwd", 1), ("7d", "ret_7d_fwd", 7)]:
    sub = m.dropna(subset=[col]).copy()
    sub["r_bps"] = sub[col] * 1e4
    # non-overlapping sampling for 7d so episodes are genuinely independent
    if days > 1:
        sub = sub.iloc[::days].copy()
    fear = sub[sub.fg_pct365 <= 0.20].copy()
    greed = sub[sub.fg_pct365 >= 0.80].copy()
    mid = sub[(sub.fg_pct365 > 0.20) & (sub.fg_pct365 < 0.80)].copy()
    # L3 episodes = maximal runs of consecutive days in the same bucket
    for nm, arm in [("fear", fear), ("greed", greed), ("mid", mid)]:
        arm["_bucket"] = nm
    allb = pd.concat([fear, greed, mid]).sort_values("day")
    allb["_bucket_"] = np.select(
        [allb.fg_pct365 <= 0.20, allb.fg_pct365 >= 0.80], ["fear", "greed"], "mid")
    chg = (allb["_bucket_"] != allb["_bucket_"].shift(1))
    allb["_ep"] = chg.cumsum()
    out = {}
    for nm in ["fear", "greed", "mid"]:
        arm = allb[allb._bucket_ == nm]
        out[nm] = L.run_gate(arm, "r_bps", f"M1_{hz}_{nm}", cost=L.COST, sign=1.0,
                             note="unconditional long BTC inside the bucket")
    out["spread_fear_minus_greed"] = L.arm_spread(out["fear"], out["greed"],
                                                  "fear_bucket minus greed_bucket")
    # baseline: always-long, same population
    allb["_ep_all"] = np.arange(len(allb)) // max(1, days)
    base = L.run_gate(allb.assign(_ep=allb["_ep_all"]), "r_bps", f"M1_{hz}_baseline_alwayslong",
                      cost=L.COST, sign=1.0, note="unconditional drift, same population")
    out["baseline_always_long"] = base
    res[hz] = out

print(json.dumps({k: {kk: (vv if not isinstance(vv, dict) or "comparison" in vv else
                          {x: vv[x] for x in ("name","n_raw","n_independent_L3","net_bps",
                                              "net_bps_stress28","t_stat_declustered",
                                              "bootstrap_ci95","eta_forward_confirmation_years")})
                      for kk, vv in v.items()} for k, v in res.items()}, indent=1, default=str))
json.dump(res, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m1_results.json","w"), indent=1, default=str)
