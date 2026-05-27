"""
level_2/short_calibrate.py — CALIBRATION SHORT ASYMÉTRIQUE
===========================================================

Différences critiques vs la calibration long :

1. PLATT par défaut (isotonic overfite sur données sparse short)
2. Seuil balayé de 0.55 à 0.90 avec step 0.01 (pas 0.45-0.85/0.02)
3. Critère de sélection : precision * sqrt(n_trades) sous contraintes strictes
4. Stabilité vérifiée avec delta=0.03 (pas 0.05) et max_drop=0.25 (pas 0.35)
5. Coût asymétrique : cost_pct * COST_SHORT_MULT (1.5x)
6. Calibration faite sur données filtrées régime (SHORTABLE + NEUTRAL seulement)

RÈGLE ANTI-OVERFIT : tout se fait sur VAL uniquement.
"""
from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression as PlattLR

from ai.level_0.constants import COST_PCT, COST_SHORT_MULT, REGIME_COL, TARGET_COL
from ai.level_0.features import FEATURES_SHORT
from ai.level_0.preprocessing import get_X
from ai.level_1.rules import REGIME_NO_SHORT


def calibrate_direction_model(
    clf,
    scaler,
    df,
    val_mask: np.ndarray,
    side: str = "short",
    cost_pct: float = COST_PCT,
    method: str = "platt",
    out_dir: Optional[Path] = None,
    filter_by_regime: bool = True,
) -> Tuple[object, Dict]:
    """
    Calibre les probabilités et seuil du modèle SHORT.

    Arguments
    ---------
    clf            : modèle entraîné
    scaler         : StandardScaler train
    df             : DataFrame avec y_short, future_ret_h, regime_short
    val_mask       : masque val
    side           : "short" (forcé)
    cost_pct       : coût long (court sera multiplié par COST_SHORT_MULT)
    method         : "platt" (défaut) ou "isotonic"
    out_dir        : si fourni, sauvegarde calibrateur
    filter_by_regime : exclure les barres NO_SHORT de la calibration

    Retourne
    --------
    calibrator, metrics_dict
    """
    side      = "short"
    label_col = "y_short"
    ret_sign  = -1.0
    cost_short = cost_pct * COST_SHORT_MULT

    X_val   = get_X(df, val_mask, FEATURES_SHORT)
    y_val   = df.loc[val_mask, label_col].values.astype(np.int32)
    ret_val = df.loc[val_mask, TARGET_COL].values.astype(np.float64)

    valid = y_val >= 0
    X_val, y_val, ret_val = X_val[valid], y_val[valid], ret_val[valid]

    regime_mask = np.ones(len(y_val), dtype=bool)
    if filter_by_regime and REGIME_COL in df.columns:
        regimes_val = df.loc[val_mask, REGIME_COL].values[valid]
        regime_mask = (regimes_val != REGIME_NO_SHORT)
        n_excluded = int((~regime_mask).sum())
        if n_excluded > 0:
            print(f"   [Calibration SHORT] {n_excluded} barres NO_SHORT exclues de la calibration")

    X_cal   = X_val[regime_mask]
    y_cal   = y_val[regime_mask]
    ret_cal = ret_val[regime_mask]

    if len(X_cal) < 30:
        print(f"   ⚠  Trop peu de données pour calibrer ({len(X_cal)} barres) — seuil défaut 0.65")
        _default_metrics = {
            "ece_before": float("nan"), "ece_after": float("nan"),
            "calibration_method": method,
            "recommended_threshold": 0.65,
            "threshold_stable": False,
            "threshold_sweep": [],
            "n_val_calibration": len(X_cal),
        }
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / "calibration_metrics.json", "w") as f:
                json.dump(_default_metrics, f, indent=2)
        cal_identity = _IdentityCalibrator()
        return cal_identity, _default_metrics

    proba_raw = clf.predict_proba(scaler.transform(X_cal))[:, 1]

    calibrator, ece_before, ece_after = _fit_calibrator(proba_raw, y_cal, method)
    proba_cal = _apply_calibrator(calibrator, proba_raw, method)

    print(f"\n   [Calibration SHORT]  méthode={method}  n={len(y_cal)}")
    print(f"   ECE avant : {ece_before:.4f}")
    print(f"   ECE après : {ece_after:.4f}  ({'amélioré' if ece_after < ece_before else 'dégradé'})")

    thr_sweep = _threshold_sweep_short(proba_cal, ret_cal, ret_sign, cost_short)

    if not thr_sweep:
        print("   ⚠  Aucun seuil viable — signal short insuffisant sur val")
        metrics = _build_empty_metrics(ece_before, ece_after, method, len(y_cal))
        _save_if_needed(out_dir, calibrator, metrics)
        return calibrator, metrics

    best_thr   = _select_best_threshold(thr_sweep)
    best_entry = next((e for e in thr_sweep if e["threshold"] == best_thr), thr_sweep[0])

    stable = _check_thr_stability_short(thr_sweep, best_thr)

    print(f"   Seuil optimal : {best_thr:.2f}  "
          f"(trades={best_entry['n_trades']}, "
          f"PF={best_entry['profit_factor']:.2f}, "
          f"WR={best_entry['win_rate']:.1%}, "
          f"precision={best_entry.get('precision', 0):.3f})")

    if not stable:
        print(f"   ⚠  Seuil fragile (delta=±0.03) — vérifier overfit val")

    if best_thr < 0.58:
        print(f"   ⚠  Seuil < 0.58 ({best_thr:.2f}) — signal short très faible")

    metrics = {
        "ece_before":             round(ece_before, 5),
        "ece_after":              round(ece_after, 5),
        "calibration_method":     method,
        "recommended_threshold":  round(best_thr, 3),
        "threshold_stable":       stable,
        "threshold_sweep":        thr_sweep,
        "n_val_calibration":      int(len(y_cal)),
        "n_val_short_signals":    int((y_cal == 1).sum()),
        "cost_short_used":        round(cost_short, 5),
        "regime_filtered":        filter_by_regime,
    }

    _save_if_needed(out_dir, calibrator, metrics)
    return calibrator, metrics


