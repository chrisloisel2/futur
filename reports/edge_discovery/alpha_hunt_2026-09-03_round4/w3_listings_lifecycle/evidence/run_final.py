#!/usr/bin/env python3
"""
W3_LISTINGS_LIFECYCLE — run_final.py : les trous restants apres l'interruption de session.

  S0  SURVIVORSHIP_AUDIT              — decompte explicite des noms MORTS par univers +
                                        contrefactuel "univers sans radies" sur A1 (mesure
                                        directe de ce que le biais de survie ajouterait).
  A5  LIST_FADE_NET_OF_FUNDING        — le fade de listing (A1) NET du funding effectivement
                                        paye par la jambe short + du funding paye par la jambe
                                        longue du panier. C'est le chainon manquant entre A1 et A3.
  E1c LISTING_WAVE_REGIME_SPELL       — E1 refait avec la BONNE unite de declustering :
                                        la PLAGE (spell) de regime, et une cible sans
                                        chevauchement inter-plages (rendement quotidien
                                        forward-1j moyen PENDANT la plage).
  E2b WAVE_COND_XSEC_MOM_SPELL        — idem pour E2 (le declustering mensuel apparie donnait L3=3).

Entrees : artefacts de scratch produits par build_panel.py / build_benchmark.py /
          build_funding_daily.py / run_axis_A_listing_event.py.
Sortie  : $W3_SCRATCH/final_results.json
"""
import os, sys, glob, json, warnings
import numpy as np, pandas as pd, duckdb
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import (_mt, block_bootstrap_ci, n_required, episode_rate_per_week, eta_days_years,
                  COST_RT, COST_STRESS, COST_LS, COST_LS_STRESS)

ROOT = "/home/qbee/futur"
OUT = os.environ["W3_SCRATCH"]
LB_END = "2026-07-31"          # fin des donnees du panel : la fenetre "6 derniers mois" s'y arrete
CAL = f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet"
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")
con.execute("SET memory_limit='1500MB'"); con.execute("SET threads=2")
res = []


# ----------------------------------------------------------------------------------
# helper : comparaison de deux bras d'episodes L3 DISJOINTS (Welch) + gate complet
# ----------------------------------------------------------------------------------
def welch_gate(a, b, dates_a, dates_b, label, family, hyp, cost, stress,
               n_raw=None, n_l1=None, n_l2=None, extra=None):
    a = np.asarray(pd.Series(a).dropna(), float); b = np.asarray(pd.Series(b).dropna(), float)
    sa, sb = _mt(a), _mt(b)
    if sa["n"] < 3 or sb["n"] < 3:
        return dict(id=label, family=family, hypothesis=hyp,
                    n_independent_L3=int(min(sa["n"], sb["n"])), verdict="DATA_LIMITED",
                    note="moins de 3 episodes L3 dans un bras")
    se = np.sqrt(sa["sd"]**2/sa["n"] + sb["sd"]**2/sb["n"])
    diff = sa["mean"] - sb["mean"]
    t = diff/se if se > 0 else np.nan
    n_eff = min(sa["n"], sb["n"])
    d = abs(diff)/(se*np.sqrt(n_eff)) if se > 0 else 0.0      # Cohen's d par episode
    nr = 7.849/(0.5*d)**2 if d > 0 else np.nan
    alld = pd.to_datetime(pd.Series(list(dates_a)+list(dates_b)), utc=True)
    rate = episode_rate_per_week(alld, lookback_end=LB_END)
    et = eta_days_years(nr, rate)
    rng = np.random.default_rng(20260905)
    bs = np.array([rng.choice(a, sa["n"], True).mean() - rng.choice(b, sb["n"], True).mean()
                   for _ in range(5000)])
    o = dict(id=label, family=family, hypothesis=hyp, paired_episodes=False,
             n_raw=int(n_raw) if n_raw is not None else int(sa["n"]+sb["n"]),
             n_independent_L1=int(n_l1) if n_l1 is not None else None,
             n_independent_L2=int(n_l2) if n_l2 is not None else None,
             n_independent_L3=int(n_eff), n_episodes_A=int(sa["n"]), n_episodes_B=int(sb["n"]),
             arm_A_bps=round(sa["mean"], 1), arm_B_bps=round(sb["mean"], 1),
             gross_bps=round(diff, 1), net_bps=round(diff-cost, 1),
             net_bps_stress28=round(diff-stress, 1),
             t_stat_declustered=round(t, 2) if np.isfinite(t) else None,
             bootstrap_ci95=[round(float(np.percentile(bs, 2.5)), 1),
                             round(float(np.percentile(bs, 97.5)), 1)],
             n_required=round(nr, 1) if np.isfinite(nr) else None,
             event_rate_per_week_6m=round(rate, 3),
             eta_forward_confirmation=dict(
                 days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                 years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))
    if extra: o.update(extra)
    return o


