#!/usr/bin/env python3
"""
W3 — Axe C (maturation de la microstructure) + Axe D (fin de vie / radiation) + A3 (carry jeune).
C1 AGE_VOL_MATURATION | C2 AGE_FUNDING_EXTREMITY | C3 AGE_LIQUIDITY_MATURATION
D1 DELIST_PRE_DRIFT   | D2 DELIST_FUNDING_BASIS_DISLOCATION | A3 LIST_FUNDING_CARRY_YOUNG
"""
import os, sys, json, glob, warnings
import numpy as np, pandas as pd, duckdb
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import (_mt, block_bootstrap_ci, n_required, episode_rate_per_week, eta_days_years,
                  COST_LS, COST_LS_STRESS)

ROOT = "/home/qbee/futur"; OUT = os.environ["W3_SCRATCH"]; LB_END = "2026-07-31"
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")

df = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date, status,
                  (status IN ('SETTLING','DELISTED','DELISTED_NO_DATA')) AS is_dead FROM '{OUT}/life.parquet'),
     f AS (SELECT * FROM '{OUT}/funding_daily.parquet'),
     m AS (SELECT p.*, l.onboard_date, l.is_dead, f.funding_paid_d, f.n_settle_d, f.abs_funding_avg_d,
             median(p.quote_vol) OVER w AS qvol_med30_causal,
             avg(p.amihud)       OVER w AS amihud30_causal
           FROM p JOIN l USING (symbol) LEFT JOIN f USING (date, symbol)
           WINDOW w AS (PARTITION BY p.symbol ORDER BY p.date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING))
SELECT date, symbol, close, hi, lo, quote_vol, n_trades, ret_d, funding_d, funding_paid_d,
       n_settle_d, abs_funding_avg_d, oi_d, abs_basis_z7_d, resid_std30_d, amihud, is_dead,
       date_diff('day', onboard_date, date) AS age_days, qvol_med30_causal, amihud30_causal
