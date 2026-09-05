#!/usr/bin/env python
"""T1e — Le verdict de la CIBLE 1 depend-il de la convention de ponderation ?

Motivation (declaree AVANT execution) : T1c/T1d montrent que la moyenne BRUTE
(size-weighted, = le PnL par trade reellement realise si l'on tradait tous les
events) et la moyenne par EPISODE L3 (equipondere, = l'unite d'inference) sont
de SIGNES OPPOSES pour SHORT_SQUEEZE exhaustion (+40.0 vs -8.65 a THR=3).
Un verdict definitif ne peut pas dependre de ce choix : il faut montrer que la
quantite TRADABLE elle-meme (moyenne size-weighted) n'est pas significativement
positive une fois le clustering pris en compte.

Test : block-bootstrap par episode L3 (on rechantillonne des EPISODES ENTIERS,
avec tous leurs events et donc leurs poids), et on recalcule a chaque tirage la
moyenne SIZE-WEIGHTED (= PnL par trade). C'est le test de significativite
correct pour la quantite effectivement tradee.

Sorties additionnelles :
  - concentration : part du PnL total portee par les k plus gros episodes
  - le meme test pour le controle LONG_CASCADE exhaustion (alpha deja en shadow)
  - decomposition OOS 2025+ (la periode du chiffre +114.6bps publie)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import decluster  # noqa: E402

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
COST = 14.0
NBOOT = 8000
SEED = 20260905


def block_boot_weighted(d, seed=SEED, nboot=NBOOT):
    """Rechantillonne des EPISODES L3 entiers ; renvoie la distribution de la
    moyenne SIZE-WEIGHTED des net_bps (= PnL par trade)."""
    rng = np.random.default_rng(seed)
    groups = [g["net_bps"].values for _, g in d.groupby("L3", sort=False)]
    sums = np.array([g.sum() for g in groups], dtype=float)
    cnts = np.array([len(g) for g in groups], dtype=float)
    m = len(groups)
    idx = rng.integers(0, m, size=(nboot, m))
    num = sums[idx].sum(axis=1)
    den = cnts[idx].sum(axis=1)
    return num / den


def analyse(d, label, thr):
    d = decluster(d)
    d["net_bps"] = d["fwd_4h"].astype(float) * 1e4 - COST
    d = d[np.isfinite(d["net_bps"])].copy()
    ep = d.groupby("L3")["net_bps"].agg(["sum", "size", "mean"])
    ep = ep.sort_values("sum", ascending=False)
    tot = float(d["net_bps"].sum())
    boot = block_boot_weighted(d)
    res = {
        "label": label, "exhaustion_thr": thr, "n_raw": int(len(d)),
        "n_episodes_L3": int(len(ep)),
        "mean_net_bps_size_weighted": round(float(d["net_bps"].mean()), 2),
        "mean_net_bps_equal_weight_L3": round(float(ep["mean"].mean()), 2),
        "block_boot_ci95_size_weighted": [round(float(np.percentile(boot, 2.5)), 2),
                                          round(float(np.percentile(boot, 97.5)), 2)],
        "block_boot_p_le_0": round(float((boot <= 0).mean()), 4),
        "naive_iid_ci95": [round(float(d["net_bps"].mean() - 1.96 * d["net_bps"].std(ddof=1) / np.sqrt(len(d))), 2),
                           round(float(d["net_bps"].mean() + 1.96 * d["net_bps"].std(ddof=1) / np.sqrt(len(d))), 2)],
    }
    # concentration du PnL
    for k in (1, 3, 5, 10):
        res["pnl_share_top%d_episodes" % k] = (
            round(float(ep["sum"].head(k).sum() / tot), 3) if tot != 0 else None)
    res["pnl_share_top1pct_episodes"] = round(
        float(ep["sum"].head(max(1, len(ep) // 100)).sum() / tot), 3) if tot != 0 else None
    # ce que devient la moyenne size-weighted si l'on retire les 5 meilleurs episodes
    drop5 = d[~d["L3"].isin(ep.index[:5])]
    res["mean_net_bps_size_weighted_ex_top5_episodes"] = round(float(drop5["net_bps"].mean()), 2)
    # split IS/OOS 2025+
    for name, sub in (("pre2025", d[d["event_time"] < "2025-01-01"]),
                      ("2025plus", d[d["event_time"] >= "2025-01-01"])):
        if len(sub) < 20:
            continue
        b = block_boot_weighted(sub)
        res[name] = {"n": int(len(sub)), "n_ep": int(sub["L3"].nunique()),
                     "mean_net_bps_size_weighted": round(float(sub["net_bps"].mean()), 2),
                     "block_boot_ci95": [round(float(np.percentile(b, 2.5)), 2),
                                         round(float(np.percentile(b, 97.5)), 2)]}
    return res


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    out = {"note": "block-bootstrap par EPISODE L3 sur la moyenne SIZE-WEIGHTED "
                   "(= PnL par trade reellement realisable). NBOOT=%d seed=%d" % (NBOOT, SEED),
           "cases": []}
    for thr in (2, 3):
        ss = df[(df.kind == "SHORT_SQUEEZE") & (df.n_events_sym_24h >= thr)]
        lc = df[(df.kind == "LONG_CASCADE") & (df.n_events_sym_24h >= thr)]
        out["cases"].append(analyse(ss, "SHORT_SQUEEZE exhaustion LONG (SSE_CONT)", thr))
        out["cases"].append(analyse(lc, "LONG_CASCADE exhaustion LONG (CONTROL, shadow alpha)", thr))
    (OUT / "t1e_weighting_robustness.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