def paired_gate(diff, dates, label, family, hyp, cost, stress, n_raw=None, n_l1=None, n_l2=None, extra=None):
    v = np.asarray(pd.Series(diff).dropna(), float)
    st = _mt(v); ci = block_bootstrap_ci(v); nr = n_required(v)
    rate = episode_rate_per_week(pd.to_datetime(pd.Series(dates), utc=True), lookback_end=LB_END)
    et = eta_days_years(nr, rate)
    o = dict(id=label, family=family, hypothesis=hyp, paired_episodes=True,
             n_raw=int(n_raw) if n_raw is not None else int(st["n"]),
             n_independent_L1=int(n_l1) if n_l1 is not None else None,
             n_independent_L2=int(n_l2) if n_l2 is not None else None,
             n_independent_L3=int(st["n"]),
             gross_bps=round(st["mean"], 1), net_bps=round(st["mean"]-cost, 1),
             net_bps_stress28=round(st["mean"]-stress, 1),
             t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
             bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
             n_required=round(nr, 1) if np.isfinite(nr) else None,
             event_rate_per_week_6m=round(rate, 3),
             eta_forward_confirmation=dict(
                 days=round(et["eta_days"], 0) if np.isfinite(et["eta_days"]) else None,
                 years=round(et["eta_years"], 2) if np.isfinite(et["eta_years"]) else None))
    if extra: o.update(extra)
    return o


# ==================================================================================
# S0 — AUDIT DE BIAIS DE SURVIE
# ==================================================================================
DEAD = ("SETTLING", "DELISTED", "DELISTED_NO_DATA")
life = con.execute(f"select * from '{OUT}/life.parquet'").df()
cal = con.execute(f"select symbol, onboard_ts, status from '{CAL}'").df()
cal["onboard_ts"] = pd.to_datetime(cal.onboard_ts, utc=True)
have_k = {os.path.basename(p)[:-8] for p in glob.glob(f"{ROOT}/data/listings_backfill/binance/klines_1h/*.parquet")}
calk = cal[cal.symbol.isin(have_k)].copy()

audit = dict(
    id="S0_SURVIVORSHIP_AUDIT", family="META", verdict="DESCRIPTIVE",
    hypothesis="verification explicite que les instruments RADIES sont dans les univers testes",
    panel_312=dict(n=int(len(life)),
                   by_status={k: int(v) for k, v in life.status.value_counts().items()},
                   n_dead=int(life.status.isin(DEAD).sum()),
                   pct_dead=round(100*float(life.status.isin(DEAD).mean()), 1)),
    listings_universe=dict(n=int(len(calk)),
                           by_status={k: int(v) for k, v in calk.status.value_counts().items()},
                           n_dead=int(calk.status.isin(DEAD).sum()),
                           pct_dead=round(100*float(calk.status.isin(DEAD).mean()), 1)),
    calendar_full=dict(n=int(len(cal)),
                       by_status={k: int(v) for k, v in cal.status.value_counts().items()}),
)
# combien de morts ont effectivement une fin de vie DANS la fenetre de donnees ?
life["last_date"] = pd.to_datetime(life.last_date, utc=True)
panel_end = pd.Timestamp(life.last_date.max())
audit["panel_312"]["n_dead_with_life_end_inside_window"] = int(
    ((life.status.isin(DEAD)) & (life.last_date < panel_end - pd.Timedelta(days=5))).sum())
