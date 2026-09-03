"""Step 1: eligible populations + PIT recompute of key features. NO forward-return statistic is printed."""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0, __file__.rsplit("/",1)[0])
from v2_common import *

ev = load_events(f"{ROOT}/data/events/cascade_dataset.parquet")
onb = load_onboard()
syms = sorted(ev["symbol"].unique())
fb = {s: first_bar(s) for s in syms}
ev["onboard_ts"] = ev["symbol"].map(onb)
ev["first_bar"] = ev["symbol"].map(fb)
missing_cal = [s for s in syms if s not in onb]
ev["onboard_eff"] = ev["onboard_ts"].fillna(ev["first_bar"])
ev["age_days"] = (ev["event_time"] - ev["onboard_eff"]).dt.total_seconds() / 86400
lc = ev[(ev["kind"] == "LONG_CASCADE") & (ev["label_full"]) & ev["fwd_4h"].notna()].copy()
lc["post2022"] = lc["event_time"] >= pd.Timestamp("2022-01-01", tz="UTC")
lc["age_ok"] = lc["age_days"] >= 30
E = lc[lc["post2022"] & lc["age_ok"] & lc["dist_low_24h"].notna()].copy()
A = E[(E["symbol"] != "BTCUSDT") & E["btc_ret_30m"].notna()].copy()
chk = {"n_all_rows": int(len(ev)), "n_long_cascade_labelfull_fwdok": int(len(lc)),
       "n_post2022": int(lc["post2022"].sum()), "n_dropped_listing_age_post2022": int((lc["post2022"] & ~lc["age_ok"]).sum()),
       "symbols_missing_in_calendar": missing_cal, "n_E": int(len(E)), "n_A_alts": int(len(A)),
       "E_symbols": int(E["symbol"].nunique()), "E_range": [str(E["event_time"].min()), str(E["event_time"].max())]}
young = lc[lc["post2022"] & ~lc["age_ok"]].groupby("symbol").size().to_dict()
chk["dropped_by_symbol"] = {k: int(v) for k, v in young.items()}
# ---- PIT recompute dist_low_24h / dist_low_7d / fwd_4h for 3 symbols ----
pit = {}
for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
    d = load_px(s); px = d["px"].astype(float)
    low24 = px.rolling(288, min_periods=144).min(); low7 = px.rolling(2016, min_periods=864).min()
    d["dist24"] = px / low24 - 1; d["dist7"] = px / low7 - 1
    pxv = px.values; n = len(pxv)
    idx = np.arange(n); e = np.minimum(idx + 1, n - 1); x = np.minimum(e + 48, n - 1)
    d["fwd4"] = np.log(pxv[x] / pxv[e])
    sub = ev[ev["symbol"] == s][["event_time","dist_low_24h","dist_low_7d","fwd_4h","px"]]
    m = sub.merge(d, left_on="event_time", right_on="create_time", how="left")
    found = m["create_time"].notna()
    def mm(a, b, tol=1e-9):
        x = m.loc[found, a].values; y = m.loc[found, b].values
        ok = np.isfinite(x) & np.isfinite(y)
        return int((np.abs(x[ok] - y[ok]) > tol).sum()), int(ok.sum()), float(np.nanmax(np.abs(x[ok]-y[ok]))) if ok.any() else None
    pit[s] = {"n_events": int(len(sub)), "n_bar_found": int(found.sum()),
              "dist_low_24h_mismatch": mm("dist_low_24h", "dist24"), "dist_low_7d_mismatch": mm("dist_low_7d", "dist7"),
              "fwd_4h_mismatch_tol1e-6": mm("fwd_4h", "fwd4", 1e-6), "px_mismatch": mm("px", "px_y", 1e-6)}
    del d
# ---- PIT recompute btc_ret_30m for ALL events (as-of backward join on BTC bars) ----
b = load_px("BTCUSDT"); b["btc30"] = b["px"].pct_change(6)
ctx = b[["create_time","btc30"]].rename(columns={"create_time":"t"}).sort_values("t")
j = pd.merge_asof(ev[["event_time","btc_ret_30m"]].sort_values("event_time"), ctx, left_on="event_time", right_on="t", direction="backward")
ok = j["btc_ret_30m"].notna() & j["btc30"].notna()
diff = (j.loc[ok, "btc_ret_30m"] - j.loc[ok, "btc30"]).abs()
lag = (j.loc[ok, "event_time"] - j.loc[ok, "t"]).dt.total_seconds() / 60
pit["btc_ret_30m_all_events"] = {"n_compared": int(ok.sum()), "n_mismatch_1e-9": int((diff > 1e-9).sum()),
                                 "max_abs_diff": float(diff.max()), "asof_lag_minutes_max": float(lag.max()),
                                 "asof_lag_gt0_count": int((lag > 0).sum())}
chk["pit"] = pit
E.to_parquet(f"{SCRATCH}/popE.parquet", index=False)
dump(chk, f"{ROOT}/reports/edge_discovery/validation_2026-09/LIQ_CASCADE_FAR_FROM_LOW/evidence/step1_population_pit.json")
print(json.dumps(chk, indent=1, default=str))
