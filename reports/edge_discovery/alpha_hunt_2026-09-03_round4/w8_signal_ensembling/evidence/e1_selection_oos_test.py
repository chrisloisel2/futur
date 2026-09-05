"""W8 - OUT-OF-SAMPLE TEST OF THE SLEEVE SELECTION IN `BESTOFBREED`.

Protocol frozen in PREREGISTRATION.md, ADDENDUM 2026-09-05 (b). One thing is tested: the
SELECTION. RULE-S ("among the four Track A sleeve candidates, take the highest SR_ann") is
applied on TRAIN only, and the sleeve it picks is evaluated on EVAL, which RULE-S never saw.
Placebo: RULE-S applied to random sleeves, same EVAL period, 400 draws.

No parameter is re-tuned. Everything except the split is imported unchanged.
"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import causal_quantile_threshold, decluster_L1, _utc, COST_RT, OUTDIR
from c1_track_c_cross_basis import align, combine, gate_daily, verdict, COST_STRESS
from d1_placebo_hour_stratified import build_scores
from a1_track_a_ensemble import APRIORI


def add_apriori_score(df, scores):
    """d1.build_scores returns the two walk-forward composites; RULE-S's candidate set also
    contains the a-priori one. Built here exactly as in a2/c0 (equal weights, frozen signs)."""
    sigs = json.load(open(os.path.join(OUTDIR, "a1_signals.json")))["signals"]
    zmat = np.load(os.path.join(OUTDIR, "a1_zmat.npy"))
    apr = np.array([APRIORI[s] for s in sigs], float)
    K = len(sigs)
    z = np.where(np.isfinite(zmat), zmat, np.nan) * np.tile(apr, (len(zmat), 1))
    n_ok = np.isfinite(z).sum(axis=1)
    out = np.nansum(np.where(np.isfinite(z), z, 0.0), axis=1) / np.maximum(n_ok, 1)
    out[n_ok < 0.6 * K] = np.nan
    scores["EW_APRIORI"] = out
    return scores

TRAIN_END = pd.Timestamp("2025-02-28")
EVAL_START = pd.Timestamp("2025-03-01")
N_DRAWS = 400
RNG = np.random.default_rng(20260907)

# RULE-S candidate set, frozen: exactly the four Track A sleeves of the Track C grid
CANDIDATES = [("EW_APRIORI", 0.90), ("EW_WALKFORWARD", 0.90),
              ("EW_WALKFORWARD", 0.80), ("CONFIDENCE_IC_WF", 0.80)]
B_LEGS = ["BLO_AMIHUD_30D", "BLO_MOM_30D"]


def hour_cell_means(net, hour, mo, pop):
    cell = pd.DataFrame({"h": hour, "m": mo, "x": net})
    mu = cell[pop].groupby(["h", "m"])["x"].mean()
    base = mu.reindex(pd.MultiIndex.from_arrays([hour, mo])).values
    return np.where(np.isfinite(base), base, float(net[pop].mean()))


def sleeve_series(df, y, mask, base, cost):
    t = df["event_time"].values[mask]
    keep = decluster_L1(t, df["symbol"].values[mask])
    d = pd.to_datetime(_utc(t[keep]).date)
    adj = pd.Series(((y - cost) - base)[mask][keep]).groupby(d).mean()
    return adj


def sr_ann(x, opy):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / x.std(ddof=1) * np.sqrt(opy))


def main():
    df, mo, y, scores = build_scores()
    scores = add_apriori_score(df, scores)
    hour = df["event_time"].dt.hour.values
    res = {"protocol": "PREREGISTRATION.md ADDENDUM 2026-09-05 (b)",
           "train": ["2023-01-02", str(TRAIN_END.date())],
           "eval": [str(EVAL_START.date()), "2026-06-26"],
           "rule_S": "argmax SR_ann over the 4 frozen Track A sleeve candidates (_HOURADJ)"}

    # ---- real candidate sleeves (hour-bar controlled), at both cost levels ---------------
    real14, real28, masks = {}, {}, {}
    for name, q in CANDIDATES:
        sc = scores[name]
        thr = causal_quantile_threshold(sc, mo, q)
        pop = np.isfinite(thr)
        m = np.isfinite(sc) & pop & (sc >= thr)
        lbl = f"A_{name}_q{int(q*100)}_HOURADJ"
        masks[lbl] = (m, pop, q)
        for cost, store in ((COST_RT, real14), (COST_STRESS, real28)):
            base = hour_cell_means(y - cost, hour, mo, pop)
            store[lbl] = sleeve_series(df, y, m, base, cost)

    b = pd.read_parquet(os.path.join(OUTDIR, "c2_longonly_sleeves.parquet"))
    b["day"] = pd.to_datetime(b["day"])

    def to_frame(store):
        return pd.concat([pd.DataFrame({"day": s.index, "sleeve": k, "track": "A",
                                        "gross_bps": s.values + COST_RT, "turnover": np.nan,
                                        "n_episodes": 1, "cost_model": "PER_EPISODE"})
                          for k, s in store.items()], ignore_index=True)
    df14 = pd.concat([b, to_frame(real14)], ignore_index=True)
    df28 = pd.concat([b, to_frame(real28)], ignore_index=True)

    # ---- RULE-S on TRAIN ------------------------------------------------------------------
    labels = list(real14)
    M, _ = align(labels + B_LEGS, df14, COST_RT)
    opy = 365.25
    tr = M.index <= TRAIN_END
    ev = M.index >= EVAL_START
    train_sr = {k: sr_ann(M.loc[tr, k], opy) for k in labels}
    picked = max(train_sr, key=lambda k: train_sr[k])
    res["train_sr_ann_by_candidate"] = train_sr
    res["rule_S_pick_on_TRAIN"] = picked
    res["original_post_hoc_pick"] = "A_CONFIDENCE_IC_WF_q80_HOURADJ"
    res["pick_agrees_with_original"] = bool(picked == "A_CONFIDENCE_IC_WF_q80_HOURADJ")
    res["eval_sr_ann_by_candidate"] = {k: sr_ann(M.loc[ev, k], opy) for k in labels}

    # ---- C1 : does the picked sleeve pay on EVAL? ----------------------------------------
    Ms, _ = align(labels + B_LEGS, df28, COST_STRESS)
    g_sel = gate_daily(M.loc[ev, picked], f"EVAL::{picked}", opy, Ms.loc[ev, picked])
    g_sel["verdict"] = verdict(g_sel)
    res["C1_selected_sleeve_on_EVAL"] = g_sel

    # ---- C3 : the full basket on EVAL ----------------------------------------------------
    baskets = {}
    for tag, sls in {"BESTOFBREED_ruleS": [picked] + B_LEGS,
                     "BESTOFBREED_original_pick": ["A_CONFIDENCE_IC_WF_q80_HOURADJ"] + B_LEGS,
                     "B_LEGS_ONLY": B_LEGS}.items():
        Mb, _ = align(sls, df14, COST_RT)
        Mbs, _ = align(sls, df28, COST_STRESS)
        rec = {"sleeves": sls}
        for mode in ["INVVOL_WF", "EQUAL_CAPITAL"]:
            c, cs = combine(Mb, mode), combine(Mbs, mode)
            evb = c.index >= EVAL_START
            g = gate_daily(c[evb], f"EVAL::{tag}::{mode}", opy, cs[cs.index >= EVAL_START])
            if g:
                g["verdict"] = verdict(g)
            rec[mode] = g
            gtr = gate_daily(c[c.index <= TRAIN_END], f"TRAIN::{tag}::{mode}", opy,
                             cs[cs.index <= TRAIN_END])
            rec[mode + "_TRAIN_for_reference"] = {
                "net_bps": (gtr or {}).get("net_bps"),
                "sr_annualised": (gtr or {}).get("sr_annualised"),
                "eta_years": (gtr or {}).get("eta_forward_confirmation_years")}
        baskets[tag] = rec
    res["C3_baskets_on_EVAL"] = baskets

    # ---- C2 : placebo — RULE-S over random sleeves ---------------------------------------
    ev_idx = None
    pop_all = {q: np.isfinite(causal_quantile_threshold(scores[n], mo, q))
               for n, q in set(CANDIDATES)}
    month = pd.Series(mo)
    base_cache = {c: hour_cell_means(y - c, hour, mo, pop_all[0.90]) for c in (COST_RT,)}
    draws_sel, draws_basket = [], []
    for _ in range(N_DRAWS):
        cand = {}
        for i, (nm, q) in enumerate(CANDIDATES):
            pop = pop_all[q]
            idx = np.where(pop)[0]
            mm = pd.Series(mo[pop])
            keep = []
            for _, grp in pd.Series(idx).groupby(mm.values):
                v = grp.values
                k = max(1, int(round((1 - q) * len(v))))
                keep.append(RNG.choice(v, size=k, replace=False))
            m = np.zeros(len(df), bool); m[np.concatenate(keep)] = True
            cand[f"R{i}"] = sleeve_series(df, y, m, base_cache[COST_RT], COST_RT)
        Mr = pd.DataFrame(cand).reindex(M.index).fillna(0.0)
        trm, evm = Mr.index <= TRAIN_END, Mr.index >= EVAL_START
        srs = {k: sr_ann(Mr.loc[trm, k], opy) for k in Mr.columns}
        pk = max(srs, key=lambda k: (srs[k] if np.isfinite(srs[k]) else -9))
        draws_sel.append(sr_ann(Mr.loc[evm, pk], opy))
        # placebo basket: the picked random sleeve + the two real B legs
        Mb = M[B_LEGS].copy(); Mb["R"] = Mr[pk]
        c = combine(Mb[["R"] + B_LEGS], "INVVOL_WF")
        draws_basket.append(sr_ann(c[c.index >= EVAL_START], opy))
    ds = np.array(draws_sel, float); db = np.array(draws_basket, float)
    real_sel_sr = g_sel["sr_annualised"]
    res["C2_placebo"] = {
        "n_draws": N_DRAWS,
        "selected_sleeve_SR_ann_EVAL": {
            "real": real_sel_sr, "placebo_mean": float(np.nanmean(ds)),
            "placebo_sd": float(np.nanstd(ds, ddof=1)),
            "placebo_p90": float(np.nanpercentile(ds, 90)),
            "placebo_max": float(np.nanmax(ds)),
            "p_value_one_sided": float(np.nanmean(ds >= real_sel_sr))},
        "basket_SR_ann_EVAL": {
            "real": baskets["BESTOFBREED_ruleS"]["INVVOL_WF"]["sr_annualised"],
            "placebo_mean": float(np.nanmean(db)),
            "placebo_p90": float(np.nanpercentile(db, 90)),
            "p_value_one_sided": float(np.nanmean(
                db >= baskets["BESTOFBREED_ruleS"]["INVVOL_WF"]["sr_annualised"]))}}

    # ---- power / DATA_LIMITED check -------------------------------------------------------
    n_eval = int(ev.sum())
    sr_eval = baskets["BESTOFBREED_ruleS"]["INVVOL_WF"]["sr_annualised"]
    n_req = (31.4 / sr_eval ** 2) * 365.25 if sr_eval and sr_eval > 0 else None
    res["power"] = {"n_eval_days": n_eval, "eval_sr_ann": sr_eval,
                    "n_required_days_at_this_SR": n_req,
                    "eval_long_enough": bool(n_req is not None and n_eval >= n_req)}

    with open(os.path.join(OUTDIR, "e1_selection_oos_results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)

    print("TRAIN SR by candidate:", {k: round(v, 3) for k, v in train_sr.items()})
    print("RULE-S picks on TRAIN :", picked, "| agrees with original post-hoc pick:",
          res["pick_agrees_with_original"])
    print("EVAL  SR by candidate:", {k: round(v, 3) for k, v in res["eval_sr_ann_by_candidate"].items()})
    print(f"\nC1 selected sleeve on EVAL: net={g_sel['net_bps']:+.2f} n28={g_sel['net_bps_stress28']:+.2f} "
          f"t={g_sel['t_stat_declustered']:.2f} SR={g_sel['sr_annualised']:.2f} "
          f"ETA={g_sel['eta_forward_confirmation_years']} n={g_sel['n_raw']}")
    for tag, rec in baskets.items():
        g = rec["INVVOL_WF"]
        print(f"C3 {tag:28s} EVAL net={g['net_bps']:+6.2f} n28={g['net_bps_stress28']:+6.2f} "
              f"t={g['t_stat_declustered']:5.2f} SR={g['sr_annualised']:.2f} "
              f"ETA={g['eta_forward_confirmation_years']:.2f}y {g['verdict']}  "
              f"(TRAIN SR {rec['INVVOL_WF_TRAIN_for_reference']['sr_annualised']:.2f})")
    p = res["C2_placebo"]["selected_sleeve_SR_ann_EVAL"]
    print(f"\nC2 placebo (selected sleeve SR on EVAL): real={p['real']:.2f} "
          f"placebo mean={p['placebo_mean']:.2f} sd={p['placebo_sd']:.2f} p90={p['placebo_p90']:.2f} "
          f"max={p['placebo_max']:.2f}  p={p['p_value_one_sided']:.4f}")
    print("power:", res["power"])
    print("wrote e1_selection_oos_results.json")


if __name__ == "__main__":
    main()
