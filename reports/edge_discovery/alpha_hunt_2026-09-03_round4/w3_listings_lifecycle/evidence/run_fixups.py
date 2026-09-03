#!/usr/bin/env python3
"""
W3 — corrections et compléments :
 E1b : regime de vague declusterise par SPELL (runs contigus) — le bon niveau L3 pour un signal persistant
 A1b : test de signe declusterise du fade de listing (la statistique reellement puissante)
 A4b : effet taille de vague, bras DISJOINTS (Welch) — le test apparie precedent renvoyait L3=0
 C2c : carry jeune vs mature AVEC winsorisation transversale (le signe depend-il des queues ?)
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

def welch(a, b, dates_a, dates_b, label, family, hyp, cost, stress, extra=None):
    a = np.asarray(a, float); b = np.asarray(b, float)
    sa, sb = _mt(a), _mt(b)
    if sa["n"] < 3 or sb["n"] < 3:
        return dict(id=label, family=family, hypothesis=hyp, n_independent_L3=int(min(sa["n"], sb["n"])),
                    verdict="DATA_LIMITED", note="< 3 episodes L3 dans un bras")
    se = np.sqrt(sa["sd"]**2/sa["n"] + sb["sd"]**2/sb["n"])
    diff = sa["mean"] - sb["mean"]; t = diff/se if se > 0 else np.nan
    n_eff = min(sa["n"], sb["n"])
    d = abs(diff)/(se*np.sqrt(n_eff)) if se > 0 else 0
    nr = 7.849/(0.5*d)**2 if d > 0 else np.nan
    alld = pd.to_datetime(pd.Series(list(dates_a)+list(dates_b)), utc=True)
    rate = episode_rate_per_week(alld, lookback_end=LB_END); et = eta_days_years(nr, rate)
    rng = np.random.default_rng(20260903)
    bs = np.array([rng.choice(a, sa["n"], True).mean() - rng.choice(b, sb["n"], True).mean() for _ in range(5000)])
    o = dict(id=label, family=family, hypothesis=hyp, paired_episodes=False,
             n_independent_L3=int(n_eff), n_episodes_A=int(sa["n"]), n_episodes_B=int(sb["n"]),
             arm_A_bps=round(sa["mean"], 1), arm_B_bps=round(sb["mean"], 1),
             gross_bps=round(diff, 1), net_bps=round(diff-cost, 1), net_bps_stress28=round(diff-stress, 1),
             t_stat_declustered=round(t, 2) if np.isfinite(t) else None,
             bootstrap_ci95=[round(float(np.percentile(bs, 2.5)), 1), round(float(np.percentile(bs, 97.5)), 1)],
             n_required=round(nr, 1) if np.isfinite(nr) else None,
             event_rate_per_week_6m=round(rate, 3),
             eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                           years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))
    if extra: o.update(extra)
    return o

# ================== E1b : declusterisation par SPELL de regime ==================
df = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     m AS (SELECT p.*, l.onboard_date,
             median(p.quote_vol) OVER w AS qm FROM p JOIN l USING (symbol)
           WINDOW w AS (PARTITION BY p.symbol ORDER BY p.date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING))
SELECT date, symbol, close, date_diff('day', onboard_date, date) AS age_days
FROM m WHERE qm >= 1e6 AND age_days >= 1 ORDER BY date, symbol""").df()
df["date"] = pd.to_datetime(df.date, utc=True); df = df.sort_values(["symbol", "date"])
for h in (7, 30):
    df[f"fwd{h}"] = np.expm1(df.groupby("symbol")["close"].transform(
        lambda s: np.log(s.shift(-h)) - np.log(s)))*1e4
    df[f"fwd{h}"] = df.groupby("date")[f"fwd{h}"].transform(lambda s: s.clip(s.quantile(.01), s.quantile(.99)))

cal = con.execute(f"select symbol,onboard_ts from '{ROOT}/data/listings_backfill/binance/listings_calendar.parquet' where onboard_ts is not null").df()
cal["onboard_ts"] = pd.to_datetime(cal.onboard_ts, utc=True)
days = pd.date_range(df.date.min(), df.date.max(), freq="D", tz="UTC")
lc = cal.onboard_ts.dt.floor("D").value_counts().sort_index().reindex(days, fill_value=0)
wave30 = lc.shift(1).rolling(30, min_periods=30).sum()
wave_pct = wave30.expanding(min_periods=250).apply(lambda s: (s[:-1] < s[-1]).mean() if len(s) > 1 else np.nan, raw=True)
sig = pd.DataFrame(dict(date=days, wave30=wave30.values, wave_pct=wave_pct.values)).dropna()
sig["regime"] = np.where(sig.wave_pct > 2/3, "HI", np.where(sig.wave_pct < 1/3, "LO", "MID"))
sig["spell"] = (sig.regime != sig.regime.shift()).cumsum()

