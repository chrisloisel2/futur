#!/usr/bin/env python3
"""W9 Phase 2 — execute les mecanismes H1-H5 du PREREGISTRATION a travers le gate."""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import load, evaluate, causal_pct, HORIZONS, DISCOVERY_END
OUT=os.environ.get("W9_OUT","/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")

df=load()
print("panel:", len(df), "rows,", df.symbol.nunique(), "symbols,", df.datetime.min(), "->", df.datetime.max(), flush=True)
g=df.groupby("symbol", sort=False)
# features causales derivees (rolling, incluent t, jamais t+1)
df["uw_pct"]=g["upper_wick_range"].transform(causal_pct)
df["lw_pct"]=g["lower_wick_range"].transform(causal_pct)
df["volp"]=g["volume_percentile_20"].transform(lambda s: s)          # deja un percentile roulant
df["er_pct"]=g["efficiency_ratio_20"].transform(causal_pct)
df["ret8"]=(df["close"]/g["close"].shift(8)-1.0)*1e4                  # momentum passe, cause
df["ret12"]=(df["close"]/g["close"].shift(12)-1.0)*1e4
df["volpct_pct"]=g["volatility_percentile_20"].transform(causal_pct)

res=[]
# ---- H1 : compression de volatilite -> expansion directionnelle
comp = df["volatility_percentile_20"]<=0.10
for H in HORIZONS:
    res.append(evaluate("H1a_compression_then_breakout_long", df,
        comp & (df["minmax_norm_close_20"]>=0.80), +1, H, "vol pct<=0.10 & close haut de range"))
    res.append(evaluate("H1b_compression_then_breakdown_short", df,
        comp & (df["minmax_norm_close_20"]<=0.20), -1, H, "vol pct<=0.10 & close bas de range"))
# ---- H2 : epuisement intrabar (meche + volume) -> reversion
for H in HORIZONS:
    res.append(evaluate("H2a_upper_wick_exhaustion_short", df,
        (df["uw_pct"]>=0.95) & (df["volume_percentile_20"]>=0.95), -1, H, "meche haute p95 & volume p95"))
    res.append(evaluate("H2b_lower_wick_exhaustion_long", df,
        (df["lw_pct"]>=0.95) & (df["volume_percentile_20"]>=0.95), +1, H, "meche basse p95 & volume p95"))
# ---- H3 : filtre de regime par efficiency ratio (test = DIFFERENCE entre bras)
mom_up = df["ret8"]>0
for H in HORIZONS:
    res.append(evaluate("H3a_momentum8h_in_trending_regime", df,
        mom_up & (df["er_pct"]>=0.80), +1, H, "bras A : momentum 8h>0 & ER haut"))
    res.append(evaluate("H3b_momentum8h_in_choppy_regime", df,
        mom_up & (df["er_pct"]<=0.20), +1, H, "bras B : momentum 8h>0 & ER bas"))
# ---- H4 : nouveau plus-haut de range non confirme par le volume
for H in HORIZONS:
    res.append(evaluate("H4a_unconfirmed_range_high_short", df,
        (df["minmax_norm_close_20"]>=0.95) & (df["volume_percentile_20"]<=0.30), -1, H, "haut de range & volume faible"))
    res.append(evaluate("H4b_confirmed_range_high_long", df,
        (df["minmax_norm_close_20"]>=0.95) & (df["volume_percentile_20"]>=0.70), +1, H, "haut de range & volume fort (bras de controle de H4a)"))
json.dump(res, open(OUT+"/mech_results.json","w"), indent=1)
cols=["mechanism","horizon_h","n_raw","n_independent_L2","gross_bps","net_bps","net_bps_stress28",
      "edge_vs_control_bps","t_stat_declustered","ex_best_year","event_rate_per_week","eta_forward_years","verdict"]
t=pd.DataFrame(res)
pd.set_option("display.width",260); pd.set_option("display.max_rows",100)
print(t[[c for c in cols if c in t.columns]].to_string())
