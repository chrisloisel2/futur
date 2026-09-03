"""Family B — SESSION CLOCK.

Central question of this worker's axis: does the CLOCK condition anything, or is any
cross-sectional effect found here just the already-known reversal wearing a session badge?
Every session test is therefore reported BOTH as a level and ARM-vs-ARM across boundaries.

Sessions (UTC, non-overlapping partition, PREREG §6): ASIA [00,07) EU [07,13) US [13,21)
LATE [21,24).
PIT: session S ends at boundary E (price known at E). Entry = price at E+5m
(`close_first5` of the hour starting at E). Exit = price at the next boundary.
"""
import json, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCRATCH, con, eligibility, xs_spread
from gate import run_gate, auto_verdict, family_maxt
OUT = os.path.dirname(os.path.abspath(__file__))

c = con()
h = c.execute(f"""
  SELECT symbol, hour_end, close_at_hour_end, close_first5, dv_usd, resid_logret_hour, n_bars
  FROM read_parquet('{SCRATCH}/hourly.parquet')
  WHERE close_at_hour_end IS NOT NULL AND n_bars >= 10
""").df()
h["hour_end"] = pd.to_datetime(h["hour_end"], utc=True)
h["d"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.floor("D")     # UTC day the BAR belongs to
h["hb"] = (h["hour_end"] - pd.Timedelta(hours=1)).dt.hour          # hour the bar starts
print("hourly rows", len(h))

el = eligibility()
h = h.merge(el, on=["symbol", "d"], how="left")
h = h[h["eligible"].fillna(False)]
print("eligible hourly rows", len(h), "days", h["d"].nunique())

SESS = [("ASIA", 0, 7), ("EU", 7, 13), ("US", 13, 21), ("LATE", 21, 24)]
def sess_of(hb):
    for nm, a, b in SESS:
        if a <= hb < b: return nm
    return None
h["sess"] = h["hb"].map(sess_of)

# --- price at each session boundary + entry price 5m later --------------------------
# boundary time B: price_at_B = close_at_hour_end of the bar ending at B
#                  price_at_B+5m = close_first5 of the bar STARTING at B (hour_end = B+1h)
bnd = h[h["hb"].isin([0, 7, 13, 21])].copy()          # bars STARTING at a boundary
bnd["B"] = bnd["hour_end"] - pd.Timedelta(hours=1)
bnd = bnd[["symbol", "B", "close_first5"]].rename(columns={"close_first5": "p_entry"})
close_at = h[h["hb"].isin([23, 6, 12, 20])].copy()    # bars ENDING at a boundary
close_at["B"] = close_at["hour_end"]
close_at = close_at[["symbol", "B", "close_at_hour_end"]].rename(columns={"close_at_hour_end": "p_at"})
bd = close_at.merge(bnd, on=["symbol", "B"], how="inner")
bd["bh"] = bd["B"].dt.hour
print("boundary rows", len(bd))

# --- session aggregates: residual return + raw return -------------------------------
sa = h.dropna(subset=["sess"]).groupby(["symbol", "d", "sess"], sort=False).agg(
    resid=("resid_logret_hour", "sum"), dv=("dv_usd", "sum"), nh=("hour_end", "size")).reset_index()
need = {"ASIA": 7, "EU": 6, "US": 8, "LATE": 3}
sa = sa[sa.apply(lambda r: r["nh"] >= need[r["sess"]], axis=1)]

SESS_END = {"ASIA": 7, "EU": 13, "US": 21, "LATE": 24}
sa["B_end"] = sa["d"] + pd.to_timedelta(sa["sess"].map(SESS_END), unit="h")
NEXT = {"ASIA": "EU", "EU": "US", "US": "LATE", "LATE": "ASIA"}
NEXT_END = {"ASIA": 13, "EU": 21, "US": 24, "LATE": 31}   # LATE -> next day's ASIA end (07:00 +24)
sa["B_next"] = sa["d"] + pd.to_timedelta(sa["sess"].map(NEXT_END), unit="h")

df = sa.merge(bd[["symbol", "B", "p_entry"]].rename(columns={"B": "B_end"}), on=["symbol", "B_end"], how="inner")
df = df.merge(bd[["symbol", "B", "p_at"]].rename(columns={"B": "B_next", "p_at": "p_exit"}), on=["symbol", "B_next"], how="inner")
df = df[(df["p_entry"] > 0) & (df["p_exit"] > 0)]
df["ret_next"] = np.log(df["p_exit"] / df["p_entry"])
print("session transitions", len(df), df.groupby('sess').size().to_dict())

results = []
def push(sub, rank, tag, nb=5, minxs=20, hypo=""):
    sp, n1 = xs_spread(sub, "B_end", rank, ["ret_next"], n_buckets=nb, min_xs=minxs)
    if len(sp) < 30: return None
    o = sp[["B_end", "ret_next_spread"]].rename(columns={"B_end": "ts", "ret_next_spread": "ret_bps"})
    r = run_gate(o, tag, hypo, n_ind_L1=n1, cost_legs=2, n_boot=1500)
    results.append(r); return r

# ---- B2: inter-session cross-sectional reversion, per transition (ARM-vs-ARM) -------
# spread = bottom-quintile-by-past-residual MINUS top-quintile  => reversion  <=> spread > 0
for s0 in ["ASIA", "EU", "US", "LATE"]:
    push(df[df["sess"] == s0], "resid", f"B2_{s0}_to_{NEXT[s0]}",
         hypo="reversion => spread(losers-winners) > 0")
push(df, "resid", "B2_ALL_transitions_pooled", hypo="reversion => spread > 0")
for nb in (10, 20):
    push(df, "resid", f"B2_ALL_pooled_q{nb}", nb=nb, hypo="concentration sweep")

# ---- B4: overnight(ASIA) -> US, skipping EU (classic equity analogue) ---------------
asia = sa[sa["sess"] == "ASIA"].copy()
asia["B_entry"] = asia["d"] + pd.Timedelta(hours=13)
asia["B_exit"] = asia["d"] + pd.Timedelta(hours=21)
b4 = asia.merge(bd[["symbol", "B", "p_entry"]].rename(columns={"B": "B_entry"}), on=["symbol", "B_entry"], how="inner")
b4 = b4.merge(bd[["symbol", "B", "p_at"]].rename(columns={"B": "B_exit", "p_at": "p_exit"}), on=["symbol", "B_exit"], how="inner")
b4 = b4[(b4["p_entry"] > 0) & (b4["p_exit"] > 0)]
b4["ret_next"] = np.log(b4["p_exit"] / b4["p_entry"])
b4["B_end"] = b4["B_entry"]
push(b4, "resid", "B4_ASIA_resid_to_US_session", hypo="reversion => spread > 0")

# ---- B1: hour-of-day MARKET FACTOR (1-leg, directional) ----------------------------
h["ret_h"] = np.log(h["close_at_hour_end"] / h.groupby("symbol")["close_at_hour_end"].shift(1))
mk = h.dropna(subset=["ret_h"]).groupby(["d", "hb"])["ret_h"].agg(["mean", "size"]).reset_index()
mk = mk[mk["size"] >= 20]
b1 = []
for hb in range(24):
    sub = mk[mk["hb"] == hb]
    o = pd.DataFrame({"ts": sub["d"] + pd.to_timedelta(hb, unit="h"), "ret_bps": sub["mean"] * 1e4})
    r = run_gate(o, f"B1_hour_{hb:02d}_market_factor", "H_B1: no reliable hour-of-day drift",
                 cost_legs=1, n_boot=1500)
    b1.append(r); results.append(r)

crit_b1 = family_maxt(b1, n_boot=1000)
core = [r for r in results if not r["mechanism"].startswith("B1_")]
crit = family_maxt(core, n_boot=1000)
print("B (sessions) max-|t| crit:", round(crit, 3), " | B1 hour-of-day max-|t| crit (24 buckets):", round(crit_b1, 3))
for r in results:
    cc = crit_b1 if r["mechanism"].startswith("B1_") else crit
    v, why = auto_verdict(r, family_maxt_crit=cc)
    r["verdict"], r["verdict_reason"], r["family_maxt_crit"] = v, why, round(cc, 3)
    r.pop("day_series", None)
with open(f"{OUT}/results_family_b.json", "w") as f:
    json.dump(results, f, indent=1, default=str)

cols = ["mechanism","n_raw","n_independent_L2","gross_bps","net_bps_2leg","net_bps",
        "t_stat_declustered","t_stat_naive_WRONG","clustering_inflation_factor","IR_day",
        "eta_forward_confirmation_years","verdict"]
pd.set_option("display.width", 240); pd.set_option("display.max_rows", 100)
print(pd.DataFrame([r for r in results if not r['mechanism'].startswith('B1_')])[cols].to_string(index=False))
print()
print(pd.DataFrame([r for r in results if r['mechanism'].startswith('B1_')])[
    ["mechanism","n_independent_L2","gross_bps","net_bps","t_stat_declustered","verdict"]].to_string(index=False))
