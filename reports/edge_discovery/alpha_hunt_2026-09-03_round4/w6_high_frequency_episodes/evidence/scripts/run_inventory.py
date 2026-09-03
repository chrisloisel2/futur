#!/usr/bin/env python3
"""W6 round-4 PHASE 1: the INDEPENDENT-EPISODE-RATE INVENTORY.

This is the primary deliverable and is computed BEFORE any edge is looked at.
For every event family available in the corpus it answers: how many genuinely
independent episodes per week does this family produce, and therefore what is
the minimum net edge that would make it confirmable inside one year?

No forward MEAN is computed here -- only dispersions (sd), rates and break-evens.
"""
import sys, os, json, glob
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_hf import (load_panel, decluster_L1, decluster_nonoverlap, K_POWER,
                    COST_BPS, RECENT_START)

HOURLY = os.environ.get("W6_HOURLY", "/tmp/w6/hourly/*.parquet")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
T_LIQ, T_ALL, T_DEEP = 2e8, 2e7, 2e9


def rates(ts, sym_code, hour_idx, label, extra=None):
    """3-level declustered counts + rates."""
    order = np.lexsort((hour_idx, sym_code))
    sc, hi, tsv = sym_code[order], hour_idx[order], np.asarray(ts)[order]
    m1 = decluster_L1(sc, hi, 24)
    day = tsv.astype("datetime64[D]")
    span_days = max((tsv.max() - tsv.min()).astype("timedelta64[D]").astype(int), 1)
    span_weeks = span_days / 7.0
    rec = tsv >= np.datetime64(RECENT_START.tz_localize(None))
    rec_days = max((tsv.max() - np.datetime64(RECENT_START.tz_localize(None))).astype("timedelta64[D]").astype(int), 1)
    rec_weeks = rec_days / 7.0
    d = {
        "family": label,
        "n_raw": int(len(hi)),
        "n_independent_L1_sym24h": int(m1.sum()),
        "n_independent_L2_days": int(len(np.unique(day))),
        "n_independent_L3_weeks": int(len(np.unique(pd.PeriodIndex(pd.to_datetime(day), freq="W")))),
        "span_days": int(span_days),
        "rate_raw_per_week": len(hi) / span_weeks,
        "rate_L1_per_week": int(m1.sum()) / span_weeks,
        "rate_L1_per_week_recent6m": int((m1 & rec).sum()) / rec_weeks,
        "rate_L1_per_day_recent6m": int((m1 & rec).sum()) / rec_days,
        "decluster_survival_L1": float(m1.sum()) / max(len(hi), 1),
        "day_coverage_recent6m": float(len(np.unique(day[rec]))) / rec_days if rec.sum() else 0.0,
    }
    if extra:
        d.update(extra)
    return d, order, m1


def breakeven(sd_daymean_bps):
    return {"net_bps_min_for_1y_confirm": 2 * sd_daymean_bps * np.sqrt(K_POWER / 365.0),
            "net_bps_min_for_2y_confirm": 2 * sd_daymean_bps * np.sqrt(K_POWER / 730.0)}


def triggers(df):
    z1, z4, fi, doi, vs, bz, fp = (df.z1.values, df.z4.values, df.fi_1h.values,
                                   df.doi_1h.values, df.vs.values, df.bz1.values, df.fpct.values)
    T = {}
    T["A_RESID_1H_z>=1.5"] = np.abs(z1) >= 1.5
    T["A_RESID_1H_z>=2.5"] = np.abs(z1) >= 2.5
    T["A_RESID_1H_z>=4.0"] = np.abs(z1) >= 4.0
    T["A_RESID_4H_z>=1.5"] = np.abs(z4) >= 1.5
    T["A_RESID_4H_z>=2.5"] = np.abs(z4) >= 2.5
    T["B_FLOWIMB_1H>=0.30"] = np.abs(fi) >= 0.30
    T["B_FLOWIMB_1H>=0.50"] = np.abs(fi) >= 0.50
    T["C_OI_BUILD>=1%"] = (doi >= 0.01) & (np.abs(z1) >= 1.0)
    T["C_OI_BUILD>=2%"] = (doi >= 0.02) & (np.abs(z1) >= 1.0)
    T["C_OI_FLUSH<=-1%"] = (doi <= -0.01) & (np.abs(z1) >= 1.0)
    T["C_OI_FLUSH<=-2%"] = (doi <= -0.02) & (np.abs(z1) >= 1.0)
    T["D_VOLSHOCK>=3x"] = (vs >= 3.0) & (np.abs(z1) >= 1.5)
    T["D_VOLSHOCK>=6x"] = (vs >= 6.0) & (np.abs(z1) >= 1.5)
    T["E_BASIS_Z>=2"] = np.abs(bz) >= 2.0
    T["E_BASIS_Z>=3"] = np.abs(bz) >= 3.0
    T["F_FLOW_PRICE_DIVERGENCE"] = (np.sign(fi) != np.sign(z1)) & (np.abs(fi) >= 0.30) & (np.abs(z1) >= 1.0)
    T["H_FUNDING_P90 (control)"] = fp >= 0.90
    return T


