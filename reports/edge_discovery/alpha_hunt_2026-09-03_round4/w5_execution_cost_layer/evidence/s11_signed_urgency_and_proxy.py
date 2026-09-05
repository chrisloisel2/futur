"""W5/s11 - (a) H4 redone with a SIGNED shock, (b) H6 the PIT high-low spread proxy.

(a) BUG FOUND AND FIXED. s05/s10 conditioned the urgency test on |5-min return|. That blends
    the case where the market just ran UP into our BUY (favourable) with the case where it ran
    DOWN into our BUY (the cascade case the project's event alphas actually trade), so the two
    cancel and the urgency penalty reads as roughly zero until the extreme tail. Here the shock
    is signed AGAINST the side: for a BUY the event is a large NEGATIVE trailing 5-min return,
    for a SELL a large POSITIVE one. Both sides are then pooled as "adverse trailing move",
    which is exactly the state an event/cascade alpha fires in.

    Declustering: the reported effect is computed per (symbol, UTC day) cell (L3), the t-stat is
    over cells, and the CI95 is a block bootstrap with blocks = calendar day (L2).

(b) H6. Corwin-Schultz (2012) and Abdi-Ranaldo (2017) high-low spread estimators on the 1h
    enriched bars, compared cross-sectionally against the spread measured by the probe on the
    same window. Pre-set threshold: usable iff Spearman > 0.6.
"""
import os, json, glob
import numpy as np, pandas as pd, duckdb
from scipy.stats import spearmanr

FEE_T, FEE_M = 5.0, 2.0
RHO_A, RHO_B, RHO_FLOOR = 0.9301, -0.3095, 0.60      # bridge from s10
HAIRCUT = 1.0
S = os.environ["W5_SCRATCH"]
ROOT = "/home/qbee/futur"
out = {}


def rho(sp):
    return float(np.clip(RHO_A + RHO_B * sp, RHO_FLOOR, 1.0))


# ============================ (a) SIGNED URGENCY =========================================
p = pd.read_parquet(f"{S}/panel.parquet")
p["date"] = p.date.astype(str)
# adverse trailing move, per side: BUY suffers a fall, SELL suffers a rise
long_ = p.assign(side="BUY",  adv=p.adv_buy,  ttf=p.ttf_buy,  fill=p.fill_buy,  adverse=-p.ret_5m)
short_ = p.assign(side="SELL", adv=p.adv_sell, ttf=p.ttf_sell, fill=p.fill_sell, adverse=p.ret_5m)
q = pd.concat([long_, short_], ignore_index=True)
q = q[np.isfinite(q.adverse) & np.isfinite(q.spread_bps)]
q["adverse_bps"] = q.adverse * 1e4