FROM m WHERE age_days >= 1 ORDER BY symbol, date
""").df()
df["date"] = pd.to_datetime(df.date, utc=True)
df["elig"] = df.qvol_med30_causal >= 1e6
iso = df.date.dt.isocalendar(); df["_isoweek"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
df["_month"] = df.date.dt.strftime("%Y-%m")
BUCKETS = ["<30j", "30-90j", "90-180j", "180-365j", "1-2a", ">2a"]
df["bucket"] = pd.cut(df.age_days, bins=[1, 30, 90, 180, 365, 730, 10**9], labels=BUCKETS, right=False)
res = []

# ---------- C1 / C3 : tables descriptives de maturation (sur univers ELIGIBLE) ----------
e = df[df.elig].copy()
e["rvol_bps"] = e.ret_d.abs() * 1e4
e["hl_range_bps"] = (e.hi / e.lo - 1) * 1e4
tab = e.groupby("bucket", observed=True).agg(
    n_obs=("ret_d", "size"), n_symbols=("symbol", "nunique"),
    rvol_med_bps=("rvol_bps", "median"), hl_range_med_bps=("hl_range_bps", "median"),
    qvol_med_musd=("quote_vol", lambda s: s.median() / 1e6),
    n_trades_med=("n_trades", "median"),
    amihud_med=("amihud", "median"),
    abs_funding_med_bps=("abs_funding_avg_d", lambda s: s.median() * 1e4),
    funding_paid_med_bps=("funding_paid_d", lambda s: s.median() * 1e4),
    frac_4h_funding=("n_settle_d", lambda s: float((s >= 6).mean())),
    abs_basis_z7_med=("abs_basis_z7_d", "median")).round(3).reset_index()
tab["bucket"] = tab.bucket.astype(str)
print("\n[C1/C3] maturation de la microstructure (univers eligible >=1M$/j)")
print(tab.to_string(index=False))
res.append(dict(id="C1_AGE_VOL_MATURATION", family="MICROSTRUCTURE_MATURATION",
                hypothesis="la vol realisee decroit avec l'age de l'instrument",
                descriptive_only=True, table=tab.to_dict("records"),
                n_raw=int(len(e)), verdict="DESCRIPTIVE"))
res.append(dict(id="C3_AGE_LIQUIDITY_MATURATION", family="MICROSTRUCTURE_MATURATION",
                hypothesis="volume/nb de trades/Amihud maturent avec l'age (borne de capacite)",
                descriptive_only=True, table=tab.to_dict("records"),
                n_raw=int(len(e)), verdict="DESCRIPTIVE"))

# ---------- helper : comparaison de bras (appariee si possible, sinon Welch) ----------
def arm_cmp(a_df, b_df, col, l3col, label, family, hyp, cost=COST_LS, stress=COST_LS_STRESS,
            date_from_isoweek=True):
    a = a_df.groupby(l3col)[col].mean().rename("A"); b = b_df.groupby(l3col)[col].mean().rename("B")
    j = pd.concat([a, b], axis=1).dropna()
    paired = len(j) >= 3
    if paired:
        diff = (j.A - j.B).to_numpy(); st = _mt(diff); ci = block_bootstrap_ci(diff); nr = n_required(diff)
        keys = sorted(j.index); n_ep = len(j)
    else:
        sa, sb = _mt(a.to_numpy()), _mt(b.to_numpy())
        if sa["n"] < 3 or sb["n"] < 3:
            return dict(id=label, family=family, hypothesis=hyp, n_raw=int(len(a_df) + len(b_df)),
                        n_independent_L3=int(min(sa["n"], sb["n"])), verdict="DATA_LIMITED",
                        note="moins de 3 episodes L3 par bras")
        se = np.sqrt(sa["sd"]**2 / sa["n"] + sb["sd"]**2 / sb["n"])
        st = dict(n=sa["n"] + sb["n"], mean=sa["mean"] - sb["mean"], sd=se * np.sqrt(sa["n"] + sb["n"]),
                  t=(sa["mean"] - sb["mean"]) / se if se > 0 else np.nan)
        ci = [st["mean"] - 1.96 * se, st["mean"] + 1.96 * se]
        d = abs(st["mean"]) / (se * np.sqrt(min(sa["n"], sb["n"]))) if se > 0 else 0
        nr = 7.849 / (0.5 * d) ** 2 if d > 0 else np.nan
        keys = sorted(set(a.index) | set(b.index)); n_ep = min(sa["n"], sb["n"])
    ks = pd.Series([str(k) for k in keys])
    dts = (pd.to_datetime(ks + "-1", format="%G-W%V-%u", utc=True, errors="coerce") if date_from_isoweek
           else pd.to_datetime(ks + "-01", utc=True, errors="coerce"))
    rate = episode_rate_per_week(dts, lookback_end=LB_END); et = eta_days_years(nr, rate)
    return dict(id=label, family=family, hypothesis=hyp, paired_episodes=bool(paired),
                n_raw=int(len(a_df) + len(b_df)), n_arm_A=int(len(a_df)), n_arm_B=int(len(b_df)),
                n_independent_L3=int(n_ep),
                arm_A_bps=round(float(a.mean()), 1), arm_B_bps=round(float(b.mean()), 1),
                gross_bps=round(st["mean"], 1), net_bps=round(st["mean"] - cost, 1),
                net_bps_stress28=round(st["mean"] - stress, 1),
                t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
                n_required=round(nr, 1) if np.isfinite(nr) else None,
                event_rate_per_week_6m=round(rate, 3),
                eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                              years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))

# ---------- C2 : carry short-perp, jeunes vs matures (funding>0), horizon 7j ----------
e = e.sort_values(["symbol", "date"])
e["fwd7_bps"] = np.expm1(e.groupby("symbol")["close"].transform(lambda s: np.log(s.shift(-7)) - np.log(s))) * 1e4
e["fund_next7_bps"] = e.groupby("symbol")["funding_paid_d"].transform(
    lambda s: s.shift(-1).rolling(7, min_periods=5).sum().shift(-6)) * 1e4
e["short_carry_bps"] = e.fund_next7_bps - e.fwd7_bps           # short perp : encaisse funding, subit -prix
pos = e[(e.funding_paid_d > 0) & e.short_carry_bps.notna()].copy()
A = pos[pos.age_days < 90]; B = pos[pos.age_days >= 365]
res.append(arm_cmp(A, B, "short_carry_bps", "_isoweek", "C2_AGE_FUNDING_CARRY_YOUNG_VS_MATURE",
                   "MICROSTRUCTURE_MATURATION",
                   "short-perp sur jeunes (<90j, funding>0) - short-perp sur matures (>=1a, funding>0), 7j, funding inclus"))
# differentiel de FUNDING SEUL (flux de tresorerie, sans le prix)
res.append(arm_cmp(A, B, "fund_next7_bps", "_isoweek", "C2b_AGE_FUNDING_LEVEL_DIFFERENTIAL",
                   "MICROSTRUCTURE_MATURATION",
                   "funding encaisse sur 7j : jeunes (<90j) - matures (>=1a), flux seul sans le prix",
                   cost=0.0, stress=0.0))

# ---------- A3 : funding des 30 premiers jours (dataset listings) vs matures contemporains ----------
CALP = f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet"
cal = con.execute(f"select symbol,onboard_ts,status from '{CALP}'").df()
cal["onboard_ts"] = pd.to_datetime(cal.onboard_ts, utc=True)
rows = []
for p in sorted(glob.glob(f"{ROOT}/data/listings_backfill/binance/funding/*.parquet")):
    sym = os.path.basename(p)[:-8]
    m = cal[cal.symbol == sym]
    if m.empty: continue
    t0 = m.onboard_ts.iloc[0]
    try: f = pd.read_parquet(p)
    except Exception: continue
    f["timestamp"] = pd.to_datetime(f.timestamp, utc=True)
    f = f[(f.timestamp >= t0) & (f.timestamp <= t0 + pd.Timedelta(days=30))]
    if len(f) < 20: continue
    rows.append(dict(symbol=sym, _dt=t0, cum_funding_30d_bps=float(f.funding_rate.sum()) * 1e4,
                     abs_funding_med_bps=float(f.funding_rate.abs().median()) * 1e4,
                     n_settle=len(f), interval_h=round(30 * 24 / len(f), 1),
                     is_dead=bool(m.status.iloc[0] in ("SETTLING", "DELISTED", "DELISTED_NO_DATA"))))
lf = pd.DataFrame(rows)
i2 = lf._dt.dt.isocalendar(); lf["_isoweek"] = i2.year.astype(str) + "-W" + i2.week.astype(str).str.zfill(2)
# contrefactuel mature : meme semaine calendaire, noms >= 1 an, funding cumule 30j
mat = e[(e.age_days >= 365)].copy()
mat["cum_funding_30d_bps"] = mat.groupby("symbol")["funding_paid_d"].transform(
    lambda s: s.shift(-1).rolling(30, min_periods=20).sum().shift(-29)) * 1e4
mat = mat.dropna(subset=["cum_funding_30d_bps"])
res.append(arm_cmp(lf, mat, "cum_funding_30d_bps", "_isoweek", "A3_LIST_FUNDING_CARRY_YOUNG",
                   "LISTING_EVENT",
                   "funding cumule sur les 30 premiers jours d'un listing - funding cumule 30j des noms matures, meme semaine",
                   cost=0.0, stress=0.0))
print("\n[A3] funding 30j : listings n=%d med=%.0fbps mean=%.0fbps | matures med=%.0fbps mean=%.0fbps"
      % (len(lf), lf.cum_funding_30d_bps.median(), lf.cum_funding_30d_bps.mean(),
         mat.cum_funding_30d_bps.median(), mat.cum_funding_30d_bps.mean()))
print("[A3] intervalle de funding median des nouveaux listings : %.1f h ; %% a 4h : %.1f%%"
      % (lf.interval_h.median(), 100 * (lf.interval_h < 6).mean()))

# ---------- Axe D : radiations ----------
life = con.execute(f"select * from '{OUT}/life.parquet'").df()
panel_end = df.date.max()
life["last_date"] = pd.to_datetime(life.last_date, utc=True)
dead = life[life.status.isin(["SETTLING", "DELISTED", "DELISTED_NO_DATA"])].copy()
dead["delisted_in_window"] = dead.last_date < (panel_end - pd.Timedelta(days=5))
print(f"\n[D] noms morts dans le panel : {len(dead)} ; dont radies DANS la fenetre de donnees : "
      f"{int(dead.delisted_in_window.sum())}")
dl = dead[dead.delisted_in_window][["symbol", "last_date"]]

bench = con.execute(f"select ts_h, idx_logret from '{OUT}/bench_hourly.parquet' order by ts_h").df()
bench["ts_h"] = pd.to_datetime(bench.ts_h, utc=True)
bench = bench.dropna().set_index("ts_h"); bcum = bench.idx_logret.cumsum()
bench_d = bcum.resample("1D").last().ffill()

d_rows = []
for _, r in dl.iterrows():
    s = df[df.symbol == r.symbol].set_index("date").close.sort_index()
    if len(s) < 10: continue
    for N in (7, 30, 90):
        t_out = r.last_date; t_in = t_out - pd.Timedelta(days=N)
        p_in, p_out = s.asof(t_in), s.asof(t_out)
        if not (np.isfinite(p_in) and np.isfinite(p_out) and p_in > 0): continue
        a, b = bench_d.asof(t_in), bench_d.asof(t_out)
        if not (np.isfinite(a) and np.isfinite(b)): continue
        bm = np.expm1(b - a)
        d_rows.append(dict(symbol=r.symbol, _dt=t_out, N=N, rel_bps=((p_out / p_in - 1) - bm) * 1e4))
dd = pd.DataFrame(d_rows)
if len(dd):
    i3 = dd._dt.dt.isocalendar(); dd["_isoweek"] = i3.year.astype(str) + "-W" + i3.week.astype(str).str.zfill(2)
    for N in (7, 30, 90):
        s = dd[dd.N == N]
        if len(s) < 3: continue
        ep = s.groupby("_isoweek").rel_bps.mean()
        st = _mt(ep.to_numpy()); ci = block_bootstrap_ci(ep.to_numpy()); nr = n_required(ep.to_numpy())
        dts = pd.to_datetime(pd.Series(sorted(ep.index)) + "-1", format="%G-W%V-%u", utc=True, errors="coerce")
        rate = episode_rate_per_week(dts, lookback_end=LB_END); et = eta_days_years(nr, rate)
        res.append(dict(id=f"D1_DELIST_PRE_DRIFT_{N}d", family="DELISTING",
                        hypothesis=f"derive relative negative dans les {N} jours precedant la radiation",
                        n_raw=int(len(s)), n_symbols=int(s.symbol.nunique()),
                        n_independent_L1=int(s.groupby(["symbol"]).ngroups),
                        n_independent_L2=int(s._dt.dt.date.nunique()), n_independent_L3=int(st["n"]),
                        gross_bps=round(st["mean"], 1), net_bps=round(st["mean"] - COST_LS, 1),
                        net_bps_stress28=round(st["mean"] - COST_LS_STRESS, 1),
                        t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                        bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
                        n_required=round(nr, 1) if np.isfinite(nr) else None,
                        event_rate_per_week_6m=round(rate, 3),
                        eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                                      years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None)))

# ---------- D2 : dislocation funding/basis en fin de vie (comparaison INTRA-nom) ----------
rows = []
for _, r in dl.iterrows():
    s = df[df.symbol == r.symbol].copy()
    if len(s) < 120: continue
    tail = s[s.date > r.last_date - pd.Timedelta(days=30)]
    body = s[s.date <= r.last_date - pd.Timedelta(days=30)]
    if len(tail) < 10 or len(body) < 60: continue
    rows.append(dict(symbol=r.symbol, _dt=r.last_date,
                     ratio_abs_funding=float(tail.abs_funding_avg_d.median() / (body.abs_funding_avg_d.median() or np.nan)),
                     ratio_abs_basis_z=float(tail.abs_basis_z7_d.median() / (body.abs_basis_z7_d.median() or np.nan)),
                     ratio_qvol=float(tail.quote_vol.median() / (body.quote_vol.median() or np.nan)),
                     ratio_rvol=float(tail.ret_d.abs().median() / (body.ret_d.abs().median() or np.nan))))
d2 = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
if len(d2) >= 3:
    st = _mt(d2.ratio_abs_funding.dropna().to_numpy() - 1.0)
    res.append(dict(id="D2_DELIST_FUNDING_BASIS_DISLOCATION", family="DELISTING",
                    hypothesis="|funding| et |basis_z| deviennent extremes dans les 30 derniers jours (comparaison intra-nom)",
                    n_raw=int(len(d2)), n_symbols=int(d2.symbol.nunique()), n_independent_L3=int(len(d2)),
                    median_ratio_abs_funding=round(float(d2.ratio_abs_funding.median()), 3),
                    median_ratio_abs_basis_z=round(float(d2.ratio_abs_basis_z.median()), 3),
                    median_ratio_qvol=round(float(d2.ratio_qvol.median()), 3),
                    median_ratio_rvol=round(float(d2.ratio_rvol.median()), 3),
                    t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                    note="ratios fin-de-vie / reste-de-vie du MEME symbole (1.0 = pas de dislocation)"))
    print("\n[D2] ratios fin de vie (median) : |funding| x%.2f  |basis_z| x%.2f  qvol x%.2f  rvol x%.2f  (n=%d)"
          % (d2.ratio_abs_funding.median(), d2.ratio_abs_basis_z.median(),
             d2.ratio_qvol.median(), d2.ratio_rvol.median(), len(d2)))

json.dump(res, open(f"{OUT}/axisCD_results.json", "w"), indent=1, default=str)
print()
for g in res:
    if g.get("descriptive_only"): continue
    print(f"{g['id']:<44} n_raw={g.get('n_raw')!s:>6} L3={g.get('n_independent_L3')!s:>4} "
          f"gross={g.get('gross_bps')!s:>9} net={g.get('net_bps')!s:>9} t={g.get('t_stat_declustered')!s:>6} "
          f"ETAy={(g.get('eta_forward_confirmation') or {}).get('years')!s:>9}")