mkt = df.groupby("date").agg(bask7=("fwd7", "mean"), bask30=("fwd30", "mean")).reset_index()
btc = df[df.symbol == "BTCUSDT"][["date", "fwd7", "fwd30"]].rename(columns={"fwd7": "btc7", "fwd30": "btc30"})
mkt = mkt.merge(btc, on="date", how="left").merge(sig, on="date", how="inner")
sp = mkt[mkt.regime != "MID"].groupby(["spell", "regime"]).agg(
    bask7=("bask7", "mean"), bask30=("bask30", "mean"), btc7=("btc7", "mean"), btc30=("btc30", "mean"),
    d0=("date", "min"), ndays=("date", "size")).reset_index()
sp = sp[sp.ndays >= 5]
print(f"[E1b] spells de regime (>=5j) : HI={int((sp.regime=='HI').sum())} LO={int((sp.regime=='LO').sum())} ; "
      f"duree mediane={sp.ndays.median():.0f}j")
for tgt in ["bask7", "bask30", "btc7", "btc30"]:
    A = sp[(sp.regime == "HI")].dropna(subset=[tgt]); B = sp[(sp.regime == "LO")].dropna(subset=[tgt])
    g = welch(A[tgt], B[tgt], A.d0, B.d0, f"E1b_LISTING_WAVE_RISK_REGIME_SPELL_{tgt}", "LISTING_WAVE_REGIME",
              f"rendement forward de {tgt} en regime HAUTE intensite de cotation - BASSE, declusterise par SPELL",
              COST_RT, COST_STRESS,
              extra=dict(n_raw=int(len(mkt)), median_spell_days=float(sp.ndays.median()),
                         note="L3 = run contigu de regime (unite macro naturelle d'un signal persistant)"))
    yr = []
    for y, s2 in mkt.groupby(mkt.date.dt.year):
        h2, l2 = s2[s2.regime == "HI"][tgt], s2[s2.regime == "LO"][tgt]
        if len(h2) > 5 and len(l2) > 5:
            yr.append(dict(year=int(y), diff_bps=round(float(h2.mean()-l2.mean()), 1), n_hi=len(h2), n_lo=len(l2)))
    g["year_by_year"] = yr
    if yr and g.get("gross_bps") is not None:
        sgn = np.sign(g["gross_bps"]); g["years_same_sign"] = f"{sum(1 for r in yr if np.sign(r['diff_bps'])==sgn)}/{len(yr)}"
        ybest = max(yr, key=lambda r: -r["diff_bps"] if sgn < 0 else r["diff_bps"])
        m2 = mkt[mkt.date.dt.year != ybest["year"]]
        g["ex_best_year"] = dict(dropped=ybest["year"],
                                 gross_bps=round(float(m2[m2.regime=="HI"][tgt].mean()-m2[m2.regime=="LO"][tgt].mean()), 1))
    res.append(g)

# ================== A1b : test de signe declusterise ==================
ev = pd.read_parquet(f"{OUT}/axisA_events.parquet")
for (d, h) in [(1, 168), (4, 168), (24, 168), (24, 72)]:
    s = ev[(ev.delay_h == d) & (ev.horizon_h == h)].dropna(subset=["rel_bps"]).copy()
    s["win"] = (-s.rel_bps > 0).astype(float)
    ep = s.groupby("wave_isoweek").win.mean().to_numpy()
    st = _mt(ep - 0.5); ci = block_bootstrap_ci(ep)
    sh = -s.rel_bps
    res.append(dict(id=f"A1b_LIST_FADE_SIGN_TEST_d{d}h_h{h}h", family="LISTING_EVENT",
                    hypothesis="taux de reussite du fade de listing (short nouveau / long panier), declusterise par semaine",
                    n_raw=int(len(s)), n_independent_L3=int(len(ep)),
                    hit_rate=round(float(s.win.mean()), 3),
                    hit_rate_declustered=round(float(ep.mean()), 3),
                    t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                    bootstrap_ci95_hitrate=[round(ci[0], 3), round(ci[1], 3)],
                    median_bps=round(float(sh.median()), 1), mean_bps=round(float(sh.mean()), 1),
                    sd_bps=round(float(sh.std()), 1), skew=round(float(sh.skew()), 2),
                    worst_single_event_bps=round(float(sh.min()), 1),
                    mean_excl_5_worst_bps=round(float(sh.sort_values().iloc[5:].mean()), 1),
                    note="taux de reussite tres significatif MAIS payoff a queue gauche non bornee : "
                         "la moyenne (ce qui compose) n'est pas confirmable, la mediane n'est pas harvestable sans structure a risque defini"))

