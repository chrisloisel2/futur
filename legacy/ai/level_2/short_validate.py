"""
level_2/short_validate.py — VALIDATION INTER-ANNÉES DU MODÈLE SHORT
====================================================================

La validation inter-années est OBLIGATOIRE pour le short.
Pourquoi :
  - Le signal short est plus volatil et régime-dépendant que le long
  - Un modèle excellent sur 2021 peut être catastrophique en 2022 ou 2023
  - Les faux signaux short coûtent plus cher (coût de portage, re-hausse rapide)

Un short instable doit être désactivé, pas ajusté à la marge.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ai.level_2.short_config import ShortModelConfig


def check_short_stability(
    clf,
    scaler,
    df: pd.DataFrame,
    val_mask: np.ndarray,
    cfg: Optional[ShortModelConfig] = None,
    threshold: float = 0.50,
    cost_pct: float = 0.0010,
) -> Tuple[bool, Dict]:
    """
    Vérifie la stabilité inter-années du modèle short.

    Calcule PF et WR pour chaque année dans la validation.
    Retourne (is_stable, details_dict).

    Arguments
    ---------
    clf        : modèle entraîné
    scaler     : StandardScaler
    df         : DataFrame complet avec index datetime et colonne future_ret_h
    val_mask   : masque val (boolean numpy array aligné sur df)
    cfg        : ShortModelConfig — seuils de stabilité
    threshold  : seuil de décision du modèle short (après calibration)
    cost_pct   : coût aller-retour
    """
    from ai.level_0.features import FEATURES_SHORT
    from ai.level_0.preprocessing import get_X

    cfg = cfg or ShortModelConfig()
    label_col = "y_short"

    df_val = df.loc[val_mask].copy()
    y_val  = df_val[label_col].values.astype(np.int32)

    valid = y_val >= 0
    df_val = df_val.iloc[valid.nonzero()[0]]

    if len(df_val) == 0:
        return False, {"error": "Aucune donnée val valide"}

    X_val = get_X(df, val_mask, FEATURES_SHORT)
    X_val = X_val[valid]

    proba = clf.predict_proba(scaler.transform(X_val))[:, 1]
    y_val = df_val[label_col].values.astype(np.int32)
    ret_val = df_val["future_ret_h"].values.astype(np.float64)

    if not isinstance(df_val.index, pd.DatetimeIndex):
        try:
            df_val = df_val.copy()
            df_val.index = pd.to_datetime(df_val.index)
        except Exception:
            return False, {"error": "Index non-datetime — impossible d'analyser par année"}

    years = df_val.index.year.unique().tolist()
    year_metrics: Dict[int, Dict] = {}
    bad_years: List[int] = []

    for yr in sorted(years):
        yr_mask = (df_val.index.year == yr)
        n_yr = int(yr_mask.sum())
        if n_yr < 20:
            year_metrics[yr] = {"n": n_yr, "skipped": True, "reason": "too_few_samples"}
            continue

        p_yr   = proba[yr_mask]
        ret_yr = ret_val[yr_mask]

        signals = p_yr >= threshold
        n_sig   = int(signals.sum())

        if n_sig < 5:
            year_metrics[yr] = {
                "n": n_yr, "n_signals": 0, "skipped": True,
                "reason": "too_few_signals"
            }
            bad_years.append(yr)
            continue

        rets_net  = ret_yr[signals] * (-1.0) - cost_pct
        wins      = (rets_net > 0).sum()
        wr        = float(wins) / n_sig
        gross_w   = float(rets_net[rets_net > 0].sum())
        gross_l   = float(abs(rets_net[rets_net < 0].sum()))
        pf        = gross_w / max(gross_l, 1e-9)

        is_yr_ok = (pf >= cfg.min_pf_per_year) and (wr >= cfg.min_wr_per_year)
        if not is_yr_ok:
            bad_years.append(yr)

        year_metrics[yr] = {
            "n": n_yr,
            "n_signals": n_sig,
            "profit_factor": round(pf, 3),
            "win_rate": round(wr, 3),
            "pnl": round(float(rets_net.sum()), 4),
            "ok": is_yr_ok,
        }

    n_bad = len(bad_years)
    is_stable = n_bad <= cfg.max_bad_years_allowed

    print(f"\n   [Short Stability Check]")
    for yr, m in sorted(year_metrics.items()):
        if m.get("skipped"):
            print(f"   {yr}: SKIP ({m.get('reason', '?')})")
        else:
            ok_str = "OK" if m["ok"] else "⚠  BAD"
            print(f"   {yr}: {ok_str}  "
                  f"signals={m['n_signals']}  "
                  f"PF={m['profit_factor']:.2f}  "
                  f"WR={m['win_rate']:.1%}  "
                  f"PnL={m['pnl']:.4f}")

    if is_stable:
        print(f"   → STABLE ({n_bad}/{len(years)} années sous les seuils, max={cfg.max_bad_years_allowed})")
    else:
        print(f"   → INSTABLE ({n_bad}/{len(years)} années sous les seuils, max={cfg.max_bad_years_allowed})")
        print(f"      Années problématiques : {bad_years}")
        if cfg.require_yearly_stability:
            print(f"      → Short doit être désactivé (require_yearly_stability=True)")

    report = {
        "is_stable": is_stable,
        "n_bad_years": n_bad,
        "bad_years": bad_years,
        "max_bad_years_allowed": cfg.max_bad_years_allowed,
        "threshold_used": threshold,
        "min_pf_per_year": cfg.min_pf_per_year,
        "min_wr_per_year": cfg.min_wr_per_year,
        "year_metrics": {str(k): v for k, v in year_metrics.items()},
    }

    return is_stable, report


def diagnose_short_failure(
    clf,
    scaler,
    df: pd.DataFrame,
    val_mask: np.ndarray,
    bad_years: List[int],
    cost_pct: float = 0.0010,
) -> Dict:
    """
    Pour les années mauvaises, diagnostique la cause probable.
    """
    from ai.level_0.features import FEATURES_SHORT
    from ai.level_0.preprocessing import get_X

    if not bad_years:
        return {"diagnosis": "no_bad_years"}

    df_val = df.loc[val_mask].copy()
    if not isinstance(df_val.index, pd.DatetimeIndex):
        try:
            df_val.index = pd.to_datetime(df_val.index)
        except Exception:
            return {"diagnosis": "non_datetime_index"}

    label_col = "y_short"
    y_val = df_val[label_col].values.astype(np.int32)
    valid = y_val >= 0
    df_val = df_val.iloc[valid.nonzero()[0]]

    X_val = get_X(df, val_mask, FEATURES_SHORT)
    X_val = X_val[valid]
    proba = clf.predict_proba(scaler.transform(X_val))[:, 1]

    results = {}
    for yr in bad_years:
        yr_mask = (df_val.index.year == yr)
        if yr_mask.sum() < 10:
            results[yr] = {"diagnosis": "too_few_samples"}
            continue

        ret_yr   = df_val.loc[yr_mask, "future_ret_h"].values.astype(np.float64)
        y_yr     = df_val.loc[yr_mask, label_col].values.astype(np.int32)
        proba_yr = proba[yr_mask]

        mean_ret    = float(ret_yr.mean())
        pos_ret_pct = float((ret_yr > 0).mean())
        short_base_rate = float((y_yr == 1).mean())

        high_conf   = float((proba_yr >= 0.55).mean())
        mean_proba  = float(proba_yr.mean())

        signals = proba_yr >= 0.50
        n_sig   = int(signals.sum())
        if n_sig > 0:
            precision = float(y_yr[signals].mean())
        else:
            precision = float("nan")

        if short_base_rate < 0.05:
            diag = "structural_bull_year"
        elif mean_proba < 0.40:
            diag = "model_signal_degraded"
        elif precision < 0.30:
            diag = "false_positive_surge"
        else:
            diag = "threshold_too_low"

        results[yr] = {
            "mean_return": round(mean_ret, 6),
            "pct_positive_bars": round(pos_ret_pct, 3),
            "short_base_rate": round(short_base_rate, 3),
            "mean_proba": round(mean_proba, 4),
            "high_conf_pct": round(high_conf, 3),
            "n_signals": n_sig,
            "signal_precision": round(precision, 3) if not np.isnan(precision) else None,
            "diagnosis": diag,
        }

        print(f"   [{yr}] {diag}: "
              f"base_rate={short_base_rate:.1%}  "
              f"mean_proba={mean_proba:.3f}  "
              f"precision={precision:.3f if not np.isnan(precision) else 'N/A'}")

    return {"year_diagnostics": results}
