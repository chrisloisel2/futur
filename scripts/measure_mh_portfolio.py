#!/usr/bin/env python3
"""
scripts/measure_mh_portfolio.py
─────────────────────────────────────────────────────────────────────────────
Juge de paix : le multi-horizon (COMBINED) améliore-t-il le PORTEFEUILLE, ou
juste le per-trade ? Compare, à la MÊME wave-unitization (gap 30min, dédup
symbole, top-3, gross-cap), les tapes ACTUELLES vs les tapes MULTI-HORIZON.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "reports" / "liq_cascade"
SIZE, GROSS_CAP, HOLD_H = 0.02, 0.60, 4


def wave_sim(tape):
    t = tape.sort_values("event_time").reset_index(drop=True)
    t["year"] = t["event_time"].dt.year
    t["rank"] = t.groupby("year")["score"].rank(pct=True)
    times = t["event_time"].values
    w, wid = np.zeros(len(t), int), 0
    for i in range(1, len(t)):
        if (times[i] - times[i - 1]) > np.timedelta64(30, "m"):
            wid += 1
        w[i] = wid
    t["wave"] = w
    t = (t.sort_values(["wave", "rank"], ascending=[True, False])
           .drop_duplicates(["wave", "symbol"]))
    t["k"] = t.groupby("wave").cumcount()
    t = t[t["k"] < 3].sort_values("event_time")
    eq, openp = 1.0, {}
    rets = []
    for _, r in t.iterrows():
        tt = r["event_time"]
        openp = {s: u for s, u in openp.items() if u > tt}
        if r["symbol"] in openp or len(openp) * SIZE >= GROSS_CAP or not np.isfinite(r["net"]):
            continue
        eq *= (1 + r["net"] * SIZE); rets.append(r["net"])
        openp[r["symbol"]] = tt + pd.Timedelta(hours=HOLD_H)
    rets = np.array(rets)
    return {"n": len(rets), "roi": round(float(eq - 1), 4),
            "pf": round(float(rets[rets > 0].sum() / max(abs(rets[rets < 0].sum()), 1e-9)), 3),
            "mean_bps": round(float(rets.mean() * 1e4), 1) if len(rets) else 0}


def load(paths):
    fr = []
    for p in paths:
        if p.exists():
            d = pd.read_parquet(p); d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
            fr.append(d[["event_time", "symbol", "net", "score"]])
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def main():
    import json
    eng = ["LIQ_CASCADE", "PREMIUM_DISLOCATION", "CROWDING_REVERSAL"]
    cur = load([DIR / f"{e}_trades.parquet" for e in eng])
    mh = load([DIR / f"{e}_MH_trades.parquet" for e in eng])
    r_cur, r_mh = wave_sim(cur), (wave_sim(mh) if not mh.empty else {})
    print("PORTEFEUILLE wave (2% sizing, 3 moteurs cascade+premium+crowding) :")
    print("  ACTUEL       :", r_cur)
    print("  MULTI-HORIZON:", r_mh)
    (DIR / "MH_PORTFOLIO.json").write_text(json.dumps(
        {"current": r_cur, "multihorizon": r_mh}, default=str, indent=2))


if __name__ == "__main__":
    main()
