"""W5/s09 - the cost model: turn the queue simulator's attempts into decision-relevant
round-trip execution costs, calibrate the project's virtual probe against it, and measure the
urgency penalty on REAL book data.

COST ALGEBRA (one-way, bps, everything marked against the post-execution FAIR price, so no
double counting of the spread and no benchmark mixing):

  taker now            cost_T   = (ask0 - mid_H)/m0*1e4 + fee_taker        [BUY; mirrored for SELL]
                                ~ s0/2 + fee_taker
  maker fill at touch  cost_M   = (L - mid_{fill+H})/L*1e4 + fee_maker = -mko_H + fee_maker
                                = -s0/2 + fee_maker + AS_H,   AS_H := s0/2 - mko_H
  post TTL=T then cross
                       cost_P(T)= P_f(T) * E[-mko_H + fee_maker | ttf<=T]
                                + (1-P_f(T)) * E[(askT - mid_{T+H})/m0*1e4 + fee_taker | ttf>T]

AS_H is the adverse-selection term the brief asks for: a "free" fill that loses 3bps in 60s is
strictly worse than a 5bps taker. It is measured, not assumed.

Round trip = 2 x one-way (entry + exit symmetric; the project's own convention).
Fees: Binance USDM VIP0, taker 5.0 bps, maker 2.0 bps one-way.

Declustering: the unit of independence for a cost statistic is the (venue, symbol, UTC day)
cell (L3). t-stats and bootstrap CIs are computed on cell means, never on the 5660 correlated
attempts inside a day.
"""
import os, sys, glob, json
import numpy as np, pandas as pd

FEE_T, FEE_M = 5.0, 2.0
H_MARK   = 60                                   # adverse-selection horizon (bps of fair-price drift)
TTLS     = [1, 5, 10, 30, 60, 120, 300, 600]
GRID     = [1, 5, 10, 30, 60, 120, 300, 600, 900]
RULES    = ["trav", "k00", "k05", "k10", "k20"]


def load_all(S):
    fs = sorted(glob.glob(f"{S}/qsim2_*.parquet"))
    d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    d = d[d.date != "2026-08-31"]               # 50 minutes only: not a day, would corrupt the L3 cell
    d["sgn"] = np.where(d.side == "BUY", 1.0, -1.0)
    return d


def cross_cost(d, T):
    """Cost of crossing the spread at t0+T, marked against the fair price T+H_MARK later."""
    u_fair = min([g for g in GRID if g >= T + H_MARK], default=GRID[-1])
    px = np.where(d.side == "BUY", d[f"ask_{T}"], d[f"bid_{T}"])
    return d.sgn * (px - d[f"mid_{u_fair}"]) / d.m0 * 1e4 + FEE_T


def taker_now_cost(d):
    px = np.where(d.side == "BUY", d.ask_1 * 0 + d.ask_1, d.bid_1)   # ask_1/bid_1 ~ arrival quote
    # arrival quote is not stored directly; rebuild it from m0 and s0
    half = d.s0_bps / 2.0
    return half + FEE_T - d.sgn * (d[f"mid_{H_MARK}"] / d.m0 - 1.0) * 1e4


def block_boot(cells, n=4000, seed=7):
    """Block bootstrap on the L3 cell means, blocks = calendar day (all venues/symbols of a day
    move together, so the day is the honest block)."""
    rng = np.random.default_rng(seed)
    days = cells.date.unique()
    out = []
    for _ in range(n):
        pick = rng.choice(days, size=len(days), replace=True)
        v = np.concatenate([cells.loc[cells.date == p, "v"].values for p in pick])
        out.append(np.nanmean(v))
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def cell_stat(d, col, tag, res, boot=True):
    c = d.groupby(["venue", "symbol", "date"])[col].mean().reset_index().rename(columns={col: "v"})
    c = c[np.isfinite(c.v)]
    m, s, n = c.v.mean(), c.v.std(ddof=1), len(c)
    r = {"mean": float(m), "sd_cells": float(s), "n_cells_L3": int(n),
         "t_declustered": float(m / (s / np.sqrt(n))) if s > 0 and n > 1 else None,
         "n_raw": int(d[col].notna().sum()),
         "n_ind_L1_symbol_day": int(d.groupby(["symbol", "date"]).ngroups),
         "n_ind_L2_day": int(d.date.nunique())}
    if boot:
        r["bootstrap_ci95"] = list(block_boot(c))
    res[tag] = r
    return r


