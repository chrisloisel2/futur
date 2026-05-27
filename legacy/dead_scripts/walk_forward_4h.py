#!/usr/bin/env python3
"""
scripts/walk_forward_4h.py — WALK-FORWARD MULTI-ACTIF (BTC + ETH + SOL)
=========================================================================

Innovation : entraînement sur BTC + ETH + SOL, test sur BTC uniquement.
  • BTC : 76k barres (2017-2026) avec macro bundle (funding, OI, F&G)
  • ETH : 76k barres (2017-2026) sans macro bundle
  • SOL : 50k barres (2020-2026) sans macro bundle
  Total labels LONG train (fold 2024) : ~3 500-4 000 (vs 1 266 BTC seul)

Architecture par fold :
  Train  : BTC[2017..T-2] + ETH[2017..T-2] + SOL[2020..T-2]  (expandant)
  Val    : BTC[T-1]   (calibration des seuils — BTC seul pour cohérence)
  Test   : BTC[T]     (évaluation out-of-sample — BTC seul)
  Stage 1 : HistGBT filtre tradeable  (entraîné sur multi-actif)
  Stage 2 : HistGBT edge model LONG   (entraîné sur multi-actif)
  Gate   : NO_LONG bloqué dans le backtest (BTC uniquement)

Critères de déploiement :
  ≥ 5/7 folds PF ≥ 1.20 | 0 fold catastrophique | Total trades ≥ 100

Usage :
  python scripts/walk_forward_4h.py
  python scripts/walk_forward_4h.py --no-eth --no-sol   # BTC seul (baseline)
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.level_0.constants import (
    TARGET_COL, TRADEABLE_QUANTILE_LONG, COST_PCT, REGIME_COL_LONG,
)
# ── Nouveau : source MongoDB institutionnelle ──────────────────────────────
from ai.level_0.institutional_loader import (
    load_institutional_data,
    build_institutional_labels,
)
from ai.level_0.institutional_features import (
    FEATURES_INST_LONG,
    FEATURES_INST_FILTER,
    get_available_features,
)
from ai.level_2.tiny_specialists import (
    TRMFleet, classify_context,
    calibrate_context_thresholds, TRM_FLEET_SIZE,
)
from ai.level_0.augmentation import augment_positives

REPORT_DIR = ROOT / "reports" / "walk_forward_4h"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEPLOY_PF           = 1.20
DEPLOY_EXP          = 0.0
# PF < 0.55 = catastrophique (perte > 45% des gains, −5%+ equity/an)
# PF 0.55-0.70 = "mauvais" mais survivable avec position sizing 0.2%
CATASTROPHIC_PF     = 0.55
CATASTROPHIC_N      = 5
CATASTROPHIC_MDD    = 10.0
MIN_FOLDS_OK        = 5
MIN_TOTAL_TRADES    = 100
MIN_TRADES_DIR      = 10
MIN_CAL_FILTER_BARS = 80


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_X(df: pd.DataFrame, mask: np.ndarray, feats: List[str]) -> np.ndarray:
    avail = [f for f in feats if f in df.columns]
    X = df.loc[mask, avail].values.astype(np.float32)
    n_miss = len(feats) - len(avail)
    if n_miss:
        X = np.hstack([X, np.zeros((X.shape[0], n_miss), dtype=np.float32)])
    return X


def _pp(clf, scaler: StandardScaler, X: np.ndarray) -> np.ndarray:
    return clf.predict_proba(scaler.transform(X))[:, 1]


# ─────────────────────────────────────────────────────────────────────────────
# Adaptateur MongoDB institutionnel → format attendu par TRMFleet
# ─────────────────────────────────────────────────────────────────────────────

# Colonnes requises par classify_context / build_specialist_scores
# (ancien nom → nom dans la collection institutionnelle)
_ALIAS_MAP: Dict[str, str] = {
    "dist_ema_20":       "distance_ema_20",
    "dist_ema_50":       "distance_ema_50",
    "dist_ema_200":      "distance_ema_200",
    "ema_spread_20_50":  "ema_21_50_spread",
    "ema_spread_50_200": "ema_50_200_spread",
    "boll_width_20":     "bb_width_20",
    "boll_pos_20":       "bb_percent_b_20",
    "rsi_14":            "rsi_13",          # EMA α=1/13 ≈ période 14
    # Signaux dérivés manquants : meilleures approximations disponibles
    "momentum_accel_6":    "return_accel_5",
    "trend_persistence_12": "efficiency_ratio_20",
    "breakout_strength_24": "breakout_score",
    "dist_vwap_pct":       "distance_vwap_20",
    "rv_ratio_24_72":      "garman_klass_vol_20",  # proxy vol relative
    "rv_ratio_12_48":      "yang_zhang_vol_20",
}

# Colonne booléenne calculée : above_vwap_4h → distance_vwap_20 > 0
_BOOL_ALIASES: Dict[str, str] = {
    "above_vwap_4h": "distance_vwap_20",
}


def _add_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute les alias de colonnes nécessaires à classify_context / TRMFleet
    depuis les colonnes institutionnelles.
    Les colonnes absentes sont laissées à leur défaut natif dans _col().
    """
    df = df.copy()
    for old, new in _ALIAS_MAP.items():
        if old not in df.columns and new in df.columns:
            df[old] = df[new]
    for old, new in _BOOL_ALIASES.items():
        if old not in df.columns and new in df.columns:
            df[old] = (df[new] > 0).astype(np.float32)
    return df