rows, detail = [], []
for Q in [0.90, 0.99, 0.999]:
    thr = q.groupby("symbol").adverse_bps.transform(lambda x: x.quantile(Q))
    e = q[q.adverse_bps >= thr]
    b = q[q.adverse_bps < q.groupby("symbol").adverse_bps.transform(lambda x: x.quantile(0.5))]
    per = []
    for sym in sorted(q.symbol.unique()):
        ee, bb = e[e.symbol == sym], b[b.symbol == sym]
        if len(ee) < 30:
            continue
        sp_e, sp_b = float(ee.spread_bps.median()), float(bb.spread_bps.median())
        r_ = rho(sp_b)
        as_e = r_ * (sp_e / 2 - ee.adv.mean())
        as_b = r_ * (sp_b / 2 - bb.adv.mean())
        f_e, f_b = float((ee.ttf <= 60).mean()), float((bb.ttf <= 60).mean())
        cm_e = 2 * (-sp_e / 2 + FEE_M + as_e) + 2 * HAIRCUT
        cm_b = 2 * (-sp_b / 2 + FEE_M + as_b) + 2 * HAIRCUT
        ct_e, ct_b = 2 * (sp_e / 2 + FEE_T), 2 * (sp_b / 2 + FEE_T)
        per.append(dict(quantile=Q, symbol=sym, spread_base=sp_b, spread_evt=sp_e,
                        spread_mult=sp_e / sp_b, AS_base=as_b, AS_evt=as_e, dAS=as_e - as_b,
                        fill60_base=f_b, fill60_evt=f_e, dfill_rel=f_e / f_b - 1,
                        cost_taker_rt_base=ct_b, cost_taker_rt_evt=ct_e, taker_pen_rt=ct_e - ct_b,
                        cost_maker_rt_base=cm_b, cost_maker_rt_evt=cm_e, maker_pen_rt=cm_e - cm_b,
                        n_evt=len(ee), n_evt_symday=int(ee.date.nunique())))
    PU = pd.DataFrame(per)
    detail.append(PU)
    # declustering: cells = (symbol, day) for the event side; L2 blocks = day
    cells = e.groupby(["symbol", "date"]).apply(
        lambda g: pd.Series({"adv": g.adv.mean(), "spread": g.spread_bps.median(),
                             "fill60": (g.ttf <= 60).mean()})).reset_index()
    base_cells = b.groupby(["symbol", "date"]).apply(
        lambda g: pd.Series({"adv_b": g.adv.mean(), "spread_b": g.spread_bps.median(),
                             "fill60_b": (g.ttf <= 60).mean()})).reset_index()
    j = cells.merge(base_cells, on=["symbol", "date"]).dropna()
    j["rho"] = j.spread_b.map(rho)
    j["dAS"] = j.rho * ((j.spread / 2 - j.adv) - (j.spread_b / 2 - j.adv_b))
    j["maker_pen_rt"] = 2 * (j.dAS - (j.spread - j.spread_b) / 2)
    j["taker_pen_rt"] = (j.spread - j.spread_b)
    rng = np.random.default_rng(11); days = j.date.unique()
    def boot(col):
        bs = [np.concatenate([j.loc[j.date == d, col].values
                              for d in rng.choice(days, len(days), True)]).mean() for _ in range(2000)]
        return [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    rec = {"quantile": Q, "n_raw": int(len(e)),
           "n_ind_L1_symbol_day": int(j.groupby(["symbol", "date"]).ngroups),
           "n_ind_L2_day": int(j.date.nunique()), "n_ind_L3_symbol": int(j.symbol.nunique()),
           "spread_mult_median": float(PU.spread_mult.median()),
           "dfill60_rel_median": float(PU.dfill_rel.median())}
    for c in ["dAS", "maker_pen_rt", "taker_pen_rt"]:
        m, sd, n = j[c].mean(), j[c].std(ddof=1), len(j)
        rec[f"{c}_mean"] = float(m)
        rec[f"{c}_t_declustered"] = float(m / (sd / np.sqrt(n)))
        rec[f"{c}_ci95"] = boot(c)
    rows.append(rec)
    print(f"\n[SIGNED] adverse-move q={Q}: n_raw={rec['n_raw']} cells={rec['n_ind_L1_symbol_day']} "
          f"days={rec['n_ind_L2_day']}")
    print(f"    spread x{rec['spread_mult_median']:.2f} | fill60 {rec['dfill60_rel_median']*100:+.0f}% | "
          f"dAS={rec['dAS_mean']:+.2f}bps t={rec['dAS_t_declustered']:.1f} CI{rec['dAS_ci95']} | "
          f"maker penalty RT={rec['maker_pen_rt_mean']:+.2f} t={rec['maker_pen_rt_t_declustered']:.1f} | "
          f"taker penalty RT={rec['taker_pen_rt_mean']:+.2f}")
    print(PU[["symbol", "spread_mult", "AS_base", "AS_evt", "dAS", "fill60_base", "fill60_evt",
              "taker_pen_rt", "maker_pen_rt", "cost_taker_rt_evt", "cost_maker_rt_evt"]].round(2).to_string())

out["h4_signed_urgency"] = rows
out["h4_signed_urgency_per_symbol"] = pd.concat(detail).round(4).to_dict("records")

# ============================ (b) H6 SPREAD PROXY ========================================
print("\n=== H6: PIT high-low spread proxies vs measured spread ===")
syms = sorted(p.symbol.unique())
con = duckdb.connect(); con.execute("SET memory_limit='1500MB'; SET threads=2;")
meas = p.groupby("symbol").spread_bps.median()
pr = []
for s in syms:
    f = f"{ROOT}/data/enriched/{s}_1h_enriched.parquet"
    if not os.path.exists(f):
        pr.append(dict(symbol=s, note="NO_ENRICHED_FILE")); continue
    d = con.execute(f"""SELECT datetime, high, low, close FROM read_parquet('{f}')
                        WHERE datetime >= TIMESTAMP '2026-07-12' AND datetime < TIMESTAMP '2026-09-04'
                        ORDER BY datetime""").df()
    if len(d) < 200:
        pr.append(dict(symbol=s, note=f"ONLY_{len(d)}_BARS")); continue
    h, l, c = d.high.values, d.low.values, d.close.values
    # Corwin-Schultz (2012)
    b_ = (np.log(h[1:] / l[1:]) ** 2 + np.log(h[:-1] / l[:-1]) ** 2)
    H2 = np.maximum(h[1:], h[:-1]); L2 = np.minimum(l[1:], l[:-1])
    g_ = np.log(H2 / L2) ** 2
    k1 = 4 * np.log(2); k2 = np.sqrt(8 / np.pi)
    a_ = (np.sqrt(2 * b_) - np.sqrt(b_)) / (3 - 2 * np.sqrt(2)) - np.sqrt(g_ / (3 - 2 * np.sqrt(2)))
    cs = 2 * (np.exp(a_) - 1) / (1 + np.exp(a_))
    cs = np.where(cs < 0, 0.0, cs)
    # Abdi-Ranaldo (2017)
    eta = (np.log(h) + np.log(l)) / 2
    ar = 4 * (np.log(c[:-1]) - eta[:-1]) * (np.log(c[:-1]) - eta[1:])
    ar = np.sqrt(np.maximum(ar, 0.0))
    pr.append(dict(symbol=s, n_bars=len(d),
                   cs_bps=float(np.nanmedian(cs) * 1e4), ar_bps=float(np.nanmedian(ar) * 1e4),
                   measured_bps=float(meas.get(s, np.nan))))
PR = pd.DataFrame(pr)
ok = PR.dropna(subset=["measured_bps"]) if "measured_bps" in PR else PR
print(PR.round(3).to_string())
h6 = {"n_symbols": int(len(ok))}
for est in ["cs_bps", "ar_bps"]:
    if est in ok and ok[est].notna().sum() > 3:
        r = spearmanr(ok[est], ok.measured_bps)
        rp = np.polyfit(ok[est], ok.measured_bps, 1)
        h6[est] = {"spearman": float(r.correlation), "p": float(r.pvalue),
                   "ols_slope": float(rp[0]), "ols_intercept": float(rp[1]),
                   "usable_pre_set_thr_0.6": bool(r.correlation > 0.6)}
        print(f"  {est}: Spearman(proxy, measured) = {r.correlation:.3f} (p={r.pvalue:.2e}) "
              f"-> {'USABLE' if r.correlation > 0.6 else 'NOT USABLE (pre-set 0.6)'}")
h6["table"] = PR.round(4).to_dict("records")
out["h6_spread_proxy"] = h6

json.dump(out, open(f"{S}/signed_urgency_proxy.json", "w"), indent=1, default=float)
print("\nwrote", f"{S}/signed_urgency_proxy.json")
