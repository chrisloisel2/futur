#!/usr/bin/env python3
"""
scripts/walk_forward_short_4h.py — WALK-FORWARD SHORT STRICT (séparé du LONG)
==============================================================================

Walk-forward avec folds annuels :
  train_mask = year <= fold_year - 2
  val_mask   = year == fold_year - 1
  test_mask  = year == fold_year

Pipeline par fold :
  1. compute_all_short_features(df)
  2. compute_short_label_columns(df)  — labels sur le dataset entier
  3. build_short_labels(df, train_mask)  — seuils sur train uniquement
  4. Filtre y != -1 (exclure gray zone du training)
  5. SMOTE / RandomOverSampler si n_positifs < 1000 sur train
  6. compute_short_permission_context(df)  — contextes de routing
  7. compute_no_short_gate(df)  — gate
  8. Entraîner TRMShortFleet
  9. Calibrer seuils sur val : calibrate_short_thresholds
 10. Backtest test fold

Métriques par fold :
  n_trades, pf, expectancy, wr, avg_win, avg_loss, max_drawdown,
  squeeze_loss_rate, cost_normal_pf, cost_stress_pf, cost_extreme_pf,
  context_contribution (dict), threshold_by_context (dict), fold_status

Verdict global :
  SHORT_REJECTED           — au moins 1 fold catastrophique
  SHORT_PROMISING_BUT_UNSAFE — pas assez de folds OK ou de trades
  SHORT_PAPER_CANDIDATE    — critères minimaux atteints (jamais SHORT_DEPLOYABLE)

Usage :
  python scripts/walk_forward_short_4h.py
  python scripts/walk_forward_short_4h.py --data data/BTCUSDT_features.parquet
  python scripts/walk_forward_short_4h.py --folds 2022,2023,2024
"""
from __future__ import annotations

import argparse
import json
import sys
import os
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Imports du projet — avec fallbacks gracieux
# ─────────────────────────────────────────────────────────────────────────────

_IMPORT_ERRORS: List[str] = []

try:
    from ai.level_0.short_labels import compute_short_label_columns, build_short_labels
    _HAS_SHORT_LABELS = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.short_labels : {e}")
    _HAS_SHORT_LABELS = False

try:
    from ai.level_0.short_features import compute_all_short_features
    _HAS_SHORT_FEATURES = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.short_features : {e}")
    _HAS_SHORT_FEATURES = False

try:
    from ai.level_1.short_rules import compute_no_short_gate, compute_short_permission_context
    _HAS_SHORT_RULES = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_1.short_rules : {e}")
    _HAS_SHORT_RULES = False

try:
    from ai.level_2.short_specialists import TRMShortFleet
    _HAS_SHORT_FLEET = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_2.short_specialists : {e}")
    _HAS_SHORT_FLEET = False

from ai.level_2.short_calibration import (
    calibrate_short_thresholds,
    save_thresholds,
    simulate_short_trades,
    get_threshold_for_context,
    summarize_calibration,
    COST_NORMAL,
    COST_STRESS,
    COST_EXTREME,
    MAX_SHORT_DD,
    MAX_SQUEEZE_LOSS_RATE,
    SHORT_DEPLOY_PF,
    SHORT_CATASTROPHIC_PF,
    MIN_SHORT_FOLDS_OK,
    MIN_SHORT_TRADES_TOTAL,
    MIN_SHORT_TRADES_PER_VALID_FOLD,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes locales
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DIR = ROOT / "reports" / "short_rebuild"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Colonnes cibles SHORT (avec fallbacks)
RET_COL_SHORT    = "future_ret_short_4h"
RET_COL_FALLBACK = "future_ret_4h"
MFE_COL          = "mfe_short_4h"
MAE_COL          = "mae_short_4h"
SQUEEZE_COL      = "squeeze_reject_4h"
CONTEXT_COL      = "short_permission_context"
GATE_COL         = "no_short_gate"

# Label short
# build_short_labels écrit y_short_clean (meilleur entre 4h et 8h).
# Fallback sur y_short si le module legacy est utilisé.
LABEL_SHORT_COL  = "y_short_clean"
LABEL_SHORT_ALT  = "y_short"        # fallback legacy

# Sizing : 0.1% equity par trade
POSITION_SIZE_PCT = 0.001

INITIAL_EQUITY = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
# Chargement des données
# ─────────────────────────────────────────────────────────────────────────────

def _find_data_files() -> List[Path]:
    """Cherche les CSV/parquet de features dans data/."""
    data_dir = ROOT / "data"
    if not data_dir.exists():
        return []
    candidates: List[Path] = []
    for pattern in ("*_features.parquet", "*_features.csv", "*USDT*features.parquet",
                    "*USDT*features.csv"):
        candidates.extend(data_dir.glob(pattern))
    return sorted(set(candidates))


def _load_file(path: Path) -> pd.DataFrame:
    """Charge un fichier CSV ou parquet."""
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True)

    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass
    return df


