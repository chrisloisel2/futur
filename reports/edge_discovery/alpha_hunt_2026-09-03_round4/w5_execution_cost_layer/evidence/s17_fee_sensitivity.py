"""W5/s17 - fee-schedule sensitivity. The report claims the fee tier is the single input that
would most change its conclusions; this quantifies that instead of asserting it.

The measured, fee-independent quantities are the SPREAD and the ADVERSE SELECTION. Fees enter
linearly, so the whole cost model can be re-run for any tier:

    cost_taker_rt(s, ft)     = s + 2*ft
    cost_maker_rt(s, fm)     = -s + 2*fm + 2*AS(s) + 2*haircut ,  AS(s) = 0.884 + 1.001*s
    resurrection floor       = 1.5 * min(cost_taker_rt, cost_maker_policy_rt)

The "resurrection band" is (1.5*cost_realistic, 14]: mechanisms that were dead under the flat
-14 convention and survive BOTH the measured cost and the preregistered 1.5x stress.
"""
import os, json
import numpy as np, pandas as pd

S = os.environ["W5_SCRATCH"]
HAIRCUT = 1.0
AS_A, AS_B = 0.884, 1.001          # AS_corrected(60s) = a + b*spread, R2=0.989 (s13 fit)
PF = {"T600": (0.916, -0.047)}     # P(fill<=600s) = a + b*spread, R2=0.937
TIERS = [("VIP0 (assumed base)", 5.0, 2.0), ("VIP1", 4.0, 1.6), ("VIP3", 3.4, 1.4),
         ("VIP5", 2.7, 1.0), ("VIP9 / market maker", 1.7, 0.0), ("MM rebate", 1.7, -0.5)]
SPREADS = {"T1_MAJOR": 0.05, "T2_LIQUID_ALT": 1.35, "T3_MID_ALT": 2.82, "T4_WIDE_ALT": 5.46}

rows = []
for name, ft, fm in TIERS:
    for tier, s in SPREADS.items():
        ct = s + 2 * ft
        as_ = AS_A + AS_B * s
        cm_fill = -s / 2 + fm + as_ + HAIRCUT
        pf = PF["T600"][0] + PF["T600"][1] * s
        cm_pol = 2 * (pf * cm_fill + (1 - pf) * (s / 2 + ft + 1.2))     # chase +1.2 at T=600s
        best = min(ct, cm_pol)
        band_lo = 1.5 * best
        rows.append(dict(fee_tier=name, taker_bps=ft, maker_bps=fm, tier=tier, spread_bps=s,
                         cost_taker_rt=round(ct, 2), cost_maker_rt=round(cm_pol, 2),
                         best_rt=round(best, 2),
                         resurrection_floor_1p5x=round(band_lo, 2),
                         resurrection_band_width=round(max(0.0, 14.0 - band_lo), 2),
                         mode="maker" if cm_pol < ct else "taker"))
D = pd.DataFrame(rows)
print("=== FEE SENSITIVITY: does the round 1-3 graveyard reopen under a better fee tier? ===")
print(D.pivot_table(index="fee_tier", columns="tier", values="resurrection_band_width",
                    sort=False).round(2).to_string())
print("\n(width in bps of the band `gross in (1.5*cost_realistic, 14]` = mechanisms dead at the")
print(" -14 convention that would survive BOTH the measured cost and the 1.5x preregistered stress)")
print("\n=== full table ===")
print(D.to_string(index=False))
json.dump(D.to_dict("records"), open(f"{S}/fee_sensitivity.json", "w"), indent=1, default=float)
print("\nwrote", f"{S}/fee_sensitivity.json")
