#!/usr/bin/env python3
"""W9 Phase 2 — execute les mecanismes H1-H5 du PREREGISTRATION a travers le gate v2.
Chaque mecanisme est evalue sur la fenetre de DECOUVERTE (< 2026-01-01) puis, si l'edge
est positif, sur la fenetre OOS (2026-01-01 -> 2026-06-29) expurgee des periodes de
source contaminee (audit A8). Les variantes de signe inverse sont declarees REFIT.
Usage: .venv/bin/python evidence/run_mechanisms_v2.py
"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate2 import load, evaluate, diff_arms, causal_pct, HORIZONS
OUT = os.environ.get("W9_OUT", "/tmp/claude-1000/-home-qbee-futur/d5f771d6-c185-415b-a11d-a8ade43c66f6/scratchpad")

df = load()
print("panel:", len(df), "lignes,", df.symbol.nunique(), "symboles,", df.datetime.min(), "->", df.datetime.max(), flush=True)
print("lignes marquees source contaminee:", int(df.src_contaminated.sum()), flush=True)
g = df.groupby("symbol", sort=False)
df["uw_pct"]   = g["upper_wick_range"].transform(causal_pct)
df["lw_pct"]   = g["lower_wick_range"].transform(causal_pct)
df["er_pct"]   = g["efficiency_ratio_20"].transform(causal_pct)
df["ret8"]     = (df["close"] / g["close"].shift(8) - 1.0) * 1e4
df["volp20"]   = df["volume_percentile_20"]
df["mm20"]     = df["minmax_norm_close_20"]
df["volatp20"] = df["volatility_percentile_20"]

comp   = df["volatp20"] <= 0.10
uwx    = (df["uw_pct"] >= 0.95) & (df["volp20"] >= 0.95)
lwx    = (df["lw_pct"] >= 0.95) & (df["volp20"] >= 0.95)
volx   = df["volp20"] >= 0.95                       # bras de controle : volume extreme SANS condition de meche
lw_no  = volx & (df["lw_pct"] < 0.95)
uw_no  = volx & (df["uw_pct"] < 0.95)
mom_up = df["ret8"] > 0
hi_rng = df["mm20"] >= 0.95
lo_vol = df["volp20"] <= 0.30
hi_vol = df["volp20"] >= 0.70

DEFS = [
  # (nom, masque, side, famille, note, prereg, refit)
  ("H1a_compression_then_breakout_long",   comp & (df["mm20"] >= 0.80), +1, "H1", "vol pct<=0.10 & close haut de range", True, False),
  ("H1b_compression_then_breakdown_short", comp & (df["mm20"] <= 0.20), -1, "H1", "vol pct<=0.10 & close bas de range", True, False),
  ("H1a_rev_compression_fade_high",        comp & (df["mm20"] >= 0.80), -1, "H1", "SIGNE INVERSE du prereg — REFIT declare", False, True),
  ("H1b_rev_compression_fade_low",         comp & (df["mm20"] <= 0.20), +1, "H1", "SIGNE INVERSE du prereg — REFIT declare", False, True),
  ("H2a_upper_wick_exhaustion_short",      uwx, -1, "H2", "meche haute p95 & volume p95", True, False),
  ("H2b_lower_wick_exhaustion_long",       lwx, +1, "H2", "meche basse p95 & volume p95", True, False),
  ("H2ctrl_volume_p95_only_long",          volx, +1, "H2", "CONTROLE : volume p95 seul, sans condition de meche", True, False),
  ("H2ctrl_volume_p95_no_lower_wick_long", lw_no, +1, "H2", "CONTROLE apparie : volume p95 SANS meche basse extreme", True, False),
  ("H2ctrl_volume_p95_no_upper_wick_short",uw_no, -1, "H2", "CONTROLE apparie : volume p95 SANS meche haute extreme", True, False),
  ("H3a_momentum8h_in_trending_regime",    mom_up & (df["er_pct"] >= 0.80), +1, "H3", "bras A : momentum 8h>0 & ER haut", True, False),
  ("H3b_momentum8h_in_choppy_regime",      mom_up & (df["er_pct"] <= 0.20), +1, "H3", "bras B : momentum 8h>0 & ER bas", True, False),
  ("H3a_rev_fade_momentum_trending",       mom_up & (df["er_pct"] >= 0.80), -1, "H3", "SIGNE INVERSE du prereg — REFIT declare", False, True),
  ("H4a_unconfirmed_range_high_short",     hi_rng & lo_vol, -1, "H4", "haut de range & volume faible", True, False),
  ("H4b_confirmed_range_high_long",        hi_rng & hi_vol, +1, "H4", "haut de range & volume fort (controle de H4a)", True, False),
  ("H4b_rev_confirmed_range_high_short",   hi_rng & hi_vol, -1, "H4", "SIGNE INVERSE du controle — REFIT declare", False, True),
]

res = []
for period in ("discovery", "oos"):
    for name, m, side, fam, note, prereg, refit in DEFS:
        for H in HORIZONS:
            r = evaluate(name, df, m, side, H, note=note, period=period, family=fam,
                         prereg=prereg, refit=refit)
            res.append(r)
            print(f"  [{period:9s}] {name:42s} H={H:2d} -> {r['verdict']:26s} "
                  f"net={r.get('net_bps')} edge={r.get('edge_vs_control_bps')} t={r.get('t_stat_declustered')}", flush=True)

# ---- H3 : le VRAI test preenregistre est la DIFFERENCE entre bras (pas le niveau)
diffs = []
for period in ("discovery", "oos"):
    for H in HORIZONS:
        diffs.append(diff_arms("H3diff_trending_minus_choppy", df,
            mom_up & (df["er_pct"] >= 0.80), mom_up & (df["er_pct"] <= 0.20), +1, H,
            period=period, note="H3 : le momentum 8h paie-t-il PLUS en regime tendanciel qu'en regime hache ?"))
        diffs.append(diff_arms("H2diff_lower_wick_minus_no_wick", df,
            lwx, lw_no, +1, H, period=period,
            note="H2b : la meche basse ajoute-t-elle quelque chose au volume p95 seul ?"))
        diffs.append(diff_arms("H4diff_lowvol_minus_highvol_at_range_high", df,
            hi_rng & lo_vol, hi_rng & hi_vol, -1, H, period=period,
            note="H4 : au haut de range, la non-confirmation par le volume ajoute-t-elle a la reversion ?"))

# ---- H5 : interaction horaire (jamais un alpha autonome) sur le mecanisme le plus fort
BUCK = {"asia_00_07": (0, 8), "eu_08_15": (8, 16), "us_16_23": (16, 24)}
hours = []
for period in ("discovery", "oos"):
    for H in HORIZONS:
        for bname, (a, b) in BUCK.items():
            hm = lwx & (df["utc_hour"] >= a) & (df["utc_hour"] < b)
            r = evaluate(f"H5_H2b_lower_wick__{bname}", df, hm, +1, H,
                         note=f"H2b restreint aux heures UTC [{a},{b}) — interaction seulement",
                         period=period, family="H5", prereg=True, refit=False)
            hours.append(r)
            print(f"  [{period:9s}] H5 {bname:11s} H={H:2d} -> {r['verdict']:26s} net={r.get('net_bps')} n2={r.get('n_independent_L2')}", flush=True)

json.dump(dict(mechanisms=res, arm_differences=diffs, hour_interaction=hours),
          open(OUT + "/mech_results_v2.json", "w"), indent=1, default=str)
print("\necrit:", OUT + "/mech_results_v2.json")
cols = ["mechanism", "period", "horizon_h", "n_raw", "n_independent_L2", "gross_bps", "net_bps",
        "net_bps_stress28", "edge_vs_control_bps", "t_stat_declustered", "t_stat_net_tradable",
        "ex_best_year", "event_rate_per_week", "eta_forward_years", "verdict"]
t = pd.DataFrame(res)
pd.set_option("display.width", 300); pd.set_option("display.max_rows", 400)
print(t[[c for c in cols if c in t.columns]].to_string())
print("\n=== differences entre bras ===")
print(pd.DataFrame(diffs).to_string())
