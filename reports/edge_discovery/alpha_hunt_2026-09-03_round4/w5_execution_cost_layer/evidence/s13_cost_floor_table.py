"""W5/s13 - the FINAL COST FLOOR TABLE: what replaces `net_bps = gross_bps - 14`.

Policy modelled = what a strategy that MUST trade actually pays:
    post-only at the touch for T seconds, cross on timeout.

    cost_rt(sym,T) = 2 * [ Pf(T) * (-s/2 + fee_maker + AS_corrected + haircut)
                         + (1-Pf(T)) * ( s/2 + fee_taker + chase(T) ) ]

chase(T) is measured on the real-book simulator: the cost of crossing after waiting T is
statistically indistinguishable from crossing immediately for T <= 300s (5.08-5.28 vs 5.19 bps
one-way) and worsens to 6.42 at T=600s. So chase(T<=300)=0 and chase(600)=+1.2 bps one-way.
This is why "post then cross" is nearly free optionality below 5 minutes and stops being so
above it.

Pf(T) is the probe's own fill rate (15 symbols x 7 weeks) under the traversal rule, which is a
LOWER bound on the true fill probability: traversal implies a real fill, but a real order also
fills at the touch without traversal (measured bias +1.7pp at TTL 600s on the overlap cells).
Using it unadjusted is deliberately conservative.

AS_corrected = rho(spread) * AS_probe_rule, the s10 bridge. haircut = 1.0 bps one-way for the
frictions no virtual instrument can see (latency, post-only rejection, queue joiners, hidden
size, our own footprint).
"""
import os, json
import numpy as np, pandas as pd

FEE_T, FEE_M = 5.0, 2.0
RHO_A, RHO_B, RHO_FLOOR = 0.9301, -0.3095, 0.60
HAIRCUT = 1.0
CHASE = {60: 0.0, 600: 1.2}
URGENCY_MAKER_RT = {"none": 0.0, "shock_p99": 1.95, "shock_p999": 10.39}   # MOMENTUM arm, s12
URGENCY_TAKER_RT = {"none": 0.0, "shock_p99": -0.23, "shock_p999": -0.20}
S = os.environ["W5_SCRATCH"]


def rho(sp):
    return float(np.clip(RHO_A + RHO_B * sp, RHO_FLOOR, 1.0))


p = pd.read_parquet(f"{S}/panel.parquet")
rows = []
for sym, g in p.groupby("symbol"):
    sp = float(g.spread_bps.median())
    as_p = sp / 2 - float(g.adv_buy.mean())
    as_c = rho(sp) * as_p
    pf60 = float((g.ttf_buy <= 60).mean())
    pf600 = float(g.fill_buy.mean())
    cm_fill = -sp / 2 + FEE_M + as_c + HAIRCUT
    ct = sp / 2 + FEE_T
    r = dict(symbol=sym, spread_bps=round(sp, 3), AS_corrected=round(as_c, 2),
             pfill_60s=round(pf60, 3), pfill_600s=round(pf600, 3),
             cost_taker_rt=round(2 * ct, 2),
             cost_maker_policy_rt_T60=round(2 * (pf60 * cm_fill + (1 - pf60) * (ct + CHASE[60])), 2),
             cost_maker_policy_rt_T600=round(2 * (pf600 * cm_fill + (1 - pf600) * (ct + CHASE[600])), 2),
             cost_maker_if_filled_rt=round(2 * cm_fill, 2))
    r["best_rt"] = min(r["cost_taker_rt"], r["cost_maker_policy_rt_T60"], r["cost_maker_policy_rt_T600"])
    r["gain_vs_convention14"] = round(14.0 - r["best_rt"], 2)
    r["best_mode"] = ["taker", "maker_T60", "maker_T600"][int(np.argmin(
        [r["cost_taker_rt"], r["cost_maker_policy_rt_T60"], r["cost_maker_policy_rt_T600"]]))]
    rows.append(r)
T = pd.DataFrame(rows).sort_values("spread_bps").reset_index(drop=True)
print("=== COST FLOOR, per symbol, round-trip bps (haircut included) ===")
print(T.to_string())

# tiers used by the retrospective
tiers = {"T1_MAJOR":     ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
         "T2_LIQUID_ALT": ["XRPUSDT", "LINKUSDT", "SOLUSDT", "DOGEUSDT", "SUIUSDT", "AVAXUSDT"],
         "T3_MID_ALT":    ["PYTHUSDT", "ORDIUSDT", "TIAUSDT"],
         "T4_WIDE_ALT":   ["ARUSDT", "ADAUSDT", "FETUSDT"]}
tt = []
for k, v in tiers.items():
    g = T[T.symbol.isin(v)]
    e = dict(tier=k, symbols=" ".join(v), spread_bps=round(g.spread_bps.median(), 2),
             cost_taker_rt=round(g.cost_taker_rt.mean(), 1),
             cost_maker_T60_rt=round(g.cost_maker_policy_rt_T60.mean(), 1),
             cost_maker_T600_rt=round(g.cost_maker_policy_rt_T600.mean(), 1))
    e["best_rt_slow"] = min(e["cost_taker_rt"], e["cost_maker_T60_rt"], e["cost_maker_T600_rt"])
    e["best_rt_urgent_p99"] = round(min(e["cost_taker_rt"] + URGENCY_TAKER_RT["shock_p99"],
                                        e["cost_maker_T60_rt"] + URGENCY_MAKER_RT["shock_p99"]), 1)
    e["best_rt_urgent_p999"] = round(min(e["cost_taker_rt"] + URGENCY_TAKER_RT["shock_p999"],
                                         e["cost_maker_T60_rt"] + URGENCY_MAKER_RT["shock_p999"]), 1)
    e["delta_vs_convention_slow"] = round(e["best_rt_slow"] - 14.0, 1)
    e["delta_vs_convention_urgent_p999"] = round(e["best_rt_urgent_p999"] - 14.0, 1)
    tt.append(e)
TT = pd.DataFrame(tt)
print("\n=== COST FLOOR BY TIER x URGENCY (round-trip bps). This replaces the flat -14. ===")
print(TT.to_string())

json.dump({"per_symbol": T.to_dict("records"), "by_tier": TT.to_dict("records"),
           "chase_cost_oneway_bps": CHASE, "haircut_oneway_bps": HAIRCUT,
           "urgency_maker_rt": URGENCY_MAKER_RT, "urgency_taker_rt": URGENCY_TAKER_RT},
          open(f"{S}/cost_floor.json", "w"), indent=1, default=float)
T.to_csv(f"{S}/cost_floor_per_symbol.csv", index=False)
TT.to_csv(f"{S}/cost_floor_by_tier.csv", index=False)
print("\nwrote", f"{S}/cost_floor.json")
