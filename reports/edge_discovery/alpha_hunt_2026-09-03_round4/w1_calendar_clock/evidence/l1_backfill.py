"""Backfill n_independent_L1 (distinct (symbol, UTC day) slots) where the family script did
not carry it, so every row of RESULTS.json has all three decluster levels.

* Cascade mechanisms (E1_*, E2_*): recomputed directly from liq_cascade_dataset.
* B1_hour_* market-factor arms: the observation IS the whole cross-section for that day and
  hour, so there is no per-symbol dimension. L1 is set equal to L2 and flagged, rather than
  left null or invented.
* DF_daily_xs_* arm-only rows: the L1 of the parent DF_daily_xs_reversal_BASELINE applies.
"""
import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
CASCADE = "/home/qbee/futur/data/events/liq_cascade_dataset.parquet"
import duckdb
c = duckdb.connect(); c.execute("SET TimeZone='UTC'")
cas = c.execute(f"""SELECT event_time, symbol, hour_utc, fwd_4h, fwd_8h, n_events_sym_24h
                    FROM read_parquet('{CASCADE}')""").df()
cas["event_time"] = pd.to_datetime(cas["event_time"], utc=True)
cas["day"] = cas["event_time"].dt.floor("D")
cas["sess"] = pd.cut(cas["hour_utc"], [-1, 6, 12, 20, 23], labels=["ASIA", "EU", "US", "LATE"])
cas["is_weekend"] = cas["event_time"].dt.dayofweek.isin([5, 6])
cas["repeat3"] = cas["n_events_sym_24h"] >= 3


def l1(mask):
    return int(cas[mask].drop_duplicates(["symbol", "day"]).shape[0])


L1 = {}
for hz in ("fwd_4h", "fwd_8h"):
    for s in ("ASIA", "EU", "US", "LATE"):
        L1[f"E1_cascade_{hz}_{s}"] = l1(cas["sess"] == s)
    for wk, lab in ((False, "weekday"), (True, "weekend")):
        L1[f"E2_cascade_{hz}_repeat3plus_{lab}"] = l1((cas["is_weekend"] == wk) & cas["repeat3"])

p = os.path.join(HERE, "results_family_cdef.json")
d = json.load(open(p))
base_l1 = None
for r in d["mechanisms"]:
    if r["mechanism"] == "DF_daily_xs_reversal_BASELINE":
        base_l1 = r.get("n_independent_L1")
n = 0
for r in d["mechanisms"]:
    if r.get("n_independent_L1") is None:
        if r["mechanism"] in L1:
            r["n_independent_L1"] = L1[r["mechanism"]]
            r["n_independent_L1_note"] = "distinct (symbol, UTC day) cascade slots"
            n += 1
        elif r["mechanism"].startswith("DF_daily_xs_") and base_l1:
            r["n_independent_L1"] = base_l1
            r["n_independent_L1_note"] = "inherited from the parent daily cross-section"
            n += 1
json.dump(d, open(p, "w"), indent=1, default=str)
print(f"cdef: backfilled {n} rows")

p = os.path.join(HERE, "results_family_b.json")
d = json.load(open(p))
n = 0
for r in d:
    if r.get("n_independent_L1") is None and r["mechanism"].startswith("B1_"):
        r["n_independent_L1"] = r["n_independent_L2"]
        r["n_independent_L1_note"] = ("one-leg market factor: the observation is the whole "
                                      "cross-section for that day+hour, so L1 has no separate "
                                      "per-symbol meaning and equals L2")
        n += 1
json.dump(d, open(p, "w"), indent=1, default=str)
print(f"family_b: flagged {n} B1 rows")
