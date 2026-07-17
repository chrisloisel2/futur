#!/usr/bin/env python3
"""
scripts/measure_bear_short_edges.py
─────────────────────────────────────────────────────────────────────────────
Teste l'existence d'un ALPHA BAISSIER (short) exploitable — même rigueur que
tout le reste. Le short directionnel a été rejeté (audit mai 2026, WR 34%) ;
on teste ici deux angles ÉVÉNEMENTIELS différents, honnêtement :

  A. SHORT_SQUEEZE_FADE : après un short-squeeze (prix↑ sur OI↓ = shorts
     liquidés), shorter le rebond qui cale. Miroir de LIQ_CASCADE.
  B. HIGH_FUNDING_SHORT : shorter quand le funding est dans le top décile
     (foule sur-longue, due pour un flush).
  C. OVERCROWD_SHORT     : shorter quand toptrader ratio est extrême (z haut).

SHORT PnL = -(forward_return) - COST_SHORT. Coût short = 16 bps (taker+slip,
un peu plus cher que long). Funding reçu si >0 (bonus, modélisé). Rule-based
ET ML-gaté walk-forward. Gates : PF≥1.30 sur ≥3/4 folds (comme le repo).
Sortie : reports/liq_cascade/BEAR_SHORT_EDGES.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.institutional.engines.liq_cascade.dataset import FEATURES_V2

OUT = ROOT / "reports" / "liq_cascade"
CACHE = ROOT / "data" / "events" / "liq_cascade_dataset.parquet"
COST_SHORT = 0.0016
YEARS = [2023, 2024, 2025, 2026]
TOP = 0.20
HORIZON = "fwd_4h"


def short_net(fwd, funding_last, hold_h=4):
    """PnL d'un short : -mouvement prix - coût + funding reçu (si funding>0)."""
    funding_bonus = np.where(np.isfinite(funding_last), np.maximum(funding_last, 0) * (hold_h / 8), 0)
    return -fwd - COST_SHORT + funding_bonus


def pf(net):
    net = net[np.isfinite(net)]
    g = net[net > 0].sum(); l = abs(net[net < 0].sum())
    return float(g / l) if l > 0 else float("inf")


def m(net):
    net = net[np.isfinite(net)]
    if not len(net):
        return {"n": 0, "pf": 0.0, "mean_bps": 0.0, "wr": 0.0}
    return {"n": int(len(net)), "pf": round(pf(net), 3),
            "mean_bps": round(float(net.mean()) * 1e4, 1),
            "wr": round(float((net > 0).mean()), 3)}


def by_year(net, years):
    out = {}
    for y in sorted(set(years)):
        n = net[years == y]; n = n[np.isfinite(n)]
        if len(n) >= 20:
            out[int(y)] = round(pf(n), 2)
    return out


def ml_walk(ev, feats):
    """ML gate walk-forward : prédit quand le SHORT est gagnant."""
    import lightgbm as lgb
    oos = pd.Series(np.nan, index=ev.index)
    for y in YEARS:
        tr = ev[ev["event_time"].dt.year < y]
        te = ev[ev["event_time"].dt.year == y]
        if len(tr) < 800 or len(te) < 30:
            continue
        ytr = (tr["short_net"].values > 0).astype(int)
        w = np.clip(np.abs(tr["short_net"].values), None,
                    np.nanpercentile(np.abs(tr["short_net"].values), 95))
        mdl = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15,
                                 max_depth=5, min_child_samples=30, reg_lambda=5.0,
                                 subsample=0.8, colsample_bytree=0.7, random_state=0, verbose=-1)
        ok = np.isfinite(tr["short_net"].values)
        mdl.fit(tr[feats].values[ok], ytr[ok], sample_weight=w[ok])
        oos.loc[te.index] = mdl.predict_proba(te[feats].values)[:, 1]
    return oos


def run_candidate(name, ev_sub, feats):
    ev_sub = ev_sub.copy()
    ev_sub["short_net"] = short_net(ev_sub[HORIZON].values,
                                    ev_sub.get("funding_last", pd.Series(np.nan, index=ev_sub.index)).values)
    years = ev_sub["event_time"].dt.year.values
    res = {"rule": m(ev_sub["short_net"].values),
           "rule_by_year": by_year(ev_sub["short_net"].values, years)}
    # ML gate
    if len(ev_sub) > 1000:
        p = ml_walk(ev_sub, feats)
        ev_sub["p"] = p
        ev_sub2 = ev_sub.dropna(subset=["p"])
        thr = np.nanquantile(ev_sub2["p"], 1 - TOP)
        sel = ev_sub2["p"].values >= thr
        res["ml"] = m(ev_sub2["short_net"].values[sel])
        res["ml_costx2"] = m((ev_sub2[HORIZON].values[sel] * -1 - 2 * COST_SHORT))
        res["ml_by_year"] = by_year(ev_sub2["short_net"].values[sel],
                                    ev_sub2["event_time"].dt.year.values[sel])
    return res


def main():
    ev = pd.read_parquet(CACHE)
    ev = ev[ev["label_full"]].copy()
    feats = FEATURES_V2

    cands = {
        "A_SHORT_SQUEEZE_FADE": ev[ev["kind"] == "SHORT_SQUEEZE"],
        "B_HIGH_FUNDING_SHORT": ev[ev.get("funding_last", pd.Series(np.nan, index=ev.index))
                                   > ev.get("funding_last", pd.Series(0, index=ev.index)).quantile(0.90)],
        "C_OVERCROWD_SHORT": ev[ev.get("toptrader_z", pd.Series(0, index=ev.index)) > 1.5],
    }
    results = {}
    print(f"Dataset : {len(ev)} events | coût short {COST_SHORT*1e4:.0f} bps\n")
    for name, sub in cands.items():
        if len(sub) < 100:
            print(f"{name}: n={len(sub)} trop peu"); results[name] = {"n": len(sub), "note": "insuffisant"}
            continue
        r = run_candidate(name, sub, feats)
        results[name] = r
        print(f"── {name} (n={len(sub)}) ──")
        print(f"   RULE  : PF {r['rule']['pf']} mean {r['rule']['mean_bps']:+.1f}bps WR {r['rule']['wr']:.0%} | par an {r['rule_by_year']}")
        if "ml" in r:
            print(f"   ML    : PF {r['ml']['pf']} mean {r['ml']['mean_bps']:+.1f}bps WR {r['ml']['wr']:.0%} | par an {r['ml_by_year']}")
            print(f"   ML×2  : PF {r['ml_costx2']['pf']} mean {r['ml_costx2']['mean_bps']:+.1f}bps")
        print()

    # verdict global
    verdicts = {}
    for name, r in results.items():
        if "ml" not in r:
            verdicts[name] = "INSUFFISANT"; continue
        by = r.get("ml_by_year", {})
        ok = sum(1 for v in by.values() if v >= 1.30)
        verdicts[name] = "EDGE" if (r["ml"]["pf"] >= 1.30 and ok >= 3) else "NO_EDGE"
    print("VERDICTS :", verdicts)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "BEAR_SHORT_EDGES.json").write_text(json.dumps(
        {"results": results, "verdicts": verdicts}, indent=2, default=str))


if __name__ == "__main__":
    main()
