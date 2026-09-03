"""THE CLOCK MAP — the central deliverable of this axis.

For every UTC entry hour H (0..23), run the SAME cross-sectional mechanism:
   signal = residual return over [H-6h, H)   (causal, beta-hedged vs BTC/ETH)
   entry  = price at H+5m   (one 5m bar of implementation lag, PREREG §1)
   exit   = price at H+8h
   trade  = equal-weight bottom-quintile-by-signal MINUS top-quintile  (dollar-neutral)

If the crypto clock is real, the SIGN of cross-sectional autocorrelation depends on H.
If it is not, all 24 arms agree and the session story is just the known reversal in a hat.
One observation per day per arm => the L2 decluster is exact by construction.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt, block_bootstrap_ci
OUT = os.path.dirname(os.path.abspath(__file__))

c = con()
h = c.execute(f"""SELECT symbol, hour_end, close_at_hour_end, close_first5, dv_usd,
                         resid_logret_hour, n_bars
                  FROM read_parquet('{SCRATCH}/hourly.parquet')
                  WHERE close_at_hour_end IS NOT NULL AND n_bars >= 10
                  ORDER BY symbol, hour_end""").df()
h["hour_end"] = pd.to_datetime(h["hour_end"], utc=True)
h["d"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.floor("D")
h = h.merge(eligibility(), on=["symbol", "d"], how="left")
h = h[h["eligible"].fillna(False)].copy()

# --- causal 6h residual momentum ending exactly at hour_end -------------------------
h = h.sort_values(["symbol", "hour_end"])
h["resid6"] = (h.set_index("hour_end").groupby("symbol")["resid_logret_hour"]
                .rolling("6h", min_periods=6).sum().reset_index(level=0, drop=True).to_numpy())

px_at = h[["symbol", "hour_end", "close_at_hour_end"]].rename(columns={"hour_end": "T", "close_at_hour_end": "p"})
ent = h[["symbol", "hour_end", "close_first5"]].copy()
ent["T"] = ent["hour_end"] - pd.Timedelta(hours=1)
ent = ent[["symbol", "T", "close_first5"]].rename(columns={"close_first5": "p5"})

sig = h[["symbol", "hour_end", "resid6", "dv_usd"]].dropna(subset=["resid6"]).rename(columns={"hour_end": "H"})
sig["hb"] = sig["H"].dt.hour
print("signal rows", len(sig))

HOLD = 8
base = sig.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="inner")
base["T_exit"] = base["H"] + pd.Timedelta(hours=HOLD)
base = base.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
base = base[(base["p_entry"] > 0) & (base["p_exit"] > 0)]
base["ret_next"] = np.log(base["p_exit"] / base["p_entry"])
print("tradeable rows", len(base))

TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
rows, results, series = [], [], {}
for hb in range(24):
    sub = base[base["hb"] == hb]
    sp, n1 = xs_spread(sub, "H", "resid6", ["ret_next"], n_buckets=5, min_xs=20)
    if len(sp) < 200: continue
    o = sp[["H", "ret_next_spread"]].rename(columns={"H": "ts", "ret_next_spread": "ret_bps"})
    r = run_gate(o, f"CLOCKMAP_h{hb:02d}", "spread(losers-winners)>0 = reversion, <0 = continuation",
                 n_ind_L1=n1, cost_legs=2, n_boot=1500)
    tr = sp[sp["H"] < TRAIN_END]["ret_next_spread"]; te = sp[sp["H"] >= TRAIN_END]["ret_next_spread"]
    r["train_gross_bps"] = round(float(tr.mean()), 2); r["test_gross_bps"] = round(float(te.mean()), 2)
    r["n_train"], r["n_test"] = int(len(tr)), int(len(te))
    series[hb] = sp.set_index(sp["H"].dt.floor("D"))["ret_next_spread"]
    results.append(r)
    rows.append(dict(hour=hb, gross_bps=r["gross_bps"], t=r["t_stat_declustered"],
                     train=r["train_gross_bps"], test=r["test_gross_bps"], n_days=r["n_independent_L2"]))

crit = family_maxt(results, n_boot=1200)
print("\nCLOCK MAP — 24 arms, identical mechanism, only the UTC entry hour changes")
print("family max-|t| 95% critical value (24 arms):", round(crit, 3))
dfm = pd.DataFrame(rows)
dfm["signif"] = np.where(dfm["t"].abs() >= crit, "***", "")
dfm["regime"] = np.where(dfm["gross_bps"] > 0, "reversion", "continuation")
print(dfm.to_string(index=False))

# --- ARM vs ARM, paired on calendar days (the actual clock claim) --------------------
def paired(a, b):
    j = pd.concat([series[a].groupby(level=0).mean().rename("a"),
                   series[b].groupby(level=0).mean().rename("b")], axis=1).dropna()
    v = (j["a"] - j["b"]).to_numpy()
    if len(v) < 30: return None
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    ci, _ = block_bootstrap_ci(v, n_boot=4000)
    return dict(comparison=f"h{a:02d} minus h{b:02d}", diff_bps=round(float(v.mean()), 2),
                n_paired_days=int(len(v)), t=round(float(t), 3), ci95=[round(ci[0], 2), round(ci[1], 2)])

hi = int(dfm.loc[dfm["gross_bps"].idxmax(), "hour"]); lo = int(dfm.loc[dfm["gross_bps"].idxmin(), "hour"])
arm = [x for x in [paired(hi, lo), paired(13, 1), paired(14, 2)] if x]
print("\n--- ARM vs ARM, paired on the same calendar days ---")
print(pd.DataFrame(arm).to_string(index=False))

for r in results:
    v, why = auto_verdict(r, family_maxt_crit=crit)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(crit, 3)
    r.pop("day_series", None)
json.dump({"clock_map": results, "arm_vs_arm": arm, "table": rows,
           "family_maxt_crit": round(crit, 3)},
          open(f"{OUT}/results_clock_map.json", "w"), indent=1, default=str)
print("\nmax |gross| across the 24 arms:", round(dfm['gross_bps'].abs().max(), 2),
      "bps vs 2-leg base cost 28bps  |  arms clearing 28bps:",
      int((dfm['gross_bps'].abs() > 28).sum()), "/", len(dfm))