audit["panel_end"] = str(panel_end.date())
audit["known_limitation"] = ("les perps radies AVANT 2023 et absents de fapi/exchangeInfo ne sont pas "
                             "recuperables (listings_backfill_store.yaml _meta.missing_delisted) : "
                             "l'axe A est propre sur 2023+ uniquement")

# contrefactuel : A1 (d24h, h168h) sur univers COMPLET vs univers TRADING seulement
ev = pd.read_parquet(f"{OUT}/axisA_events.parquet")
srow = []
for (d, h) in [(24, 168), (24, 672), (1, 168)]:
    s = ev[(ev.delay_h == d) & (ev.horizon_h == h)].dropna(subset=["rel_bps"]).copy()
    if not len(s): continue
    s["sh"] = -s.rel_bps
    full = s.groupby("wave_isoweek").sh.mean()
    surv = s[~s.is_dead].groupby("wave_isoweek").sh.mean()
    srow.append(dict(delay_h=d, horizon_h=h,
                     n_all=int(len(s)), n_dead=int(s.is_dead.sum()),
                     pct_dead=round(100*float(s.is_dead.mean()), 1),
                     fade_bps_all_names=round(float(full.mean()), 1),
                     fade_bps_survivors_only=round(float(surv.mean()), 1),
                     survivorship_inflation_bps=round(float(surv.mean()-full.mean()), 1),
                     dead_names_fade_bps=round(float(s[s.is_dead].sh.mean()), 1),
                     live_names_fade_bps=round(float(s[~s.is_dead].sh.mean()), 1)))
audit["A1_survivorship_counterfactual"] = srow
res.append(audit)
print("[S0] panel 312 :", audit["panel_312"]["by_status"], "-> morts",
      audit["panel_312"]["n_dead"], f'({audit["panel_312"]["pct_dead"]}%)')
print("[S0] univers listings :", audit["listings_universe"]["by_status"], "-> morts",
      audit["listings_universe"]["n_dead"], f'({audit["listings_universe"]["pct_dead"]}%)')
for r in srow:
    print(f"[S0] A1 d{r['delay_h']}h/h{r['horizon_h']}h : tous={r['fade_bps_all_names']}bps  "
          f"survivants_seuls={r['fade_bps_survivors_only']}bps  "
          f"(inflation de survie {r['survivorship_inflation_bps']:+.1f}bps ; "
          f"{r['pct_dead']}% de morts)")


# ==================================================================================
# A5 — LE FADE DE LISTING, NET DU FUNDING REELLEMENT PAYE
# ==================================================================================
# jambe SHORT sur le nouveau perp : encaisse +somme(funding_rate) sur la fenetre
# jambe LONG sur le panier de matures : paie -somme(funding_rate) contemporain des matures
fd = con.execute(f"""
WITH p AS (SELECT date, symbol, quote_vol FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     f AS (SELECT * FROM '{OUT}/funding_daily.parquet'),
     m AS (SELECT p.date, p.symbol, l.onboard_date, f.funding_paid_d,
             median(p.quote_vol) OVER (PARTITION BY p.symbol ORDER BY p.date
                                       ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING) AS qm
           FROM p JOIN l USING (symbol) LEFT JOIN f USING (date, symbol))
SELECT date, avg(funding_paid_d) AS bench_funding_d
FROM m WHERE qm >= 1e6 AND date_diff('day', onboard_date, date) >= 365
  AND funding_paid_d IS NOT NULL
GROUP BY 1 ORDER BY 1""").df()
fd["date"] = pd.to_datetime(fd.date, utc=True)
bench_fund = fd.set_index("date").bench_funding_d.sort_index()
bench_fund_cum = bench_fund.cumsum()

fund_cache = {}
def listing_funding(sym, t_in, t_out):
    """somme des funding_rate REGLES dans (t_in, t_out] pour un nouveau listing."""
    if sym not in fund_cache:
        try:
            f = pd.read_parquet(f"{ROOT}/data/listings_backfill/binance/funding/{sym}.parquet")
            f["timestamp"] = pd.to_datetime(f.timestamp, utc=True)
            fund_cache[sym] = f.sort_values("timestamp")
        except Exception:
            fund_cache[sym] = None
    f = fund_cache[sym]
    if f is None: return np.nan
    m = f[(f.timestamp > t_in) & (f.timestamp <= t_out)]
    return float(m.funding_rate.sum()) if len(m) else np.nan

