"""RECONCILIATION — B2_EU_to_US (family_b.py) vs CLOCKMAP_h13 (clock_map.py).

These two are, by construction, the SAME trade:
  signal = residual return over the EU session [07:00,13:00)
  entry  = price at 13:05  (one 5m bar of implementation lag)
  exit   = price at 21:00
  trade  = bottom-quintile-by-signal MINUS top-quintile, equal weight, dollar-neutral

family_b.py reports -10.66 bps (t=-3.53).  clock_map.py reports +0.80 bps (t=+0.27).
They cannot both be right.  This script rebuilds both code paths on the same panel and
finds where they diverge.  Run: .venv/bin/python verify_b2_vs_clockmap.py
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread

c = con()
h = c.execute(f"""SELECT symbol, hour_end, close_at_hour_end, close_first5, dv_usd,
                         resid_logret_hour, n_bars
                  FROM read_parquet('{SCRATCH}/hourly.parquet')
                  WHERE close_at_hour_end IS NOT NULL AND n_bars >= 10
                  ORDER BY symbol, hour_end""").df()
h["hour_end"] = pd.to_datetime(h["hour_end"], utc=True)
h["d"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.floor("D")
h["hb"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.hour
h = h.merge(eligibility(), on=["symbol", "d"], how="left")
h = h[h["eligible"].fillna(False)].copy()
print("eligible hourly rows", len(h))

px_at = h[["symbol", "hour_end", "close_at_hour_end"]].rename(columns={"hour_end": "T", "close_at_hour_end": "p"})
ent = h[["symbol", "hour_end", "close_first5"]].copy()
ent["T"] = ent["hour_end"] - pd.Timedelta(hours=1)
ent = ent[["symbol", "T", "close_first5"]].rename(columns={"close_first5": "p5"})

# ---------------- path A : family_b.py (session groupby) ---------------------------
SESS = [("ASIA", 0, 7), ("EU", 7, 13), ("US", 13, 21), ("LATE", 21, 24)]
h["sess"] = h["hb"].map(lambda x: next((n for n, a, b in SESS if a <= x < b), None))
sa = h.dropna(subset=["sess"]).groupby(["symbol", "d", "sess"], sort=False).agg(
    resid=("resid_logret_hour", "sum"), nh=("hour_end", "size")).reset_index()
eu = sa[(sa["sess"] == "EU") & (sa["nh"] >= 6)].copy()
eu["H"] = eu["d"] + pd.Timedelta(hours=13)
A = eu[["symbol", "H", "resid"]].rename(columns={"resid": "sig_A"})

# ---------------- path B : clock_map.py (time-based rolling) -----------------------
h = h.sort_values(["symbol", "hour_end"])
h["resid6"] = (h.set_index("hour_end").groupby("symbol")["resid_logret_hour"]
                .rolling("6h", min_periods=6).sum().reset_index(level=0, drop=True).to_numpy())
B = h.loc[h["hour_end"].dt.hour == 13, ["symbol", "hour_end", "resid6"]].rename(
        columns={"hour_end": "H", "resid6": "sig_B"}).dropna()

# ---------------- 1. do the two SIGNALS agree? -------------------------------------
j = A.merge(B, on=["symbol", "H"], how="outer", indicator=True)
print("\n--- signal reconciliation at H=13:00 ---")
print(j["_merge"].value_counts().to_dict())
both = j[j["_merge"] == "both"].dropna(subset=["sig_A", "sig_B"])
print("rows in both:", len(both),
      "| corr:", round(float(both["sig_A"].corr(both["sig_B"])), 6),
      "| max |diff|:", float((both["sig_A"] - both["sig_B"]).abs().max()))
bad = both[(both["sig_A"] - both["sig_B"]).abs() > 1e-9]
print("rows where signals differ:", len(bad), f"({100*len(bad)/max(len(both),1):.2f}%)")
if len(bad):
    print(bad.head(8).to_string(index=False))

# ---------------- 2. same trade, each signal --------------------------------------
def trade(sig_df, sigcol, tag):
    d = sig_df.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="inner")
    d["T_exit"] = d["H"] + pd.Timedelta(hours=8)
    d = d.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
    d = d[(d["p_entry"] > 0) & (d["p_exit"] > 0)].copy()
    d["ret_next"] = np.log(d["p_exit"] / d["p_entry"])
    sp, n1 = xs_spread(d, "H", sigcol, ["ret_next"], n_buckets=5, min_xs=20)
    v = sp["ret_next_spread"].to_numpy()
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    print(f"{tag:34s} n_events={len(sp):5d} rows={len(d):8d} gross={v.mean():8.3f} bps  t={t:6.3f}")
    return sp.set_index("H")["ret_next_spread"]

print("\n--- same execution path, the two signals ---")
sA = trade(A, "sig_A", "A: session-groupby signal")
sB = trade(B, "sig_B", "B: 6h-rolling signal")

# ---------------- 3. the actual clock_map code path, verbatim ---------------------
sig = h[["symbol", "hour_end", "resid6", "dv_usd"]].dropna(subset=["resid6"]).rename(columns={"hour_end": "H"})
sig["hb"] = sig["H"].dt.hour
base = sig.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="inner")
base["T_exit"] = base["H"] + pd.Timedelta(hours=8)
base = base.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="inner")
base = base[(base["p_entry"] > 0) & (base["p_exit"] > 0)]
base["ret_next"] = np.log(base["p_exit"] / base["p_entry"])
sub = base[base["hb"] == 13]
sp, _ = xs_spread(sub, "H", "resid6", ["ret_next"], n_buckets=5, min_xs=20)
v = sp["ret_next_spread"].to_numpy()
print(f"{'C: clock_map verbatim h13':34s} n_events={len(sp):5d} rows={len(sub):8d} "
      f"gross={v.mean():8.3f} bps  t={v.mean()/(v.std(ddof=1)/np.sqrt(len(v))):6.3f}")

# ---------------- 4. THE SUSPECT: clock_map's rolling ignores the day boundary -----
# `.rolling('6h')` is applied to the symbol's whole eligible series. If the symbol has a
# hole, the 6h window ending at 13:00 can be satisfied by FEWER than the 6 EU hours plus
# older bars -- but more importantly min_periods=6 with a 6h window is only satisfiable by
# exactly the 6 hourly bars 08:00..13:00, so a hole makes it NaN rather than wrong.
# The real suspect is the EXIT merge: check the exit-price coverage of each path.
print("\n--- exit-price / entry-price coverage ---")
for nm, dd in (("A", A), ("B", B)):
    x = dd.merge(ent.rename(columns={"T": "H", "p5": "p_entry"}), on=["symbol", "H"], how="left")
    x["T_exit"] = x["H"] + pd.Timedelta(hours=8)
    x = x.merge(px_at.rename(columns={"T": "T_exit", "p": "p_exit"}), on=["symbol", "T_exit"], how="left")
    print(f"  {nm}: n={len(x)} entry_missing={x['p_entry'].isna().mean():.4f} exit_missing={x['p_exit'].isna().mean():.4f}")

# ---------------- 5. per-year comparison of the two spread series -----------------
cmp = pd.concat([sA.rename("A"), sB.rename("B")], axis=1)
cmp["year"] = cmp.index.year
print("\n--- per-year gross spread, both signals ---")
print(cmp.groupby("year")[["A", "B"]].agg(["mean", "count"]).round(2).to_string())
