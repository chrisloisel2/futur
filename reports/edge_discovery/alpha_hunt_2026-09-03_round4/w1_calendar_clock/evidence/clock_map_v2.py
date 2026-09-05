"""THE CLOCK MAP (v2, bug-fixed) — the central deliverable of the W1 axis.

Supersedes clock_map.py, whose 6h signal was destroyed by the pandas offset-rolling bug
documented in clock_lib.py. Every number in results_clock_map.json is void.

For every UTC entry hour H (0..23) the SAME mechanism is run, only the hour changes:
    signal = residual return over the 6 contiguous hours [H-6h, H)   (causal, beta-residual)
    entry  = price at H+5m      (one full 5m bar of implementation lag, PREREG §1)
    exit   = price at H+8h
    trade  = equal-weight bottom-quintile-by-signal MINUS top-quintile (dollar-neutral)
             spread > 0 = reversion ; spread < 0 = continuation
H=13 is EXACTLY the B2_EU_to_US session mechanism, so the map contains its own cross-check.

One observation per arm per day => the L2 decluster (calendar day) is exact by construction,
which is the binding constraint on a clock axis: every symbol sees the same hour at once.

ARM-vs-ARM is the actual clock claim and is reported three ways, never against zero:
  (i)  each hour minus the mean of the other 23 arms, paired on the same calendar day;
  (ii) the extreme pair (argmax minus argmin);
  (iii) the four session-boundary hours against each other.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
from clock_lib import resid_roll_hours, assert_roll_ok, load_hourly, paired_contrast
OUT = os.path.dirname(os.path.abspath(__file__))

SIG_H, HOLD_H, TRAIN_END = 6, 8, pd.Timestamp("2024-01-01", tz="UTC")

h, px_at, ent = load_hourly(SCRATCH, con, eligibility)
print("eligible hourly rows", len(h), "| symbols", h["symbol"].nunique())

h = resid_roll_hours(h, SIG_H)
assert_roll_ok(h, SIG_H)
print(f"resid{SIG_H} built and independently verified against a ns-index reference window")

sig = h[["symbol", "hour_end", f"resid{SIG_H}", "dv_usd"]].dropna(subset=[f"resid{SIG_H}"]).rename(
    columns={"hour_end": "H"})
sig["hb"] = sig["H"].dt.hour
base = sig.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="inner")
base["T_exit"] = base["H"] + pd.Timedelta(hours=HOLD_H)
base = base.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
base = base[(base["p_entry"] > 0) & (base["p_exit"] > 0)].copy()
base["ret_next"] = np.log(base["p_exit"] / base["p_entry"])
print("tradeable rows", len(base))

rows, results, series = [], [], {}
for hb in range(24):
    sub = base[base["hb"] == hb]
    sp, n1 = xs_spread(sub, "H", f"resid{SIG_H}", ["ret_next"], n_buckets=5, min_xs=20)
    if len(sp) < 200:
        continue
    o = sp[["H", "ret_next_spread"]].rename(columns={"H": "ts", "ret_next_spread": "ret_bps"})
    r = run_gate(o, f"CLOCKMAP_h{hb:02d}", "spread(losers-winners)>0 = reversion, <0 = continuation",
                 n_ind_L1=n1, cost_legs=2, n_boot=1500)
    tr = sp[sp["H"] < TRAIN_END]["ret_next_spread"]
    te = sp[sp["H"] >= TRAIN_END]["ret_next_spread"]
    r["train_gross_bps"], r["test_gross_bps"] = round(float(tr.mean()), 2), round(float(te.mean()), 2)
    r["n_train"], r["n_test"] = int(len(tr)), int(len(te))
    te_v = te.to_numpy()
    r["test_t_signfrozen"] = round(float(np.sign(tr.mean()) * te_v.mean() /
                                        (te_v.std(ddof=1) / np.sqrt(len(te_v)))), 3)
    series[hb] = sp.set_index(sp["H"].dt.floor("D"))["ret_next_spread"].groupby(level=0).mean()
    results.append(r)
    rows.append(dict(hour=hb, gross_bps=r["gross_bps"], t=r["t_stat_declustered"],
                     train=r["train_gross_bps"], test=r["test_gross_bps"],
                     test_t_signfrozen=r["test_t_signfrozen"], n_days=r["n_independent_L2"]))

crit = family_maxt(results, n_boot=1200)
dfm = pd.DataFrame(rows)
dfm["signif"] = np.where(dfm["t"].abs() >= crit, "***", "")
dfm["regime"] = np.where(dfm["gross_bps"] > 0, "reversion", "continuation")
print(f"\nCLOCK MAP v2 — 24 arms, signal {SIG_H}h, hold {HOLD_H}h, only the UTC entry hour changes")
print("family max-|t| 95% critical value (24 arms):", round(crit, 3))
print(dfm.to_string(index=False))

# ---------------- ARM vs ARM (i): hour H minus the other 23, paired on days ----------
allser = pd.concat([s.rename(k) for k, s in series.items()], axis=1)
contrasts = []
for hb in series:
    others = allser.drop(columns=[hb]).mean(axis=1)
    j = pd.concat([allser[hb].rename("a"), others.rename("b")], axis=1).dropna()
    v = (j["a"] - j["b"]).to_numpy()
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    ci, _ = block_bootstrap_ci(v, n_boot=3000)
    contrasts.append(dict(comparison=f"h{hb:02d} minus mean(other 23 hours)",
                          diff_bps=round(float(v.mean()), 3), n_paired_days=int(len(v)),
                          t=round(float(t), 3), ci95=[round(ci[0], 2), round(ci[1], 2)]))
dfc = pd.DataFrame(contrasts)

# family-wise critical value for the 24 CONTRASTS (not the 24 levels)
rng = np.random.default_rng(4242)
cv = allser.dropna()
cent = {hb: (cv[hb] - cv.drop(columns=[hb]).mean(axis=1)).to_numpy() for hb in series}
for k in cent:
    cent[k] = cent[k] - cent[k].mean()
n = len(cv)
maxts = np.zeros(1500)
for k in range(1500):
    st = rng.integers(0, n - 7 + 1, size=int(np.ceil(n / 7)))
    sel = (st[:, None] + np.arange(7)).ravel()[:n]
    maxts[k] = max(abs(x[sel].mean() / (x[sel].std(ddof=1) / np.sqrt(n))) for x in cent.values())
crit_contrast = float(np.percentile(maxts, 95))
dfc["signif"] = np.where(dfc["t"].abs() >= crit_contrast, "***", "")
print(f"\n--- ARM vs ARM (i): each hour minus the other 23, paired on the same calendar days ---")
print(f"family-wise max-|t| 95% critical value over the 24 contrasts: {crit_contrast:.3f}")
print(dfc.to_string(index=False))

# ---------------- ARM vs ARM (ii)/(iii) ---------------------------------------------
hi = int(dfm.loc[dfm["gross_bps"].idxmax(), "hour"])
lo = int(dfm.loc[dfm["gross_bps"].idxmin(), "hour"])
pairs = [(hi, lo), (13, 7), (13, 21), (13, 0), (7, 21), (21, 0)]
arm = [x for x in (paired_contrast(series, a, b, block_bootstrap_ci) for a, b in pairs) if x]
arm = [dict(x, comparison=f"h{p[0]:02d} minus h{p[1]:02d}") for x, p in zip(arm, pairs)]
print("\n--- ARM vs ARM (ii)+(iii): extreme pair and the four session boundaries ---")
print(pd.DataFrame(arm).to_string(index=False))

for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"config": {"signal_hours": SIG_H, "hold_hours": HOLD_H,
                      "supersedes": "results_clock_map.json (void: pandas offset-rolling bug)"},
           "clock_map": results, "table": rows,
           "arm_vs_arm_vs_rest": contrasts, "arm_vs_arm_pairs": arm,
           "family_maxt_crit_levels": round(crit, 3),
           "family_maxt_crit_contrasts": round(crit_contrast, 3)},
          open(f"{OUT}/results_clock_map_v2.json", "w"), indent=1, default=str)
print("\nmax |gross| across 24 arms:", round(dfm["gross_bps"].abs().max(), 2),
      "bps vs 2-leg base cost 28bps | arms clearing 28bps:",
      int((dfm["gross_bps"].abs() > 28).sum()), "/", len(dfm))
print("arms significant at the family max-t level:", int((dfm["t"].abs() >= crit).sum()), "/", len(dfm))
print("contrasts significant at the family max-t level:", int((dfc["t"].abs() >= crit_contrast).sum()), "/", len(dfc))