rows = []
for (d, h) in [(24, 168), (24, 672), (1, 168), (4, 168)]:
    s = ev[(ev.delay_h == d) & (ev.horizon_h == h)].dropna(subset=["rel_bps"]).copy()
    if len(s) < 30: continue
    s["_dt"] = pd.to_datetime(s._dt, utc=True)
    rec = []
    for _, r in s.iterrows():
        t_in = r._dt + pd.Timedelta(hours=d); t_out = t_in + pd.Timedelta(hours=h)
        fnew = listing_funding(r.symbol, t_in, t_out)
        a = bench_fund_cum.asof(t_in); b = bench_fund_cum.asof(t_out)
        fben = (b - a) if (np.isfinite(a) and np.isfinite(b)) else np.nan
        rec.append((fnew, fben))
    s["f_new_bps"] = [x[0]*1e4 for x in rec]
    s["f_bench_bps"] = [x[1]*1e4 for x in rec]
    # P&L du spread : short nouveau (prix -ret, funding +f_new) / long panier (prix +bench, funding -f_bench)
    s["short_rel_bps"] = -s.rel_bps
    s["spread_net_funding_bps"] = s.short_rel_bps + s.f_new_bps - s.f_bench_bps
    s = s.dropna(subset=["spread_net_funding_bps"])
    ep_px = s.groupby("wave_isoweek").short_rel_bps.mean()
    ep_tot = s.groupby("wave_isoweek").spread_net_funding_bps.mean()
    dts = pd.to_datetime(pd.Series(sorted(ep_tot.index)) + "-1", format="%G-W%V-%u", utc=True, errors="coerce")
    g = paired_gate(ep_tot.to_numpy(), dts, f"A5_LIST_FADE_NET_OF_FUNDING_d{d}h_h{h}h", "LISTING_EVENT",
                    "fade de listing (short nouveau / long panier) NET du funding effectivement regle "
                    "sur les DEUX jambes",
                    COST_LS, COST_LS_STRESS,
                    n_raw=len(s), n_l1=int(s.symbol.nunique()), n_l2=int(s._dt.dt.date.nunique()),
                    extra=dict(price_only_gross_bps=round(float(ep_px.mean()), 1),
                               funding_drag_short_leg_bps=round(float(s.f_new_bps.mean()), 1),
                               funding_drag_long_leg_bps=round(-float(s.f_bench_bps.mean()), 1),
                               total_funding_drag_bps=round(float((s.f_new_bps-s.f_bench_bps).mean()), 1),
                               median_funding_new_listing_bps=round(float(s.f_new_bps.median()), 1),
                               n_events=int(len(s))))
    yr = []
    for y, sub in s.groupby(s._dt.dt.year):
        e2 = sub.groupby("wave_isoweek").spread_net_funding_bps.mean()
        st2 = _mt(e2.to_numpy())
        yr.append(dict(year=int(y), n_ep=int(st2["n"]), mean_bps=round(st2["mean"], 1),
                       t=round(st2["t"], 2) if np.isfinite(st2["t"]) else None))
    g["year_by_year"] = yr
    ys = [r for r in yr if r["n_ep"] >= 3]
    if len(ys) >= 2:
        sgn = np.sign(g["gross_bps"])
        g["years_same_sign"] = f"{sum(1 for r in ys if np.sign(r['mean_bps'])==sgn)}/{len(ys)}"
        best = max(ys, key=lambda r: r["mean_bps"] if sgn >= 0 else -r["mean_bps"])
        sx = s[s._dt.dt.year != best["year"]]
        ex = _mt(sx.groupby("wave_isoweek").spread_net_funding_bps.mean().to_numpy())
        g["ex_best_year"] = dict(dropped=best["year"], gross_bps=round(ex["mean"], 1),
                                 t=round(ex["t"], 2) if np.isfinite(ex["t"]) else None, n_ep=int(ex["n"]))
    res.append(g)
    rows.append((d, h, g["price_only_gross_bps"], g["total_funding_drag_bps"], g["gross_bps"], g["t_stat_declustered"]))
    print(f"[A5] d{d}h/h{h}h : prix seul {g['price_only_gross_bps']:+.0f}bps | funding "
          f"{g['total_funding_drag_bps']:+.0f}bps | total {g['gross_bps']:+.0f}bps "
          f"(t_L3={g['t_stat_declustered']}, n={g['n_raw']})")


