"""W7 round4 — M3: dealer gamma proxy -> momentum vs mean-reversion regime on BTC perp.
This is the mechanism the project has never had. PROXY, not real GEX: no open interest exists
in this dataset, so the inventory is accumulated from observed TRADES since 2023-01-01 under
the assumption taker==customer. Level is unanchored (initial inventory unknown) -> only the
VARIATION is interpreted, exactly as preregistered."""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, two_arm, causal_z
from prep import load_all, uniform_position
D = os.path.dirname(os.path.abspath(__file__))

opt, dvol, px, ret, fwd = load_all()
idx = opt.index.intersection(fwd.index)
g = opt["dealer_gamma"].reindex(idx)
r_btc = ret["BTCUSDT"].reindex(idx)
f_btc = fwd["BTCUSDT"].reindex(idx)
res, diag = {}, {}

# ---- proxy quality diagnostics (reported, not hidden) ----
oi = opt["open_interest_proxy"].reindex(idx)
diag["proxy_quality"] = {
    "open_interest_proxy_start_btc": round(float(oi.dropna().iloc[0]), 1),
    "open_interest_proxy_end_btc": round(float(oi.dropna().iloc[-1]), 1),
    "gross_position_drift_ratio": round(float(oi.dropna().iloc[-1]/oi.dropna().iloc[0]), 2),
    "dealer_gamma_sign_share_positive": round(float((g > 0).mean()), 3),
    "dealer_gamma_autocorr_1d": round(float(g.autocorr(1)), 3),
    "note": ("Gross accumulated |position| grows monotonically because closing trades of "
             "positions opened BEFORE 2023-01-01 have no opening leg in the sample, and because "
             "taker==customer is violated by market-maker taker flow. Level therefore drifts; "
             "only trailing-window-relative measures are used below."),
}

# three constructions: raw level, detrended level, and drift-free daily gamma FLOW
g_detr = g - g.rolling(60, min_periods=20).mean()
g_flow = g.diff()
variants = {"gamma_level": g, "gamma_detrended_60d": g_detr, "gamma_flow_1d": g_flow}

for tag, sig in variants.items():
    pct = sig.rolling(252, min_periods=90).apply(
        lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
    short_gamma = pct < 0.20      # dealers most short gamma -> should AMPLIFY (momentum, high RV)
    long_gamma = pct > 0.80       # dealers most long gamma  -> should DAMPEN  (reversion, low RV)

    # (a) RV leg — the mechanical prediction: short gamma => higher forward realised vol
    diag[f"{tag}_rv_arm"] = two_arm((f_btc[short_gamma].abs()*1e4).dropna(),
                                    (f_btc[long_gamma].abs()*1e4).dropna(),
                                    "short_gamma_absret", "long_gamma_absret")
    # (b) autocorrelation leg — the tradable prediction
    ac_s = np.corrcoef(r_btc[short_gamma].dropna().values[:-1], f_btc[short_gamma].dropna().values[:-1])[0,1] \
        if short_gamma.sum() > 30 else np.nan
    sub_s = pd.DataFrame({"r": r_btc, "f": f_btc})[short_gamma].dropna()
    sub_l = pd.DataFrame({"r": r_btc, "f": f_btc})[long_gamma].dropna()
    diag[f"{tag}_autocorr_arm"] = {
        "n_short_gamma": len(sub_s), "n_long_gamma": len(sub_l),
        "autocorr_short_gamma": round(float(np.corrcoef(sub_s.r, sub_s.f)[0,1]), 4) if len(sub_s) > 30 else None,
        "autocorr_long_gamma": round(float(np.corrcoef(sub_l.r, sub_l.f)[0,1]), 4) if len(sub_l) > 30 else None,
    }
    # tradable: momentum when dealers short gamma, reversion when dealers long gamma
    pos_m = pd.Series(0.0, index=idx); pos_m[short_gamma] = np.sign(r_btc[short_gamma])
    pos_r = pd.Series(0.0, index=idx); pos_r[long_gamma] = -np.sign(r_btc[long_gamma])
    res[f"M3_{tag}_momentum_arm"] = run_gate(
        pd.DataFrame({"BTCUSDT": pos_m}), fwd[["BTCUSDT"]].reindex(idx),
        f"M3 — dealer {tag} in bottom quintile (short gamma) -> follow yesterday's BTC move (momentum)",
        notes="Direction preregistered by the mechanism (short gamma => dealers amplify), NOT fitted.")
    res[f"M3_{tag}_reversion_arm"] = run_gate(
        pd.DataFrame({"BTCUSDT": pos_r}), fwd[["BTCUSDT"]].reindex(idx),
        f"M3 — dealer {tag} in top quintile (long gamma) -> fade yesterday's BTC move (reversion)",
        notes="Direction preregistered by the mechanism (long gamma => dealers dampen), NOT fitted.")
    res[f"M3_{tag}_combined"] = run_gate(
        pd.DataFrame({"BTCUSDT": pos_m + pos_r}), fwd[["BTCUSDT"]].reindex(idx),
        f"M3 — combined gamma-regime momentum/reversion switch on {tag}",
        notes="Both arms in one book; the §1.3 arm-vs-arm comparison is in diagnostics.")
    # §1.3 arm vs arm on the SAME population: momentum payoff under short vs long gamma
    mom_pnl_s = (np.sign(r_btc)*f_btc*1e4)[short_gamma].dropna()
    mom_pnl_l = (np.sign(r_btc)*f_btc*1e4)[long_gamma].dropna()
    diag[f"{tag}_momentum_arm_vs_arm"] = two_arm(mom_pnl_s, mom_pnl_l, "short_gamma", "long_gamma")

json.dump({"results": res, "diagnostics": diag}, open(f"{D}/results_m3.json","w"), indent=1, default=str)
print(json.dumps(diag, indent=1, default=str))
print()
for k, v in res.items():
    print(f"{k:44s} net={v['net_bps']:>8} stress={v['net_bps_stress28']:>8} t={v['t_stat_declustered']:>6} "
          f"SR={v['sharpe_annual_net']:>7} L3={v['n_independent_L3']:>5} ETA_y={v['eta_forward_confirmation_years']}")
