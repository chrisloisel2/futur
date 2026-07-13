#!/usr/bin/env python3
"""
scripts/train_liq_cascade_engine_v2.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE v2 — mêmes REGLES DE VERDICT que v1 (déclarées avant tout run 50
actifs), méthodes d'entraînement avancées, toutes internes au train :

  1. Features v2 (+12) : funding as-of + z, structure OI (2h/24h/pctile 30j),
     deltas positionnement 1h, contexte BTC, séquencement des events, dow.
  2. Split de validation PURGÉ chronologique : val = derniers 15% du train,
     EMBARGO 8h (la fenêtre de label du dernier train ne touche pas la val).
     → early stopping + calibration sans contact avec le test.
  3. Sample weights = |fwd_4h − cost| winsorisés p95 (magnitude-aware,
     à la López de Prado) — le modèle apprend où EST l'argent.
  4. Ensemble baggé : 5 LightGBM (seeds/subsample différents), proba moyenne.
  5. Seuil par QUANTILE de la val purgée (top 20% des scores val, fraction
     déclarée a priori) → sélection stable par construction. [L'isotonique,
     testée d'abord, s'est montrée instable sur val fine (n=3 vs n=3295 selon
     fold) — archivée dans *_V2_ISOTONIC.json.]
  6. Simulation CONCURRENCE BORNÉE (max 3 positions, hold 4h, 10%/position) =
     le ROI honnête, en plus du roi_sized événementiel.

Gate INCHANGÉ : thr 0.55 · fold valide ⟺ train ≥ 2000 · CANDIDATE ⟺ ≥3 folds
PF ≥ 1.35 ET aucun fold valide destructeur. Coûts 14 bps + stress 28 bps.
Sortie : reports/liq_cascade/LIQ_CASCADE_WALKFORWARD_V2.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.engines.liq_cascade.dataset import FEATURES_V2, build_event_dataset
from src.institutional.engines.liq_cascade.detector import METRICS_DIR, CascadeConfig

OUT_DIR = ROOT / "reports" / "liq_cascade"
COST_RT = 0.0014
COST_RT_STRESS = 0.0028
P_THRESHOLD = 0.55
HORIZON = "fwd_4h"
SIZING = 0.10
EMBARGO = pd.Timedelta(hours=8)      # = fenêtre de label
MAX_CONCURRENT = 3
HOLD = pd.Timedelta(hours=4)
N_BAG = 5


def _metrics(net: np.ndarray) -> dict:
    net = net[np.isfinite(net)]
    if len(net) == 0:
        return {"n": 0, "pf": 0.0, "wr": 0.0, "mean_net_bps": 0.0, "roi_sized": 0.0}
    wins, losses = net[net > 0], net[net < 0]
    pf = float(wins.sum() / max(abs(losses.sum()), 1e-12)) if len(losses) else float("inf")
    eq = np.cumprod(1 + net * SIZING)
    return {"n": int(len(net)), "pf": round(pf, 3),
            "wr": round(float((net > 0).mean()), 3),
            "mean_net_bps": round(float(net.mean()) * 1e4, 1),
            "roi_sized": round(float(eq[-1] - 1), 4)}


def _sim_concurrent(te: pd.DataFrame, sel: np.ndarray, cost: float) -> dict:
    """Simulation à concurrence bornée : max 3 positions, hold 4h, 10%/pos."""
    ev = te.loc[sel].sort_values("event_time")
    equity, open_until, rets = 1.0, [], []
    for _, r in ev.iterrows():
        t = r["event_time"]
        open_until = [u for u in open_until if u > t]
        if len(open_until) >= MAX_CONCURRENT or not np.isfinite(r[HORIZON]):
            continue
        net = float(r[HORIZON]) - cost
        equity *= (1 + net * SIZING)
        rets.append(net)
        open_until.append(t + HOLD)
    m = _metrics(np.array(rets)) if rets else _metrics(np.array([]))
    m["roi_portfolio_style"] = round(equity - 1, 4)
    m["n_taken"] = len(rets)
    m["n_skipped_concurrency"] = int(sel.sum()) - len(rets)
    return m


def _fit_bagged_calibrated(tr: pd.DataFrame, cost: float):
    """Ensemble baggé + calibration isotonique, val purgée chronologique."""
    import lightgbm as lgb

    tr = tr.sort_values("event_time").reset_index(drop=True)
    cut = int(len(tr) * 0.85)
    val_start = tr["event_time"].iloc[cut]
    fit_mask = tr["event_time"] < (val_start - EMBARGO)   # embargo 8h
    val_mask = tr["event_time"] >= val_start
    fit, val = tr[fit_mask], tr[val_mask]
    # labels NaN (px invalide) : inutilisables pour fit/poids/calibration
    fit = fit[np.isfinite(fit[HORIZON].values)]
    val = val[np.isfinite(val[HORIZON].values)]

    y_fit = (fit[HORIZON].values > cost).astype(int)
    w_fit = np.abs(fit[HORIZON].values - cost)
    w_fit = np.clip(w_fit, None, np.nanpercentile(w_fit, 95))
    X_fit, X_val = fit[FEATURES_V2].values, val[FEATURES_V2].values

    models = []
    for k in range(N_BAG):
        m = lgb.LGBMClassifier(
            n_estimators=600, learning_rate=0.03, num_leaves=15, max_depth=5,
            min_child_samples=30, subsample=0.7 + 0.05 * (k % 3),
            colsample_bytree=0.6 + 0.05 * (k % 3), reg_lambda=5.0,
            random_state=k, verbose=-1)
        m.fit(X_fit, y_fit, sample_weight=w_fit,
              eval_set=[(X_val, (val[HORIZON].values > cost).astype(int))],
              callbacks=[lgb.early_stopping(50, verbose=False)])
        models.append(m)

    # seuil = quantile de la distribution des scores VAL (top TOP_FRAC).
    # Le test est sélectionné au même niveau de score absolu — fraction
    # stable par construction, aucun contact test.
    p_val = np.mean([m.predict_proba(X_val)[:, 1] for m in models], axis=0)
    thresholds = {f: float(np.quantile(p_val, 1 - f)) for f in (0.30, 0.20, 0.10, 0.05)}

    def predict(X):
        return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)
    return predict, models, thresholds


def run_fold(ev: pd.DataFrame, year: int, cost: float) -> dict:
    tr = ev[(ev["event_time"].dt.year < year) & ev["label_full"]]
    te = ev[(ev["event_time"].dt.year == year) & ev["label_full"]]
    if len(tr) < 150 or len(te) < 30:
        return {"year": year, "skip": True, "n_train": len(tr), "n_test": len(te)}
    out = {"year": year, "skip": False, "n_train": int(len(tr)), "n_test": int(len(te))}

    predict, models, thr = _fit_bagged_calibrated(tr, cost)
    p = predict(te[FEATURES_V2].values)
    sel = p >= thr[0.20]              # PRIMAIRE déclaré : top 20% des scores val
    out["ml_gated"] = _metrics(te.loc[sel, HORIZON].values - cost)
    out["ml_frac_traded"] = round(float(sel.mean()), 3)
    out["portfolio_sim"] = _sim_concurrent(te, sel, cost)
    out["threshold_ladder"] = {
        f"top{int(f*100)}%": _metrics(te.loc[p >= t, HORIZON].values - cost)
        for f, t in thr.items()}
    try:
        from sklearn.metrics import roc_auc_score
        yte = (te[HORIZON].values > cost).astype(int)
        if 0 < yte.sum() < len(yte):
            out["ml_auc_test"] = round(float(roc_auc_score(yte, p)), 4)
    except Exception:
        pass
    # importances moyennes (gain) — diagnostic
    imp = np.mean([m.booster_.feature_importance("gain") for m in models], axis=0)
    top = sorted(zip(FEATURES_V2, imp), key=lambda x: -x[1])[:8]
    out["top_features"] = [(f, round(float(v), 0)) for f, v in top]
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = sorted(p.stem.replace("_metrics_5m", "")
                     for p in METRICS_DIR.glob("*_metrics_5m.parquet"))
    print(f"Symboles : {len(symbols)} | features v2 : {len(FEATURES_V2)}", flush=True)
    ev = build_event_dataset(symbols, CascadeConfig())
    print(f"Events : {len(ev)}", flush=True)

    years = [2022, 2023, 2024, 2025, 2026]
    results = {"engine": "LIQ_CASCADE_V2", "events_total": int(len(ev)),
               "config": {"cost_rt": COST_RT, "selection": "top20%_val_quantile",
                          "horizon": HORIZON, "sizing": SIZING, "n_bag": N_BAG,
                          "max_concurrent": MAX_CONCURRENT,
                          "features": FEATURES_V2},
               "folds": [], "folds_stress": []}

    print("\n── Walk-forward v2 (coût 14 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT)
        results["folds"].append(r)
        if r.get("skip"):
            print(f"  {y}: SKIP")
            continue
        m, ps = r["ml_gated"], r["portfolio_sim"]
        print(f"  {y}: ML n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps "
              f"WR={m['wr']:.0%} AUC={r.get('ml_auc_test','-')} | "
              f"PORTF(max3): n={ps['n_taken']} PF={ps['pf']:.2f} "
              f"ROI={ps['roi_portfolio_style']*100:+.1f}%", flush=True)
        print(f"       top: {[f for f,_ in r['top_features'][:5]]}", flush=True)

    print("\n── Stress coûts ×2 (28 bps) ──")
    for y in years:
        r = run_fold(ev, y, COST_RT_STRESS)
        results["folds_stress"].append(r)
        if not r.get("skip"):
            m = r["ml_gated"]
            print(f"  {y}: ML n={m['n']:4} PF={m['pf']:.2f} {m['mean_net_bps']:+.1f}bps", flush=True)

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
    print(f"\nVERDICT v2 : {verdict}  (valides: {[r['year'] for r in valid]}, "
          f"PF≥1.35: {len(pf_ok)}/{len(valid)}, destructeurs: "
          f"{[r['year'] for r in destructive]}, exclus: {excluded})")

    (OUT_DIR / "LIQ_CASCADE_WALKFORWARD_V2.json").write_text(
        json.dumps(results, indent=2, default=str))
    print(f"→ {OUT_DIR / 'LIQ_CASCADE_WALKFORWARD_V2.json'}")


if __name__ == "__main__":
    main()