# ─────────────────────────────────────────────────────────────────────────────
# Sweep et sélection du seuil
# ─────────────────────────────────────────────────────────────────────────────

def _threshold_sweep_short(
    proba_cal: np.ndarray,
    ret: np.ndarray,
    ret_sign: float,
    cost_short: float,
) -> List[Dict]:
    """
    Sweep de seuil avec critère de sélection PnL/PF-centric.

    Score = expectancy × max(pf - 1.0, 0) × sqrt(n_trades)
      • expectancy : gain moyen par trade net de frais (direction et amplitude)
      • (pf - 1.0) : edge net (0 = breakeven, pas de bonus)
      • sqrt(n_trades) : couverture statistique, bonus pour plus de trades

    Cette métrique pénalise fortement les seuils qui produisent PF < 1.0
    ou WR < 50%, ce qui était la cause du 44% WR observé.
    """
    results = []
    for thr in np.arange(0.52, 0.90, 0.01):
        mask = proba_cal >= thr
        n_tr = int(mask.sum())
        if n_tr < 8:
            continue

        rets     = ret[mask] * ret_sign - cost_short
        pnl      = float(rets.sum())
        wins     = float((rets > 0).sum())
        wr       = wins / n_tr
        gross_w  = float(rets[rets > 0].sum())
        gross_l  = float(abs(rets[rets < 0].sum()))
        pf       = gross_w / max(gross_l, 1e-9)
        exp      = float(rets.mean())

        # Score PnL-centrique : récompense edge réel, pas le volume pur
        edge = max(pf - 1.0, 0.0)
        score = exp * edge * (n_tr ** 0.5)

        results.append({
            "threshold":     round(float(thr), 2),
            "n_trades":      n_tr,
            "pnl":           round(pnl, 4),
            "profit_factor": round(pf, 3),
            "win_rate":      round(wr, 3),
            "expectancy":    round(exp, 5),
            "score":         round(score, 6),
            "precision":     round(wr, 3),
        })
    return results


