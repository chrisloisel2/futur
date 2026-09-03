#!/usr/bin/env python3
"""
W3 — Axe B : l'age de l'instrument comme facteur transversal.
B1 XSEC_AGE_FACTOR_1D / _7D ; B2 XSEC_AGE_RESID_LIQ ; B3 AGE_BUCKET_MONOTONICITY
Panel PIT 312 symboles (53 morts), 2020-01 -> 2026-07.
"""
import os, sys, json, warnings
import numpy as np, pandas as pd, duckdb
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import (run_gate, _mt, block_bootstrap_ci, n_required, episode_rate_per_week,
                  eta_days_years, COST_LS, COST_LS_STRESS)

OUT = os.environ["W3_SCRATCH"]; LB_END = "2026-07-31"
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")

# ---------- panel + eligibilite PIT ----------
df = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date, status,
                  (status IN ('SETTLING','DELISTED','DELISTED_NO_DATA')) AS is_dead FROM '{OUT}/life.parquet'),
     m AS (SELECT p.*, l.onboard_date, l.is_dead,
             median(p.quote_vol) OVER w AS qvol_med30_causal,
             avg(p.amihud)       OVER w AS amihud30_causal
           FROM p JOIN l USING (symbol)
           WINDOW w AS (PARTITION BY p.symbol ORDER BY p.date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING))
SELECT date, symbol, close, quote_vol, ret_d, funding_d, oi_d, is_dead,
       date_diff('day', onboard_date, date) AS age_days,
       qvol_med30_causal, amihud30_causal
