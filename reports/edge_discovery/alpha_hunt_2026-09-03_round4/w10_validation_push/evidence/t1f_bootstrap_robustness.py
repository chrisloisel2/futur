#!/usr/bin/env python
"""T1f — Robustesse du test decisif de la cible 1 (t1e) au choix du gap
d'episode et a la graine du bootstrap. Verification, pas nouveau test."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/qbee/futur")
OUT = Path(__file__).resolve().parent
COST = 14.0


def episodes(d, gap):
    d = d.sort_values("event_time")
    t = d["event_time"].values
    return np.cumsum(np.r_[True, (np.diff(t) > pd.Timedelta(gap).to_timedelta64())])


def boot(d, gap, seed, nb=8000):
    d = d.copy()
    d["ep"] = episodes(d, gap)
    d["b"] = d["fwd_4h"].astype(float) * 1e4 - COST
    g = d.groupby("ep")["b"]
    s, c = g.sum().values, g.size().values
    rng = np.random.default_rng(seed)
    i = rng.integers(0, len(s), (nb, len(s)))
    v = s[i].sum(1) / c[i].sum(1)
    return {"n_ep": int(len(s)),
            "ci95": [round(float(np.percentile(v, 2.5)), 2), round(float(np.percentile(v, 97.5)), 2)],
            "p_le_0": round(float((v <= 0).mean()), 4)}


def main():
    df = pd.read_parquet(ROOT / "data/events/liq_cascade_dataset.parquet")
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df = df[df["label_full"] & df["fwd_4h"].notna()].copy()
    out = {}
    for thr in (2, 3):
        for kind, lab in (("SHORT_SQUEEZE", "SS_exhaustion"),
                          ("LONG_CASCADE", "LC_exhaustion_CONTROL")):
            d = df[(df.kind == kind) & (df.n_events_sym_24h >= thr)]
            for gap in ("1h", "4h", "12h", "24h"):
                for seed in (20260905, 7, 999):
                    out["thr%d|%s|gap%s|seed%d" % (thr, lab, gap, seed)] = boot(d, gap, seed)
    (OUT / "t1f_bootstrap_robustness.json").write_text(json.dumps(out, indent=2))
    for k in sorted(out):
        print(k, out[k])


if __name__ == "__main__":
    main()
