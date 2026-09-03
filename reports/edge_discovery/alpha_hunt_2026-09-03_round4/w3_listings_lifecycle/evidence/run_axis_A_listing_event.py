#!/usr/bin/env python3
"""
W3 — Axe A : effet de cotation, NEUTRALISE PAR LA COUPE TRANSVERSALE.
A1 LIST_DRIFT_XSNEUTRAL / A2 LIST_D0_CONDITIONAL_SPREAD / A4 LIST_WAVE_SIZE_COND
(A3 funding-carry est dans run_axis_C.py)
"""
import os, sys, json, glob, warnings
import numpy as np, pandas as pd, duckdb
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import run_gate, add_time_keys, COST_THIN, _mt, decluster, block_bootstrap_ci, n_required, episode_rate_per_week, eta_days_years

ROOT = "/home/qbee/futur"; OUT = os.environ["W3_SCRATCH"]
LB_END = "2026-07-31"
CAL = f"{ROOT}/data/listings_backfill/binance/listings_calendar.parquet"
K1 = f"{ROOT}/data/listings_backfill/binance/klines_1h"
con = duckdb.connect(); con.execute(f"SET temp_directory='{OUT}/duckdb_tmp'")

# ---------- 1. evenements + panier de reference horaire ----------
cal = con.execute(f"select symbol, onboard_ts, status from '{CAL}'").df()
cal["onboard_ts"] = pd.to_datetime(cal.onboard_ts, utc=True)
cal["is_dead"] = cal.status.isin(["SETTLING", "DELISTED", "DELISTED_NO_DATA"])
have = {os.path.basename(p)[:-8] for p in glob.glob(f"{K1}/*.parquet")}
cal = cal[cal.symbol.isin(have)].sort_values("onboard_ts").reset_index(drop=True)
print(f"[univers A] {len(cal)} listings avec klines ; morts inclus = {int(cal.is_dead.sum())} "
      f"({100*cal.is_dead.mean():.1f}%) ; {cal.onboard_ts.min().date()} -> {cal.onboard_ts.max().date()}")

bench = con.execute(f"select ts_h, idx_logret, n_eligible from '{OUT}/bench_hourly.parquet' order by ts_h").df()
bench["ts_h"] = pd.to_datetime(bench.ts_h, utc=True)
bench = bench.dropna(subset=["idx_logret"]).set_index("ts_h")
bench["cum"] = bench.idx_logret.cumsum()
bcum = bench["cum"]

def bench_ret(t_in, t_out):
    """rendement simple du panier eligible entre deux instants (cumul de log-rendements horaires)."""
    try:
        a = bcum.asof(pd.Timestamp(t_in).floor("h")); b = bcum.asof(pd.Timestamp(t_out).floor("h"))
    except Exception:
        return np.nan
    if not np.isfinite(a) or not np.isfinite(b): return np.nan
    return float(np.expm1(b - a))

