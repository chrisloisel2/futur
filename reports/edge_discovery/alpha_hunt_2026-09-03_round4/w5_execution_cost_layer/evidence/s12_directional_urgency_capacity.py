"""W5/s12 - the two urgency ARMS, and the capacity table with the okx contract-unit fix.

WHY TWO ARMS. s11 showed that conditioning on |5-min move| hides a sign flip. Split it:
  ADVERSE arm   : the market has just moved AGAINST the side we want (buy after a fall).
                  This is the CONTRARIAN / cascade-bounce entry the project's event alphas use.
  MOMENTUM arm  : the market has just moved WITH the side we want (buy after a rally).
                  This is the CHASE / continuation entry.
A resting order fills in both arms, but the information content of the fill is opposite, so the
adverse-selection cost is opposite. Reporting a single "urgency penalty" averages them to
roughly nothing and is the reason a naive urgency test looks harmless.

CAPACITY (H3). data/microstructure_reduced normalises okx sizes in CONTRACTS, not base units
(BTC-USDT-SWAP = 0.01 BTC, ETH = 0.1 ETH, SOL = 1 SOL). Verified against binance/HL on the same
instant and the same day. Fill logic is unaffected (book and trades share the unit inside a
venue) but every okx NOTIONAL is inflated 100x / 10x / 1x. Corrected here.
"""
import os, json, glob
import numpy as np, pandas as pd

FEE_T, FEE_M = 5.0, 2.0
RHO_A, RHO_B, RHO_FLOOR = 0.9301, -0.3095, 0.60
HAIRCUT = 1.0
OKX_CONTRACT = {"BTCUSDT": 0.01, "ETHUSDT": 0.1, "SOLUSDT": 1.0}
S = os.environ["W5_SCRATCH"]
out = {}


def rho(sp):
    return float(np.clip(RHO_A + RHO_B * sp, RHO_FLOOR, 1.0))


# ---------------- A. two arms on the 15-symbol / 7-week probe panel ----------------------
p = pd.read_parquet(f"{S}/panel.parquet")
p["date"] = p.date.astype(str)
q = pd.concat([
    p.assign(side="BUY",  adv=p.adv_buy,  ttf=p.ttf_buy,  toward=p.ret_5m),
    p.assign(side="SELL", adv=p.adv_sell, ttf=p.ttf_sell, toward=-p.ret_5m)], ignore_index=True)
q = q[np.isfinite(q.toward) & np.isfinite(q.spread_bps)]
q["toward_bps"] = q.toward * 1e4          # >0 : the market has already moved OUR way (chase)