def _create_regime_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construit regime_long depuis les features institutionnelles.

    NO_LONG  : prix nettement sous l'EMA200 + death cross + trend négatif fort
               → éviter les longs en bear structurel
    NEUTRAL  : tout le reste (le modèle décide)
    """
    df = df.copy()
    ema200_dist = df.get("distance_ema_200", pd.Series(0.0, index=df.index))
    ema50_200   = df.get("ema_50_200_spread", pd.Series(0.0, index=df.index))
    trend_sc    = df.get("trend_score",       pd.Series(0.0, index=df.index))

    no_long = (
        (ema200_dist < -0.08) &  # prix fortement sous l'EMA200
        (ema50_200   < -0.02) &  # EMA50 < EMA200 (death cross)
        (trend_sc    < -0.15)    # trend composite très négatif
    )
    df[REGIME_COL_LONG] = np.where(no_long, "NO_LONG", "NEUTRAL")

    pct_no_long = float(no_long.mean() * 100)
    print(f"   Régimes LONG : NO_LONG={pct_no_long:.1f}%  NEUTRAL={100-pct_no_long:.1f}%")
    return df


def load_btc_institutional() -> pd.DataFrame:
    """
    Charge BTC/USDT 1h depuis ohlcv_institutional_features_btcusdt.
    Applique les alias de colonnes + la colonne de régime.
    Garantit que TARGET_COL et REGIME_COL_LONG sont présents.
    """
    # Projection : uniquement les colonnes nécessaires (10× plus rapide)
    all_cols = list(dict.fromkeys(FEATURES_INST_LONG + FEATURES_INST_FILTER))
    df = load_institutional_data(interval="1h", columns=all_cols)

    # Alias de colonnes pour classify_context / TRMFleet
    df = _add_aliases(df)

    # Régime long (remplace l'ancien compute_long_regime_col)
    df = _create_regime_col(df)

    # TARGET_COL et labels seront construits par build_institutional_labels()
    # à chaque fold pour éviter le leakage (seuils calibrés sur train uniquement)

    return df


def _feature_sets() -> Dict[str, Dict[str, List[str]]]:
    return {
        "institutional": {
            "filter": list(FEATURES_INST_FILTER),
            "long":   list(FEATURES_INST_LONG),
        },
    }


def _select_feature_set(name: str) -> Dict[str, List[str]]:
    try:
        selected = _feature_sets()[name]
    except KeyError:
        raise ValueError("Feature set inconnu: %s" % name)
    return {
        "filter": list(dict.fromkeys(selected["filter"])),
        "long": list(dict.fromkeys(selected["long"])),
    }


def _json_default(o):
    if isinstance(o, (bool, np.bool_)):  return bool(o)
    if isinstance(o, np.integer):        return int(o)
    if isinstance(o, np.floating):       return float(o)
    if isinstance(o, np.ndarray):        return o.tolist()
    if isinstance(o, Path):              return str(o)
    raise TypeError(type(o))


def _find_dir_threshold(
    p: np.ndarray, y: np.ndarray,
    beta: float = 1.5, min_prec: float = 0.05,
    min_trades: int = MIN_TRADES_DIR, lo: float = 0.50, hi: float = 0.65,
) -> float:
    valid = y >= 0
    p, y = p[valid], y[valid]
    if len(p) < 6 or y.sum() < 3:
        return lo
    best_thr, best_score = lo, -1.0
    for thr in np.arange(lo, hi + 0.001, 0.01):
        pred = (p >= thr).astype(int)
        if pred.sum() < 3:
            continue
        tp   = int((pred & (y == 1)).sum())
        fp   = int((pred & (y == 0)).sum())
        prec = tp / (tp + fp + 1e-9)
        rec  = tp / max(y.sum(), 1)
        fb   = (1 + beta**2) * prec * rec / (beta**2 * prec + rec + 1e-9)
        if fb > best_score and prec >= min_prec:
            best_score, best_thr = fb, thr
    if int((p >= best_thr).sum()) < min_trades:
        for thr in np.arange(best_thr - 0.01, lo - 0.001, -0.01):
            if int((p >= thr).sum()) >= min_trades:
                return round(float(thr), 2)
    return round(float(best_thr), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Chargement multi-actif
# ─────────────────────────────────────────────────────────────────────────────

def load_asset(path: str) -> pd.DataFrame:
    """
    Charge un fichier features d'un actif alternatif (CSV historique ou parquet max_public).
    Applique les features event/vwap si absentes (backward compat).
    Garantit que les features macro manquantes sont à 0.
    """
    print(f"   Chargement {path}…")
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
        ts_col = "timestamp" if "timestamp" in df.columns else ("datetime" if "datetime" in df.columns else None)
        if ts_col is not None:
            df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
            df = df.sort_values(ts_col).set_index(ts_col)
        elif not isinstance(df.index, pd.DatetimeIndex):
            raise RuntimeError("Parquet sans timestamp lisible: %s" % path)
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not isinstance(df.index, pd.DatetimeIndex) and "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df.sort_values("datetime").set_index("datetime")

    # Features event/vwap si absentes
    if "gc_fresh" not in df.columns:
        df = compute_event_features(df)
    if "vwap_daily" not in df.columns:
        df = compute_vwap_features(df)
    if TARGET_COL not in df.columns:
        df = compute_label_columns(df)

    # Régime long si absent
    if REGIME_COL_LONG not in df.columns:
        df = compute_long_regime_col(df)

    # Imputer 0 sur toutes les colonnes numériques sans valeur
    label_cols = {TARGET_COL, "future_ret_h8_min", "future_ret_h8_max"}
    num_cols = [c for c in df.columns
                if c not in label_cols and pd.api.types.is_numeric_dtype(df[c])
                and df[c].isna().any()]
    if num_cols:
        df[num_cols] = df[num_cols].ffill().fillna(0.0)

    df = df[df[TARGET_COL].notna()].copy()
    print(f"     → {len(df):,} barres  {df.index[0].date()} → {df.index[-1].date()}")
    return df


def _symbol_from_feature_path(path: Path) -> str:
    stem = path.stem.upper()
    if stem.endswith("_1H_FEATURES"):
        stem = stem[:-len("_1H_FEATURES")]
    return "BTCUSDT" if stem == "BTCUSD" else stem


def discover_extra_asset_paths(
    features_root: Path,
    *,
    include_eth: bool = True,
    include_sol: bool = True,
) -> List[Path]:
    candidates: List[Path] = []
    if features_root.exists():
        candidates.extend(sorted(features_root.glob("*.parquet")))

    import glob
    candidates.extend(
        Path(f)
        for f in sorted(glob.glob(str(ROOT / "data" / "*USDT*features.csv")))
    )

    seen = set()
    paths: List[Path] = []
    for path in candidates:
        symbol = _symbol_from_feature_path(path)
        if symbol.startswith("BTC"):
            continue
        if not include_eth and symbol.startswith("ETH"):
            continue
        if not include_sol and symbol.startswith("SOL"):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        paths.append(path)
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Backtest
# ─────────────────────────────────────────────────────────────────────────────

def _backtest(
    df_btc: pd.DataFrame, test_mask: np.ndarray,
    filter_clf, filt_scaler: StandardScaler,
    edge_clf, edge_scaler: StandardScaler,
    filter_thr: float, dir_thr: float,
    cost_pct: float = COST_PCT,
    filter_features: Optional[Sequence[str]] = None,
    long_features: Optional[Sequence[str]] = None,
) -> Dict:
    """Backtest HistGBT standard (gate NO_LONG + filtre + direction)."""
    df_test = df_btc.loc[test_mask].copy()
    n       = len(df_test)
    ones    = np.ones(n, dtype=bool)

    active_filter = list(filter_features or FEATURES_FILTER)
    active_long = list(long_features or FEATURES_LONG)
    p_filt = _pp(filter_clf, filt_scaler, _get_X(df_test, ones, active_filter))
    p_edge = _pp(edge_clf,   edge_scaler, _get_X(df_test, ones, active_long))

    regime = (df_test[REGIME_COL_LONG].values
              if REGIME_COL_LONG in df_test.columns
              else np.full(n, "NEUTRAL"))

    pnl_list: List[float] = []
    equity = 10_000.0
    eq_max = equity
    max_dd = 0.0

    for i, (_, row) in enumerate(df_test.iterrows()):
        if regime[i] == "NO_LONG":                    continue
        if p_filt[i] < filter_thr:                    continue
        if p_edge[i] < dir_thr:                       continue
        ret_4h = row.get(TARGET_COL)
        if ret_4h is None or np.isnan(float(ret_4h)): continue

        pnl_pct  = float(ret_4h) - cost_pct
        pnl_abs  = pnl_pct * 0.002 * equity
        equity  += pnl_abs
        eq_max   = max(eq_max, equity)
        max_dd   = max(max_dd, (eq_max - equity) / eq_max * 100)
        pnl_list.append(pnl_pct)

    m = len(pnl_list)
    if m == 0:
        return {"n": 0, "pf": 0.0, "expectancy": 0.0,
                "win_rate": 0.0, "max_dd": 0.0, "total_return_pct": 0.0}
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    return {
        "n":               m,
        "pf":              round(sum(wins) / max(abs(sum(losses)), 1e-9), 4),
        "expectancy":      round(float(np.mean(pnl_list)) * 100, 4),
        "win_rate":        round(len(wins) / m, 4),
        "max_dd":          round(max_dd, 4),
        "total_return_pct":round((equity - 10_000.0) / 100, 4),
    }


def _backtest_fleet_v2(
    df_btc: pd.DataFrame, test_mask: np.ndarray,
    filter_clf, filt_scaler: StandardScaler,
    fleet: "TRMFleet",
    filter_thr: float,
    ctx_thresholds: Dict[str, float],
    cost_pct: float = COST_PCT,
    filter_features: Optional[Sequence[str]] = None,
) -> Dict:
    """
    Backtest TRM v2 : seuil de direction par contexte.
    Chaque spécialiste a son propre seuil calibré sur val BTC.
    """
    df_test = df_btc.loc[test_mask].copy()
    n       = len(df_test)
    ones    = np.ones(n, dtype=bool)

    active_filter = list(filter_features or FEATURES_FILTER)
    p_filt  = _pp(filter_clf, filt_scaler, _get_X(df_test, ones, active_filter))
    p_fleet = fleet.predict(df_test, ones, verbose=False)
    ctx_arr = classify_context(df_test)

    regime = (df_test[REGIME_COL_LONG].values
              if REGIME_COL_LONG in df_test.columns
              else np.full(n, "NEUTRAL"))

    pnl_list: List[float] = []
    equity = 10_000.0
    eq_max = equity
    max_dd = 0.0

    for i, (_, row) in enumerate(df_test.iterrows()):
        if regime[i] == "NO_LONG":                    continue
        if p_filt[i] < filter_thr:                    continue

        ctx = ctx_arr[i]
        thr = ctx_thresholds.get(ctx, ctx_thresholds.get("general", 0.54))
        if p_fleet[i] < thr:                          continue

        ret_4h = row.get(TARGET_COL)
        if ret_4h is None or np.isnan(float(ret_4h)): continue

        pnl_pct  = float(ret_4h) - cost_pct
        pnl_abs  = pnl_pct * 0.002 * equity
        equity  += pnl_abs
        eq_max   = max(eq_max, equity)
        max_dd   = max(max_dd, (eq_max - equity) / eq_max * 100)
        pnl_list.append(pnl_pct)

    m = len(pnl_list)
    if m == 0:
        return {"n": 0, "pf": 0.0, "expectancy": 0.0,
                "win_rate": 0.0, "max_dd": 0.0, "total_return_pct": 0.0}
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    return {
        "n":               m,
        "pf":              round(sum(wins) / max(abs(sum(losses)), 1e-9), 4),
        "expectancy":      round(float(np.mean(pnl_list)) * 100, 4),
        "win_rate":        round(len(wins) / m, 4),
        "max_dd":          round(max_dd, 4),
        "total_return_pct":round((equity - 10_000.0) / 100, 4),
    }


def _backtest_fleet(
    df_btc: pd.DataFrame, test_mask: np.ndarray,
    filter_clf, filt_scaler: StandardScaler,
    fleet: "TRMFleet",
    filter_thr: float, dir_thr: float,
    cost_pct: float = COST_PCT,
    filter_features: Optional[Sequence[str]] = None,
) -> Dict:
    """Backtest avec la flottée TRM comme edge model."""
    df_test = df_btc.loc[test_mask].copy()
    n       = len(df_test)
    ones    = np.ones(n, dtype=bool)

    active_filter = list(filter_features or FEATURES_FILTER)
    p_filt = _pp(filter_clf, filt_scaler, _get_X(df_test, ones, active_filter))
    p_edge = fleet.predict(df_test, ones, verbose=False)

    regime = (df_test[REGIME_COL_LONG].values
              if REGIME_COL_LONG in df_test.columns
              else np.full(n, "NEUTRAL"))

    pnl_list: List[float] = []
    equity = 10_000.0
    eq_max = equity
    max_dd = 0.0

    for i, (_, row) in enumerate(df_test.iterrows()):
        if regime[i] == "NO_LONG":                    continue
        if p_filt[i] < filter_thr:                    continue
        if p_edge[i] < dir_thr:                       continue
        ret_4h = row.get(TARGET_COL)
        if ret_4h is None or np.isnan(float(ret_4h)): continue

        pnl_pct  = float(ret_4h) - cost_pct
        pnl_abs  = pnl_pct * 0.002 * equity
        equity  += pnl_abs
        eq_max   = max(eq_max, equity)
        max_dd   = max(max_dd, (eq_max - equity) / eq_max * 100)
        pnl_list.append(pnl_pct)

    m = len(pnl_list)
    if m == 0:
        return {"n": 0, "pf": 0.0, "expectancy": 0.0,
                "win_rate": 0.0, "max_dd": 0.0, "total_return_pct": 0.0}
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    return {
        "n":               m,
        "pf":              round(sum(wins) / max(abs(sum(losses)), 1e-9), 4),
        "expectancy":      round(float(np.mean(pnl_list)) * 100, 4),
        "win_rate":        round(len(wins) / m, 4),
        "max_dd":          round(max_dd, 4),
        "total_return_pct":round((equity - 10_000.0) / 100, 4),
    }


def _backtest_transformer(
    df_btc: pd.DataFrame, test_mask: np.ndarray,
    filter_clf, filt_scaler: StandardScaler,
    edge_model: TradingTransformer, edge_scaler: StandardScaler,
    filter_thr: float, dir_thr: float,
    cost_pct: float = COST_PCT,
    filter_features: Optional[Sequence[str]] = None,
    long_features: Optional[Sequence[str]] = None,
) -> Dict:
    """Backtest régime-conditionnel avec Transformer pour l'edge model."""
    df_test = df_btc.loc[test_mask].copy()
    n       = len(df_test)
    ones    = np.ones(n, dtype=bool)

    active_filter = list(filter_features or FEATURES_FILTER)
    active_long = list(long_features or FEATURES_LONG)
    p_filt = _pp(filter_clf, filt_scaler, _get_X(df_test, ones, active_filter))
    p_edge = predict_transformer(edge_model, edge_scaler, df_btc, test_mask, active_long)

    regime = (df_test[REGIME_COL_LONG].values
              if REGIME_COL_LONG in df_test.columns
              else np.full(n, "NEUTRAL"))

    pnl_list: List[float] = []
    equity = 10_000.0
    eq_max = equity
    max_dd = 0.0

    for i, (_, row) in enumerate(df_test.iterrows()):
        if regime[i] == "NO_LONG":                    continue
        if p_filt[i] < filter_thr:                    continue
        if p_edge[i] < dir_thr:                       continue
        ret_4h = row.get(TARGET_COL)
        if ret_4h is None or np.isnan(float(ret_4h)): continue

        pnl_pct  = float(ret_4h) - cost_pct
        pnl_abs  = pnl_pct * 0.002 * equity
        equity  += pnl_abs
        eq_max   = max(eq_max, equity)
        max_dd   = max(max_dd, (eq_max - equity) / eq_max * 100)
        pnl_list.append(pnl_pct)

    m = len(pnl_list)
    if m == 0:
        return {"n": 0, "pf": 0.0, "expectancy": 0.0,
                "win_rate": 0.0, "max_dd": 0.0, "total_return_pct": 0.0}
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    return {
        "n":               m,
        "pf":              round(sum(wins) / max(abs(sum(losses)), 1e-9), 4),
        "expectancy":      round(float(np.mean(pnl_list)) * 100, 4),
        "win_rate":        round(len(wins) / m, 4),
        "max_dd":          round(max_dd, 4),
        "total_return_pct":round((equity - 10_000.0) / 100, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward
# ─────────────────────────────────────────────────────────────────────────────

def run_walk_forward(
    df_btc: pd.DataFrame,
    extra_assets: List[pd.DataFrame],
    *,
    feature_set_name: str = "full_public",
    long_features: Optional[Sequence[str]] = None,
    filter_features: Optional[Sequence[str]] = None,
) -> Dict:
    """
    Walk-forward avec training multi-actif et test BTC uniquement.
    extra_assets : liste de DataFrames ETH, SOL, etc.
    """
    active_long_features = list(long_features or FEATURES_LONG)
    active_filter_features = list(filter_features or FEATURES_FILTER)
    years_btc  = np.array(df_btc.index.year)
    first_year = int(years_btc.min())
    test_years = sorted(int(y) for y in np.unique(years_btc) if y >= 2020)

    n_assets = 1 + len(extra_assets)
    print(f"  BTC : {first_year} → {int(years_btc.max())}  |  "
          f"Actifs d'entraînement : {n_assets}  |  Folds : {test_years}")
    print(f"  Feature set : {feature_set_name}  |  "
          f"filter={len(active_filter_features)}  long={len(active_long_features)}")

    fold_results: List[Dict] = []

    for t_year in test_years:
        # ── Fenêtrage : lag 2 ans, val = année t-1 complète ─────────────────────
        # Val complète (12 mois) nécessaire pour calibrer les 73 spécialistes TRM.
        # Le fleet est entraîné sur t-2 ans de données pour éviter le leakage de régime.
        train_end = t_year - 2
        val_year  = t_year - 1

        if train_end < first_year:
            print(f"  [{t_year}] skip"); continue

        btc_train = (years_btc <= train_end)
        btc_val   = (years_btc == val_year)
        btc_test  = (years_btc == t_year)

        if btc_train.sum() < 4000 or btc_val.sum() < 500 or btc_test.sum() < 400:
            print(f"  [{t_year}] skip — données BTC insuffisantes"); continue

        # ── FIX 3 : exclure les folds partiels (< 8 mois de données de test) ──
        # Un fold partiel (ex: 2026 avec Jan-Mai seulement) produit trop peu de
        # trades pour être statistiquement fiable et biaise le verdict final.
        test_months = df_btc.loc[btc_test].index.month.nunique()
        if test_months < 8:
            print(f"  [{t_year}] skip — fold partiel ({test_months} mois < 8 requis)")
            continue

        # ── Labels BTC — calibrés sur train uniquement (anti-leakage) ───────────
        df_btc_fold = build_institutional_labels(
            df_btc, train_mask=btc_train, horizon=5,
            cost_pct=COST_PCT, tradeable_quantile=TRADEABLE_QUANTILE_LONG,
        )
        n_long_btc = int((df_btc_fold.loc[btc_train, "y_long"] == 1).sum())
        if n_long_btc < 40:
            print(f"  [{t_year}] skip — trop peu de labels BTC ({n_long_btc})"); continue

        # Extra assets désactivés (collection institutionnelle = BTC only pour l'instant)
        extra_train_frames: List[pd.DataFrame] = []
        n_long_extra = 0

        # ── Train frame (BTC uniquement) ──────────────────────────────────────
        train_combined = df_btc_fold.loc[btc_train].copy()

        n_long_total = n_long_btc + n_long_extra
        print(f"  [{t_year}] train={len(train_combined):,} barres  "
              f"LONG positifs: BTC={n_long_btc} + extra={n_long_extra} = {n_long_total}")

        # ── Stage 1 : filtre tradeable ────────────────────────────────────────
        filt_avail = [f for f in active_filter_features if f in train_combined.columns]
        if not filt_avail:
            print(f"  [{t_year}] skip — aucune feature filtre disponible")
            continue
        X_ftr = train_combined[filt_avail].values.astype(np.float32)
        y_ftr = train_combined["tradeable_net"].values.astype(np.int32)

        filt_scaler = StandardScaler()
        Xf = filt_scaler.fit_transform(X_ftr)
        np_f = int(y_ftr.sum()); nn_f = len(y_ftr) - np_f
        spw_f = min(nn_f / max(np_f, 1), 10.0)

        filter_clf = HistGradientBoostingClassifier(
            max_iter=300, max_depth=4, learning_rate=0.05,
            l2_regularization=1.0, min_samples_leaf=20,
            class_weight={0: 1.0, 1: spw_f}, random_state=42,
        )
        filter_clf.fit(Xf, y_ftr)

        # Calibration filtre sur val BTC (F1, max=0.65, contrainte min_cal_bars)
        X_fv = filt_scaler.transform(
            _get_X(df_btc_fold, btc_val, filt_avail)
        )
        p_fv   = filter_clf.predict_proba(X_fv)[:, 1]
        y_fv   = df_btc_fold.loc[btc_val, "tradeable_net"].values.astype(np.int32)

        best_filt, best_f1 = 0.40, -1.0
        # FIX 1 : cap filtre à 0.55 (évite sur-fitting sur bear → 0 trades l'année suivante)
        for thr in np.arange(0.35, 0.56, 0.02):
            pred = (p_fv >= thr).astype(int)
            tp = int((pred & (y_fv == 1)).sum())
            fp = int((pred & (y_fv == 0)).sum())
            fn = int(((1 - pred) & (y_fv == 1)).sum())
            f1 = 2 * tp / max(2 * tp + fp + fn, 1)
            if f1 > best_f1:
                best_f1, best_filt = f1, thr

        if int((p_fv >= best_filt).sum()) < MIN_CAL_FILTER_BARS:
            for thr in np.arange(best_filt - 0.02, 0.33, -0.02):
                if int((p_fv >= thr).sum()) >= MIN_CAL_FILTER_BARS:
                    best_filt = thr
                    break

        # ── SMOTE augmentation des labels positifs ────────────────────────────
        # Objectif : tripler les exemples positifs pour les folds précoces
        # Cap à 5000 positifs pour éviter de ralentir les folds tardifs
        long_avail      = [f for f in active_long_features if f in train_combined.columns]
        if not long_avail:
            print(f"  [{t_year}] skip — aucune feature LONG disponible")
            continue
        train_augmented = augment_positives(
            train_combined, features=long_avail,
            multiplier=3, k_neighbors=5,
            min_pos_for_augment=50, max_pos_threshold=5_000,
        )

        # ── Stage 2 : TRM Fleet v2 (spécialistes complets, toutes features) ───
        fleet = TRMFleet(features=long_avail, n_recursive_rounds=2)
        fleet.train(
            df          = train_augmented.reset_index(drop=True),
            train_mask  = np.ones(len(train_augmented), dtype=bool),
            df_val_btc  = df_btc_fold,
            val_mask_in_btc = btc_val,
        )

        # ── Calibration seuil par contexte (PnL, plancher 0.54) ───────────────
        p_fv2    = _pp(filter_clf, filt_scaler, _get_X(df_btc_fold, btc_val, filt_avail))
        ret_val  = df_btc_fold.loc[btc_val, TARGET_COL].values.astype(np.float64)
        # Threshold minimum adaptatif : quand le modèle est meilleur (AUC élevée)
        # on peut être plus sélectif (threshold plus haut = moins de trades, plus propres).
        # Quand AUC est faible, on reste permissif pour avoir assez de trades.
        mean_auc_raw = [s.val_auc_ for s in fleet.specialists.values() if s.val_auc_ > 0]
        mean_auc_val = float(np.mean(mean_auc_raw)) if mean_auc_raw else 0.60
        adaptive_min = 0.57 if mean_auc_val >= 0.68 else (0.55 if mean_auc_val >= 0.62 else 0.54)

        ctx_thrs = calibrate_context_thresholds(
            fleet, df_btc_fold.loc[btc_val], p_fv2, best_filt,
            ret_val, cost_pct=COST_PCT, min_thr=adaptive_min, max_thr=0.65,
        )
        print(f"   Threshold min adaptatif : {adaptive_min:.2f}  (AUC moyen={mean_auc_val:.3f})")

        # AUC moyen de la flottée sur val BTC
        aucs_dict  = fleet.val_auc_summary()
        aucs_valid = [v for v in aucs_dict.values() if v > 0]
        fleet_auc  = float(np.mean(aucs_valid)) if aucs_valid else 0.5
        dir_thr_display = round(np.mean(list(ctx_thrs.values())), 2)

        # ── Backtest BTC test ─────────────────────────────────────────────────
        fold = _backtest_fleet_v2(
            df_btc_fold, btc_test, filter_clf, filt_scaler, fleet,
            filter_thr=best_filt, ctx_thresholds=ctx_thrs,
            filter_features=filt_avail,
        )

        bh  = float(df_btc_fold.loc[btc_test, TARGET_COL].dropna().sum())
        ok  = fold["pf"] >= DEPLOY_PF and fold["expectancy"] > DEPLOY_EXP
        cat = (fold["pf"] < CATASTROPHIC_PF and fold["n"] > CATASTROPHIC_N) \
               or fold["max_dd"] > CATASTROPHIC_MDD

        m = "✓" if ok else ("💀" if cat else "✗")
        aucs_str = " ".join(f"{k[:5]}={v:.2f}" for k, v in aucs_dict.items() if v > 0)
        print(
            f"  [{t_year}] {m}  n={fold['n']:3d}  "
            f"PF={fold['pf']:.3f}  E={fold['expectancy']:+.4f}  "
            f"WR={fold['win_rate']:.2f}  "
            f"filt={best_filt:.2f}  auc_mean={fleet_auc:.3f}  "
            f"B&H={bh:+.0%}  n_long={n_long_total}"
        )
        print(f"         AUC par ctx : {aucs_str}")
        print(f"         Seuils ctx  : " +
              " ".join(f"{k[:5]}={v:.2f}" for k, v in ctx_thrs.items()))

        try:
            fleet_report = fleet.to_fleet_report()
        except Exception:
            fleet_report = {"error": "to_fleet_report_failed", "n_specialists": len(fleet.specialists)}

        fold.update({
            "year": t_year, "ok": bool(ok), "catastrophic": bool(cat),
            "bh_log_return": round(bh * 100, 2),
            "filter_thr": best_filt,
            "ctx_thresholds": ctx_thrs,
            "fleet_auc_mean": round(fleet_auc, 4),
            "fleet_auc_by_ctx": {k: round(v, 3) for k, v in aucs_dict.items()},
            "n_long_btc": int(n_long_btc),
            "n_long_extra": int(n_long_extra),
            "n_long_total": int(n_long_total),
            "n_train_total": int(len(train_combined)),
            "feature_set": feature_set_name,
            "n_filter_features": int(len(filt_avail)),
            "n_long_features": int(len(long_avail)),
        })
        fold_results.append(fold)

        # Persist fleet metrics so the API can display all TRM specialists
        fleet_out = REPORT_DIR / "fleet_report.json"
        with open(fleet_out, "w") as _f:
            json.dump({
                "year": t_year,
                "n_train_total": int(len(train_combined)),
                **fleet_report,
            }, _f, indent=2)

    n_f   = len(fold_results)
    n_ok  = sum(1 for f in fold_results if f["ok"])
    n_cat = sum(1 for f in fold_results if f["catastrophic"])
    n_tr  = sum(f["n"] for f in fold_results)
    pf_v  = [f["pf"] for f in fold_results if f["n"] > 0]
    ex_v  = [f["expectancy"] for f in fold_results if f["n"] > 0]
    mpf   = float(np.median(pf_v))  if pf_v  else 0.0
    mex   = float(np.median(ex_v)) if ex_v else 0.0

    reasons = []
    if n_ok < MIN_FOLDS_OK:
        failed = [f"{f['year']}: PF={f['pf']:.2f}" for f in fold_results if not f["ok"]]
        reasons.append(f"only_{n_ok}/{n_f}_folds_ok: {failed}")
    if n_cat > 0:
        reasons.append(f"catastrophic: {[f['year'] for f in fold_results if f['catastrophic']]}")
    if n_tr < MIN_TOTAL_TRADES:
        reasons.append(f"not_enough_trades: {n_tr}")

    return {
        "deployable":        n_ok >= MIN_FOLDS_OK and n_cat == 0 and n_tr >= MIN_TOTAL_TRADES,
        "reasons":           reasons,
        "n_folds":           n_f,
        "n_ok":              n_ok,
        "n_catastrophic":    n_cat,
        "total_trades":      n_tr,
        "median_pf":         round(mpf, 4),
        "median_expectancy": round(mex, 4),
        "feature_set":       feature_set_name,
        "n_filter_features": len(active_filter_features),
        "n_long_features":   len(active_long_features),
        "n_extra_assets":    len(extra_assets),
        "folds":             fold_results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-set", choices=sorted(_feature_sets()), default="institutional",
                    help="Famille de features (défaut: institutional)")
    ap.add_argument("--ablation-report", default=str(REPORT_DIR / "ablation_report.json"))
    args = ap.parse_args()

    print("=" * 70)
    print("WALK-FORWARD 4h — TRM FLEET v3 × ohlcv_institutional_features_btcusdt")
    print("=" * 70)
    print(f"  Stage 2     : TRMFleet v3 — {TRM_FLEET_SIZE} TRM multi-horizon")
    print("                (9 horizons × 8 mouvements, routage top-k, AUC val réel)")
    print(f"  Source      : MongoDB ohlcv_institutional_features_btcusdt (interval=1h)")
    print(f"  Feature set : {args.feature_set}  "
          f"({len(FEATURES_INST_LONG)} long / {len(FEATURES_INST_FILTER)} filtre)")
    print(f"  TARGET_COL  : {TARGET_COL}  (alias label_future_log_return_5)")
    print(f"  Quantile    : {TRADEABLE_QUANTILE_LONG}")
    print(f"  Deploy      : ≥{MIN_FOLDS_OK}/7 folds  PF≥{DEPLOY_PF}")
    print()

    print("Chargement BTC depuis MongoDB…")
    df_btc = load_btc_institutional()
    print(f"  BTC : {len(df_btc):,} barres  {df_btc.index[0].date()} → {df_btc.index[-1].date()}")

    extra_assets: List[pd.DataFrame] = []   # BTC-only pour l'instant

    print()
    selected = _select_feature_set(args.feature_set)

    # Filtrer aux features réellement disponibles (fill ≥ 75%)
    selected["long"]   = get_available_features(df_btc, selected["long"],   min_fill=0.75)
    selected["filter"] = get_available_features(df_btc, selected["filter"], min_fill=0.75)
    print(f"  Features disponibles : {len(selected['long'])} long  /  {len(selected['filter'])} filtre")
    print()

    result = run_walk_forward(
        df_btc,
        extra_assets,
        feature_set_name=args.feature_set,
        long_features=selected["long"],
        filter_features=selected["filter"],
    )

    verdict = "✓ DEPLOYABLE" if result["deployable"] else "✗ NOT_DEPLOYABLE"
    print()
    print("─" * 70)
    print(f"VERDICT : {verdict}")
    print("─" * 70)
    print(f"  Folds OK          : {result['n_ok']}/{result['n_folds']}")
    print(f"  Folds cat.        : {result['n_catastrophic']}")
    print(f"  Total trades      : {result['total_trades']:,}")
    print(f"  PF médian         : {result['median_pf']:.4f}")
    print(f"  Expectancy méd.   : {result['median_expectancy']:+.4f}")
    if result["reasons"]:
        print("\n  Raisons :")
        for r in result["reasons"]:
            print(f"    • {r}")

    out = REPORT_DIR / "walk_forward_4h.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=_json_default)
    print(f"\n  Rapport → {out}")

    ablation_report = {
        "selected_run": result,
        "feature_set":  args.feature_set,
        "n_features":   {"long": len(selected["long"]), "filter": len(selected["filter"])},
        "source":       "ohlcv_institutional_features_btcusdt",
    }

    ablation_path = Path(args.ablation_report)
    ablation_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ablation_path, "w") as f:
        json.dump(ablation_report, f, indent=2, default=_json_default)
    print(f"  Rapport ablations → {ablation_path}")
    print("─" * 70)


if __name__ == "__main__":
    main()
