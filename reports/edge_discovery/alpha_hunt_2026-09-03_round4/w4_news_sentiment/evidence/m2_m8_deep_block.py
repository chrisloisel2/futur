"""M2-M8 — F&G (exogenous sentiment regime) applied to the project's OWN established
mechanisms. Deep history: F&G 2018-2026 x liq_cascade_dataset 2021-2026.

Every arm is compared to the OTHER arms on the same population (never to zero).
Every t-stat is computed on L3 = F&G regime episodes (maximal consecutive-day runs in
the same tercile), because F&G is a slow autocorrelated series.
"""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence")
import w4_lib as L

D = pd.read_parquet("/home/qbee/futur/data/events/liq_cascade_dataset.parquet")
D = L.attach_fg(D, "event_time")
D["r_bps"] = D["fwd_4h"] * 1e4
D = D.dropna(subset=["r_bps", "fg_pct365"])
print("events with causal F&G:", len(D), D["_ts"].min(), D["_ts"].max())

# ---- fixed tercile cuts on the CAUSAL percentile, preregistered as terciles
LO, HI = 1.0 / 3.0, 2.0 / 3.0


def bucketize(df, col="fg_pct365"):
    d = df.copy()
    d["_bucket_"] = np.select([d[col] <= LO, d[col] >= HI], ["low_fear", "high_greed"], "mid")
    return d


def add_fg_episodes(d):
    """L3 = maximal run of consecutive CALENDAR DAYS in the same F&G bucket."""
    day_reg = d[["day", "_bucket_"]].drop_duplicates("day").sort_values("day").reset_index(drop=True)
    chg = (day_reg["_bucket_"] != day_reg["_bucket_"].shift(1)) | (day_reg["day"].diff() > pd.Timedelta("1D"))
    day_reg["_ep"] = chg.cumsum()
    return d.merge(day_reg[["day", "_ep"]], on="day", how="left")


def run_mech(base, mech, sign, note):
    b = add_fg_episodes(bucketize(base))
    out = {"_base_note": note, "_n_base_raw": int(len(b))}
    for nm in ["low_fear", "mid", "high_greed"]:
        arm = b[b._bucket_ == nm]
        out[nm] = L.run_gate(arm, "r_bps", f"{mech}_{nm}", sign=sign, note=note)
    # unconditional baseline on the SAME population (round-2 rule: compare arms, not zero)
    ball = b.copy()
    ball["_ep"] = ball["_ep"]  # same episode grid
    out["unconditional"] = L.run_gate(ball, "r_bps", f"{mech}_unconditional", sign=sign,
                                      note="same population, no F&G gate")
    out["spread_low_minus_high"] = L.arm_spread(out["low_fear"], out["high_greed"],
                                                "low_fear minus high_greed")
    out["delta_low_vs_uncond"] = L.arm_spread(out["low_fear"], out["unconditional"],
                                              "low_fear minus unconditional")
    # year composition of each bucket (the year-confound audit)
    comp = {}
    for nm in ["low_fear", "mid", "high_greed"]:
        s = b[b._bucket_ == nm]["_ts"].dt.year.value_counts(normalize=True).round(3)
        comp[nm] = {int(k): float(v) for k, v in s.items()}
        comp[nm + "_max_year_share"] = float(s.max()) if len(s) else None
    out["year_composition"] = comp
    return out


RES = {}

# ---------------- M2: LIQ_CASCADE_REPEAT_V1 (the frozen alpha) x F&G
m2_base = D[(D.n_events_sym_24h >= 2) & (D.is_long_cascade == 1)]
RES["M2_liq_cascade_repeat_x_fg"] = run_mech(
    m2_base, "M2", +1.0, "frozen LIQ_CASCADE_REPEAT_V1 rule: n_events_sym_24h>=2 & long cascade -> LONG fwd_4h")

# ---------------- M3: SHORT_SQUEEZE repeat, momentum convention x F&G
m3_base = D[(D.n_events_sym_24h >= 2) & (D.is_long_cascade == 0)]
RES["M3_short_squeeze_repeat_x_fg"] = run_mech(
    m3_base, "M3", +1.0, "round-3 A4 convention: short-squeeze repeat -> LONG (momentum) fwd_4h")

