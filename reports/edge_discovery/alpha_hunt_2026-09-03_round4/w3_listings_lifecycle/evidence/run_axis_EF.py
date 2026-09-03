#!/usr/bin/env python3
"""
W3 — Axe E (vagues de cotation = marqueur de regime) + Axe F (age x alphas existants).
E1 LISTING_WAVE_RISK_REGIME | E2 WAVE_COND_XSEC_MOM | F1 AGE_X_XSEC_MOM_7D | F2 AGE_X_LIQ_CASCADE_REPEAT
"""
import os, sys, json, warnings
import numpy as np, pandas as pd, duckdb
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import (_mt, block_bootstrap_ci, n_required, episode_rate_per_week, eta_days_years,
                  COST_RT, COST_STRESS, COST_LS, COST_LS_STRESS)

ROOT = "/home/qbee/futur"; OUT = os.environ["W3_SCRATCH"]; LB_END = "2026-07-31"
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")
res = []

def ep_gate(vals, keys_dates, label, family, hyp, cost, stress, extra=None, month_keys=False):
    v = np.asarray(vals, float); st = _mt(v); ci = block_bootstrap_ci(v); nr = n_required(v)
    ks = pd.Series([str(k) for k in keys_dates])
    dts = (pd.to_datetime(ks + "-01", utc=True, errors="coerce") if month_keys
           else pd.to_datetime(ks + "-1", format="%G-W%V-%u", utc=True, errors="coerce"))
    rate = episode_rate_per_week(dts, lookback_end=LB_END); et = eta_days_years(nr, rate)
    d = dict(id=label, family=family, hypothesis=hyp, n_independent_L3=int(st["n"]),
             gross_bps=round(st["mean"], 1), net_bps=round(st["mean"] - cost, 1),
             net_bps_stress28=round(st["mean"] - stress, 1),
             t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
             bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
             n_required=round(nr, 1) if np.isfinite(nr) else None,
             event_rate_per_week_6m=round(rate, 3),
             eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                           years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))
    if extra: d.update(extra)
    return d

# ================= panel + signal de vague (PIT) =================
df = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     m AS (SELECT p.*, l.onboard_date,
             median(p.quote_vol) OVER w AS qvol_med30_causal
           FROM p JOIN l USING (symbol)
           WINDOW w AS (PARTITION BY p.symbol ORDER BY p.date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING))