def _make_synthetic_data(n_bars: int = 10_000, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Génère des données synthétiques de test.
    Utilisé uniquement quand aucun fichier de données réel n'est trouvé.
    """
    rng = np.random.default_rng(42)
    start = pd.Timestamp("2019-01-01")
    idx = pd.date_range(start, periods=n_bars, freq="1h")

    log_rets = rng.normal(0.0001, 0.008, n_bars)
    log_price = np.cumsum(log_rets)
    close = np.exp(log_price) * 10_000

    df = pd.DataFrame(index=idx)
    df["Close"]  = close
    df["Open"]   = close * (1 + rng.normal(0, 0.001, n_bars))
    df["High"]   = close * (1 + np.abs(rng.normal(0, 0.004, n_bars)))
    df["Low"]    = close * (1 - np.abs(rng.normal(0, 0.004, n_bars)))
    df["Volume"] = rng.lognormal(10, 1, n_bars)
    df["symbol"] = symbol

    # Features techniques minimales
    df["rsi_14"]           = 50 + rng.normal(0, 10, n_bars).clip(-40, 40)
    df["atr_14"]           = close * rng.uniform(0.005, 0.015, n_bars)
    df["ema_spread_50_200"] = rng.normal(0, 0.02, n_bars)
    df["dist_ema_50"]      = rng.normal(0, 0.02, n_bars)
    df["boll_width_20"]    = rng.uniform(0.01, 0.05, n_bars)
    df["rv_ratio_24_72"]   = rng.uniform(0.6, 2.0, n_bars)
    df["above_vwap_4h"]    = rng.uniform(0, 1, n_bars)
    df["gc_fresh"]         = (rng.uniform(0, 1, n_bars) > 0.9).astype(float)
    df["days_since_golden_cross"] = rng.uniform(0, 500, n_bars)
    df["mom_logret_4"]     = rng.normal(0, 0.01, n_bars)
    df["regime_short"]     = rng.choice(
        ["NO_SHORT", "SHORTABLE", "NEUTRAL"],
        n_bars, p=[0.35, 0.35, 0.30]
    )

    # Labels synthétiques : future_ret_4h = somme des 4 log-rets suivants
    future_ret = np.zeros(n_bars)
    for i in range(n_bars - 4):
        future_ret[i] = log_rets[i + 1] + log_rets[i + 2] + log_rets[i + 3] + log_rets[i + 4]
    future_ret[-4:] = np.nan

    df["future_ret_4h"]       = future_ret
    df[RET_COL_SHORT]         = future_ret  # symétrique (synthétique)
    df[MFE_COL]               = np.abs(rng.normal(0.005, 0.003, n_bars))
    df[MAE_COL]               = np.abs(rng.normal(0.004, 0.003, n_bars))
    df[SQUEEZE_COL]           = rng.uniform(0, 1, n_bars) > 0.8

    # Label y_short synthétique
    thr_short = np.nanpercentile(np.abs(future_ret[~np.isnan(future_ret)]), 88)
    y_short = np.where(
        np.isnan(future_ret), -1,
        np.where(future_ret < -thr_short, 1,
        np.where(future_ret > thr_short * 0.85, -1, 0))
    )
    df[LABEL_SHORT_COL] = y_short.astype(np.int32)

    return df


def load_data(data_path: Optional[str] = None) -> Tuple[pd.DataFrame, bool]:
    """
    Charge les données réelles ou génère des données synthétiques.

    Retourne (df, is_synthetic).
    """
    # Chemin explicite fourni
    if data_path:
        p = Path(data_path)
        if p.exists():
            print(f"  Chargement : {p}")
            return _load_file(p), False
        else:
            print(f"  AVERTISSEMENT : fichier non trouvé ({p}), recherche auto…")

    # Recherche automatique
    files = _find_data_files()
    if files:
        p = files[0]
        print(f"  Chargement auto : {p}")
        return _load_file(p), False

    # Données synthétiques
    print()
    print("  " + "!" * 60)
    print("  AVERTISSEMENT : aucune donnée réelle trouvée dans data/")
    print("  Génération de données SYNTHÉTIQUES pour les tests.")
    print("  Les résultats n'ont aucune valeur financière.")
    print("  " + "!" * 60)
    print()
    return _make_synthetic_data(), True


# ─────────────────────────────────────────────────────────────────────────────
# Feature/label engineering — avec fallbacks
# ─────────────────────────────────────────────────────────────────────────────

def _apply_short_features(df: pd.DataFrame) -> pd.DataFrame:
    """Applique compute_all_short_features si disponible."""
    if _HAS_SHORT_FEATURES:
        try:
            return compute_all_short_features(df)
        except Exception as e:
            print(f"  WARN compute_all_short_features : {e}")
    return df


def _apply_short_labels_full(df: pd.DataFrame) -> pd.DataFrame:
    """Applique compute_short_label_columns sur le dataset entier si disponible."""
    if _HAS_SHORT_LABELS:
        try:
            return compute_short_label_columns(df)
        except Exception as e:
            print(f"  WARN compute_short_label_columns : {e}")

    # Fallback : si les colonnes nécessaires sont déjà présentes, ne rien faire
    if RET_COL_SHORT not in df.columns and "future_ret_4h" in df.columns:
        df = df.copy()
        df[RET_COL_SHORT] = df["future_ret_4h"]
    return df


def _build_short_labels_train(df: pd.DataFrame, train_mask: np.ndarray) -> pd.DataFrame:
    """Applique build_short_labels sur train uniquement si disponible."""
    if _HAS_SHORT_LABELS:
        try:
            return build_short_labels(df, train_mask)
        except Exception as e:
            print(f"  WARN build_short_labels : {e}")
    return df


def _apply_permission_context(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule le contexte de permission short si disponible.

    compute_short_permission_context retourne un petit DataFrame de colonnes ctx_*.
    On fusionne ces colonnes dans df (on ne remplace pas df entier).
    """
    if _HAS_SHORT_RULES:
        try:
            ctx_df = compute_short_permission_context(df)
            # ctx_df est un DataFrame de colonnes ctx_* + no_short (même index que df)
            df = df.copy()
            for col in ctx_df.columns:
                df[col] = ctx_df[col].values
            # Construire CONTEXT_COL comme nom de contexte textuel
            if CONTEXT_COL not in df.columns:
                ctx_cols_ordered = [
                    "ctx_crowded_longs", "ctx_breakdown", "ctx_failed_breakout",
                    "ctx_liquidity_stress", "ctx_bear_continuation",
                    "ctx_macro_riskoff", "ctx_general_short",
                ]
                available = [c for c in ctx_cols_ordered if c in df.columns]
                if available:
                    ctx_names = np.full(len(df), "general", dtype=object)
                    for col in reversed(available):
                        ctx_label = col.replace("ctx_", "")
                        mask_col = df[col].values.astype(bool)
                        ctx_names[mask_col] = ctx_label
                    df[CONTEXT_COL] = ctx_names
            return df
        except Exception as e:
            print(f"  WARN compute_short_permission_context : {e}")

    # Fallback : utiliser le régime existant ou un contexte générique
    if CONTEXT_COL not in df.columns:
        df = df.copy()
        if "regime_short" in df.columns:
            # Mapper régime → contexte simple
            ctx_map = {"SHORTABLE": "shortable", "NEUTRAL": "neutral", "NO_SHORT": "no_short"}
            df[CONTEXT_COL] = df["regime_short"].map(ctx_map).fillna("general")
        else:
            df[CONTEXT_COL] = "general"
    return df


def _apply_no_short_gate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule le gate no_short si disponible.
    compute_no_short_gate retourne une Series "no_short" — on l'ajoute à df.
    """
    if _HAS_SHORT_RULES:
        try:
            gate_series = compute_no_short_gate(df)
            # gate_series est une pd.Series(bool) nommée "no_short"
            df = df.copy()
            df[GATE_COL] = gate_series.values.astype(bool)
            return df
        except Exception as e:
            print(f"  WARN compute_no_short_gate : {e}")

    # Fallback : gate basé sur le régime
    if GATE_COL not in df.columns:
        df = df.copy()
        if "regime_short" in df.columns:
            df[GATE_COL] = (df["regime_short"] == "NO_SHORT")
        else:
            df[GATE_COL] = False
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Oversampling
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_label_col(df: pd.DataFrame) -> str:
    """Retourne le nom de la colonne label SHORT disponible dans df."""
    if LABEL_SHORT_COL in df.columns:
        return LABEL_SHORT_COL
    if LABEL_SHORT_ALT in df.columns:
        return LABEL_SHORT_ALT
    return LABEL_SHORT_COL  # retournera une KeyError explicite plus tard


def _maybe_oversample(
    df_train: pd.DataFrame,
    label_col: str = LABEL_SHORT_COL,
    min_positives: int = 1_000,
) -> pd.DataFrame:
    """
    Applique RandomOverSampler si le nombre de positifs est < min_positives.
    Ne fait rien si imblearn n'est pas installé.
    """
    valid_mask = df_train[label_col] >= 0
    df_valid   = df_train.loc[valid_mask]
    n_pos      = int((df_valid[label_col] == 1).sum())

    if n_pos >= min_positives:
        return df_train

    try:
        from imblearn.over_sampling import RandomOverSampler
    except ImportError:
        print(f"  WARN : imblearn non disponible — oversampling ignoré (n_pos={n_pos})")
        return df_train

    print(f"  Oversampling : n_positifs={n_pos} < {min_positives} → RandomOverSampler")

    num_cols = [c for c in df_valid.columns if pd.api.types.is_numeric_dtype(df_valid[c])]
    X = df_valid[num_cols].fillna(0.0).values
    y = df_valid[label_col].values.astype(np.int32)

    ros = RandomOverSampler(random_state=42)
    try:
        X_res, y_res = ros.fit_resample(X, y)
    except Exception as e:
        print(f"  WARN RandomOverSampler échoué : {e}")
        return df_train

    df_res = pd.DataFrame(X_res, columns=num_cols)
    df_res[label_col] = y_res

    # Restaurer colonnes non-numériques avec la valeur modale
    for col in df_valid.columns:
        if col not in num_cols and col != label_col:
            df_res[col] = df_valid[col].mode().iloc[0] if len(df_valid[col].mode()) > 0 else None

    n_pos_after = int((df_res[label_col] == 1).sum())
    print(f"  Après oversampling : {len(df_res):,} barres  (SHORT positifs : {n_pos_after:,})")
    return df_res


# ─────────────────────────────────────────────────────────────────────────────
# TRMShortFleet fallback
# ─────────────────────────────────────────────────────────────────────────────

class _FallbackShortModel:
    """
    Modèle SHORT minimaliste utilisé quand TRMShortFleet n'est pas disponible.
    Entraîne un HistGradientBoostingClassifier sur toutes les colonnes numériques.
    """
    def __init__(self):
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        self.clf    = HistGradientBoostingClassifier(
            max_iter=200, max_depth=4, learning_rate=0.05,
            min_samples_leaf=15, class_weight="balanced", random_state=42,
        )
        self.scaler = StandardScaler()
        self.feats: List[str] = []

    def fit(self, df: pd.DataFrame, train_mask: np.ndarray, label_col: Optional[str] = None):
        if label_col is None:
            label_col = _resolve_label_col(df)
        self._label_col = label_col

        num_cols = [
            c for c in df.columns
            if pd.api.types.is_numeric_dtype(df[c])
            and c != label_col
            and "future_ret" not in c
            and "y_long" not in c
            and "tradeable" not in c
        ]
        self.feats = num_cols

        df_tr = df.loc[train_mask]
        valid  = df_tr[label_col] >= 0
        df_tr  = df_tr.loc[valid]

        if len(df_tr) == 0 or int((df_tr[label_col] == 1).sum()) < 5:
            self._trained = False
            return self

        X = df_tr[self.feats].fillna(0.0).values.astype(np.float32)
        y = df_tr[label_col].values.astype(np.int32)

        self.scaler.fit(X)
        self.clf.fit(self.scaler.transform(X), y)
        self._trained = True
        return self

    def predict_proba_short(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        if not getattr(self, "_trained", False):
            return np.full(int(mask.sum()), 0.5)
        X = df.loc[mask, self.feats].fillna(0.0).values.astype(np.float32)
        return self.clf.predict_proba(self.scaler.transform(X))[:, 1]


def _train_short_model(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
) -> object:
    """Entraîne le modèle SHORT (TRMShortFleet ou fallback)."""
    if _HAS_SHORT_FLEET:
        try:
            from ai.level_0.features import FEATURES_SHORT_GAMECHANGER, FEATURES_SHORT
            feats = list(dict.fromkeys(FEATURES_SHORT_GAMECHANGER + FEATURES_SHORT))
            feats = [f for f in feats if f in df.columns]
            if not feats:
                raise ValueError("Aucune feature SHORT disponible dans le DataFrame")

            fleet = TRMShortFleet(features=feats)

            label_col = _resolve_label_col(df)
            df_tr = df.iloc[train_mask] if isinstance(train_mask, np.ndarray) and train_mask.dtype == bool \
                    else df.loc[train_mask]
            df_vl = df.iloc[val_mask]   if isinstance(val_mask,   np.ndarray) and val_mask.dtype == bool \
                    else df.loc[val_mask]

            y_tr  = df_tr[label_col].values.astype(np.int32)
            y_vl  = df_vl[label_col].values.astype(np.int32)

            ctx_cols = [c for c in df_tr.columns if c.startswith("ctx_")]
            ctx_tr   = df_tr[ctx_cols] if ctx_cols else pd.DataFrame(
                index=df_tr.index, data={"ctx_general_short": np.ones(len(df_tr), dtype=bool)}
            )

            fleet.fit(df_tr, y_tr, df_vl, y_vl, ctx_tr)
            return fleet
        except Exception as e:
            print(f"  WARN TRMShortFleet.train : {e} — utilisation du fallback")

    model = _FallbackShortModel()
    model.fit(df, train_mask)
    return model


def _predict_short(model, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
    """Prédit les probabilités SHORT sur le sous-ensemble mask."""
    if _HAS_SHORT_FLEET and isinstance(model, TRMShortFleet):
        try:
            df_sub = df.iloc[mask] if mask.dtype == bool else df.loc[mask]
            ctx_cols = [c for c in df_sub.columns if c.startswith("ctx_")]
            ctx_df = df_sub[ctx_cols] if ctx_cols else pd.DataFrame(
                index=df_sub.index,
                data={"ctx_general_short": np.ones(len(df_sub), dtype=bool)}
            )
            return model.predict_short_proba(df_sub, ctx_df)
        except Exception as e:
            print(f"  WARN TRMShortFleet.predict : {e}")
            return np.full(int(mask.sum()), 0.5)

    if isinstance(model, _FallbackShortModel):
        return model.predict_proba_short(df, mask)

    return np.full(int(mask.sum()), 0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Backtest d'un fold test
# ─────────────────────────────────────────────────────────────────────────────

def _backtest_fold(
    df_test: pd.DataFrame,
    p_short: np.ndarray,
    thresholds: dict,
    costs: float = COST_NORMAL,
) -> Dict:
    """
    Backtest sur le fold test en appliquant les seuils par contexte.

    Logique :
      - Pour chaque barre : si p_short >= threshold[context] AND NOT no_short_gate
        → prendre le trade SHORT
      - Rendement trade = -future_ret_short_4h - costs
      - Sizing : 0.1% equity par trade

    Retourne les métriques détaillées du fold.
    """
    n = len(df_test)
    if n == 0:
        return _empty_fold_metrics()

    # Colonnes de base
    ret_col = RET_COL_SHORT if RET_COL_SHORT in df_test.columns else RET_COL_FALLBACK
    if ret_col not in df_test.columns:
        return _empty_fold_metrics()

    ret_array     = df_test[ret_col].values.astype(np.float64)
    ctx_array     = df_test[CONTEXT_COL].values   if CONTEXT_COL in df_test.columns \
                    else np.full(n, "general", dtype=object)
    gate_array    = df_test[GATE_COL].values.astype(bool) if GATE_COL in df_test.columns \
                    else np.zeros(n, dtype=bool)
    squeeze_array = df_test[SQUEEZE_COL].values.astype(bool) if SQUEEZE_COL in df_test.columns \
                    else np.zeros(n, dtype=bool)
    mae_array     = df_test[MAE_COL].values.astype(np.float64) if MAE_COL in df_test.columns \
                    else np.zeros(n)

    equity    = INITIAL_EQUITY
    eq_max    = equity
    max_dd    = 0.0
    pnl_list: List[float]  = []
    ctx_trades: Dict[str, List[float]] = {}

    n_squeeze_losses = 0

    for i in range(n):
        if gate_array[i]:
            continue

        ctx = str(ctx_array[i])
        thr = get_threshold_for_context(thresholds, ctx)
        if thr is None:
            continue
        if p_short[i] < thr:
            continue

        ret_raw = ret_array[i]
        if np.isnan(ret_raw):
            continue

        # SHORT : profit si prix baisse
        net_ret  = -ret_raw - costs
        pnl_abs  = net_ret * POSITION_SIZE_PCT * equity
        equity  += pnl_abs
        eq_max   = max(eq_max, equity)
        dd_pct   = (eq_max - equity) / eq_max * 100.0
        max_dd   = max(max_dd, dd_pct)

        pnl_list.append(net_ret)
        ctx_trades.setdefault(ctx, []).append(net_ret)

        # Squeeze : trade pris alors que le squeeze_col dit de rejeter
        if squeeze_array[i] and net_ret < 0:
            n_squeeze_losses += 1

    m = len(pnl_list)
    if m == 0:
        return _empty_fold_metrics()

    pnl_arr = np.array(pnl_list)
    wins    = pnl_arr[pnl_arr > 0]
    losses  = pnl_arr[pnl_arr <= 0]

    gross_w = float(wins.sum())
    gross_l = float(abs(losses.sum()))
    pf      = gross_w / max(gross_l, 1e-9)

    # Contexte : contribution par contexte
    ctx_contribution: Dict[str, dict] = {}
    for ctx_k, trades_k in ctx_trades.items():
        arr_k = np.array(trades_k)
        w_k = arr_k[arr_k > 0]; l_k = arr_k[arr_k <= 0]
        ctx_contribution[ctx_k] = {
            "n_trades":  len(trades_k),
            "pf":        round(float(w_k.sum()) / max(float(abs(l_k.sum())), 1e-9), 4),
            "expectancy": round(float(arr_k.mean()), 6),
        }

    # PF stress / extreme
    sim_stress   = simulate_short_trades(pd.Series(ret_array), np.ones(n, bool), costs=COST_STRESS)
    sim_extreme  = simulate_short_trades(pd.Series(ret_array), np.ones(n, bool), costs=COST_EXTREME)
    # Recalculer sur le sous-ensemble tradé pour cohérence
    trade_mask = np.zeros(n, dtype=bool)
    # On reconstruit le masque pour stress/extreme en re-simulant uniquement les trades pris
    traded_idx: List[int] = []
    for i in range(n):
        if gate_array[i]:
            continue
        ctx = str(ctx_array[i])
        thr = get_threshold_for_context(thresholds, ctx)
        if thr is None:
            continue
        if p_short[i] < thr:
            continue
        if np.isnan(ret_array[i]):
            continue
        traded_idx.append(i)

    trade_mask[traded_idx] = True
    sq_rate = n_squeeze_losses / max(m, 1)

    if traded_idx:
        sim_stress_sub  = simulate_short_trades(
            pd.Series(ret_array), trade_mask, COST_STRESS)
        sim_extreme_sub = simulate_short_trades(
            pd.Series(ret_array), trade_mask, COST_EXTREME)
        pf_stress  = sim_stress_sub["pf"]
        pf_extreme = sim_extreme_sub["pf"]
    else:
        pf_stress  = 0.0
        pf_extreme = 0.0

    total_ret_pct = (equity - INITIAL_EQUITY) / INITIAL_EQUITY * 100.0

    return {
        "n_trades":         m,
        "pf":               round(pf, 4),
        "expectancy":       round(float(pnl_arr.mean()), 6),
        "wr":               round(float(len(wins)) / m, 4),
        "avg_win":          round(float(wins.mean()) if len(wins) > 0 else 0.0, 6),
        "avg_loss":         round(float(losses.mean()) if len(losses) > 0 else 0.0, 6),
        "max_drawdown":     round(max_dd, 4),
        "squeeze_loss_rate": round(sq_rate, 4),
        "cost_normal_pf":   round(pf, 4),
        "cost_stress_pf":   round(pf_stress, 4),
        "cost_extreme_pf":  round(pf_extreme, 4),
        "context_contribution": ctx_contribution,
        "total_return_pct": round(total_ret_pct, 4),
    }


def _empty_fold_metrics() -> Dict:
    return {
        "n_trades": 0, "pf": 0.0, "expectancy": 0.0,
        "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "max_drawdown": 0.0, "squeeze_loss_rate": 0.0,
        "cost_normal_pf": 0.0, "cost_stress_pf": 0.0, "cost_extreme_pf": 0.0,
        "context_contribution": {}, "total_return_pct": 0.0,
    }


def _fold_status(metrics: Dict) -> str:
    n     = metrics["n_trades"]
    pf    = metrics["pf"]
    exp   = metrics["expectancy"]
    dd    = metrics["max_drawdown"]
    sq    = metrics["squeeze_loss_rate"]

    if n < MIN_SHORT_TRADES_PER_VALID_FOLD:
        return "NO_TRADES"
    if pf < SHORT_CATASTROPHIC_PF or dd > MAX_SHORT_DD or sq > 0.50:
        return "CATASTROPHIC"
    if pf >= SHORT_DEPLOY_PF and exp > 0 and dd <= MAX_SHORT_DD and sq <= MAX_SQUEEZE_LOSS_RATE:
        return "OK"
    return "WEAK"


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward principal
# ─────────────────────────────────────────────────────────────────────────────

def run_walk_forward(
    df: pd.DataFrame,
    fold_years: Optional[List[int]] = None,
) -> Dict:
    """
    Walk-forward SHORT sur tous les folds annuels.

    Arguments
    ---------
    df         : DataFrame features + labels (index DatetimeIndex)
    fold_years : liste d'années test optionnelle (sinon auto-détection 2020-2026)

    Retourne
    --------
    dict avec verdict global + métriques par fold
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)

    years_all = np.array(df.index.year)
    first_year = int(years_all.min())
    last_year  = int(years_all.max())

    if fold_years is None:
        fold_years = [y for y in range(2020, 2027) if y <= last_year]

    print(f"  Données : {first_year} → {last_year}  |  Folds test : {fold_years}")
    print(f"  Imports manquants : {_IMPORT_ERRORS if _IMPORT_ERRORS else 'aucun'}")

    fold_results: List[Dict] = []

    for t_year in fold_years:
        train_end = t_year - 2
        val_year  = t_year - 1

        if train_end < first_year:
            print(f"  [{t_year}] SKIP — données insuffisantes avant {train_end}")
            continue

        train_mask = years_all <= train_end
        val_mask   = years_all == val_year
        test_mask  = years_all == t_year

        n_train = int(train_mask.sum())
        n_val   = int(val_mask.sum())
        n_test  = int(test_mask.sum())

        if n_train < 1000:
            print(f"  [{t_year}] SKIP — train trop petit ({n_train} barres)")
            continue
        if n_val < 200:
            print(f"  [{t_year}] SKIP — val trop petit ({n_val} barres)")
            continue
        if n_test < 200:
            print(f"  [{t_year}] SKIP — test trop petit ({n_test} barres)")
            continue

        print(f"\n  ── Fold {t_year}  "
              f"[train ≤{train_end} : {n_train:,}] "
              f"[val {val_year} : {n_val:,}] "
              f"[test {t_year} : {n_test:,}]")

        # ── Step 1-2 : features + labels colonnes sur tout le dataset ─────────
        df_fold = _apply_short_features(df)
        df_fold = _apply_short_labels_full(df_fold)

        # ── Step 3 : seuils labels sur train uniquement ───────────────────────
        df_fold = _build_short_labels_train(df_fold, train_mask)

        # Vérifier que y_short_clean (ou y_short) existe
        label_col = _resolve_label_col(df_fold)
        if label_col not in df_fold.columns:
            print(f"  [{t_year}] SKIP — colonne label '{label_col}' absente")
            continue

        n_pos_train   = int((df_fold.loc[train_mask, label_col] == 1).sum())
        n_valid_train = int((df_fold.loc[train_mask, label_col] >= 0).sum())
        print(f"   Labels train [{label_col}] : {n_valid_train:,} valides  "
              f"(SHORT=1 : {n_pos_train:,} = "
              f"{n_pos_train / max(n_valid_train, 1):.1%})")

        if n_pos_train < 20:
            print(f"   [{t_year}] SKIP — trop peu de labels positifs ({n_pos_train})")
            continue

        # ── Step 4 : exclure gray zone du training ────────────────────────────
        # (build_short_labels devrait déjà le faire, mais on s'assure)
        df_train_clean = df_fold.loc[train_mask & (df_fold[label_col] >= 0)].copy()

        # ── Step 5 : Oversampling si n_positifs < 1000 ───────────────────────
        df_train_os = _maybe_oversample(df_train_clean, label_col=label_col)
        # Reconstruction du masque pour le fold (on re-indexe)
        train_mask_os = np.ones(len(df_train_os), dtype=bool)

        # ── Step 6-7 : contextes + gate ───────────────────────────────────────
        df_fold = _apply_permission_context(df_fold)
        df_fold = _apply_no_short_gate(df_fold)

        # Appliquer aussi sur df_train_os (copier les colonnes si disponibles)
        # df_train_os est toujours un DataFrame (même sans imblearn)
        if hasattr(df_train_os, "columns"):
            if CONTEXT_COL not in df_train_os.columns and CONTEXT_COL in df_fold.columns:
                try:
                    ctx_src = df_fold.loc[
                        train_mask & (df_fold[label_col] >= 0), CONTEXT_COL
                    ].values
                    df_train_os = df_train_os.copy()
                    df_train_os[CONTEXT_COL] = ctx_src[:len(df_train_os)]
                except Exception:
                    df_train_os = df_train_os.copy()
                    df_train_os[CONTEXT_COL] = "general"

        # ── Step 8 : Entraîner le modèle SHORT ───────────────────────────────
        # On entraîne sur df_fold (le dataset complet avec le bon index)
        # mais restreint au masque train propre
        print(f"   Entraînement modèle SHORT…")
        train_mask_full = (
            np.isin(np.arange(len(df_fold)), np.where(train_mask)[0])
            & (df_fold[label_col].values >= 0)
        )
        model = _train_short_model(df_fold, train_mask_full, val_mask)
        print(f"   Modèle entraîné.")

        # ── Step 9 : Calibration des seuils sur val ───────────────────────────
        ones_val = np.ones(int(val_mask.sum()), dtype=bool)
        p_short_val = _predict_short(model, df_fold, val_mask)

        y_val_arr = df_fold.loc[val_mask, label_col].values.astype(np.int32)
        df_val    = df_fold.loc[val_mask].copy()

        print(f"   Calibration seuils sur val ({n_val:,} barres)…")
        try:
            thresholds = calibrate_short_thresholds(
                df_val     = df_val,
                y_val      = y_val_arr,
                p_short    = p_short_val,
                context_col = CONTEXT_COL,
                costs      = COST_NORMAL,
                ret_col    = RET_COL_SHORT,
                mfe_col    = MFE_COL,
                mae_col    = MAE_COL,
                squeeze_col = SQUEEZE_COL,
            )
        except Exception as e:
            print(f"   WARN calibration échouée : {e} — seuil uniforme 0.65")
            thresholds = {"general": {
                "enabled": True, "threshold": 0.65,
                "val_pf": None, "val_expectancy": None,
                "n_val_trades": 0, "squeeze_loss_rate": None,
                "cost_stress_pf": None, "avg_mae": None, "score": None,
            }}

        print("   Seuils calibrés :")
        print(summarize_calibration(thresholds))

        # Sauvegarder les seuils du fold
        thr_path = REPORT_DIR / f"thresholds_fold_{t_year}.json"
        save_thresholds(thresholds, str(thr_path))

        # ── Step 10 : Backtest sur test ───────────────────────────────────────
        p_short_test = _predict_short(model, df_fold, test_mask)
        df_test      = df_fold.loc[test_mask].copy()

        metrics = _backtest_fold(
            df_test   = df_test,
            p_short   = p_short_test,
            thresholds = thresholds,
            costs     = COST_NORMAL,
        )

        status = _fold_status(metrics)
        is_ok          = status == "OK"
        is_catastrophic = status == "CATASTROPHIC"

        _print_fold_summary(t_year, metrics, status, thresholds)

        fold_record = {
            "year":                t_year,
            "fold_status":         status,
            "ok":                  is_ok,
            "catastrophic":        is_catastrophic,
            "n_train":             n_train,
            "n_val":               n_val,
            "n_test":              n_test,
            "n_pos_train":         n_pos_train,
            "threshold_by_context": {
                ctx: e.get("threshold") for ctx, e in thresholds.items()
            },
            **metrics,
        }
        fold_results.append(fold_record)

    # ── Verdict global ────────────────────────────────────────────────────────
    return _compute_global_verdict(fold_results)