# ---------------- M4: cascade ONSET null, F&G rescue attempt
m4_base = D[(D.n_events_sym_24h == 0)]
RES["M4_cascade_onset_x_fg"] = run_mech(
    m4_base, "M4", -1.0, "onset (1st hit in 24h) FADE -> short the move, fwd_4h; round-3 A1/A2/T1.9 all DEAD")

# ---------------- M6: F&G 7d CHANGE (surprise) rather than level, on the M2 base
m6 = m2_base.dropna(subset=["fg_chg_7d"]).copy()
m6["_bucket_"] = np.select([m6.fg_chg_7d <= -10, m6.fg_chg_7d >= 10],
                           ["sent_deteriorating", "sent_improving"], "flat")
m6 = add_fg_episodes(m6)
o6 = {"_base_note": "M2 base gated by 7d CHANGE in F&G (+-10 points, preregistered)"}
for nm in ["sent_deteriorating", "flat", "sent_improving"]:
    o6[nm] = L.run_gate(m6[m6._bucket_ == nm], "r_bps", f"M6_{nm}", sign=+1.0)
o6["spread_deteriorating_minus_improving"] = L.arm_spread(
    o6["sent_deteriorating"], o6["sent_improving"], "deteriorating minus improving")
RES["M6_fg_change_x_liq_repeat"] = o6

# ---------------- M7: sentiment/money divergence, F&G vs FUNDING (76% NaN -> N risk)
m7 = m2_base.dropna(subset=["funding_z30"]).copy()
m7["_bucket_"] = np.select(
    [(m7.fg_pct365 <= LO) & (m7.funding_z30 > 0),      # fear in the talk, longs still paying
     (m7.fg_pct365 <= LO) & (m7.funding_z30 <= 0)],    # fear in the talk AND in the money
    ["divergent_fear_vs_longfunding", "aligned_fear"], "other")
m7 = add_fg_episodes(m7)
o7 = {"_base_note": "M2 base; divergence = low F&G (fear) but positive funding z (money still long)",
      "_funding_nan_frac_in_base": round(float(m2_base.funding_z30.isna().mean()), 3)}
for nm in ["divergent_fear_vs_longfunding", "aligned_fear", "other"]:
    o7[nm] = L.run_gate(m7[m7._bucket_ == nm], "r_bps", f"M7_{nm}", sign=+1.0)
o7["spread_divergent_minus_aligned"] = L.arm_spread(
    o7["divergent_fear_vs_longfunding"], o7["aligned_fear"], "divergent minus aligned")
RES["M7_fg_vs_funding_divergence"] = o7

# ---------------- M8: sentiment/money divergence, F&G vs LSR (long history, 1.2% NaN)
m8 = m2_base.dropna(subset=["ls_ratio_z"]).copy()
m8["_bucket_"] = np.select(
    [(m8.fg_pct365 <= LO) & (m8.ls_ratio_z > 0),
     (m8.fg_pct365 <= LO) & (m8.ls_ratio_z <= 0)],
    ["divergent_fear_vs_crowdedlong", "aligned_fear"], "other")
m8 = add_fg_episodes(m8)
o8 = {"_base_note": "M2 base; divergence = low F&G (fear) but positive ls_ratio_z (retail still crowded long)"}
for nm in ["divergent_fear_vs_crowdedlong", "aligned_fear", "other"]:
    o8[nm] = L.run_gate(m8[m8._bucket_ == nm], "r_bps", f"M8_{nm}", sign=+1.0)
o8["spread_divergent_minus_aligned"] = L.arm_spread(
    o8["divergent_fear_vs_crowdedlong"], o8["aligned_fear"], "divergent minus aligned")
RES["M8_fg_vs_lsr_divergence"] = o8

json.dump(RES, open("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment/evidence/m2_m8_results.json", "w"), indent=1, default=str)

# compact console view
for mech, r in RES.items():
    print("\n=====", mech)
    for k, v in r.items():
        if isinstance(v, dict) and "net_bps" in v:
            print(f"  {k:34s} nRaw={v['n_raw']:6d} L1={v['n_independent_L1']:6d} L3={v['n_independent_L3']:5d} "
                  f"net={v['net_bps']:8.2f} s28={v['net_bps_stress28']:8.2f} t={str(v['t_stat_declustered']):>7s} "
                  f"ci={v['bootstrap_ci95']} ETAy={v['eta_forward_confirmation_years']}")
        elif isinstance(v, dict) and "comparison" in v:
            print(f"  >> {v['comparison']}: spread={v['spread_bps']}")