def main():
    S = os.environ["W5_SCRATCH"]
    OUT = sys.argv[1] if len(sys.argv) > 1 else f"{S}/cost_model.json"
    d = load_all(S)
    res = {"meta": {"n_attempts": int(len(d)),
                    "dates": sorted(d.date.unique().tolist()),
                    "venues": sorted(d.venue.unique().tolist()),
                    "symbols": sorted(d.symbol.unique().tolist()),
                    "fee_taker_bps": FEE_T, "fee_maker_bps": FEE_M,
                    "adverse_selection_horizon_s": H_MARK}}

    # ---------------- 1. adverse selection curve (the brief's first decisive point) -----------
    d["cost_taker"] = taker_now_cost(d)
    as_curve = {}
    for r in RULES:
        row = {}
        for h in [1, 10, 60, 300]:
            mk = d[f"mko_{r}_{h}"]
            row[f"mko_{h}s"] = float(mk.mean())
            row[f"AS_{h}s"] = float((d.s0_bps / 2.0 - mk).mean())
        row["fill_rate_ttl600"] = float(d[f"fill_{r}"].mean())
        row["ttf_median_s"] = float(d[f"ttf_{r}"].median())
        d[f"costM_{r}"] = -d[f"mko_{r}_{H_MARK}"] + FEE_M
        row["cost_maker_oneway_bps"] = float(d[f"costM_{r}"].mean())
        row["cost_taker_oneway_bps"] = float(d.cost_taker.mean())
        row["maker_adv_oneway_bps"] = row["cost_taker_oneway_bps"] - row["cost_maker_oneway_bps"]
        as_curve[r] = row
    res["h2_adverse_selection_and_cost"] = as_curve

    # per venue/symbol under the conservative-realistic rule k10 and the haircut rule k20
    per = []
    for (v, s_), g in d.groupby(["venue", "symbol"]):
        e = {"venue": v, "symbol": s_, "spread_bps": float(g.s0_bps.mean()),
             "top_of_book_notional_usd_med": float(g.notional0.median()),
             "cost_taker_rt": float(2 * g.cost_taker.mean())}
        for r in ["trav", "k00", "k10", "k20"]:
            e[f"fill600_{r}"] = float(g[f"fill_{r}"].mean())
            e[f"AS60_{r}"] = float((g.s0_bps / 2 - g[f"mko_{r}_60"]).mean())
            e[f"cost_maker_rt_{r}"] = float(2 * (-g[f"mko_{r}_60"] + FEE_M).mean())
        per.append(e)
    res["h2_per_venue_symbol"] = per

    # declustered significance of the maker-vs-taker difference (k10 and k20)
    for r in ["k10", "k20"]:
        d[f"adv_{r}"] = d.cost_taker - d[f"costM_{r}"]
        cell_stat(d, f"adv_{r}", f"h2_maker_advantage_oneway_{r}", res)

    # ---------------- 2. TTL / policy cost: what waiting actually costs -----------------------
    pol = {}
    for T in TTLS:
        cc = cross_cost(d, T)
        row = {"ttl_s": T}
        for r in ["trav", "k10", "k20"]:
            f = d[f"ttf_{r}"] <= T
            pf = float(f.mean())
            cm = float((-d.loc[f, f"mko_{r}_{H_MARK}"] + FEE_M).mean()) if f.any() else np.nan
            cx = float(cc[~f].mean()) if (~f).any() else np.nan
            tot = pf * cm + (1 - pf) * cx
            row[f"pfill_{r}"] = pf
            row[f"cost_fill_leg_{r}"] = cm
            row[f"cost_cross_leg_{r}"] = cx
            row[f"cost_policy_oneway_{r}"] = float(tot)
            row[f"cost_policy_rt_{r}"] = float(2 * tot)
        row["cost_taker_now_rt"] = float(2 * d.cost_taker.mean())
        pol[str(T)] = row
    res["h2_policy_cost_by_ttl"] = pol

    # ---------------- 3. probe calibration (how wrong is data/execution_probe?) ---------------
    cal = {"fill_rate_probe_rule": float(d.fill_trav.mean()),
           "fill_rate_queue_k10": float(d.fill_k10.mean()),
           "fill_rate_queue_k20": float(d.fill_k20.mean()),
           "fill_rate_queue_k00": float(d.fill_k00.mean()),
           "mko60_probe_rule": float(d.mko_trav_60.mean()),
           "mko60_queue_k10": float(d.mko_k10_60.mean()),
           "mko60_queue_k20": float(d.mko_k20_60.mean()),
           "ttf_median_probe_rule_s": float(d.ttf_trav.median()),
           "ttf_median_queue_k10_s": float(d.ttf_k10.median())}
    cal["probe_markout_bias_bps"] = cal["mko60_probe_rule"] - cal["mko60_queue_k10"]
    cal["probe_fillrate_bias"] = cal["fill_rate_probe_rule"] - cal["fill_rate_queue_k10"]
    d["cal_gap"] = d.mko_trav_60 - d.mko_k10_60
    cell_stat(d, "cal_gap", "h2_probe_markout_bias_declustered", res)
    res["h2_probe_calibration"] = cal

    # ---------------- 4. URGENCY (H4) on real book: condition on the trailing 5-min shock ----
    d["absshock_q"] = d.groupby(["venue", "symbol"]).absshock5_bps.transform(
        lambda x: pd.qcut(x.rank(method="first"), 10, labels=False))
    urg = []
    for q, g in d.groupby("absshock_q"):
        e = {"shock_decile": int(q), "n": int(len(g)),
             "abs_shock_5m_bps": float(g.absshock5_bps.mean()),
             "spread_bps": float(g.s0_bps.mean()),
             "top_of_book_notional_usd_med": float(g.notional0.median()),
             "cost_taker_rt": float(2 * g.cost_taker.mean())}
        for r in ["k10", "k20"]:
            for T in [10, 60, 600]:
                e[f"pfill_{T}s_{r}"] = float((g[f"ttf_{r}"] <= T).mean())
            e[f"AS60_{r}"] = float((g.s0_bps / 2 - g[f"mko_{r}_60"]).mean())
            e[f"cost_maker_rt_{r}"] = float(2 * (-g[f"mko_{r}_60"] + FEE_M).mean())
            cc = cross_cost(g, 60); f = g[f"ttf_{r}"] <= 60
            pf = float(f.mean())
            e[f"cost_policy60_rt_{r}"] = float(2 * (pf * (-g.loc[f, f"mko_{r}_60"] + FEE_M).mean()
                                                    + (1 - pf) * cc[~f].mean()))
        urg.append(e)
    res["h4_urgency_shock_deciles"] = urg

    # ADVERSE-SIDE shock: for a BUY, a trailing DOWN move is the cascade case the alphas trade
    d["adverse_shock"] = -d.shock5_bps        # >0 = the market just moved against the side we want
    thr = d.adverse_shock.quantile([0.9, 0.99, 0.999]).to_dict()
    tail = {}
    for p, t in thr.items():
        g = d[d.adverse_shock >= t]; b = d[d.adverse_shock < d.adverse_shock.quantile(0.5)]
        e = {"quantile": float(p), "threshold_bps": float(t), "n_raw": int(len(g)),
             "n_ind_L1_symbol_day": int(g.groupby(["symbol", "date"]).ngroups),
             "n_ind_L3_venue_symbol_day": int(g.groupby(["venue", "symbol", "date"]).ngroups),
             "spread_evt": float(g.s0_bps.mean()), "spread_base": float(b.s0_bps.mean()),
             "spread_mult": float(g.s0_bps.mean() / b.s0_bps.mean()),
             "tob_notional_evt": float(g.notional0.median()),
             "tob_notional_base": float(b.notional0.median()),
             "tob_notional_mult": float(g.notional0.median() / b.notional0.median())}
        for r in ["k10", "k20"]:
            e[f"pfill_60s_evt_{r}"] = float((g[f"ttf_{r}"] <= 60).mean())
            e[f"pfill_60s_base_{r}"] = float((b[f"ttf_{r}"] <= 60).mean())
            e[f"AS60_evt_{r}"] = float((g.s0_bps / 2 - g[f"mko_{r}_60"]).mean())
            e[f"AS60_base_{r}"] = float((b.s0_bps / 2 - b[f"mko_{r}_60"]).mean())
            e[f"dAS60_{r}"] = e[f"AS60_evt_{r}"] - e[f"AS60_base_{r}"]
            ccg = cross_cost(g, 60); fg = g[f"ttf_{r}"] <= 60; pg = float(fg.mean())
            ccb = cross_cost(b, 60); fb = b[f"ttf_{r}"] <= 60; pb = float(fb.mean())
            e[f"cost_policy60_rt_evt_{r}"] = float(2 * (pg * (-g.loc[fg, f"mko_{r}_60"] + FEE_M).mean()
                                                        + (1 - pg) * ccg[~fg].mean()))
            e[f"cost_policy60_rt_base_{r}"] = float(2 * (pb * (-b.loc[fb, f"mko_{r}_60"] + FEE_M).mean()
                                                         + (1 - pb) * ccb[~fb].mean()))
            e[f"urgency_penalty_rt_{r}"] = e[f"cost_policy60_rt_evt_{r}"] - e[f"cost_policy60_rt_base_{r}"]
        e["cost_taker_rt_evt"] = float(2 * g.cost_taker.mean())
        e["cost_taker_rt_base"] = float(2 * b.cost_taker.mean())
        e["urgency_penalty_taker_rt"] = e["cost_taker_rt_evt"] - e["cost_taker_rt_base"]
        tail[f"p{p}"] = e
    res["h4_adverse_shock_tail"] = tail

    # declustered t on the taker urgency penalty (top-decile shock vs rest), cells = L3
    d["_top"] = d.adverse_shock >= d.adverse_shock.quantile(0.99)
    a = d[d._top].groupby(["venue", "symbol", "date"]).cost_taker.mean()
    bb = d[~d._top].groupby(["venue", "symbol", "date"]).cost_taker.mean()
    j = pd.concat([a.rename("e"), bb.rename("b")], axis=1).dropna()
    j["v"] = 2 * (j.e - j.b)
    j = j.reset_index()
    m, sd, n = j.v.mean(), j.v.std(ddof=1), len(j)
    res["h4_taker_urgency_penalty_declustered"] = {
        "mean_rt_bps": float(m), "n_cells_L3": int(n),
        "t_declustered": float(m / (sd / np.sqrt(n))), "bootstrap_ci95": list(block_boot(j))}

    # ---------------- 5. H3 capacity from top-of-book notional --------------------------------
    cap = []
    for (v, s_), g in d.groupby(["venue", "symbol"]):
        q = g.notional0.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).to_dict()
        cap.append({"venue": v, "symbol": s_,
                    "tob_notional_usd": {str(k): float(x) for k, x in q.items()},
                    "spread_bps": float(g.s0_bps.mean()),
                    "clip_at_tob_p25_usd": float(q[0.25])})
    res["h3_top_of_book_capacity"] = cap

    json.dump(res, open(OUT, "w"), indent=1, default=float)
    print(json.dumps({k: res[k] for k in ["meta", "h2_probe_calibration",
                                          "h2_maker_advantage_oneway_k10",
                                          "h4_taker_urgency_penalty_declustered"]},
                     indent=1, default=float))
    print("\n=== cost by rule (one-way bps) ===")
    print(pd.DataFrame(res["h2_adverse_selection_and_cost"]).T.round(3).to_string())
    print("\n=== policy cost by TTL (round-trip bps) ===")
    print(pd.DataFrame(pol).T[["ttl_s", "pfill_k10", "cost_policy_rt_k10",
                               "pfill_k20", "cost_policy_rt_k20", "cost_taker_now_rt"]].round(3).to_string())
    print("\n=== per venue/symbol ===")
    print(pd.DataFrame(per).round(3).to_string())
    print("\n=== urgency: adverse-shock tail ===")
    print(pd.DataFrame(tail).T.round(3).to_string())
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