def main():
    os.makedirs(OUT, exist_ok=True)
    print("loading panel...", flush=True)
    df = load_panel(HOURLY, min_dv7d=T_ALL)
    print(f"panel rows={len(df):,} symbols={df.symbol.nunique()}", flush=True)

    liq = df[df.dv_7d >= T_LIQ].reset_index(drop=True)
    print(f"T_LIQ rows={len(liq):,} symbols={liq.symbol.nunique()}", flush=True)

    rows = []
    # ---- universe-scale reference rows -------------------------------------
    for name, sub in [("Z_UNIVERSE_ALL_SYMBOL_HOURS_T_LIQ", liq),
                      ("Z_UNIVERSE_ALL_SYMBOL_HOURS_T_ALL", df)]:
        d, order, m1 = rates(sub.ts.values, sub.sym_code.values, sub.hour_idx.values, name)
        for h, col in [(1, "fwd_1h"), (4, "fwd_4h"), (12, "fwd_12h")]:
            v = sub[col].values * 1e4
            ok = np.isfinite(v)
            dm = pd.Series(v[ok]).groupby(sub.ts.values.astype("datetime64[D]")[ok]).mean()
            d[f"sd_episode_bps_h{h}"] = float(np.nanstd(v[ok], ddof=1))
            d[f"sd_daymean_bps_h{h}"] = float(dm.std(ddof=1))
            be = breakeven(d[f"sd_daymean_bps_h{h}"]); d[f"net_bps_min_1y_h{h}"] = be["net_bps_min_for_1y_confirm"]
        d["n_symbols"] = int(sub.symbol.nunique())
        rows.append(d)

    # ---- preregistered high-frequency trigger families ---------------------
    T = triggers(liq)
    for name, mask in T.items():
        mask = np.asarray(mask) & np.isfinite(liq.fwd_1h.values)
        if mask.sum() < 50:
            rows.append({"family": name, "n_raw": int(mask.sum()), "note": "insufficient"}); continue
        sub = liq[mask]
        d, order, m1 = rates(sub.ts.values, sub.sym_code.values, sub.hour_idx.values, name)
        d["n_symbols"] = int(sub.symbol.nunique())
        d["pct_of_symbol_hours"] = float(mask.sum()) / len(liq)
        for h, col in [(1, "fwd_1h"), (4, "fwd_4h"), (12, "fwd_12h")]:
            v = sub[col].values * 1e4
            ok = np.isfinite(v)
            if ok.sum() < 30:
                continue
            dm = pd.Series(v[ok]).groupby(sub.ts.values.astype("datetime64[D]")[ok]).mean()
            d[f"sd_episode_bps_h{h}"] = float(np.nanstd(v[ok], ddof=1))
            d[f"sd_daymean_bps_h{h}"] = float(dm.std(ddof=1))
            d[f"net_bps_min_1y_h{h}"] = breakeven(d[f"sd_daymean_bps_h{h}"])["net_bps_min_for_1y_confirm"]
            d[f"net_bps_min_2y_h{h}"] = breakeven(d[f"sd_daymean_bps_h{h}"])["net_bps_min_for_2y_confirm"]
        d["capacity_usd_estimate_h4"] = float(np.nanmedian(sub.dv_1h.values) * 0.10 * 4)
        rows.append(d)

    # ---- cross-sectional portfolio families (episode = one hourly rebalance)
    for name, feat in [("G_XS_HOURLY_PORTFOLIO_z1", "z1"), ("G_XS_HOURLY_PORTFOLIO_fi_1h", "fi_1h"),
                       ("G_XS_HOURLY_PORTFOLIO_doi_1h", "doi_1h"), ("G_XS_HOURLY_PORTFOLIO_vs", "vs"),
                       ("G_XS_HOURLY_PORTFOLIO_bz1", "bz1")]:
        sub = liq[np.isfinite(liq[feat].values) & np.isfinite(liq.fwd_1h.values)]
        cnt = sub.groupby("hour_idx")[feat].transform("size")
        sub = sub[cnt >= 30]
        hrs = np.sort(sub.hour_idx.unique())
        span_days = max((hrs.max() - hrs.min()) / 24.0, 1)
        rec_hours = hrs[hrs >= int(pd.Timestamp(RECENT_START).value // 10**9 // 3600)]
        d = {"family": name, "n_raw": int(len(hrs)),
             "n_independent_L1_sym24h": int(decluster_nonoverlap(hrs, 1).sum()),
             "n_independent_L1_h4": int(decluster_nonoverlap(hrs, 4).sum()),
             "n_independent_L1_h12": int(decluster_nonoverlap(hrs, 12).sum()),
             "n_independent_L2_days": int(len(np.unique(hrs // 24))),
             "n_independent_L3_weeks": int(len(np.unique(hrs // 168))),
             "span_days": int(span_days),
             "rate_raw_per_week": len(hrs) / (span_days / 7.0),
             "rate_L1_per_week": len(hrs) / (span_days / 7.0),
             "rate_L1_per_week_recent6m": len(rec_hours) / max((hrs.max() - rec_hours.min()) / 24.0 / 7.0, 1e-9) if len(rec_hours) else 0.0,
             "decluster_survival_L1": 1.0,
             "n_symbols": int(sub.symbol.nunique())}
        d["rate_L1_per_day_recent6m"] = d["rate_L1_per_week_recent6m"] / 7.0
        d["day_coverage_recent6m"] = 1.0
        rows.append(d)

    # ---- benchmark: the project's EXISTING event families ------------------
    con = duckdb.connect(); con.execute("SET TimeZone='UTC'")
    for p in sorted(glob.glob("/home/qbee/futur/data/events/*.parquet")):
        nm = os.path.basename(p).replace("_dataset.parquet", "")
        try:
            e = con.execute(f"SELECT event_time, symbol FROM read_parquet('{p}') WHERE event_time IS NOT NULL").df()
        except Exception as ex:
            print("skip", p, ex); continue
        e["event_time"] = pd.to_datetime(e.event_time, utc=True)
        e = e.sort_values(["symbol", "event_time"])
        sc = pd.factorize(e.symbol)[0].astype(np.int32)
        hi = (e.event_time.values.astype("datetime64[h]").astype(np.int64))
        d, _, _ = rates(e.event_time.values, sc, hi, f"Y_EXISTING_EVENT_{nm}")
        d["n_symbols"] = int(e.symbol.nunique())
        rows.append(d)

    with open(os.path.join(OUT, "INVENTORY.json"), "w") as f:
        json.dump(rows, f, indent=1, default=float)
    t = pd.DataFrame(rows)
    cols = ["family", "n_raw", "n_independent_L1_sym24h", "n_independent_L2_days",
            "n_independent_L3_weeks", "rate_L1_per_week", "rate_L1_per_week_recent6m",
            "decluster_survival_L1", "sd_daymean_bps_h4", "net_bps_min_1y_h4", "n_symbols"]
    cols = [c for c in cols if c in t.columns]
    t[cols].to_csv(os.path.join(OUT, "INVENTORY.csv"), index=False)
    pd.set_option("display.width", 250)
    print(t[cols].to_string())


if __name__ == "__main__":
    main()