# ==================================================================================
# E1c / E2b — REGIME DE VAGUE, DECLUSTERISE PAR PLAGE (SPELL), CIBLE SANS CHEVAUCHEMENT
# ==================================================================================
df = con.execute(f"""
WITH p AS (SELECT * FROM '{OUT}/daily_panel.parquet'),
     l AS (SELECT symbol, onboard_date FROM '{OUT}/life.parquet'),
     m AS (SELECT p.*, l.onboard_date,
             median(p.quote_vol) OVER (PARTITION BY p.symbol ORDER BY p.date
                                       ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING) AS qm
           FROM p JOIN l USING (symbol))
SELECT date, symbol, close, date_diff('day', onboard_date, date) AS age_days
FROM m WHERE qm >= 1e6 AND age_days >= 1 ORDER BY symbol, date""").df()
df["date"] = pd.to_datetime(df.date, utc=True)
df = df.sort_values(["symbol", "date"])
# rendement forward 1 JOUR (unite additive, sans chevauchement) + momentum 7j causal
df["fwd1"] = np.expm1(df.groupby("symbol")["close"].transform(lambda s: np.log(s.shift(-1)) - np.log(s)))*1e4
df["mom7"] = np.expm1(df.groupby("symbol")["close"].transform(lambda s: np.log(s) - np.log(s.shift(7))))*1e4
df["fwd1"] = df.groupby("date").fwd1.transform(lambda s: s.clip(s.quantile(.01), s.quantile(.99)))

# signal de vague, PIT (fenetre fermee a gauche + percentile expanding)
c2 = cal.dropna(subset=["onboard_ts"])
days = pd.date_range(df.date.min(), df.date.max(), freq="D", tz="UTC")
lc = c2.onboard_ts.dt.floor("D").value_counts().sort_index().reindex(days, fill_value=0)
wave30 = lc.shift(1).rolling(30, min_periods=30).sum()
wave_pct = wave30.expanding(min_periods=250).apply(lambda s: (s[:-1] < s[-1]).mean() if len(s) > 1 else np.nan, raw=True)
sig = pd.DataFrame(dict(date=days, wave30=wave30.values, wave_pct=wave_pct.values)).dropna()
sig["regime"] = np.where(sig.wave_pct > 2/3, "HI", np.where(sig.wave_pct < 1/3, "LO", "MID"))
sig["spell"] = (sig.regime != sig.regime.shift()).cumsum()

# cibles quotidiennes : panier equal-weight eligible, BTC, et livre momentum 7j (quintiles)
bask = df.groupby("date").fwd1.mean().rename("bask").reset_index()
btc = df[df.symbol == "BTCUSDT"][["date", "fwd1"]].rename(columns={"fwd1": "btc"})
mrec = []
for dt, g in df.groupby("date"):
    g = g.dropna(subset=["fwd1", "mom7"])
    if len(g) < 25: continue
    r = g.mom7.rank(pct=True)
    mrec.append(dict(date=dt, mom=g[r > 0.8].fwd1.mean() - g[r <= 0.2].fwd1.mean()))
mom = pd.DataFrame(mrec)
mkt = bask.merge(btc, on="date", how="left").merge(mom, on="date", how="left").merge(sig, on="date", how="inner")

MIN_SPELL = 5
sp = mkt[mkt.regime != "MID"].groupby(["spell", "regime"]).agg(
    bask=("bask", "mean"), btc=("btc", "mean"), mom=("mom", "mean"),
    d0=("date", "min"), d1=("date", "max"), ndays=("date", "size")).reset_index()
