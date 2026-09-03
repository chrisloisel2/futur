#!/usr/bin/env python
"""T1d — Diagnostic du declustering : l'unite L3 est-elle raisonnable, et l'ecart
entre moyenne brute et moyenne par episode est-il un artefact de ponderation ?

Sanity check obligatoire avant de conclure : si L3 fabrique quelques mega-episodes
de plusieurs semaines, la moyenne equiponderee par episode serait dominee par des
episodes minuscules et le verdict serait un artefact de methode, pas un fait.
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


def desc(d, key, label):
    g = d.groupby(key)
    size = g.size()
    dur = (g["event_time"].max() - g["event_time"].min()).dt.total_seconds() / 3600
    return {"unit": label, "n_units": int(len(size)),
            "size_mean": round(float(size.mean()), 2),
            "size_median": float(size.median()),
            "size_p95": float(size.quantile(0.95)),
            "size_max": int(size.max()),
            "dur_h_median": round(float(dur.median()), 2),
            "dur_h_p95": round(float(dur.quantile(0.95)), 2),
            "dur_h_max": round(float(dur.max()), 2),
            "pct_units_size1": round(float((size == 1).mean()), 3)}


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    df["bucket"] = pd.cut(df["n_events_sym_24h"], [-1, 0, 1, 10 ** 9],
                          labels=["onset", "mid", "exhaustion"])
    out = {}

    for name, sub in [("SS_exhaustion", df[(df.kind == "SHORT_SQUEEZE") & (df.bucket == "exhaustion")]),
                      ("SS_onset", df[(df.kind == "SHORT_SQUEEZE") & (df.bucket == "onset")]),
                      ("LC_exhaustion", df[(df.kind == "LONG_CASCADE") & (df.bucket == "exhaustion")]),
                      ("ALL", df)]:
        d = decluster(sub)
        d["bps"] = d["fwd_4h"] * 1e4 - 14.0
        rec = {"n_raw": int(len(d)),
               "mean_raw_bps_LONG": round(float(d["bps"].mean()), 2)}
        for key, lab in (("L1", "symbol|24h"), ("L2", "calendar day"), ("L3", "cross-sym chain 4h")):
            rec[key] = desc(d, key, lab)
            ep = d.groupby(key)["bps"].mean()
            rec[key]["mean_of_episode_means_bps"] = round(float(ep.mean()), 2)
            rec[key]["t"] = round(float(ep.mean() / (ep.std(ddof=1) / np.sqrt(len(ep)))), 2)
            # ponderation par taille = doit redonner ~la moyenne brute
            w = d.groupby(key)["bps"].agg(["mean", "size"])
            rec[key]["size_weighted_check_bps"] = round(
                float((w["mean"] * w["size"]).sum() / w["size"].sum()), 2)
            # correlation taille d'episode <-> perf de l'episode
            rec[key]["corr_size_vs_perf"] = round(float(np.corrcoef(
                np.log(w["size"].values), w["mean"].values)[0, 1]), 3)
        # variante L3b : chainage cross-symbole plus strict (gap 1h) et plus lache (12h)
        for gap, lab in (("1h", "L3b_gap1h"), ("12h", "L3c_gap12h")):
            t = d["event_time"].values
            newep = np.r_[True, (np.diff(t) > pd.Timedelta(gap).to_timedelta64())]
            d[lab] = np.cumsum(newep)
            ep = d.groupby(lab)["bps"].mean()
            rec[lab] = {"n_units": int(len(ep)),
                        "mean_of_episode_means_bps": round(float(ep.mean()), 2),
                        "t": round(float(ep.mean() / (ep.std(ddof=1) / np.sqrt(len(ep)))), 2)}
        out[name] = rec

    (OUT / "t1d_decluster_diagnostics.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
