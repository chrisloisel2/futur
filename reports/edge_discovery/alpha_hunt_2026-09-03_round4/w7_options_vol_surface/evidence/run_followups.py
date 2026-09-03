"""W7 round4 — follow-ups, each declared as an EXPLORATORY EXTENSION of a preregistered mechanism.

F1: M7 with correct non-overlapping accounting. The first M7 run used H-hour forward returns
    against hourly positions, which counts each hour's return H times. Corrected: hold the
    signal for H hours (rolling mean of the raw signal) and settle against 1h forward returns.
    This is a BUG FIX in my own harness, not a threshold refit -- the H=1 case is unchanged.
F2: M5 risk-on outright arm (VRP regime -> long the alt basket), the leg the arm-vs-arm
    diagnostic pointed at.
F3: M6 horizon variants (the structurally best ETA candidate gets its fair shot).
"""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, two_arm, causal_z
from prep import load_all, uniform_position, sign_from_first_half
D = os.path.dirname(os.path.abspath(__file__))
opt, dvol, px, ret, fwd = load_all()
res, diag = {}, {}

# ---------------- F1 : M7 corrected accounting ----------------
hb = pd.read_parquet(f"{D}/hourly_block_flow.parquet"); hb.index = pd.to_datetime(hb.index, utc=True)
ph = pd.read_parquet(f"{D}/perp_hourly_close_core.parquet"); ph.index = pd.to_datetime(ph.index, utc=True)
ix = ph.index
f1h = pd.DataFrame({"BTCUSDT": ph["BTCUSDT"].pct_change().shift(-1)})   # non-overlapping 1h forward
flow = hb["blk_delta_flow"].reindex(ix).fillna(0.0)
z = causal_z(flow, 24*90, 24*30)
raw = pd.Series(0.0, index=ix); raw[z > 1.0] = 1.0; raw[z < -1.0] = -1.0
for H in (1, 4, 12, 24, 72):
    pos = raw.rolling(H, min_periods=1).mean()      # hold H hours => turnover /H, no double count
    r = run_gate(pd.DataFrame({"BTCUSDT": pos}), f1h,
                 f"M7-corrected — block delta flow, signal held {H}h, settled on 1h bars",
                 notes=("Non-overlapping accounting. Preregistered sign (customer net-long delta "
                        "=> dealer buys perp). Turnover falls as 1/H, so this is the mechanism's "
                        "best legitimate shot at clearing the 14bps cost."))
    res[f"F1_M7_hold{H}h"] = r

# ---------------- F2 : M5 risk-on outright arm ----------------
rv30 = ret["BTCUSDT"].rolling(30, min_periods=20).std()*np.sqrt(365.25)*100.0
vrp = (dvol["dvol_btc"] - rv30).dropna()
common = vrp.index.intersection(fwd.index)
alts = [c for c in px.columns if c != "BTCUSDT"]
_, vrp_pct = uniform_position(vrp.reindex(common), common)
basket_w = pd.DataFrame(1.0/len(alts), index=common, columns=alts)
for tag, m in [("high_vrp_long", vrp_pct > 0.80), ("low_vrp_short", vrp_pct < 0.20)]:
    sgn = 1.0 if "long" in tag else -1.0
    w = basket_w.mul(m.reindex(common).fillna(False).astype(float)*sgn, axis=0)
    res[f"F2_M5_{tag}"] = run_gate(w, fwd[alts].reindex(common),
        f"M5 risk-on — equal-weight alt basket, {tag} (VRP = DVOL_BTC - trailing RV30)",
        notes="Directional beta bet: sigma is NOT reduced, so the ETA frontier is unforgiving here.")
tilt = (basket_w*fwd[alts].reindex(common)*1e4).sum(axis=1)
diag["F2_arm_vs_arm_basket"] = two_arm(tilt[(vrp_pct > 0.80).reindex(common).fillna(False)],
                                       tilt[(vrp_pct < 0.20).reindex(common).fillna(False)],
                                       "high_vrp", "low_vrp")

# ---------------- F3 : M6 horizon variants ----------------
zratio = causal_z(dvol["dvol_eth"]/dvol["dvol_btc"], 252, 60)
cm = zratio.dropna().index.intersection(fwd.index)
pf = pd.DataFrame({"BTCUSDT": fwd["BTCUSDT"].reindex(cm), "ETHUSDT": fwd["ETHUSDT"].reindex(cm)})
leg = 0.5*(pf["BTCUSDT"] - pf["ETHUSDT"])
raw_pos, _ = uniform_position(zratio.reindex(cm), cm)
sgn, cut = sign_from_first_half(raw_pos, leg)
for H in (1, 3, 5, 10):
    pos_s = raw_pos.rolling(H, min_periods=1).mean()*sgn
    pos = pd.DataFrame({"BTCUSDT": 0.5*pos_s, "ETHUSDT": -0.5*pos_s})
    res[f"F3_M6_pair_hold{H}d"] = run_gate(pos.loc[cut:], pf.reindex(pos.index).loc[cut:],
        f"M6 — DVOL_ETH/DVOL_BTC ratio -> BTC/ETH dollar-neutral pair, signal held {H}d",
        notes=f"Sign learned on first half (sign={sgn:+.0f}); gate OOS from {cut.date()}.",
        extra={"sign_learned": sgn, "oos_start": str(cut.date())})

json.dump({"results": res, "diagnostics": diag}, open(f"{D}/results_followups.json","w"), indent=1, default=str)
print(json.dumps(diag, indent=1, default=str)); print()
for k, v in res.items():
    print(f"{k:28s} gross={v['gross_bps_per_episode']:>8} net={v['net_bps']:>8} stress={v['net_bps_stress28']:>8} "
          f"t={v['t_stat_declustered']:>6} SR={v['sharpe_annual_net']:>7} L3={v['n_independent_L3']:>5} ETA_y={v['eta_forward_confirmation_years']}")