sp = sp[sp.ndays >= MIN_SPELL]
nhi, nlo = int((sp.regime == "HI").sum()), int((sp.regime == "LO").sum())
print(f"\n[E1c] plages de regime (>= {MIN_SPELL} j) : HI={nhi} LO={nlo} ; "
      f"duree mediane={sp.ndays.median():.0f}j, min={sp.ndays.min():.0f}j, max={sp.ndays.max():.0f}j ; "
      f"couverture={sp.ndays.sum()} jours sur {len(mkt)}")

SPEC = [("bask", "panier equal-weight eligible", COST_RT, COST_STRESS),
        ("btc",  "BTCUSDT",                       COST_RT, COST_STRESS),
        ("mom",  "livre momentum transversal 7j (Q5-Q1)", COST_LS, COST_LS_STRESS)]
for col, name, cost, stress in SPEC:
    A = sp[sp.regime == "HI"].dropna(subset=[col]); B = sp[sp.regime == "LO"].dropna(subset=[col])
    for scale, sname in [(30.0, "30d")]:
        lbl = ("E2b_WAVE_COND_XSEC_MOM_SPELL" if col == "mom"
               else f"E1c_LISTING_WAVE_REGIME_SPELL_{col}")
        g = welch_gate(A[col]*scale, B[col]*scale, A.d0, B.d0, lbl, "LISTING_WAVE_REGIME",
                       f"rendement quotidien moyen de {name} PENDANT une plage de regime HAUTE "
                       f"intensite de cotation - PENDANT une plage BASSE (exprime en bps/{sname}) ; "
                       f"L3 = plage de regime (aucun chevauchement inter-plages)",
                       cost, stress,
                       n_raw=int(len(mkt)),
                       n_l1=int(sp.ndays.sum()),
                       n_l2=int(np.ceil(sp.ndays.sum()/30)),
                       extra=dict(unit=f"bps par {sname} de detention continue",
                                  median_spell_days=float(sp.ndays.median()),
                                  n_days_HI=int(A.ndays.sum()), n_days_LO=int(B.ndays.sum()),
                                  mean_spell_cum_bps_HI=round(float((A[col]*A.ndays).mean()), 1),
                                  mean_spell_cum_bps_LO=round(float((B[col]*B.ndays).mean()), 1),
                                  note="cible = rendement forward 1 JOUR moyen sur la plage : additif, "
                                       "sans chevauchement entre plages, contrairement a fwd7/fwd30"))
        yr = []
        for y, s2 in mkt.groupby(mkt.date.dt.year):
            h2 = s2[s2.regime == "HI"][col]; l2 = s2[s2.regime == "LO"][col]
            if len(h2) > 5 and len(l2) > 5:
                yr.append(dict(year=int(y), n_hi=int(len(h2)), n_lo=int(len(l2)),
                               diff_bps=round(float((h2.mean()-l2.mean())*scale), 1)))
        g["year_by_year"] = yr
        if yr and g.get("gross_bps") is not None:
            sgn = np.sign(g["gross_bps"])
            g["years_same_sign"] = f"{sum(1 for r in yr if np.sign(r['diff_bps'])==sgn)}/{len(yr)}"
            best = max(yr, key=lambda r: r["diff_bps"] if sgn >= 0 else -r["diff_bps"])
            m2 = mkt[mkt.date.dt.year != best["year"]]
            g["ex_best_year"] = dict(dropped=best["year"],
                                     gross_bps=round(float((m2[m2.regime == "HI"][col].mean()
                                                            - m2[m2.regime == "LO"][col].mean())*scale), 1))
        res.append(g)
        print(f"[{lbl}] HI={g.get('arm_A_bps')} LO={g.get('arm_B_bps')} diff={g.get('gross_bps')}bps/{sname} "
              f"t={g.get('t_stat_declustered')} L3={g.get('n_independent_L3')} "
              f"ETA={(g.get('eta_forward_confirmation') or {}).get('years')}a "
              f"signes={g.get('years_same_sign')} ex_best={(g.get('ex_best_year') or {}).get('gross_bps')}")

json.dump(res, open(f"{OUT}/final_results.json", "w"), indent=1, default=str)
print("\nOK ->", f"{OUT}/final_results.json", f"({len(res)} entrees)")