def _compute_global_verdict(fold_results: List[Dict]) -> Dict:
    """Calcule le verdict global à partir des métriques par fold."""
    n_f   = len(fold_results)
    n_ok  = sum(1 for f in fold_results if f.get("ok", False))
    n_cat = sum(1 for f in fold_results if f.get("catastrophic", False))
    n_tr  = sum(f.get("n_trades", 0) for f in fold_results)

    pf_vals  = [f["pf"]        for f in fold_results if f.get("n_trades", 0) > 0]
    exp_vals = [f["expectancy"] for f in fold_results if f.get("n_trades", 0) > 0]
    sq_vals  = [f["squeeze_loss_rate"] for f in fold_results if f.get("n_trades", 0) > 0]
    spf_vals = [f["cost_stress_pf"]    for f in fold_results if f.get("n_trades", 0) > 0]

    median_pf       = float(np.median(pf_vals))  if pf_vals  else 0.0
    median_exp      = float(np.median(exp_vals)) if exp_vals else 0.0
    median_sq       = float(np.median(sq_vals))  if sq_vals  else 0.0
    median_stress_pf = float(np.median(spf_vals)) if spf_vals else 0.0

    # Verdict strict — jamais SHORT_DEPLOYABLE
    if n_cat > 0:
        verdict = "SHORT_REJECTED"
    elif n_ok < MIN_SHORT_FOLDS_OK or n_tr < MIN_SHORT_TRADES_TOTAL:
        verdict = "SHORT_PROMISING_BUT_UNSAFE"
    elif n_ok >= MIN_SHORT_FOLDS_OK and n_cat == 0 and median_stress_pf >= 1.0:
        verdict = "SHORT_PAPER_CANDIDATE"
    else:
        verdict = "SHORT_PROMISING_BUT_UNSAFE"

    reasons: List[str] = []
    if n_cat > 0:
        reasons.append(
            f"{n_cat} fold(s) catastrophique(s) : "
            + str([f["year"] for f in fold_results if f.get("catastrophic")])
        )
    if n_ok < MIN_SHORT_FOLDS_OK:
        reasons.append(f"seulement {n_ok}/{n_f} folds OK (min={MIN_SHORT_FOLDS_OK})")
    if n_tr < MIN_SHORT_TRADES_TOTAL:
        reasons.append(f"total trades {n_tr} < {MIN_SHORT_TRADES_TOTAL}")
    if median_stress_pf < 1.0 and n_cat == 0 and n_ok >= MIN_SHORT_FOLDS_OK:
        reasons.append(f"PF stress médian {median_stress_pf:.2f} < 1.0")

    return {
        "verdict":           verdict,
        "reasons":           reasons,
        "n_folds":           n_f,
        "n_folds_ok":        n_ok,
        "n_folds_catastrophic": n_cat,
        "total_trades":      n_tr,
        "median_pf":         round(median_pf, 4),
        "median_expectancy": round(median_exp, 6),
        "median_squeeze_rate": round(median_sq, 4),
        "median_stress_pf":  round(median_stress_pf, 4),
        "folds":             fold_results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Affichage
# ─────────────────────────────────────────────────────────────────────────────

def _print_fold_summary(
    year: int,
    metrics: Dict,
    status: str,
    thresholds: dict,
) -> None:
    status_sym = {
        "OK": "[OK]", "CATASTROPHIC": "[CATA]",
        "WEAK": "[WEAK]", "NO_TRADES": "[--]",
    }.get(status, "[?]")

    ctx_thrs = {
        ctx: e.get("threshold")
        for ctx, e in thresholds.items()
        if e.get("enabled", False) and e.get("threshold") is not None
    }

    print(
        f"  [{year}] {status_sym}  "
        f"n={metrics['n_trades']:3d}  "
        f"PF={metrics['pf']:.3f}  "
        f"E={metrics['expectancy']:+.5f}  "
        f"WR={metrics['wr']:.2f}  "
        f"DD={metrics['max_drawdown']:.1f}%  "
        f"SQ={metrics['squeeze_loss_rate']:.2f}  "
        f"PF_stress={metrics['cost_stress_pf']:.3f}"
    )
    if ctx_thrs:
        thr_str = "  ".join(f"{k[:8]}={v:.2f}" for k, v in ctx_thrs.items())
        print(f"         seuils : {thr_str}")


def _print_final_report(result: Dict) -> None:
    verdict = result["verdict"]
    print()
    print("=" * 72)
    print(f"VERDICT FINAL : {verdict}")
    print("=" * 72)
    print(f"  Folds OK          : {result['n_folds_ok']}/{result['n_folds']}")
    print(f"  Folds catastroph. : {result['n_folds_catastrophic']}")
    print(f"  Total trades      : {result['total_trades']:,}")
    print(f"  PF médian         : {result['median_pf']:.4f}")
    print(f"  Expectancy méd.   : {result['median_expectancy']:+.6f}")
    print(f"  Squeeze méd.      : {result['median_squeeze_rate']:.3f}")
    print(f"  PF stress méd.    : {result['median_stress_pf']:.4f}")

    if result["reasons"]:
        print("\n  Raisons :")
        for r in result["reasons"]:
            print(f"    - {r}")

    print()
    print("  Détail par fold :")
    header = (
        f"  {'Année':>6}  {'Status':>12}  {'N':>5}  {'PF':>6}  "
        f"{'E':>8}  {'WR':>5}  {'DD%':>6}  {'SQ':>5}  {'PF_str':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for f in result["folds"]:
        print(
            f"  {f['year']:>6}  {f['fold_status']:>12}  "
            f"{f['n_trades']:>5}  {f['pf']:>6.3f}  "
            f"{f['expectancy']:>+8.5f}  {f['wr']:>5.2f}  "
            f"{f['max_drawdown']:>5.1f}%  {f['squeeze_loss_rate']:>5.2f}  "
            f"{f['cost_stress_pf']:>7.3f}"
        )
    print("=" * 72)


# ─────────────────────────────────────────────────────────────────────────────
# Sauvegarde
# ─────────────────────────────────────────────────────────────────────────────

def _json_default(obj):
    if isinstance(obj, (bool, np.bool_)):   return bool(obj)
    if isinstance(obj, np.integer):         return int(obj)
    if isinstance(obj, np.floating):        return float(obj)
    if isinstance(obj, np.ndarray):         return obj.tolist()
    raise TypeError(type(obj))


def _save_results(result: Dict) -> None:
    # JSON
    json_path = REPORT_DIR / "walk_forward_short_results.json"
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2, default=_json_default)
    print(f"\n  Rapport JSON  → {json_path}")

    # CSV (une ligne par fold)
    csv_rows = []
    scalar_keys = [
        "year", "fold_status", "n_trades", "pf", "expectancy", "wr",
        "avg_win", "avg_loss", "max_drawdown", "squeeze_loss_rate",
        "cost_normal_pf", "cost_stress_pf", "cost_extreme_pf",
        "n_train", "n_val", "n_test", "n_pos_train", "total_return_pct",
    ]
    for fold in result.get("folds", []):
        row = {k: fold.get(k) for k in scalar_keys}
        csv_rows.append(row)

    if csv_rows:
        df_csv = pd.DataFrame(csv_rows)
        csv_path = REPORT_DIR / "walk_forward_short_results.csv"
        df_csv.to_csv(csv_path, index=False)
        print(f"  Rapport CSV   → {csv_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Entrée principale
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Walk-forward SHORT 4h — validation scientifique stricte"
    )
    ap.add_argument(
        "--data", default=None,
        help="Chemin vers le fichier de données (CSV ou parquet). "
             "Auto-détection si absent."
    )
    ap.add_argument(
        "--folds", default=None,
        help="Années test séparées par virgule, ex. 2022,2023,2024. "
             "Auto-détection si absent."
    )
    args = ap.parse_args()

    fold_years: Optional[List[int]] = None
    if args.folds:
        try:
            fold_years = [int(y.strip()) for y in args.folds.split(",")]
        except ValueError:
            print(f"  ERREUR : --folds invalide ({args.folds!r})")
            sys.exit(1)

    print("=" * 72)
    print("WALK-FORWARD SHORT 4h — VALIDATION SCIENTIFIQUE")
    print("=" * 72)
    print(f"  Coûts : normal={COST_NORMAL*1e4:.0f}bps  "
          f"stress={COST_STRESS*1e4:.0f}bps  "
          f"extreme={COST_EXTREME*1e4:.0f}bps")
    print(f"  Critères OK : PF>={SHORT_DEPLOY_PF}  DD<={MAX_SHORT_DD}%  SQ<={MAX_SQUEEZE_LOSS_RATE}")
    print(f"  Critères déploiement : {MIN_SHORT_FOLDS_OK} folds OK  "
          f"total trades>={MIN_SHORT_TRADES_TOTAL}")
    print(f"  Sizing : {POSITION_SIZE_PCT*100:.1f}% equity par trade")
    print(f"  Reports : {REPORT_DIR}")
    print()

    # Chargement données
    print("Chargement des données…")
    df, is_synthetic = load_data(args.data)
    print(f"  {len(df):,} barres  "
          f"{df.index[0].date()} → {df.index[-1].date()}"
          + ("  [SYNTHÉTIQUE]" if is_synthetic else ""))

    if is_synthetic:
        print()
        print("  NOTE : données synthétiques — résultats à titre de test uniquement.")
        print()

    print()
    result = run_walk_forward(df, fold_years)

    _print_final_report(result)
    _save_results(result)


if __name__ == "__main__":
    main()