def _select_best_threshold(sweep: List[Dict]) -> float:
    # Niveau 1 : seuil strict — WR ≥ 52%, PF ≥ 1.10, n ≥ 20
    candidates = [
        e for e in sweep
        if e["n_trades"] >= 20
        and e["win_rate"] >= 0.52
        and e["profit_factor"] >= 1.10
    ]

    if not candidates:
        # Niveau 2 : WR ≥ 50%, PF ≥ 1.05, n ≥ 12
        candidates = [
            e for e in sweep
            if e["n_trades"] >= 12
            and e["win_rate"] >= 0.50
            and e["profit_factor"] >= 1.05
        ]

    if not candidates:
        # Niveau 3 : WR ≥ 48%, PF ≥ 1.0, n ≥ 8
        candidates = [
            e for e in sweep
            if e["n_trades"] >= 8
            and e["win_rate"] >= 0.48
            and e["profit_factor"] >= 1.0
        ]

    if not candidates:
        # Niveau 4 : WR ≥ 46%, PF ≥ 1.0, n ≥ 8 — seuil minimal acceptable
        candidates = [
            e for e in sweep
            if e["n_trades"] >= 8
            and e["win_rate"] >= 0.46
            and e["profit_factor"] >= 1.0
        ]

    if not candidates:
        # Niveau 5 : au moins PF ≥ 1.0, n ≥ 5 — désespoir mais mieux que rien
        candidates = [
            e for e in sweep
            if e["n_trades"] >= 5 and e["profit_factor"] >= 1.0
        ]

    if not candidates:
        # Niveau 6 : meilleur expectancy positif quel que soit WR
        pos_exp = [e for e in sweep if e["expectancy"] > 0 and e["n_trades"] >= 3]
        if pos_exp:
            return max(pos_exp, key=lambda e: e["expectancy"])["threshold"]

    if not candidates:
        # Pas de seuil viable meme avec espérance positive : seuil haut pour eviter les pertes
        return 0.72

    return max(candidates, key=lambda e: e["score"])["threshold"]


def _check_thr_stability_short(
    sweep: List[Dict],
    best_thr: float,
    delta: float = 0.04,    # fenêtre de stabilite plus large
    max_drop_pct: float = 0.35,  # tolere plus de variation car PnL-metric est plus volatile
) -> bool:
    """
    Verifie que le seuil optimal n'est pas un pic isole.
    Utilise maintenant le PF comme critere de stabilite (plus robuste que le score composite).
    """
    def get_pf(thr):
        e = next((x for x in sweep if abs(x["threshold"] - thr) < 0.015), None)
        return e["profit_factor"] if e else 0.0

    best_pf = get_pf(best_thr)
    if best_pf <= 1.0:
        return False

    lo_pf = get_pf(best_thr - delta)
    hi_pf = get_pf(best_thr + delta)

    # Les deux voisins doivent avoir PF >= 1.0 (pas de breakeven)
    return lo_pf >= 1.0 and hi_pf >= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Calibrateurs
# ─────────────────────────────────────────────────────────────────────────────

def _fit_calibrator(proba_raw: np.ndarray, y_true: np.ndarray, method: str):
    ece_before = _ece(proba_raw, y_true)

    if method == "platt":
        cal = PlattLR(C=1.0, random_state=42)
        cal.fit(proba_raw.reshape(-1, 1), y_true)
        proba_cal = cal.predict_proba(proba_raw.reshape(-1, 1))[:, 1]

    elif method == "isotonic":
        cal = IsotonicRegression(out_of_bounds="clip")
        cal.fit(proba_raw, y_true)
        proba_cal = cal.predict(proba_raw)

    else:
        raise ValueError(f"Méthode inconnue : {method!r}")

    ece_after = _ece(proba_cal, y_true)
    return cal, ece_before, ece_after


def _apply_calibrator(cal, proba_raw: np.ndarray, method: str) -> np.ndarray:
    if method == "platt":
        return cal.predict_proba(proba_raw.reshape(-1, 1))[:, 1]
    elif method == "isotonic":
        return cal.predict(proba_raw)
    return proba_raw


def _ece(proba: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
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


class _IdentityCalibrator:
    """Calibrateur identité — retourne les probas brutes."""
    def predict(self, X):
        return np.asarray(X).ravel()
    def predict_proba(self, X):
        p = np.asarray(X).ravel()
        return np.column_stack([1 - p, p])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_empty_metrics(ece_before, ece_after, method, n_val) -> Dict:
    return {
        "ece_before":             round(ece_before, 5) if not np.isnan(ece_before) else None,
        "ece_after":              round(ece_after, 5)  if not np.isnan(ece_after)  else None,
        "calibration_method":     method,
        "recommended_threshold":  0.65,
        "threshold_stable":       False,
        "threshold_sweep":        [],
        "n_val_calibration":      int(n_val),
    }


def _save_if_needed(out_dir: Optional[Path], calibrator, metrics: Dict) -> None:
    if out_dir is None:
        return
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "calibrator.pkl", "wb") as f:
        pickle.dump(calibrator, f)
    with open(out_dir / "calibration_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
