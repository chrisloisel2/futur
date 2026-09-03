"""W7 round4 — M1 (IV term structure -> direction), M2 (skew dynamics), M6 (DVOL BTC/ETH divergence)."""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, two_arm, causal_z
from prep import load_all, uniform_position, sign_from_first_half

opt, dvol, px, ret, fwd = load_all()
D = os.path.dirname(os.path.abspath(__file__))
res = {}

# =========================== M1 — IV term structure slope / inversion ===========================
opt["term_slope"] = opt["iv_near"] - opt["iv_far"]          # >0 = inverted (near above far) = stress
idx = opt.index.intersection(fwd.index)
sl = opt["term_slope"].reindex(idx)
f_btc = fwd["BTCUSDT"].reindex(idx)

# descriptive two-arm test FIRST (§1.3): inverted vs contango, forward BTC return, day-declustered
inv = sl > 0
arm = two_arm((f_btc[inv]*1e4).dropna(), (f_btc[~inv & sl.notna()]*1e4).dropna(), "inverted", "contango")
raw_pos, pct = uniform_position(sl, idx)
sgn, cut = sign_from_first_half(raw_pos, f_btc)
pos = pd.DataFrame({"BTCUSDT": sgn*raw_pos})
res["M1_term_structure_direction"] = run_gate(
    pos.loc[cut:], fwd[["BTCUSDT"]].reindex(pos.index).loc[cut:],
    "M1 — IV term-structure slope (near 2-10d minus far 45-180d) -> BTC perp direction",
    notes=(f"Sign learned on first half (sign={sgn:+.0f}), gate run OOS from {cut.date()}. "
           f"Two-arm descriptive (full sample, inverted vs contango, forward 1d BTC bps): {arm}. "
           "Distinct from W6-M7/M8 which targeted forward RV, not direction."),
    extra={"two_arm_full_sample": arm, "sign_learned": sgn, "oos_start": str(cut.date())})
res["M1_term_structure_direction_FULLSAMPLE"] = run_gate(
    pos, fwd[["BTCUSDT"]].reindex(pos.index),
    "M1b — same, full sample (sign-fitted in-sample, reported for completeness only)",
    notes="SIGN-FITTED on this same sample -> optimistic, not a validation basis.")

# =========================== M2 — skew dynamics (velocity + normalisation) ===========================
opt["skew"] = opt["iv_put_wing"] - opt["iv_call_wing"]
opt["d_skew_1d"] = opt["skew"].diff()
opt["d_skew_3d"] = opt["skew"].diff(3)
sk = opt["skew"].reindex(idx)
for tag, sig in [("velocity_1d", opt["d_skew_1d"].reindex(idx)),
                 ("velocity_3d", opt["d_skew_3d"].reindex(idx))]:
    raw_pos, _ = uniform_position(sig, idx)
    sgn, cut = sign_from_first_half(raw_pos, f_btc)
    pos = pd.DataFrame({"BTCUSDT": sgn*raw_pos})
    res[f"M2_skew_{tag}"] = run_gate(
        pos.loc[cut:], fwd[["BTCUSDT"]].reindex(pos.index).loc[cut:],
        f"M2 — skew {tag} (d put-wing IV minus call-wing IV) -> BTC perp direction",
        notes=f"Sign learned first half (sign={sgn:+.0f}), gate OOS from {cut.date()}. "
              "Level is NOT tested (already covered by LIQ_REPEAT_SKEW_OVERLAY + W6-M6).",
        extra={"sign_learned": sgn, "oos_start": str(cut.date())})

# M2c — 'capitulation done': skew was in top decile within last 5d AND is now falling fast
sk_pct = sk.rolling(252, min_periods=90).apply(lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
was_panic = (sk_pct > 0.90).rolling(5, min_periods=1).max().astype(float)
normalising = (opt["d_skew_3d"].reindex(idx) < 0).astype(float)
state = ((was_panic > 0) & (normalising > 0) & (sk_pct < 0.90))
arm2 = two_arm((f_btc[state]*1e4).dropna(), (f_btc[~state & sk_pct.notna()]*1e4).dropna(), "normalising", "rest")
pos = pd.DataFrame({"BTCUSDT": state.astype(float)})       # LONG only, preregistered direction
res["M2_skew_capitulation_normalisation"] = run_gate(
    pos, fwd[["BTCUSDT"]].reindex(pos.index),
    "M2c — skew normalising after a put-panic (top-decile skew within 5d, then 3d fall) -> LONG BTC perp",
    notes=f"Direction preregistered LONG (no sign fitting). Two-arm vs rest: {arm2}",
    extra={"two_arm": arm2})

# =========================== M6 — DVOL BTC vs ETH divergence -> BTC/ETH pair ===========================
dv = dvol.dropna()
common = dv.index.intersection(fwd.index)
zb = causal_z(dv["dvol_btc"], 252, 60).reindex(common)
ze = causal_z(dv["dvol_eth"], 252, 60).reindex(common)
div = (ze - zb)                                    # ETH vol repricing rich vs BTC
ratio = (dv["dvol_eth"]/dv["dvol_btc"]).reindex(common)
zratio = causal_z(dv["dvol_eth"]/dv["dvol_btc"], 252, 60).reindex(common)
pair_fwd = pd.DataFrame({"BTCUSDT": fwd["BTCUSDT"].reindex(common),
                         "ETHUSDT": fwd["ETHUSDT"].reindex(common)})
for tag, sig in [("z_divergence", div), ("dvol_ratio", zratio)]:
    raw_pos, _ = uniform_position(sig, common)
    # pair leg return, dollar-neutral 50/50
    leg = 0.5*(pair_fwd["BTCUSDT"] - pair_fwd["ETHUSDT"])
    sgn, cut = sign_from_first_half(raw_pos, leg)
    pos = pd.DataFrame({"BTCUSDT": 0.5*sgn*raw_pos, "ETHUSDT": -0.5*sgn*raw_pos})
    res[f"M6_dvol_{tag}_pair"] = run_gate(
        pos.loc[cut:], pair_fwd.reindex(pos.index).loc[cut:],
        f"M6 — DVOL {tag} -> dollar-neutral BTC/ETH perp pair",
        notes=f"Sign learned first half (sign={sgn:+.0f}), gate OOS from {cut.date()}. "
              f"DVOL_ETH is used here for the first time in this project (W6 had no ETH leg). "
              f"Coverage {common.min().date()}..{common.max().date()}.",
        extra={"sign_learned": sgn, "oos_start": str(cut.date())})
    # outright BTC comparison arm to prove the pair is what buys the Sharpe
    sgn2, cut2 = sign_from_first_half(raw_pos, pair_fwd["BTCUSDT"])
    pos2 = pd.DataFrame({"BTCUSDT": sgn2*raw_pos})
    res[f"M6_dvol_{tag}_outright_btc"] = run_gate(
        pos2.loc[cut2:], pair_fwd[["BTCUSDT"]].reindex(pos2.index).loc[cut2:],
        f"M6b — DVOL {tag} -> outright BTC perp (control arm for the pair)",
        notes="Control: same signal, outright instead of market-neutral, to isolate the sigma reduction.")

json.dump(res, open(f"{D}/results_m1_m2_m6.json","w"), indent=1, default=str)
for k, v in res.items():
    print(f"{k:46s} net={v['net_bps']:>8} stress={v['net_bps_stress28']:>8} t={v['t_stat_declustered']:>6} "
          f"SR={v['sharpe_annual_net']:>7} L3={v['n_independent_L3']:>5} ETA_y={v['eta_forward_confirmation_years']}")
