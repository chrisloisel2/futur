"""
level_2/long_calibrate.py — CALIBRATION DU MODÈLE LONG
=======================================================

Deux étapes de calibration indépendantes :
  1. Calibration des probabilités : aligner P_model(y=1) avec la fréquence réelle
  2. Calibration du seuil direction : choisir le seuil sur business metric (val)

RÈGLE ANTI-OVERFIT :
  - Les deux étapes se font sur VAL uniquement.
  - JAMAIS ajuster le seuil en regardant les résultats du test.
  - Un seuil fragile (score chute si ±0.05) signale un overfit.
"""
from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression as PlattLR

from ai.level_0.constants import COST_PCT
from ai.level_0.features import FEATURES_LONG, FEATURES_SHORT
from ai.level_0.preprocessing import get_X


def calibrate_direction_model(
    clf,
    scaler,
    df,
    val_mask: np.ndarray,
    side: str = "long",
    cost_pct: float = COST_PCT,
    method: str = "isotonic",
    out_dir: Optional[Path] = None,
) -> Tuple[object, Dict]:
    """
    Calibre les probabilités du modèle sur la validation.

    Arguments
    ---------
    clf        : modèle entraîné (predict_proba disponible)
    scaler     : StandardScaler ajusté sur train
    df         : DataFrame avec colonnes y_long/y_short et future_ret_h
    val_mask   : masque val
    side       : "long" ou "short"
    cost_pct   : coût pour la calibration du seuil
    method     : "isotonic" (non-paramétrique) ou "platt" (sigmoïde)
    out_dir    : si fourni, sauvegarde le calibrateur

    Retourne
    --------
    calibrator, metrics_dict
    """
    feature_list = FEATURES_LONG if side == "long" else FEATURES_SHORT
    label_col    = f"y_{side}"
    ret_sign     = +1.0 if side == "long" else -1.0

    X_val   = get_X(df, val_mask, feature_list)
    y_val   = df.loc[val_mask, label_col].values.astype(np.int32)
    ret_val = df.loc[val_mask, "future_ret_h"].values.astype(np.float64)

    # Exclure gray zones
    valid = y_val >= 0
    X_val, y_val, ret_val = X_val[valid], y_val[valid], ret_val[valid]

    proba_raw = clf.predict_proba(scaler.transform(X_val))[:, 1]

    # ── 1. Calibration des probabilités (ECE) ────────────────────────────────
    calibrator, ece_before, ece_after = _fit_calibrator(proba_raw, y_val, method)
    proba_cal = _apply_calibrator(calibrator, proba_raw, method)

    print(f"\n   [Calibration {side.upper()}]")
    print(f"   ECE avant : {ece_before:.4f}")
    print(f"   ECE après : {ece_after:.4f}  ({'amélioré' if ece_after < ece_before else 'dégradé'})")

    # ── 2. Calibration du seuil direction (business metric) ──────────────────
    thr_sweep = _threshold_sweep_pnl(proba_cal, ret_val, ret_sign, cost_pct)
    best_thr   = max(thr_sweep, key=lambda x: x["pnl"])["threshold"]
    best_entry  = next(e for e in thr_sweep if e["threshold"] == best_thr)

    stable = _check_thr_stability(thr_sweep, best_thr)

    print(f"   Seuil optimal : {best_thr:.2f}  "
          f"(trades={best_entry['n_trades']}, "
          f"PF={best_entry['profit_factor']:.2f}, "
          f"WR={best_entry['win_rate']:.1%})")
    if not stable:
        print(f"   ⚠  Seuil fragile — score instable autour de ±0.05. Vérifier.")

    metrics = {
        "ece_before":        round(ece_before, 5),
        "ece_after":         round(ece_after, 5),
        "calibration_method": method,
        "recommended_threshold": round(best_thr, 3),
        "threshold_stable":   stable,
        "threshold_sweep":    thr_sweep,
    }

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "calibrator.pkl", "wb") as f: pickle.dump(calibrator, f)
        with open(out_dir / "calibration_metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)

    return calibrator, metrics


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fit_calibrator(proba_raw: np.ndarray, y_true: np.ndarray, method: str):
    ece_before = _ece(proba_raw, y_true)
    if method == "isotonic":
        cal = IsotonicRegression(out_of_bounds="clip")
        cal.fit(proba_raw, y_true)
        proba_cal = cal.predict(proba_raw)
    elif method == "platt":
        cal = PlattLR(C=1.0, random_state=42)
        cal.fit(proba_raw.reshape(-1, 1), y_true)
        proba_cal = cal.predict_proba(proba_raw.reshape(-1, 1))[:, 1]
    else:
        raise ValueError(f"Méthode de calibration inconnue : {method!r}")
    ece_after = _ece(proba_cal, y_true)
    return cal, ece_before, ece_after


def _apply_calibrator(cal, proba_raw: np.ndarray, method: str) -> np.ndarray:
    if method == "isotonic":
        return cal.predict(proba_raw)
    elif method == "platt":
        return cal.predict_proba(proba_raw.reshape(-1, 1))[:, 1]
    return proba_raw


def _ece(proba: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error — mesure l'écart entre confiance et fréquence."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece  = 0.0
    n    = len(proba)
    for i in range(n_bins):
        mask = (proba >= bins[i]) & (proba < bins[i + 1])
        if mask.sum() == 0:
            continue
        acc  = float(y_true[mask].mean())
        conf = float(proba[mask].mean())
        ece += mask.sum() * abs(acc - conf)
    return ece / max(n, 1)


def _threshold_sweep_pnl(
    proba_cal: np.ndarray,
    ret: np.ndarray,
    ret_sign: float,
    cost_pct: float,
) -> List[Dict]:
    """
    Balaye les seuils de direction et calcule le PnL net sur val.
    Critère : PnL total net de frais (pas AUC, pas F1).
    Ne jamais utiliser sur le test.
    """
    results = []
    for thr in np.arange(0.40, 0.85, 0.02):
        mask = proba_cal >= thr
        n_tr = int(mask.sum())
        if n_tr < 10:
            continue
        rets     = ret[mask] * ret_sign - cost_pct
        pnl      = float(rets.sum())
        wins     = float((rets > 0).sum())
        wr       = wins / n_tr
        gross_w  = float(rets[rets > 0].sum())
        gross_l  = float(abs(rets[rets < 0].sum()))
        pf       = gross_w / max(gross_l, 1e-9)
        exp      = float(rets.mean())
        results.append({
            "threshold":     round(float(thr), 2),
            "n_trades":      n_tr,
            "pnl":           round(pnl, 4),
            "profit_factor": round(pf, 3),
            "win_rate":      round(wr, 3),
            "expectancy":    round(exp, 5),
        })
    return results


def _check_thr_stability(sweep: List[Dict], best_thr: float,
                         delta: float = 0.05, max_drop_pct: float = 0.35) -> bool:
    """Vérifie que le seuil optimal est robuste à une perturbation de ±delta."""
    def get_pnl(thr):
        e = next((x for x in sweep if abs(x["threshold"] - thr) < 0.015), None)
        return e["pnl"] if e else 0.0
    best_pnl = get_pnl(best_thr)
    if best_pnl <= 0:
        return False
    lo_pnl = get_pnl(best_thr - delta)
    hi_pnl = get_pnl(best_thr + delta)
    min_nbr = min(lo_pnl, hi_pnl)
    drop = (best_pnl - min_nbr) / best_pnl
    return drop <= max_drop_pct