SELECT date, symbol, close, quote_vol, date_diff('day', onboard_date, date) AS age_days, qvol_med30_causal
FROM m WHERE qvol_med30_causal >= 1e6 AND age_days >= 1 ORDER BY date, symbol
""").df()
df["date"] = pd.to_datetime(df.date, utc=True)
df = df.sort_values(["symbol", "date"])
for h in (7, 30):
    df[f"fwd{h}"] = np.expm1(df.groupby("symbol")["close"].transform(
        lambda s: np.log(s.shift(-h)) - np.log(s))) * 1e4
df["mom7"] = np.expm1(df.groupby("symbol")["close"].transform(lambda s: np.log(s) - np.log(s.shift(7)))) * 1e4
for h in (7, 30):
    c = f"fwd{h}"
    df[c] = df.groupby("date")[c].transform(lambda s: s.clip(s.quantile(.01), s.quantile(.99)))

CALP = f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet"
cal = con.execute(f"select symbol,onboard_ts from '{CALP}' where onboard_ts is not null").df()
cal["onboard_ts"] = pd.to_datetime(cal.onboard_ts, utc=True)
onb = cal.onboard_ts.dt.floor("D").value_counts().sort_index()
days = pd.date_range(df.date.min(), df.date.max(), freq="D", tz="UTC")
lc = onb.reindex(days, fill_value=0)
# fenetre FERMEE a gauche : listings des 30 jours PRECEDENTS, connus a t
wave30 = lc.shift(1).rolling(30, min_periods=30).sum()
# percentile EXPANDING (aucun lookahead) : rang parmi l'historique disponible a t
wave_pct = wave30.expanding(min_periods=250).apply(lambda s: (s[:-1] < s[-1]).mean() if len(s) > 1 else np.nan, raw=True)
sig = pd.DataFrame(dict(date=days, wave30=wave30.values, wave_pct=wave_pct.values)).dropna()
sig["_month"] = sig.date.dt.strftime("%Y-%m"); sig["_quarter"] = sig.date.dt.to_period("Q").astype(str)
print(f"[E] signal de vague : {len(sig)} jours, {sig.date.min().date()} -> {sig.date.max().date()} ; "
      f"wave30 median={sig.wave30.median():.0f} p10={sig.wave30.quantile(.1):.0f} p90={sig.wave30.quantile(.9):.0f}")

# marche = panier equal-weight eligible + BTC
mkt = df.groupby("date").agg(bask7=("fwd7", "mean"), bask30=("fwd30", "mean"), n=("symbol", "size")).reset_index()
btc = df[df.symbol == "BTCUSDT"][["date", "fwd7", "fwd30"]].rename(columns={"fwd7": "btc7", "fwd30": "btc30"})
mkt = mkt.merge(btc, on="date", how="left").merge(sig, on="date", how="inner")

# ---------- E1 ----------
for tgt, hz, l3 in [("bask7", 7, "_month"), ("bask30", 30, "_quarter"),
                    ("btc7", 7, "_month"), ("btc30", 30, "_quarter")]:
    s = mkt.dropna(subset=[tgt]).copy()
    hi = s[s.wave_pct > 2/3]; lo = s[s.wave_pct < 1/3]
    a = hi.groupby(l3)[tgt].mean(); b = lo.groupby(l3)[tgt].mean()
    j = pd.concat([a.rename("A"), b.rename("B")], axis=1).dropna()
    if len(j) >= 3:
        diff = (j.A - j.B).to_numpy()
        g = ep_gate(diff, list(j.index), f"E1_LISTING_WAVE_RISK_REGIME_{tgt}", "LISTING_WAVE_REGIME",
                    f"regime haute intensite de cotation - basse intensite, cible {tgt} ({hz}j)",
                    COST_RT, COST_STRESS, month_keys=True)
        g["paired_episodes"] = True
    else:                                    # bras disjoints -> Welch
        sa, sb = _mt(a.to_numpy()), _mt(b.to_numpy())
        se = np.sqrt(sa["sd"]**2/sa["n"] + sb["sd"]**2/sb["n"])
        dd = abs(sa["mean"]-sb["mean"])/(se*np.sqrt(min(sa["n"], sb["n"]))) if se > 0 else 0
        nr = 7.849/(0.5*dd)**2 if dd > 0 else np.nan
        keys = sorted(set(a.index) | set(b.index))
        rate = episode_rate_per_week(pd.to_datetime(pd.Series(keys)+"-01", utc=True, errors="coerce"), lookback_end=LB_END)
        et = eta_days_years(nr, rate)
        g = dict(id=f"E1_LISTING_WAVE_RISK_REGIME_{tgt}", family="LISTING_WAVE_REGIME", paired_episodes=False,
                 hypothesis=f"regime haute intensite de cotation - basse intensite, cible {tgt} ({hz}j)",
                 n_independent_L3=int(min(sa["n"], sb["n"])),
                 gross_bps=round(sa["mean"]-sb["mean"], 1), net_bps=round(sa["mean"]-sb["mean"]-COST_RT, 1),
                 net_bps_stress28=round(sa["mean"]-sb["mean"]-COST_STRESS, 1),
                 t_stat_declustered=round((sa["mean"]-sb["mean"])/se, 2) if se > 0 else None,
                 n_required=round(nr, 1) if np.isfinite(nr) else None,
                 event_rate_per_week_6m=round(rate, 3),
                 eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                               years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))
    g.update(n_raw=int(len(s)), n_days_high=int(len(hi)), n_days_low=int(len(lo)),
             arm_high_bps=round(float(hi[tgt].mean()), 1), arm_low_bps=round(float(lo[tgt].mean()), 1))
    yr = []
    for y, sub in s.groupby(s.date.dt.year):
        h2, l2 = sub[sub.wave_pct > 2/3][tgt], sub[sub.wave_pct < 1/3][tgt]
        if len(h2) > 5 and len(l2) > 5:
            yr.append(dict(year=int(y), n_hi=len(h2), n_lo=len(l2), diff_bps=round(float(h2.mean()-l2.mean()), 1)))
    g["year_by_year"] = yr
    if yr:
        sgn = np.sign(g["gross_bps"])
        g["years_same_sign"] = f"{sum(1 for r in yr if np.sign(r['diff_bps'])==sgn)}/{len(yr)}"
    res.append(g)

# ---------- livre momentum 7d (reference commune a E2 et F1) ----------
def mom_book(mask=None, nq=5, min_names=25):
    recs = []
    d = df if mask is None else df[mask]
    for dt, g in d.groupby("date"):
        g = g.dropna(subset=["fwd7", "mom7"])
        if len(g) < min_names: continue
        r = g.mom7.rank(pct=True)
        recs.append(dict(date=dt, book=g[r > 1-1/nq].fwd7.mean() - g[r <= 1/nq].fwd7.mean(), n=len(g)))
    b = pd.DataFrame(recs)
    b["_month"] = b.date.dt.strftime("%Y-%m")
    return b

bk = mom_book()
bk7 = bk.iloc[::7].copy()                     # non chevauchant
bk7 = bk7.merge(sig[["date", "wave_pct"]], on="date", how="inner")
hi = bk7[bk7.wave_pct > 2/3]; lo = bk7[bk7.wave_pct < 1/3]
a = hi.groupby("_month").book.mean(); b = lo.groupby("_month").book.mean()
j = pd.concat([a.rename("A"), b.rename("B")], axis=1).dropna()
if len(j) >= 3:
    g = ep_gate((j.A-j.B).to_numpy(), list(j.index), "E2_WAVE_COND_XSEC_MOM_7D", "LISTING_WAVE_REGIME",
                "momentum transversal 7d en regime haute-vague - en regime basse-vague",
                COST_LS, COST_LS_STRESS, month_keys=True)
    g.update(n_raw=int(len(bk7)), arm_high_bps=round(float(hi.book.mean()), 1),
             arm_low_bps=round(float(lo.book.mean()), 1), n_periods_high=len(hi), n_periods_low=len(lo))
    res.append(g)

# ---------- F1 : momentum 7d restreint aux jeunes vs aux vieux ----------
med_age = df.groupby("date").age_days.transform("median")
by = mom_book(mask=(df.age_days <= med_age), min_names=15)
bo = mom_book(mask=(df.age_days > med_age), min_names=15)
m = by.merge(bo, on="date", suffixes=("_y", "_o"))
m7 = m.iloc[::7].copy()
a = m7.groupby("_month_y").book_y.mean(); b = m7.groupby("_month_y").book_o.mean()
j = pd.concat([a.rename("A"), b.rename("B")], axis=1).dropna()
g = ep_gate((j.A-j.B).to_numpy(), list(j.index), "F1_AGE_X_XSEC_MOM_7D", "AGE_INTERACTION",
            "momentum transversal 7d sur la moitie JEUNE - sur la moitie VIEILLE (age median PIT)",
            COST_LS, COST_LS_STRESS, month_keys=True)
g.update(n_raw=int(len(m7)), arm_young_bps=round(float(m7.book_y.mean()), 1),
         arm_old_bps=round(float(m7.book_o.mean()), 1))
res.append(g)

# ---------- F2 : cascade repetee, jeunes vs vieux ----------
casc = con.execute(f"""select event_time, symbol, kind, n_events_sym_24h, fwd_4h, fwd_8h
                       from '{ROOT}/data/events/liq_cascade_dataset.parquet'""").df()
casc["event_time"] = pd.to_datetime(casc.event_time, utc=True)
casc = casc.merge(cal.rename(columns={"onboard_ts": "onb"}), on="symbol", how="left")
casc["age_days"] = (casc.event_time - casc.onb).dt.total_seconds()/86400
i = casc.event_time.dt.isocalendar(); casc["_isoweek"] = i.year.astype(str)+"-W"+i.week.astype(str).str.zfill(2)
rep = casc[(casc.kind == "LONG_CASCADE") & (casc.n_events_sym_24h >= 3) & casc.fwd_4h.notna() & casc.age_days.notna()].copy()
rep["fwd_4h_bps"] = rep.fwd_4h*1e4
print(f"[F2] cascades repetees (3e+) : n={len(rep)} ; age median={rep.age_days.median():.0f}j ; "
      f"p10={rep.age_days.quantile(.1):.0f}j p90={rep.age_days.quantile(.9):.0f}j ; symboles={rep.symbol.nunique()}")
med_a = rep.age_days.median()
A = rep[rep.age_days <= med_a]; B = rep[rep.age_days > med_a]
a = A.groupby("_isoweek").fwd_4h_bps.mean(); b = B.groupby("_isoweek").fwd_4h_bps.mean()
j = pd.concat([a.rename("A"), b.rename("B")], axis=1).dropna()
g = ep_gate((j.A-j.B).to_numpy(), list(j.index), "F2_AGE_X_LIQ_CASCADE_REPEAT", "AGE_INTERACTION",
            "effet cascade-repetee (3e+, fwd 4h) sur contrats JEUNES - sur contrats VIEUX (age median des evenements)",
            COST_RT, COST_STRESS)
g.update(n_raw=int(len(rep)), n_arm_A=int(len(A)), n_arm_B=int(len(B)),
         arm_young_bps=round(float(A.fwd_4h_bps.mean()), 1), arm_old_bps=round(float(B.fwd_4h_bps.mean()), 1),
         median_age_split_days=round(float(med_a), 0), n_symbols=int(rep.symbol.nunique()),
         note="univers cascade = 49 symboles seulement (dataset enrichi), majoritairement matures")
res.append(g)

json.dump(res, open(f"{OUT}/axisEF_results.json", "w"), indent=1, default=str)
print()
for g in res:
    print(f"{g['id']:<40} L3={g.get('n_independent_L3')!s:>4} gross={g.get('gross_bps')!s:>9} "
          f"net={g.get('net_bps')!s:>9} t={g.get('t_stat_declustered')!s:>6} "
          f"ETAy={(g.get('eta_forward_confirmation') or {}).get('years')!s:>8} yrs={g.get('years_same_sign')}")