FROM m WHERE qvol_med30_causal >= 1e6 AND age_days >= 1
ORDER BY date, symbol
""").df()
df["date"] = pd.to_datetime(df.date, utc=True)
print(f"[B] panel eligible : {len(df)} lignes, {df.symbol.nunique()} symboles, "
      f"{df.date.min().date()} -> {df.date.max().date()}, morts={df[df.is_dead].symbol.nunique()}")

# forward returns (causal : ret_d de t+1..t+h) + winsorisation transversale 1/99
df = df.sort_values(["symbol", "date"])
lp = np.log(df.close)
for h in (1, 7):
    df[f"fwd{h}"] = np.expm1(df.groupby("symbol")["close"].transform(
        lambda s: np.log(s.shift(-h)) - np.log(s))) * 1e4
for h in (1, 7):
    c = f"fwd{h}"
    q = df.groupby("date")[c].transform(lambda s: s.clip(s.quantile(.01), s.quantile(.99)))
    df[c] = q
df["log_age"] = np.log(df.age_days.clip(lower=1))
df["log_qvol"] = np.log(df.qvol_med30_causal.clip(lower=1))
df["log_amihud"] = np.log(df.amihud30_causal.clip(lower=1e-12))

def iso_week(s): 
    i = s.dt.isocalendar(); return i.year.astype(str) + "-W" + i.week.astype(str).str.zfill(2)

res = []

# ---------- B1 : livre quintile vieux - quintile jeune ----------
def build_book(h, min_names=25, nq=5):
    recs = []
    for dt, g in df.groupby("date"):
        g = g.dropna(subset=[f"fwd{h}", "log_age"])
        if len(g) < min_names: continue
        r = g.log_age.rank(pct=True)
        old = g[r > 1 - 1/nq][f"fwd{h}"].mean()
        yng = g[r <= 1/nq][f"fwd{h}"].mean()
        recs.append(dict(_dt=dt, book_bps=old - yng, old_bps=old, young_bps=yng,
                         xs_mean_bps=g[f"fwd{h}"].mean(), n_names=len(g)))
    b = pd.DataFrame(recs).sort_values("_dt").reset_index(drop=True)
    b["_date"] = b._dt.dt.date
    b["_isoweek"] = iso_week(b._dt); b["_month"] = b._dt.dt.strftime("%Y-%m")
    b["_quarter"] = b._dt.dt.to_period("Q").astype(str)
    # L2 = periodes de detention NON CHEVAUCHANTES
    b["_nonoverlap"] = (np.arange(len(b)) // h)
    b["symbol"] = "BOOK"; b["_sym24"] = "BOOK|" + b._dt.astype(str)
    return b

for h, l3, l3alt in [(1, "_isoweek", "_month"), (7, "_month", "_quarter")]:
    b = build_book(h)
    b2 = b.iloc[::h].copy() if h > 1 else b.copy()      # serie non chevauchante pour L2/L3
    g = run_gate(b2, "book_bps", ["_date"], ["_nonoverlap"], [l3],
                 cost_rt=COST_LS, cost_stress=COST_LS_STRESS, l3_alt_keys=[l3alt],
                 label=f"B1_XSEC_AGE_FACTOR_{h}D", family="INSTRUMENT_AGE_FACTOR",
                 lookback_end=LB_END,
                 hypothesis="long quintile le plus vieux / short quintile le plus jeune, equal-weight")
    g["arm_old_bps"] = round(float(b2.old_bps.mean()), 1)
    g["arm_young_bps"] = round(float(b2.young_bps.mean()), 1)
    g["xs_universe_bps"] = round(float(b2.xs_mean_bps.mean()), 1)
    g["n_rebalance_dates_overlapping"] = int(len(b))
    g["mean_names_per_date"] = round(float(b2.n_names.mean()), 1)
    res.append(g)
    b.to_parquet(f"{OUT}/axisB_book_{h}d.parquet")

# ---------- B3 : monotonie par bucket d'age (rendement DEMEANE transversalement) ----------
BUCKETS = [(1, 30, "<30j"), (30, 90, "30-90j"), (90, 180, "90-180j"),
           (180, 365, "180-365j"), (365, 730, "1-2a"), (730, 10**9, ">2a")]
for h in (1, 7):
    d = df.dropna(subset=[f"fwd{h}"]).copy()
    d["xs_dm"] = d[f"fwd{h}"] - d.groupby("date")[f"fwd{h}"].transform("mean")
    d["bucket"] = pd.cut(d.age_days, bins=[b[0] for b in BUCKETS] + [10**9],
                         labels=[b[2] for b in BUCKETS], right=False)
    d["_isoweek"] = iso_week(d.date)
    tab = []
    for lab in [b[2] for b in BUCKETS]:
        s = d[d.bucket == lab]
        if len(s) < 50: continue
        ep = s.groupby("_isoweek").xs_dm.mean()
        st = _mt(ep)
        tab.append(dict(bucket=lab, n_obs=int(len(s)), n_ep_L3=int(st["n"]),
                        mean_dm_bps=round(st["mean"], 1),
                        t=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                        n_symbols=int(s.symbol.nunique()), frac_dead=round(float(s.is_dead.mean()), 3)))
    ranks = np.arange(len(tab)); vals = np.array([t["mean_dm_bps"] for t in tab])
    rho = float(pd.Series(ranks).corr(pd.Series(vals), method="spearman")) if len(tab) > 2 else np.nan
    # bras extremes compares entre eux, sur episodes L3 apparies
    a = d[d.bucket == tab[0]["bucket"]].groupby("_isoweek").xs_dm.mean()
    z = d[d.bucket == tab[-1]["bucket"]].groupby("_isoweek").xs_dm.mean()
    j = pd.concat([a.rename("young"), z.rename("old")], axis=1).dropna()
    diff = (j.old - j.young).to_numpy()
    st = _mt(diff); ci = block_bootstrap_ci(diff); nr = n_required(diff)
    dts = pd.to_datetime(pd.Series(sorted(j.index)) + "-1", format="%G-W%V-%u", utc=True, errors="coerce")
    rate = episode_rate_per_week(dts, lookback_end=LB_END); e = eta_days_years(nr, rate)
    res.append(dict(id=f"B3_AGE_BUCKET_MONOTONICITY_{h}D", family="INSTRUMENT_AGE_FACTOR",
                    hypothesis="rendement forward demeane monotone en age ; bras extremes compares entre eux",
                    n_raw=int(len(d)), n_independent_L3=int(len(j)),
                    spearman_rho_bucket_rank=round(rho, 3) if np.isfinite(rho) else None,
                    bucket_table=tab,
                    gross_bps=round(st["mean"], 1), net_bps=round(st["mean"] - COST_LS, 1),
                    net_bps_stress28=round(st["mean"] - COST_LS_STRESS, 1),
                    t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                    bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
                    n_required=round(nr, 1) if np.isfinite(nr) else None,
                    event_rate_per_week_6m=round(rate, 3),
                    eta_forward_confirmation=dict(days=round(e["eta_days"], 0) if np.isfinite(e["eta_days"]) else None,
                                                  years=round(e["eta_years"], 2) if np.isfinite(e["eta_years"]) else None)))

# ---------- B2 : coefficient d'age APRES controle liquidite/taille (Fama-MacBeth) ----------
for h in (1, 7):
    recs = []
    for dt, g in df.groupby("date"):
        g = g.dropna(subset=[f"fwd{h}", "log_age", "log_qvol", "log_amihud"])
        if len(g) < 30: continue
        X = np.column_stack([np.ones(len(g)),
                             (g.log_age - g.log_age.mean()) / (g.log_age.std() or 1),
                             (g.log_qvol - g.log_qvol.mean()) / (g.log_qvol.std() or 1),
                             (g.log_amihud - g.log_amihud.mean()) / (g.log_amihud.std() or 1)])
        y = g[f"fwd{h}"].to_numpy()
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
        except Exception:
            continue
        # controle : coefficient d'age SEUL (univarie), pour mesurer ce que les controles retirent
        Xu = np.column_stack([np.ones(len(g)), X[:, 1]])
        bu = np.linalg.lstsq(Xu, y, rcond=None)[0]
        recs.append(dict(_dt=dt, b_age=beta[1], b_qvol=beta[2], b_amihud=beta[3], b_age_uni=bu[1]))
    fm = pd.DataFrame(recs).sort_values("_dt")
    fm["_isoweek"] = iso_week(fm._dt); fm["_month"] = fm._dt.dt.strftime("%Y-%m")
    fm["_date"] = fm._dt.dt.date; fm["_nonoverlap"] = np.arange(len(fm)) // h
    fm["symbol"] = "FM"; fm["_sym24"] = "FM|" + fm._dt.astype(str)
    fm2 = fm.iloc[::h].copy() if h > 1 else fm.copy()
    l3 = "_isoweek" if h == 1 else "_month"
    g1 = run_gate(fm2, "b_age", ["_date"], ["_nonoverlap"], [l3], cost_rt=0.0, cost_stress=0.0,
                  l3_alt_keys=["_month" if h == 1 else "_month"],
                  label=f"B2_XSEC_AGE_RESID_LIQ_{h}D", family="INSTRUMENT_AGE_FACTOR",
                  lookback_end=LB_END,
                  hypothesis="coefficient Fama-MacBeth de log(age) apres controle log(qvol) et Amihud (bps par 1 sigma d'age)")
    epu = fm2.groupby(l3).b_age_uni.mean(); stu = _mt(epu)
    g1["univariate_age_coef_bps"] = round(stu["mean"], 1)
    g1["univariate_age_t"] = round(stu["t"], 2) if np.isfinite(stu["t"]) else None
    g1["note"] = "coefficient de regression, PAS un rendement de livre : cout non applicable (cost_rt=0)"
    res.append(g1)

json.dump(res, open(f"{OUT}/axisB_results.json", "w"), indent=1, default=str)
for g in res:
    print(f"{g['id']:<36} n_raw={g.get('n_raw'):>7} L3={g.get('n_independent_L3'):>4} "
          f"gross={g.get('gross_bps')!s:>8} net={g.get('net_bps')!s:>8} t={g.get('t_stat_declustered')!s:>6} "
          f"ETAy={(g.get('eta_forward_confirmation') or {}).get('years')!s:>7} yrs={g.get('years_same_sign')}")