# vagues de cotation : ISO-week (repli prevu au PREREG §5c, la regle gap>=7j degenere : 130 listings dans une seule vague)
iso = cal.onboard_ts.dt.isocalendar()
cal["wave_isoweek"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
gaps = cal.onboard_ts.diff().dt.total_seconds() / 86400
cal["wave_gap7"] = (gaps.fillna(1e9) > 7).cumsum()
cal["wave_size"] = cal.groupby("wave_isoweek").symbol.transform("size")

# ---------- 2. chemins de prix post-listing ----------
DELAYS = [1, 4, 24]; HORIZONS = [24, 72, 168]; H_MAX = 696   # 30j de klines dispo -> 720h max
rows = []
for _, r in cal.iterrows():
    try:
        k = pd.read_parquet(f"{K1}/{r.symbol}.parquet", columns=["timestamp", "close", "quote_volume"])
    except Exception:
        continue
    k["timestamp"] = pd.to_datetime(k.timestamp, utc=True)
    k = k.dropna(subset=["close"]).sort_values("timestamp")
    if len(k) < 30: continue
    t0 = r.onboard_ts
    s = k.set_index("timestamp").close
    # reaction jour-0 (PIT : connue a t0+24h, utilisee seulement pour des entrees >= t0+24h)
    p0 = s.asof(t0 + pd.Timedelta(hours=1)); p24 = s.asof(t0 + pd.Timedelta(hours=24))
    ret_24h = (p24 / p0 - 1) if (np.isfinite(p0) and np.isfinite(p24) and p0 > 0) else np.nan
    qv24 = float(k[k.timestamp <= t0 + pd.Timedelta(hours=24)].quote_volume.sum())
    for d in DELAYS:
        t_in = t0 + pd.Timedelta(hours=d)
        p_in = s.asof(t_in)
        if not np.isfinite(p_in) or p_in <= 0: continue
        hs = HORIZONS + [H_MAX - d]
        for h in hs:
            t_out = t_in + pd.Timedelta(hours=h)
            if t_out > s.index.max() + pd.Timedelta(hours=1): continue
            p_out = s.asof(t_out)
            if not np.isfinite(p_out) or p_out <= 0: continue
            raw = p_out / p_in - 1
            bm = bench_ret(t_in, t_out)
            rows.append(dict(symbol=r.symbol, _dt=t0, year=t0.year, is_dead=bool(r.is_dead),
                             wave_isoweek=r.wave_isoweek, wave_gap7=int(r.wave_gap7),
                             wave_size=int(r.wave_size), delay_h=d, horizon_h=h,
                             ret_bps=raw * 1e4, bench_bps=(bm * 1e4 if np.isfinite(bm) else np.nan),
                             rel_bps=((raw - bm) * 1e4 if np.isfinite(bm) else np.nan),
                             ret_24h=ret_24h, qvol_24h=qv24))
ev = pd.DataFrame(rows)
ev = add_time_keys(ev)
ev.to_parquet(f"{OUT}/axisA_events.parquet")
print(f"[A] {len(ev)} lignes (symbole x delay x horizon) ; {ev.symbol.nunique()} symboles")

L1 = ["_sym24"]; L2 = ["_date"]; L3 = ["wave_isoweek"]; L3ALT = ["wave_gap7"]
res = []

# ---------- A0 : reproduction du chiffre absolu (controle de coherence avec l'etude de juillet) ----------
sub = ev[(ev.delay_h == 1) & (ev.horizon_h == 168)]
print("\n[A0 controle] delay1h/h168  ABSOLU median_bps=%.0f mean_bps=%.0f | RELATIF median=%.0f mean=%.0f"
      % (sub.ret_bps.median(), sub.ret_bps.mean(), sub.rel_bps.median(), sub.rel_bps.mean()))

# ---------- A1 ----------
for d in DELAYS:
    for h in sorted(ev[ev.delay_h == d].horizon_h.unique()):
        sub = ev[(ev.delay_h == d) & (ev.horizon_h == h)].copy()
        if len(sub) < 30: continue
        sub["short_rel_bps"] = -sub["rel_bps"]     # jambe tradable : short le nouveau / long le panier
        extra = {"net_bps_thin60": COST_THIN} if d < 4 else None
        g = run_gate(sub, "short_rel_bps", L1, L2, L3, cost_rt=28.0, cost_stress=56.0,
                     extra_costs=({"net_bps_thin60": COST_THIN} if d < 4 else None),
                     l3_alt_keys=L3ALT, label=f"A1_LIST_DRIFT_XSNEUTRAL_d{d}h_h{h}h",
                     family="LISTING_EVENT", lookback_end=LB_END,
                     hypothesis="sous-performance relative post-listing (short nouveau / long panier eligible)")
        g["abs_gross_bps_long"] = round(sub.ret_bps.mean(), 1)
        g["bench_gross_bps"] = round(sub.bench_bps.mean(), 1)
        g["frac_dead_names"] = round(float(sub.is_dead.mean()), 3)
        g["n_symbols"] = int(sub.symbol.nunique())
        res.append(g)

# ---------- A2 : spread conditionnel jour-0 (entree t0+24h uniquement, PIT) ----------
def arm_spread(dfA, dfB, col, l3, label, family, hyp, cost=28.0, stress=56.0):
    """Compare deux bras SUR LA MEME UNITE L3 : difference des moyennes d'episodes appariees."""
    a = dfA.groupby(l3)[col].mean().rename("A"); b = dfB.groupby(l3)[col].mean().rename("B")
    j = pd.concat([a, b], axis=1).dropna()
    if len(j) < 3:
        return dict(id=label, family=family, hypothesis=hyp, n_raw=len(dfA) + len(dfB),
                    n_independent_L3=len(j), verdict="DATA_LIMITED", note="episodes L3 apparies < 3")
    diff = (j.A - j.B).to_numpy()
    st = _mt(diff); ci = block_bootstrap_ci(diff); nr = n_required(diff)
    dates = pd.to_datetime(pd.Series(sorted(j.index)).str.replace("W", "") + "-1", format="%G-%V-%u", utc=True, errors="coerce")
    rate = episode_rate_per_week(dates, lookback_end=LB_END)
    e = eta_days_years(nr, rate)
    return dict(id=label, family=family, hypothesis=hyp,
                n_raw=int(len(dfA) + len(dfB)), n_arm_A=int(len(dfA)), n_arm_B=int(len(dfB)),
                n_independent_L3=int(len(j)),
                arm_A_bps=round(float(j.A.mean()), 1), arm_B_bps=round(float(j.B.mean()), 1),
                gross_bps=round(st["mean"], 1), net_bps=round(st["mean"] - cost, 1),
                net_bps_stress28=round(st["mean"] - stress, 1),
                t_stat_declustered=round(st["t"], 2) if np.isfinite(st["t"]) else None,
                bootstrap_ci95=[round(ci[0], 1), round(ci[1], 1)] if np.isfinite(ci[0]) else None,
                n_required=round(nr, 1) if np.isfinite(nr) else None,
                event_rate_per_week_6m=round(rate, 3),
                eta_forward_confirmation=dict(days=round(e["eta_days"], 0) if np.isfinite(e["eta_days"]) else None,
                                              years=round(e["eta_years"], 2) if np.isfinite(e["eta_years"]) else None))

for h in [24, 72, 168]:
    sub = ev[(ev.delay_h == 24) & (ev.horizon_h == h)].copy()
    sub["short_rel_bps"] = -sub["rel_bps"]
    A = sub[sub.ret_24h > 0.20]; B = sub[sub.ret_24h < 0]
    g = arm_spread(A, B, "short_rel_bps", "wave_isoweek", f"A2_LIST_D0_COND_SPREAD_h{h}h",
                   "LISTING_EVENT", "fade plus rentable apres un pump jour-0 qu'apres un dump jour-0 (A-B)")
    g["year_by_year"] = None
    res.append(g)

# ---------- A4 : taille de vague ----------
med_ws = ev.wave_size.median()
for h in [72, 168]:
    sub = ev[(ev.delay_h == 24) & (ev.horizon_h == h)].copy()
    sub["short_rel_bps"] = -sub["rel_bps"]
    A = sub[sub.wave_size >= med_ws]; B = sub[sub.wave_size < med_ws]
    g = arm_spread(A, B, "short_rel_bps", "wave_isoweek", f"A4_LIST_WAVE_SIZE_COND_h{h}h",
                   "LISTING_EVENT", f"sous-performance plus forte dans les grandes vagues (wave_size>={med_ws:.0f}) - petites")
    g["median_wave_size"] = float(med_ws)
    res.append(g)

json.dump(res, open(f"{OUT}/axisA_results.json", "w"), indent=1, default=str)
for g in res:
    print(f"{g['id']:<44} n_raw={g.get('n_raw'):>5} L3={g.get('n_independent_L3'):>4} "
          f"gross={g.get('gross_bps')!s:>8} netLS={g.get('net_bps')!s:>8} t={g.get('t_stat_declustered')!s:>6} "
          f"ETAy={(g.get('eta_forward_confirmation') or {}).get('years')!s:>8} yrs={g.get('years_same_sign')}")