# ================== A4b : taille de vague, bras disjoints ==================
med_ws = ev.wave_size.median()
for h in [72, 168]:
    s = ev[(ev.delay_h == 24) & (ev.horizon_h == h)].dropna(subset=["rel_bps"]).copy()
    s["sh"] = -s.rel_bps
    A = s[s.wave_size >= med_ws].groupby("wave_isoweek").agg(v=("sh", "mean"), d0=("_dt", "min"))
    B = s[s.wave_size < med_ws].groupby("wave_isoweek").agg(v=("sh", "mean"), d0=("_dt", "min"))
    res.append(welch(A.v, B.v, A.d0, B.d0, f"A4b_LIST_WAVE_SIZE_COND_h{h}h", "LISTING_EVENT",
                     f"fade plus rentable dans les grandes vagues (>={med_ws:.0f} listings/semaine) que dans les petites",
                     COST_LS, COST_LS_STRESS,
                     extra=dict(n_raw=int(len(s)), median_wave_size=float(med_ws),
                                note="bras disjoints par construction (la taille de vague est constante dans une semaine) -> Welch")))

# ================== C2c : carry jeune vs mature, AVEC winsorisation ==================
e = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     f AS (SELECT * FROM '{OUT}/funding_daily.parquet'),
     m AS (SELECT p.*, l.onboard_date, f.funding_paid_d, median(p.quote_vol) OVER w AS qm
           FROM p JOIN l USING (symbol) LEFT JOIN f USING (date, symbol)
           WINDOW w AS (PARTITION BY p.symbol ORDER BY p.date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING))
SELECT date, symbol, close, funding_paid_d, date_diff('day', onboard_date, date) AS age_days
FROM m WHERE qm >= 1e6 AND age_days >= 1 ORDER BY symbol, date""").df()
e["date"] = pd.to_datetime(e.date, utc=True)
e["fwd7_bps"] = np.expm1(e.groupby("symbol")["close"].transform(lambda s: np.log(s.shift(-7))-np.log(s)))*1e4
e["fund7_bps"] = e.groupby("symbol")["funding_paid_d"].transform(
    lambda s: s.shift(-1).rolling(7, min_periods=5).sum().shift(-6))*1e4
i = e.date.dt.isocalendar(); e["_isoweek"] = i.year.astype(str)+"-W"+i.week.astype(str).str.zfill(2)
for winsor in (False, True):
    ee = e.copy()
    if winsor:
        ee["fwd7_bps"] = ee.groupby("date").fwd7_bps.transform(lambda s: s.clip(s.quantile(.01), s.quantile(.99)))
    ee["short_carry_bps"] = ee.fund7_bps - ee.fwd7_bps
    pos = ee[(ee.funding_paid_d > 0) & ee.short_carry_bps.notna()]
    A = pos[pos.age_days < 90].groupby("_isoweek").agg(v=("short_carry_bps", "mean"), d0=("date", "min"))
    B = pos[pos.age_days >= 365].groupby("_isoweek").agg(v=("short_carry_bps", "mean"), d0=("date", "min"))
    j = pd.concat([A.v.rename("A"), B.v.rename("B")], axis=1).dropna()
    diff = (j.A-j.B).to_numpy(); st = _mt(diff); ci = block_bootstrap_ci(diff); nr = n_required(diff)
    dts = pd.to_datetime(pd.Series(sorted(j.index))+"-1", format="%G-W%V-%u", utc=True, errors="coerce")
    rate = episode_rate_per_week(dts, lookback_end=LB_END); et = eta_days_years(nr, rate)
    res.append(dict(id=f"C2c_AGE_CARRY_{'WINSORIZED' if winsor else 'RAW'}", family="MICROSTRUCTURE_MATURATION",
                    hypothesis="short-perp jeunes(<90j) - matures(>=1a), funding>0, 7j — sensibilite a la winsorisation 1/99",
                    winsorized=winsor, n_raw=int(len(pos)), n_independent_L3=int(len(j)),
                    gross_bps=round(st["mean"], 1), net_bps=round(st["mean"]-COST_LS, 1),
                    net_bps_stress28=round(st["mean"]-COST_LS_STRESS, 1),
                    t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                    bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
                    n_required=round(nr, 1) if np.isfinite(nr) else None,
                    event_rate_per_week_6m=round(rate, 3),
                    eta_forward_confirmation=dict(days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                                                  years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None)))

json.dump(res, open(f"{OUT}/fixups_results.json", "w"), indent=1, default=str)
print()
for g in res:
    print(f"{g['id']:<46} L3={g.get('n_independent_L3')!s:>4} gross={g.get('gross_bps')!s:>9} "
          f"net={g.get('net_bps')!s:>9} t={g.get('t_stat_declustered')!s:>6} "
          f"ETAy={(g.get('eta_forward_confirmation') or {}).get('years')!s:>8} "
          f"hit={g.get('hit_rate_declustered')!s:>6} yrs={g.get('years_same_sign')}")
