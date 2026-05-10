"""
ai/level_0/short_labels.py
===========================
Labellisation SHORT asymétrique — horizon 4h primaire, 8h alternatif.

Colonnes produites par compute_short_label_columns()
─────────────────────────────────────────────────────
  future_ret_short_4h   : -log(Close[t+4] / Close[t])
  future_ret_short_8h   : -log(Close[t+8] / Close[t])
  mfe_short_4h          : max profit potentiel pour un short dans les 4 prochaines barres
  mfe_short_8h          : max profit potentiel pour un short dans les 8 prochaines barres
  mae_short_4h          : max perte adverse contre un short dans les 4 prochaines barres
  mae_short_8h          : max perte adverse contre un short dans les 8 prochaines barres
  squeeze_reject_4h     : bool — mae_short_4h > SHORT_SQUEEZE_LIMIT
  squeeze_reject_8h     : bool — mae_short_8h > SHORT_SQUEEZE_LIMIT
  late_short_reject     : bool — entrée tardive (prix déjà baissé > SHORT_LATE_ENTRY_ATR × ATR)

Colonnes produites par build_short_labels() (seuils calibrés sur train_mask)
─────────────────────────────────────────────────────────────────────────────
  y_short_4h     : 1 (positif) / 0 (négatif) / -1 (gray)
  y_short_8h     : idem sur horizon 8h
  y_short_clean  : meilleur label entre 4h et 8h (selon reward)
  y_short_gray   : 1 si gray (exclu de l'entraînement), 0 sinon

Conventions anti-leakage
─────────────────────────
  • compute_short_label_columns() appelé sur le DataFrame ENTIER avant tout split.
  • Les seuils de quantile (threshold) sont calibrés UNIQUEMENT sur train_mask dans build_short_labels().
  • Les colonnes forward ne doivent JAMAIS être exposées comme features au modèle.
  • Implémentation vectorisée via numpy.lib.stride_tricks.sliding_window_view — O(n).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ai.level_0.constants import (
    CLOSE_COL,
    ATR_COL,
    SHORT_HORIZON_BARS,
    SHORT_ALT_HORIZON_BARS,
    SHORT_TRADEABLE_QUANTILE,
    SHORT_MIN_ABS_RETURN,
    SHORT_COST_PCT,
    SHORT_SQUEEZE_LIMIT,
    SHORT_GRAY_MULT,
    SHORT_NON_REVERSAL_WINDOW,
    SHORT_LATE_ENTRY_ATR,
)

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "short_rebuild"

# ─── Helpers vectorisés ───────────────────────────────────────────────────────

def _forward_log_ret(log_close: np.ndarray, h: int) -> np.ndarray:
    """
    log(Close[t+h]) − log(Close[t]), NaN pour les h dernières barres.
    Résultat négatif = short profitable ← on retourne le signe (MFE convention).
    """
    n = len(log_close)
    out = np.full(n, np.nan, dtype=np.float64)
    out[: n - h] = log_close[h:] - log_close[: n - h]
    return out


def _sliding_mfe_mae_short(
    log_close: np.ndarray,
    h: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Pour chaque barre t, calcule dans la fenêtre [t+1, t+h] :
      mfe_short = max(log_close[t] - log_close[t+k])  k=1..h   (profit max du short)
      mae_short = max(log_close[t+k] - log_close[t])  k=1..h   (perte adverse max)

    Retourne (mfe_short, mae_short), NaN pour les h dernières barres.
    Implémentation O(n) via sliding_window_view sur les prix futurs.
    """
    n = len(log_close)
    mfe = np.full(n, np.nan, dtype=np.float64)
    mae = np.full(n, np.nan, dtype=np.float64)

    if n <= h:
        return mfe, mae

    # windows shape : (n - h, h)  — windows[i] = log_close[i+1 .. i+h]
    future = log_close[1:]
    if len(future) < h:
        return mfe, mae

    wins = sliding_window_view(future, window_shape=h)  # (len(future)-h+1, h)
    valid = wins.shape[0]

    ref = log_close[:valid]
    wins_min = wins.min(axis=1)
    wins_max = wins.max(axis=1)

    mfe[:valid] = ref - wins_min   # prix descend → short gagne
    mae[:valid] = wins_max - ref   # prix monte  → short perd

    mfe = np.clip(mfe, 0.0, None)
    mae = np.clip(mae, 0.0, None)
    return mfe, mae


