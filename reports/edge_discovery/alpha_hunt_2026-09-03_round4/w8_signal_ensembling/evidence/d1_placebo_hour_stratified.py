"""W8 / audit - PLACEBO test for the W9 bias (intraday observations judged against a
calendar-day-level control).

Only ONE of this worker's three sleeves is an intraday-event sleeve: Track A (LONG 4h at
event_time). Track B legs are close-to-close daily returns judged against the SAME-DAY
cross-section, with an identical clock interval for every symbol, so a random score there has
an exactly zero expected excess by construction.

This script therefore attacks Track A, with two placebos:
  P1 UNSTRATIFIED : a uniform random score, same causal quantile threshold, same L1
                    declustering, same daily aggregation.
  P2 HOUR x MONTH STRATIFIED : random events drawn so that the placebo reproduces EXACTLY the
                    hour-of-day and calendar-month histogram of the real selection. If the
                    real edge were an hour-of-day drift artefact (the W9 mechanism), P2 would
                    reproduce it.
Reports the placebo distribution and the one-sided empirical p-value of the real statistic.
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from a1_track_a_ensemble import (APRIORI, causal_quantile_threshold, decluster_L1, _utc,
                                 BURN_EVENTS, COST_RT, OUTDIR)

N_DRAWS = 400
RNG = np.random.default_rng(20260905)


def build_scores():
    df = pd.read_parquet(os.path.join(OUTDIR, "a1_events.parquet"))
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df.sort_values("event_time").reset_index(drop=True)
    sigs = json.load(open(os.path.join(OUTDIR, "a1_signals.json")))["signals"]
    zmat = np.load(os.path.join(OUTDIR, "a1_zmat.npy"))
    wfs = np.load(os.path.join(OUTDIR, "a1_wfsign.npy"))
    K = len(sigs)
    mo = df["event_time"].dt.to_period("M").astype(str).values
    y = df["y_bps"].values

    def comp(sign_mat, W=None):
        s = np.where(np.isfinite(zmat), zmat, np.nan) * sign_mat
        if W is not None:
            s = s * W
        n_ok = np.isfinite(s).sum(axis=1)
        out = np.nansum(np.where(np.isfinite(s), s, 0.0), axis=1)
        if W is None:
            out = out / np.maximum(n_ok, 1)
        out[n_ok < 0.6 * K] = np.nan
        return out

    umo = pd.unique(mo)
    mi = np.array([{m: i for i, m in enumerate(umo)}[m] for m in mo])
    ep = y - COST_RT
    W_cf = np.zeros_like(zmat)
    for b in range(len(umo)):
        prior = mi < b
        if prior.sum() < BURN_EVENTS:
            continue
        cur = mi == b
        for j in range(K):
            zj = zmat[prior, j] * wfs[prior, j]
            good = np.isfinite(zj) & np.isfinite(ep[prior])
            if good.sum() < 500:
                continue
            ic = stats.spearmanr(zj[good], ep[prior][good]).correlation
            W_cf[cur, j] = max(0.0, ic if np.isfinite(ic) else 0.0)
    sw = W_cf.sum(axis=1)
    Wn = np.where(sw[:, None] > 0, W_cf / np.maximum(sw[:, None], 1e-12), np.nan)
    return df, mo, y, {"EW_WALKFORWARD": comp(wfs), "CONFIDENCE_IC_WF": comp(wfs, Wn)}


def daily_stats(df, y, mask):
    """The two statistics used in the report: episode mean and DAY-level mean (Track C unit)."""
    t = df["event_time"].values[mask]
    keep = decluster_L1(t, df["symbol"].values[mask])
    x = y[mask][keep] - COST_RT
    d = pd.Series(_utc(t[keep]).date)
    dser = pd.Series(x).groupby(d.values).mean()
    return {"episode_net_bps": float(x.mean()), "day_net_bps": float(dser.mean()),
            "n_L1": int(keep.sum()), "n_days": int(len(dser)),
            "t_day": float(dser.mean() / (dser.std(ddof=1) / np.sqrt(len(dser))))}


def main():
    df, mo, y, scores = build_scores()
    hour = df["event_time"].dt.hour.values
    out = {"n_draws": N_DRAWS,
           "note": "Track B legs are excluded by construction: same-day cross-section, "
                   "identical close-to-close clock interval for every symbol, so a random "
                   "score has exactly zero expected excess. Only Track A is tested."}

    for name, q in [("EW_WALKFORWARD", 0.90), ("CONFIDENCE_IC_WF", 0.80)]:
        sc = scores[name]
        thr = causal_quantile_threshold(sc, mo, q)
        pop = np.isfinite(thr)                      # evaluable window (post burn-in)
        real_mask = np.isfinite(sc) & pop & (sc >= thr)
        real = daily_stats(df, y, real_mask)
        pop_idx = np.where(pop)[0]

        # ---- P1 unstratified ------------------------------------------------------------
        k = int(real_mask.sum())
        p1 = []
        for _ in range(N_DRAWS):
            pick = RNG.choice(pop_idx, size=k, replace=False)
            m = np.zeros(len(df), bool); m[pick] = True
            p1.append(daily_stats(df, y, m))

        # ---- P2 hour x month stratified --------------------------------------------------
        strat_all = pd.DataFrame({"h": hour, "m": mo, "i": np.arange(len(df))})[pop]
        groups = {kk: v["i"].values for kk, v in strat_all.groupby(["h", "m"])}
        need = pd.Series(1, index=pd.MultiIndex.from_arrays(
            [hour[real_mask], mo[real_mask]])).groupby(level=[0, 1]).size()
        p2 = []
        for _ in range(N_DRAWS):
            pick = []
            for kk, cnt in need.items():
                pool = groups.get(kk)
                if pool is None or len(pool) == 0:
                    continue
                n = min(int(cnt), len(pool))
                pick.append(RNG.choice(pool, size=n, replace=False))
            pick = np.concatenate(pick)
            m = np.zeros(len(df), bool); m[pick] = True
            p2.append(daily_stats(df, y, m))

        def summarise(lst, key):
            v = np.array([d[key] for d in lst])
            return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)),
                    "p05": float(np.percentile(v, 5)), "p50": float(np.percentile(v, 50)),
                    "p95": float(np.percentile(v, 95)), "max": float(v.max())}

        rec = {"selection_quantile": q, "real": real,
               "unconditional_population_net_bps": float(y[pop].mean() - COST_RT),
               "placebo_P1_unstratified": {
                   "day_net_bps": summarise(p1, "day_net_bps"),
                   "episode_net_bps": summarise(p1, "episode_net_bps"),
                   "p_value_one_sided": float(np.mean(
                       np.array([d["day_net_bps"] for d in p1]) >= real["day_net_bps"]))},
               "placebo_P2_hour_x_month_stratified": {
                   "day_net_bps": summarise(p2, "day_net_bps"),
                   "episode_net_bps": summarise(p2, "episode_net_bps"),
                   "p_value_one_sided": float(np.mean(
                       np.array([d["day_net_bps"] for d in p2]) >= real["day_net_bps"])),
                   "share_of_real_edge_explained": float(
                       summarise(p2, "day_net_bps")["mean"] / real["day_net_bps"])}}
        out[name] = rec
        print(f"\n=== A::{name} q{int(q*100)} ===")
        print(f"  REAL      day_net={real['day_net_bps']:+7.2f} bps  episode_net="
              f"{real['episode_net_bps']:+7.2f}  t_day={real['t_day']:+.2f}  n_L1={real['n_L1']}")
        print(f"  pop mean  {rec['unconditional_population_net_bps']:+7.2f} bps (net of cost)")
        for tag, blk in [("P1 random    ", rec["placebo_P1_unstratified"]),
                         ("P2 hour-strat", rec["placebo_P2_hour_x_month_stratified"])]:
            s = blk["day_net_bps"]
            print(f"  {tag} day_net mean={s['mean']:+7.2f} sd={s['sd']:.2f} "
                  f"p95={s['p95']:+7.2f} max={s['max']:+7.2f}  p={blk['p_value_one_sided']:.4f}")
        print(f"  share of the real day-level edge reproduced by the hour-stratified placebo: "
              f"{100*rec['placebo_P2_hour_x_month_stratified']['share_of_real_edge_explained']:.1f}%")

    with open(os.path.join(OUTDIR, "d1_placebo_results.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\nwrote d1_placebo_results.json")


if __name__ == "__main__":
    main()
