#!/usr/bin/env python3
"""
scripts/train_liq_cascade_engine.py
─────────────────────────────────────────────────────────────────────────────
Walk-forward HONNÊTE du moteur LIQ_CASCADE (events OI 5-min multi-actifs).

  • Dataset : build_event_dataset (features causales, labels forward, testé).
  • Folds par année de test : train = events < année, test = année.
  • Baseline RULE (sans ML) : long sur chaque LONG_CASCADE, horizon 4h.
  • ML : LightGBM P(fwd_4h > coût) entraîné sur train, seuil FIXE 0.55
    (pas de calibration sur test ; le seuil est une constante déclarée).
  • Coûts : 14 bps round-trip (taker 5 + slip 2, ×2 jambes) + stress ×2.

Gates (répo) : PF ≥ 1.35 sur ≥ 3/4 folds récents pour statut CANDIDATE.
Sortie : reports/liq_cascade/LIQ_CASCADE_WALKFORWARD.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.liq_cascade.dataset import FEATURES, build_event_dataset
from src.institutional.engines.liq_cascade.detector import METRICS_DIR, CascadeConfig

OUT_DIR = ROOT / "reports" / "liq_cascade"
COST_RT = 0.0014          # 14 bps round-trip
COST_RT_STRESS = 0.0028
P_THRESHOLD = 0.55        # constante déclarée (pas calibrée sur test)
HORIZON = "fwd_4h"
SIZING = 0.10             # 10% de l'equity par event (métrique ROI indicative)


def _metrics(net: np.ndarray) -> dict:
    net = net[np.isfinite(net)]   # labels nan (px invalide) exclus
    if len(net) == 0:
        return {"n": 0, "pf": 0.0, "wr": 0.0, "mean_net_bps": 0.0, "roi_sized": 0.0}
    wins, losses = net[net > 0], net[net < 0]
    pf = float(wins.sum() / max(abs(losses.sum()), 1e-12)) if len(losses) else float("inf")
    eq = np.cumprod(1 + net * SIZING)
    return {"n": int(len(net)), "pf": round(pf, 3),
            "wr": round(float((net > 0).mean()), 3),
            "mean_net_bps": round(float(net.mean()) * 1e4, 1),
            "roi_sized": round(float(eq[-1] - 1), 4)}


def run_fold(ev: pd.DataFrame, year: int, cost: float) -> dict:
    tr = ev[(ev["event_time"].dt.year < year) & ev["label_full"]]
    te = ev[(ev["event_time"].dt.year == year) & ev["label_full"]]
    if len(tr) < 150 or len(te) < 30:
        return {"year": year, "skip": True, "n_train": len(tr), "n_test": len(te)}

    out = {"year": year, "skip": False, "n_train": int(len(tr)), "n_test": int(len(te))}

    # ── baseline RULE : long sur chaque LONG_CASCADE ──
    mask_rule = te["is_long_cascade"] == 1.0
    net_rule = te.loc[mask_rule, HORIZON].values - cost
    out["rule_long_cascade"] = _metrics(net_rule)

    # ── ML gate ──
    import lightgbm as lgb
    Xtr = tr[FEATURES].values
    ytr = (tr[HORIZON].values > cost).astype(int)
    Xte = te[FEATURES].values
    model = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.03, num_leaves=15, max_depth=5,
        min_child_samples=30, subsample=0.8, colsample_bytree=0.7,
        reg_lambda=5.0, random_state=0, verbose=-1)
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    sel = p >= P_THRESHOLD
    net_ml = te.loc[sel, HORIZON].values - cost
    out["ml_gated"] = _metrics(net_ml)
    out["ml_frac_traded"] = round(float(sel.mean()), 3)
    # sensibilité du seuil (REPORTÉE, pas sélectionnée — le primaire reste 0.55)
    out["threshold_ladder"] = {
        str(t): _metrics(te.loc[p >= t, HORIZON].values - cost)
        for t in (0.50, 0.60, 0.65)}

    # AUC test (info)
    try:
        from sklearn.metrics import roc_auc_score
        yte = (te[HORIZON].values > cost).astype(int)
        if 0 < yte.sum() < len(yte):
            out["ml_auc_test"] = round(float(roc_auc_score(yte, p)), 4)
    except Exception:
        pass
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = sorted(p.stem.replace("_metrics_5m", "")
                     for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
    print(f"Symboles avec metrics : {len(symbols)} → {symbols}", flush=True)

    ev = build_event_dataset(symbols, CascadeConfig())
    if ev.empty:
        print("Aucun event — backfill incomplet ?")
        sys.exit(1)
    print(f"Events détectés : {len(ev)}  ({ev['event_time'].min()} → {ev['event_time'].max()})")
    print(ev.groupby(ev["event_time"].dt.year).size().to_string(), flush=True)
    print("\nEdge BRUT par type (fwd_4h net de 14 bps, tous folds confondus — info) :")
    for kind, g in ev[ev.label_full].groupby("kind"):
        net = g[HORIZON].values - COST_RT
        m = _metrics(net)
        print(f"  {kind:14} n={m['n']:5}  PF={m['pf']:.3f}  WR={m['wr']:.1%}  "
              f"mean={m['mean_net_bps']:+.1f} bps", flush=True)

    years = [2022, 2023, 2024, 2025, 2026]
    results = {"events_total": int(len(ev)), "symbols": symbols,
               "config": {"cost_rt": COST_RT, "p_threshold": P_THRESHOLD,
                          "horizon": HORIZON, "sizing": SIZING},
               "folds": [], "folds_stress": []}

    print("\n── Walk-forward (coût 14 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT)
        results["folds"].append(r)
        if r.get("skip"):
            print(f"  {y}: SKIP (train={r['n_train']}, test={r['n_test']})")
            continue
        b, m = r["rule_long_cascade"], r["ml_gated"]
        print(f"  {y}: RULE n={b['n']:4} PF={b['pf']:.2f} {b['mean_net_bps']:+.1f}bps | "
              f"ML n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps "
              f"WR={m['wr']:.0%} AUC={r.get('ml_auc_test','-')}", flush=True)

    print("\n── Stress coûts ×2 (28 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT_STRESS)
        results["folds_stress"].append(r)
        if not r.get("skip"):
            m = r["ml_gated"]
            print(f"  {y}: ML n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps", flush=True)

    # verdict — RÈGLE DÉCLARÉE AVANT le run 50 actifs (2026-07-06, sur structure
    # des 12) : un fold ne compte que si train ≥ MIN_TRAIN_EVENTS (un modèle
    # entraîné sur ~350 events n'est pas un modèle ; même logique que les
    # minimums de données du repo). L'engine live n'opérera qu'au-dessus de ce
    # seuil d'entraînement. Gate : PF ≥ 1.35 sur ≥ 3 folds valides ET aucun
    # fold valide < 1.0 (pas d'année destructrice).
    MIN_TRAIN_EVENTS = 2000
    valid = [r for r in results["folds"]
             if not r.get("skip") and r["n_train"] >= MIN_TRAIN_EVENTS]
    excluded = [r["year"] for r in results["folds"]
                if not r.get("skip") and r["n_train"] < MIN_TRAIN_EVENTS]
    pf_ok = [r for r in valid if r["ml_gated"]["pf"] >= 1.35 and r["ml_gated"]["n"] >= 20]
    destructive = [r for r in valid if r["ml_gated"]["pf"] < 1.0]
    verdict = ("CANDIDATE" if len(valid) >= 3 and len(pf_ok) >= 3 and not destructive
               else "NO_EDGE" if valid else "INSUFFICIENT_DATA")
    results["verdict"] = verdict
    results["verdict_rule"] = {"min_train_events": MIN_TRAIN_EVENTS,
                               "excluded_folds": excluded,
                               "pf_ok_folds": [r["year"] for r in pf_ok],
                               "destructive_folds": [r["year"] for r in destructive]}
    print(f"\nVERDICT : {verdict}  (folds valides: {[r['year'] for r in valid]}, "
          f"PF≥1.35 : {len(pf_ok)}/{len(valid)}, destructeurs: "
          f"{[r['year'] for r in destructive]}, exclus min-train: {excluded})")

    (OUT_DIR / "LIQ_CASCADE_WALKFORWARD.json").write_text(
        json.dumps(results, indent=2, default=str))
    print(f"→ {OUT_DIR / 'LIQ_CASCADE_WALKFORWARD.json'}")


if __name__ == "__main__":
    main()
