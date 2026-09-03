"""W7 round4 — robustness + confound control for M6, the only mechanism with t>2 after
correct declustering. The question that decides it: is DVOL_ETH/DVOL_BTC carrying OPTIONS
information, or is it a laundered version of trailing BTC-vs-ETH relative performance /
relative realised vol? W6-round2 killed four of its own findings on exactly this test."""
import os, sys, json
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import causal_z, ZSQ
from prep import load_all, uniform_position, sign_from_first_half
D = os.path.dirname(os.path.abspath(__file__))
opt, dvol, px, ret, fwd = load_all()
out = {}

ratio = dvol["dvol_eth"]/dvol["dvol_btc"]
z = causal_z(ratio, 252, 60)
cm = z.dropna().index.intersection(fwd.index)
z = z.reindex(cm)
legf = 0.5*(fwd["BTCUSDT"].reindex(cm) - fwd["ETHUSDT"].reindex(cm))*1e4     # bps, long BTC / short ETH

def episodes_stats(pos, legbps, cost_per_unit=7.0):
    on = pos != 0
    if on.sum() == 0: return None
    turn = pos.diff().abs().fillna(pos.abs())
    daily = pos*legbps - turn*cost_per_unit
    st = on.ne(on.shift()).cumsum()[on]
    ep = daily[on].groupby(st).sum().dropna()
    if len(ep) < 3: return None
    t = float(ep.mean()/(ep.std(ddof=1)/np.sqrt(len(ep))))
    sr = float(daily.reindex(pos.index).fillna(0).mean()/daily.reindex(pos.index).fillna(0).std(ddof=1)*np.sqrt(365.25))
    return {"n_ep": int(len(ep)), "net_bps": round(float(ep.mean()), 2), "t": round(t, 2),
            "sharpe": round(sr, 3), "eta_years_from_sharpe": round(31.4/sr**2, 2) if sr > 0 else None}

raw_pos, _ = uniform_position(z, cm)
sgn, cut = sign_from_first_half(raw_pos, legf/1e4)
out["sign_learned"] = sgn
out["sign_interpretation"] = ("+1 => high DVOL_ETH/DVOL_BTC (ETH vol rich vs BTC) => LONG BTC / SHORT ETH"
                              if sgn > 0 else
                              "-1 => high DVOL_ETH/DVOL_BTC => SHORT BTC / LONG ETH")
out["oos_start"] = str(cut.date()); out["sample"] = [str(cm.min().date()), str(cm.max().date())]
pos = (sgn*raw_pos)
oos = pos.loc[cut:]
out["baseline_oos"] = episodes_stats(oos, legf.loc[cut:])
out["baseline_full"] = episodes_stats(pos, legf)

# ---- drop the single huge 2023 episode ----
m = oos.index.year != 2023
out["oos_ex2023"] = episodes_stats(oos[m], legf.loc[cut:][m])

# ---- threshold sensitivity (is the uniform 0.80/0.20 rule a knife edge?) ----
out["threshold_sensitivity"] = {}
for hi, lo in [(0.75, 0.25), (0.80, 0.20), (0.85, 0.15), (0.90, 0.10), (0.70, 0.30)]:
    rp, _ = uniform_position(z, cm, hi=hi, lo=lo)
    out["threshold_sensitivity"][f"{hi}/{lo}"] = episodes_stats((sgn*rp).loc[cut:], legf.loc[cut:])

# ---- CONFOUND CONTROL: is this just relative momentum / relative realised vol? ----
rel_mom = (ret["BTCUSDT"] - ret["ETHUSDT"]).rolling(30, min_periods=20).sum().reindex(cm)
rv_b = ret["BTCUSDT"].rolling(30, min_periods=20).std().reindex(cm)
rv_e = ret["ETHUSDT"].rolling(30, min_periods=20).std().reindex(cm)
rel_rv = causal_z(rv_e/rv_b, 252, 60).reindex(cm)
sub = pd.DataFrame({"z": z, "rel_mom": rel_mom, "rel_rv": rel_rv, "y": legf}).dropna()
out["confound_correlations"] = {
    "spearman_dvolratio_vs_rel_realised_vol_ratio": round(float(sub.z.corr(sub.rel_rv, method="spearman")), 3),
    "spearman_dvolratio_vs_rel_momentum_30d": round(float(sub.z.corr(sub.rel_mom, method="spearman")), 3)}
def rankres(a, ctrl):
    ar = stats.rankdata(a); cr = np.column_stack([stats.rankdata(c) for c in ctrl])
    cr = np.column_stack([np.ones(len(ar)), cr])
    return ar - cr@np.linalg.lstsq(cr, ar, rcond=None)[0]
out["partial_IC"] = {
    "raw_IC_spearman": round(float(sub.z.corr(sub.y, method="spearman")), 4),
    "partial_IC_ctrl_rel_realised_vol": round(float(stats.spearmanr(rankres(sub.z, [sub.rel_rv]), rankres(sub.y, [sub.rel_rv]))[0]), 4),
    "partial_IC_ctrl_rel_momentum": round(float(stats.spearmanr(rankres(sub.z, [sub.rel_mom]), rankres(sub.y, [sub.rel_mom]))[0]), 4),
    "partial_IC_ctrl_both": round(float(stats.spearmanr(rankres(sub.z, [sub.rel_rv, sub.rel_mom]), rankres(sub.y, [sub.rel_rv, sub.rel_mom]))[0]), 4)}
# ---- the decisive substitution test: does the REALISED vol ratio alone do the same job? ----
rp_rv, _ = uniform_position(rel_rv, cm)
out["substitution_realised_vol_ratio_only"] = episodes_stats((sgn*rp_rv).loc[cut:], legf.loc[cut:])
rp_mom, _ = uniform_position(rel_mom, cm)
out["substitution_rel_momentum_only"] = episodes_stats((sgn*rp_mom).loc[cut:], legf.loc[cut:])
# ---- orthogonalised signal: DVOL ratio with realised-vol ratio and momentum projected out ----
orth = pd.Series(rankres(sub.z, [sub.rel_rv, sub.rel_mom]), index=sub.index).reindex(cm)
rp_o, _ = uniform_position(orth, cm)
out["orthogonalised_dvol_ratio"] = episodes_stats((sgn*rp_o).loc[cut:], legf.loc[cut:])

json.dump(out, open(f"{D}/results_m6_robustness.json","w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