arms = []
for arm, Q in [("MOMENTUM_chase", 0.999), ("MOMENTUM_chase", 0.99),
               ("ADVERSE_contrarian", 0.999), ("ADVERSE_contrarian", 0.99)]:
    if arm.startswith("MOM"):
        thr = q.groupby("symbol").toward_bps.transform(lambda x: x.quantile(Q))
        e = q[q.toward_bps >= thr]
    else:
        thr = q.groupby("symbol").toward_bps.transform(lambda x: x.quantile(1 - Q))
        e = q[q.toward_bps <= thr]
    lo = q.groupby("symbol").toward_bps.transform(lambda x: x.quantile(0.25))
    hi = q.groupby("symbol").toward_bps.transform(lambda x: x.quantile(0.75))
    b = q[(q.toward_bps > lo) & (q.toward_bps < hi)]          # quiet middle 50% = baseline
    per = []
    for sym in sorted(q.symbol.unique()):
        ee, bb = e[e.symbol == sym], b[b.symbol == sym]
        if len(ee) < 30: continue
        sp_e, sp_b = float(ee.spread_bps.median()), float(bb.spread_bps.median())
        r_ = rho(sp_b)
        as_e, as_b = r_ * (sp_e / 2 - ee.adv.mean()), r_ * (sp_b / 2 - bb.adv.mean())
        cm_e = 2 * (-sp_e / 2 + FEE_M + as_e) + 2 * HAIRCUT
        cm_b = 2 * (-sp_b / 2 + FEE_M + as_b) + 2 * HAIRCUT
        ct_e, ct_b = 2 * (sp_e / 2 + FEE_T), 2 * (sp_b / 2 + FEE_T)
        per.append(dict(symbol=sym, spread_mult=sp_e / sp_b, AS_base=as_b, AS_evt=as_e,
                        dAS=as_e - as_b, fill60_base=float((bb.ttf <= 60).mean()),
                        fill60_evt=float((ee.ttf <= 60).mean()),
                        cost_maker_rt_base=cm_b, cost_maker_rt_evt=cm_e, maker_pen_rt=cm_e - cm_b,
                        cost_taker_rt_evt=ct_e, taker_pen_rt=ct_e - ct_b, n=len(ee)))
    PU = pd.DataFrame(per)
    # decluster: cells = (symbol, day); block bootstrap blocks = day
    ce = e.groupby(["symbol", "date"]).agg(adv=("adv", "mean"), spr=("spread_bps", "median")).reset_index()
    cb = b.groupby(["symbol", "date"]).agg(adv_b=("adv", "mean"), spr_b=("spread_bps", "median")).reset_index()
    j = ce.merge(cb, on=["symbol", "date"]).dropna()
    j["rho"] = j.spr_b.map(rho)
    j["dAS"] = j.rho * ((j.spr / 2 - j.adv) - (j.spr_b / 2 - j.adv_b))
    j["maker_pen_rt"] = 2 * (j.dAS - (j.spr - j.spr_b) / 2)
    rng = np.random.default_rng(12); days = j.date.unique()
    bs = [np.concatenate([j.loc[j.date == d, "maker_pen_rt"].values
                          for d in rng.choice(days, len(days), True)]).mean() for _ in range(2000)]
    m, sd, n = j.maker_pen_rt.mean(), j.maker_pen_rt.std(ddof=1), len(j)
    rec = dict(arm=arm, quantile=Q, n_raw=int(len(e)), n_ind_L1_symbol_day=int(n),
               n_ind_L2_day=int(j.date.nunique()), n_ind_L3_symbol=int(j.symbol.nunique()),
               spread_mult_median=float(PU.spread_mult.median()),
               dAS_mean=float(j.dAS.mean()),
               maker_penalty_rt_mean=float(m),
               maker_penalty_rt_t_declustered=float(m / (sd / np.sqrt(n))),
               maker_penalty_rt_ci95=[float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
               taker_penalty_rt_median=float(PU.taker_pen_rt.median()),
               fill60_base_median=float(PU.fill60_base.median()),
               fill60_evt_median=float(PU.fill60_evt.median()),
               per_symbol=PU.round(3).to_dict("records"))
    arms.append(rec)
    print(f"\n[{arm}] top {(1-Q)*100 if arm.startswith('MOM') else (1-Q)*100:.1f}% move "
          f"(q={Q}) n_raw={rec['n_raw']} cells={n} days={rec['n_ind_L2_day']}")
    print(f"   spread x{rec['spread_mult_median']:.2f} | fill60 {rec['fill60_base_median']:.2f}->{rec['fill60_evt_median']:.2f} "
          f"| dAS={rec['dAS_mean']:+.2f} | MAKER penalty RT={m:+.2f} t={rec['maker_penalty_rt_t_declustered']:.1f} "
          f"CI[{bs and np.percentile(bs,2.5):.2f},{np.percentile(bs,97.5):.2f}] | TAKER penalty RT={rec['taker_penalty_rt_median']:+.2f}")
    print(PU[["symbol", "spread_mult", "AS_base", "AS_evt", "dAS", "fill60_base", "fill60_evt",
              "maker_pen_rt", "cost_maker_rt_evt", "cost_taker_rt_evt"]].round(2).to_string())
out["urgency_two_arms"] = arms

# ---------------- B. capacity (H3) with the okx contract fix ----------------------------
d = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(f"{S}/qsim2_*.parquet"))], ignore_index=True)
d = d[d.date != "2026-08-31"]
mult = np.where(d.venue == "okx", d.symbol.map(OKX_CONTRACT).astype(float), 1.0)
d["notional_usd"] = d.notional0 * mult
cap = []
for (v, s_), g in d.groupby(["venue", "symbol"]):
    qs = g.notional_usd.quantile([0.05, 0.25, 0.5, 0.75]).to_dict()
    sp = float(g.s0_bps.mean())
    # clip that stays inside the touch 75% of the time = the p25 of top-of-book notional
    cap.append(dict(venue=v, symbol=s_, spread_bps=round(sp, 3),
                    tob_p05=round(qs[0.05]), tob_p25=round(qs[0.25]),
                    tob_med=round(qs[0.5]), tob_p75=round(qs[0.75]),
                    unit_fixed=(v == "okx")))
CAP = pd.DataFrame(cap)
print("\n=== H3 top-of-book capacity, USD, okx contract-unit CORRECTED ===")
print(CAP.to_string())
out["h3_capacity_corrected"] = CAP.to_dict("records")

# capacity during the shock (real book): top-of-book halves
d["adverse_shock"] = -d.shock5_bps
t99 = d.adverse_shock.quantile(0.99)
sh = {"tob_med_baseline_usd": float(d.loc[d.adverse_shock < d.adverse_shock.quantile(.5), "notional_usd"].median()),
      "tob_med_shock99_usd": float(d.loc[d.adverse_shock >= t99, "notional_usd"].median())}
sh["ratio"] = sh["tob_med_shock99_usd"] / sh["tob_med_baseline_usd"]
out["h3_capacity_under_shock"] = sh
print(f"\ntop-of-book notional: baseline ${sh['tob_med_baseline_usd']:,.0f} -> "
      f"top-1% adverse shock ${sh['tob_med_shock99_usd']:,.0f}  (x{sh['ratio']:.2f})")

json.dump(out, open(f"{S}/directional_urgency_capacity.json", "w"), indent=1, default=float)
print("\nwrote", f"{S}/directional_urgency_capacity.json")