def _late_short_reject(
    log_close: np.ndarray,
    atr: np.ndarray,
    window: int,
    atr_mult: float,
) -> np.ndarray:
    """
    late_short_reject[t] = True si le prix a déjà baissé de plus de
    atr_mult × ATR[t] dans les `window` barres précédentes.

    Indicateur backward-looking — aucun leakage.
    """
    n = len(log_close)
    reject = np.zeros(n, dtype=bool)

    if n <= window:
        return reject

    # backward returns : ret_back[t] = log_close[t] - log_close[t-window]
    ret_back = np.full(n, np.nan, dtype=np.float64)
    ret_back[window:] = log_close[window:] - log_close[:-window]

    atr_safe = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
    threshold = atr_mult * atr_safe

    already_dropped = ret_back < -threshold
    reject = np.where(np.isfinite(ret_back) & np.isfinite(threshold), already_dropped, False)
    return reject.astype(bool)


# ─── Étape 0 : colonnes forward (AVANT tout split) ───────────────────────────

def compute_short_label_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les colonnes forward nécessaires aux labels SHORT.

    DOIT être appelée sur le DataFrame ENTIER avant tout split train/val/test.
    Ne jamais exposer ces colonnes comme features au modèle.

    Colonnes ajoutées
    -----------------
    future_ret_short_4h, future_ret_short_8h : -log-rendement forward
    mfe_short_4h, mfe_short_8h               : max favourable excursion
    mae_short_4h, mae_short_8h               : max adverse excursion
    squeeze_reject_4h, squeeze_reject_8h     : bool filtre squeeze
    late_short_reject                        : bool filtre entrée tardive
    """
    if CLOSE_COL not in df.columns:
        raise RuntimeError(
            f"Colonne '{CLOSE_COL}' manquante. "
            "compute_short_label_columns() requiert les prix de clôture."
        )

    df = df.copy()
    close_raw = df[CLOSE_COL].values.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.where(close_raw > 0, np.log(close_raw), np.nan)

    n = len(log_close)

    for h, suffix in [(SHORT_HORIZON_BARS, "4h"), (SHORT_ALT_HORIZON_BARS, "8h")]:
        raw_ret = _forward_log_ret(log_close, h)
        df[f"future_ret_short_{suffix}"] = -raw_ret  # négatif → short profite

        mfe, mae = _sliding_mfe_mae_short(log_close, h)
        df[f"mfe_short_{suffix}"] = mfe
        df[f"mae_short_{suffix}"] = mae
        df[f"squeeze_reject_{suffix}"] = mae > SHORT_SQUEEZE_LIMIT

    atr = df[ATR_COL].values.astype(np.float64) if ATR_COL in df.columns \
          else np.full(n, np.nan, dtype=np.float64)

    reject = _late_short_reject(log_close, atr, window=SHORT_NON_REVERSAL_WINDOW,
                                 atr_mult=SHORT_LATE_ENTRY_ATR)
    df["late_short_reject"] = reject

    n_valid_4h = int(np.isfinite(df["future_ret_short_4h"].values).sum())
    n_valid_8h = int(np.isfinite(df["future_ret_short_8h"].values).sum())
    n_squeeze  = int(df["squeeze_reject_4h"].sum())
    n_late     = int(df["late_short_reject"].sum())

    print(
        f"   compute_short_label_columns : "
        f"ret_4h={n_valid_4h:,} valides | ret_8h={n_valid_8h:,} valides | "
        f"squeeze_reject_4h={n_squeeze:,} | late_reject={n_late:,} ({n_late/n:.1%})"
    )
    return df


# ─── Étape 1 : labels avec seuils calibrés sur train ────────────────────────

def _build_short_label_for_horizon(
    ret: np.ndarray,
    mfe: np.ndarray,
    mae: np.ndarray,
    squeeze_reject: np.ndarray,
    late_reject: np.ndarray,
    threshold: float,
) -> np.ndarray:
    """
    Construit le label 0/1/-1 pour un horizon donné.

    Label 1 (positif)
    ─────────────────
    • ret > threshold
    • ret >= SHORT_MIN_ABS_RETURN
    • mfe > SHORT_COST_PCT * 2
    • mae < SHORT_SQUEEZE_LIMIT
    • NOT late_short_reject

    Label -1 (gray — exclu de l'entraînement)
    ─────────────────────────────────────────
    • ret entre threshold et threshold × SHORT_GRAY_MULT
    • OU mae proche de SHORT_SQUEEZE_LIMIT (±20%)
    • OU breakdown tardif détecté

    Label 0 (négatif) : tout le reste.
    """
    thr_gray = threshold * SHORT_GRAY_MULT
    mae_squeeze_proximity = (mae >= SHORT_SQUEEZE_LIMIT * 0.80) & (mae < SHORT_SQUEEZE_LIMIT * 1.20)

    positive_mask = (
        (ret > threshold)
        & (ret >= SHORT_MIN_ABS_RETURN)
        & (mfe > SHORT_COST_PCT * 2)
        & (mae < SHORT_SQUEEZE_LIMIT)
        & (~squeeze_reject)
        & (~late_reject)
        & np.isfinite(ret)
    )

    gray_mask = (
        ~positive_mask
        & np.isfinite(ret)
        & (
            ((ret > threshold) & (ret <= thr_gray))
            | mae_squeeze_proximity
            | (late_reject & (ret > threshold * 0.5))
        )
    )

    y = np.zeros(len(ret), dtype=np.int8)
    y[positive_mask] = 1
    y[gray_mask] = -1
    y[~np.isfinite(ret)] = -1
    return y


def build_short_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
) -> pd.DataFrame:
    """
    Construit les labels SHORT y_short_4h, y_short_8h, y_short_clean, y_short_gray.

    Pré-requis : compute_short_label_columns(df) doit avoir été appelé.

    Arguments
    ---------
    df          : DataFrame complet avec les colonnes forward déjà calculées.
    train_mask  : masque booléen numpy identifiant les barres d'entraînement.
                  Les seuils de quantile sont calibrés UNIQUEMENT sur ce sous-ensemble.

    Retourne
    --------
    df enrichi des colonnes y_short_*.
    """
    required = ["future_ret_short_4h", "mfe_short_4h", "mae_short_4h",
                "future_ret_short_8h", "mfe_short_8h", "mae_short_8h",
                "squeeze_reject_4h", "squeeze_reject_8h", "late_short_reject"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Colonnes manquantes : {missing}. "
            "Appeler compute_short_label_columns(df) avant build_short_labels()."
        )

    df = df.copy()

    def _to_arr(col: str) -> np.ndarray:
        return df[col].values.astype(np.float64)

    def _to_bool(col: str) -> np.ndarray:
        return df[col].values.astype(bool)

    ret_4h = _to_arr("future_ret_short_4h")
    ret_8h = _to_arr("future_ret_short_8h")
    mfe_4h = _to_arr("mfe_short_4h")
    mfe_8h = _to_arr("mfe_short_8h")
    mae_4h = _to_arr("mae_short_4h")
    mae_8h = _to_arr("mae_short_8h")
    sq_4h  = _to_bool("squeeze_reject_4h")
    sq_8h  = _to_bool("squeeze_reject_8h")
    late   = _to_bool("late_short_reject")

    # Calibration des seuils UNIQUEMENT sur train
    train_ret_4h = ret_4h[train_mask & np.isfinite(ret_4h)]
    train_ret_8h = ret_8h[train_mask & np.isfinite(ret_8h)]

    if len(train_ret_4h) == 0 or len(train_ret_8h) == 0:
        raise RuntimeError(
            "Aucune donnée valide dans train_mask — impossible de calibrer le seuil."
        )

    thr_4h = float(np.quantile(train_ret_4h, SHORT_TRADEABLE_QUANTILE))
    thr_8h = float(np.quantile(train_ret_8h, SHORT_TRADEABLE_QUANTILE))

    thr_4h = max(thr_4h, SHORT_MIN_ABS_RETURN)
    thr_8h = max(thr_8h, SHORT_MIN_ABS_RETURN)

    y_4h = _build_short_label_for_horizon(ret_4h, mfe_4h, mae_4h, sq_4h, late, thr_4h)
    y_8h = _build_short_label_for_horizon(ret_8h, mfe_8h, mae_8h, sq_8h, late, thr_8h)

    # y_short_clean : meilleur label entre 4h et 8h (selon reward = ret)
    reward_4h = np.where(np.isfinite(ret_4h), ret_4h, -np.inf)
    reward_8h = np.where(np.isfinite(ret_8h), ret_8h, -np.inf)
    prefer_8h = reward_8h > reward_4h

    y_clean = np.where(prefer_8h, y_8h, y_4h).astype(np.int8)

    df["y_short_4h"]    = y_4h
    df["y_short_8h"]    = y_8h
    df["y_short_clean"] = y_clean
    df["y_short_gray"]  = (y_clean == -1).astype(np.int8)

    n = len(df)
    n4_pos  = int((y_4h  ==  1).sum())
    n4_gray = int((y_4h  == -1).sum())
    n8_pos  = int((y_8h  ==  1).sum())
    n8_gray = int((y_8h  == -1).sum())
    nc_pos  = int((y_clean == 1).sum())
    nc_gray = int((y_clean == -1).sum())

    print(
        f"\n   Labels SHORT construits :\n"
        f"   4h  : positif={n4_pos:,} ({n4_pos/n:.1%})  gray={n4_gray:,}  thr={thr_4h:.5f}\n"
        f"   8h  : positif={n8_pos:,} ({n8_pos/n:.1%})  gray={n8_gray:,}  thr={thr_8h:.5f}\n"
        f"   clean (best): positif={nc_pos:,} ({nc_pos/n:.1%})  gray={nc_gray:,}"
    )

    if nc_pos < 200:
        print(f"   Peu de labels positifs SHORT ({nc_pos}) — vérifier SHORT_TRADEABLE_QUANTILE")

    return df


# ─── Audit ────────────────────────────────────────────────────────────────────

def audit_short_labels(df: pd.DataFrame) -> Dict:
    """
    Calcule et retourne les statistiques de qualité des labels SHORT.
    Sauvegarde les résultats dans reports/short_rebuild/short_label_audit.{csv,json}.

    Pré-requis : build_short_labels(df, train_mask) doit avoir été appelé.
    """
    required_label_cols = ["y_short_4h", "y_short_8h", "y_short_clean", "y_short_gray"]
    missing = [c for c in required_label_cols if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Colonnes de labels manquantes : {missing}. "
            "Appeler build_short_labels(df, train_mask) avant audit_short_labels()."
        )

    n = len(df)

    def _safe_mean(arr: np.ndarray) -> float:
        finite = arr[np.isfinite(arr)]
        return float(finite.mean()) if len(finite) > 0 else float("nan")

    def _safe_median(arr: np.ndarray) -> float:
        finite = arr[np.isfinite(arr)]
        return float(np.median(finite)) if len(finite) > 0 else float("nan")

    y_clean = df["y_short_clean"].values.astype(np.int8)
    positive_mask = y_clean == 1
    negative_mask = y_clean == 0
    gray_mask     = y_clean == -1

    positive_rate = float(positive_mask.mean())
    negative_rate = float(negative_mask.mean())
    gray_rate     = float(gray_mask.mean())

    def _arr(col: str) -> np.ndarray:
        if col not in df.columns:
            return np.full(n, np.nan)
        return df[col].values.astype(np.float64)

    ret_4h = _arr("future_ret_short_4h")
    ret_8h = _arr("future_ret_short_8h")
    mfe_4h = _arr("mfe_short_4h")
    mae_4h = _arr("mae_short_4h")

    median_return = _safe_median(ret_4h)
    mean_return   = _safe_mean(ret_4h)
    mean_mfe      = _safe_mean(mfe_4h)
    mean_mae      = _safe_mean(mae_4h)

    sq_4h = _arr("squeeze_reject_4h").astype(bool) if "squeeze_reject_4h" in df.columns \
            else np.zeros(n, dtype=bool)
    late  = df["late_short_reject"].values.astype(bool) if "late_short_reject" in df.columns \
            else np.zeros(n, dtype=bool)

    squeeze_reject_rate   = float(sq_4h.mean())
    late_short_reject_rate = float(late.mean())

    # Recalcule le seuil sur toutes les barres valides (approximation en mode audit)
    valid_ret = ret_4h[np.isfinite(ret_4h)]
    threshold_used = float(np.quantile(valid_ret, SHORT_TRADEABLE_QUANTILE)) \
                     if len(valid_ret) > 0 else float("nan")
    threshold_used = max(threshold_used, SHORT_MIN_ABS_RETURN)

    breakeven_win_rate = SHORT_COST_PCT / (SHORT_COST_PCT + threshold_used) \
                         if threshold_used > 0 else float("nan")

    stats: Dict = {
        "positive_rate":         round(positive_rate, 4),
        "negative_rate":         round(negative_rate, 4),
        "gray_rate":             round(gray_rate, 4),
        "median_return":         round(median_return, 6),
        "mean_return":           round(mean_return, 6),
        "mean_mfe":              round(mean_mfe, 6),
        "mean_mae":              round(mean_mae, 6),
        "squeeze_reject_rate":   round(squeeze_reject_rate, 4),
        "late_short_reject_rate": round(late_short_reject_rate, 4),
        "threshold_used":        round(threshold_used, 6),
        "cost_used":             SHORT_COST_PCT,
        "breakeven_win_rate":    round(breakeven_win_rate, 4),
        "n_total":               n,
        "n_positive":            int(positive_mask.sum()),
        "n_negative":            int(negative_mask.sum()),
        "n_gray":                int(gray_mask.sum()),
        "short_horizon_bars":    SHORT_HORIZON_BARS,
        "short_alt_horizon_bars": SHORT_ALT_HORIZON_BARS,
        "squeeze_limit":         SHORT_SQUEEZE_LIMIT,
        "tradeable_quantile":    SHORT_TRADEABLE_QUANTILE,
    }

    print(f"\n   Audit labels SHORT :")
    print(f"   positif={positive_rate:.1%}  négatif={negative_rate:.1%}  gray={gray_rate:.1%}")
    print(f"   ret médian={median_return:.4%}  MFE moyen={mean_mfe:.4%}  MAE moyen={mean_mae:.4%}")
    print(f"   squeeze_reject={squeeze_reject_rate:.1%}  late_reject={late_short_reject_rate:.1%}")
    print(f"   threshold={threshold_used:.5f}  breakeven_WR={breakeven_win_rate:.1%}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # CSV
    row_stats = {k: [v] for k, v in stats.items()}
    csv_df = pd.DataFrame(row_stats)
    csv_path = REPORT_DIR / "short_label_audit.csv"
    csv_df.to_csv(csv_path, index=False)
    print(f"   CSV sauvegardé : {csv_path}")

    # JSON
    json_path = REPORT_DIR / "short_label_audit.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, default=str)
    print(f"   JSON sauvegardé : {json_path}")

    return stats
