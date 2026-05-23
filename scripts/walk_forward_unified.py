#!/usr/bin/env python3
"""
scripts/walk_forward_unified.py — WALK-FORWARD UNIFIÉ v5
=========================================================

Pipeline canonique du projet — SEUL point d'entrée pour la validation.

Architecture :
  LONG   : TRMFleetLongV4  (100 TRM, 10 horizons × 10 archétypes)
           Labels : quantile 8h (défaut) OU Triple Barrier Lopez de Prado (--triple-barrier)
           Training : pool multi-actif (BTC+ETH+SOL+...) + SMOTE

  HEDGE  : RegimeAllocatorV5  (remplace TRM SHORT — audit 2026-05 → SHORT_REJECTED)
     1. Macro-régime BEAR/BULL/NEUTRAL (vote 24h sur EMA200+momentum)
        → En BEAR confirmé : taille LONG × 0.65 (réduction 35%)
        → Impact : -15 à -25% drawdown max sans toucher au PF LONG
     2. Funding Harvest Signal (rare, 20-40/an)
        → Short quand funding > 0.05%/8h ET OI↑ ET RSI > 60

Source de données :
  Priorité : data/enriched/{SYM}_1h_enriched.parquet (253 colonnes enrichies)
  Fallback  : data/*.csv (features basiques)

Critères de déploiement LONG :
  ≥ 5/7 folds OK, PF médian ≥ 1.30, 0 fold catastrophique

Usage :
  python scripts/walk_forward_unified.py
  python scripts/walk_forward_unified.py --triple-barrier
  python scripts/walk_forward_unified.py --folds 2022,2023,2024,2025
  python scripts/walk_forward_unified.py --no-regime
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_2.trm_fleet_long_v4  import (
    TRMFleetLongV4, calibrate_context_thresholds_v4, classify_context_v4,
    TEMPORAL_HORIZONS_V4, MOVEMENT_ARCHETYPES_V4,
)

# Regime Allocator v5 — remplace TRM SHORT (SHORT_REJECTED, audit 2026-05)
try:
    from ai.level_2.regime_allocator import run_regime_fold
    _HAS_REGIME_ALLOCATOR = True
except ImportError as _e_reg:
    print(f"   ⚠  Regime allocator non disponible : {_e_reg}")
    _HAS_REGIME_ALLOCATOR = False
from ai.level_0.features            import (
    get_available_features, FEATURES_LONG, FEATURES_COMMON,
)
# FEATURES_INST_LONG disponible si MongoDB — sinon fallback sur FEATURES_LONG (CSV)
try:
    from ai.level_0.institutional_features import FEATURES_INST_LONG, FEATURES_INST_FILTER
    _HAS_INST_FEATURES = True
except ImportError:
    _HAS_INST_FEATURES = False
    FEATURES_INST_LONG   = FEATURES_LONG
    FEATURES_INST_FILTER = FEATURES_COMMON
from ai.level_0.labels              import (
    compute_label_columns, build_labels, compute_long_regime_col,
    build_triple_barrier_labels_long,
)
from ai.level_0.constants           import (
    COST_PCT, COST_SHORT_MULT, TARGET_COL, INITIAL_EQUITY,
    TRAIN_END_YEAR, VAL_YEAR, TEST_FROM_YEAR,
)

# TRM SHORT pipeline désactivé — SHORT_REJECTED (audit 2026-05).
# Remplacé par RegimeAllocatorV5 ci-dessus.
_HAS_SHORT_PIPELINE = False

# Augmentation
try:
    from ai.level_0.augmentation    import augment_positives
    _HAS_SMOTE = True
except ImportError:
    _HAS_SMOTE = False

# Stage 1 filter (tradeable)
try:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    _HAS_SKL = True
except ImportError:
    _HAS_SKL = False


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DIR = ROOT / "reports" / "walk_forward_unified"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR           = ROOT / "data"
DATA_ENRICHED_DIR  = ROOT / "data" / "enriched"
DATA_HF_DIR        = ROOT / "data_hedge_fund"
PRIMARY_SYMBOL     = "BTCUSDT"        # symbole hedge_fund en priorité

DEPLOY_PF_LONG        = 1.30
DEPLOY_PF_SHORT       = 1.30
CATASTROPHIC_PF       = 0.70
MIN_FOLDS_OK_LONG     = 5
MIN_FOLDS_OK_SHORT    = 3
MIN_TRADES_PER_FOLD   = 5
MIN_TRADES_TOTAL_LONG = 80
MIN_TRADES_TOTAL_SHORT= 50
SMOTE_THRESHOLD       = 2000   # augmenter si moins de X positifs sur train

COST_SHORT = COST_PCT * COST_SHORT_MULT


# ─────────────────────────────────────────────────────────────────────────────
# Chargement des données multi-actifs
# ─────────────────────────────────────────────────────────────────────────────

def _load_enriched(
    symbol: str,
    required_cols: Optional[List[str]] = None,
) -> Optional[pd.DataFrame]:
    """
    Charge le parquet enrichi 1h depuis data/enriched/.

    Si required_cols est fourni, ne charge QUE ces colonnes + les colonnes
    systématiques (datetime, OHLCV, labels). Réduit la mémoire de ~40×
    (4045 → ~120 colonnes).
    """
    path = DATA_ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        return None
    try:
        if required_cols is not None:
            # Colonnes systématiques toujours chargées
            _ALWAYS = {
                "datetime", "open", "high", "low", "close", "Close", "volume",
                "taker_buy_base_asset_volume", "number_of_trades",
                # Colonnes nécessaires pour les labels et le régime gate
                "dist_ema_50", "dist_ema_200", "dist_ema_20",
                "ema_spread_50_200", "rsi_14", "mom_logret_72", "mom_logret_168",
                "ema_spread_20_50",
            }
            # Lire les colonnes disponibles dans le fichier
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(path)
            file_cols = set(pf.schema_arrow.names)
            cols_to_load = list((set(required_cols) | _ALWAYS) & file_cols)
            df = pd.read_parquet(path, columns=cols_to_load)
        else:
            df = pd.read_parquet(path)

        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"   ⚠  Impossible de charger {path.name}: {e}")
        return None


def _load_csv(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(path, parse_dates=["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        elif "Close" not in df.columns:
            return None
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        return df
    except Exception:
        return None


def load_multi_asset_data(
    primary_symbol:  str              = PRIMARY_SYMBOL,
    max_extra_assets: int             = 30,
    required_cols:   Optional[List[str]] = None,
) -> Tuple[Optional[pd.DataFrame], List[pd.DataFrame]]:
    """
    Charge BTC + altcoins en priorité depuis data/enriched/ (parquet 1h enrichi),
    fallback sur data/*.csv si le parquet n'existe pas.

    GARANTIT que le dataset primaire a les features complètes — lève RuntimeError
    si ni le parquet enrichi ni un CSV valide ne sont trouvés.
    """
    # ── Priorité 1 : parquet enrichi hedge_fund ───────────────────────────────
    # required_cols = None pour le premier chargement (découverte des colonnes)
    df_primary = _load_enriched(primary_symbol, required_cols=required_cols)
    data_source = "enriched"

    if df_primary is None:
        for alt in ("BTCUSDT", "BTC", "BTCUSD_1h_alpha", "BTCUSD"):
            df_primary = _load_enriched(alt, required_cols=required_cols)
            if df_primary is not None:
                primary_symbol = alt
                break

    # ── Fallback : CSV classiques ─────────────────────────────────────────────
    if df_primary is None:
        data_source = "csv"
        print("   ⚠  Aucun parquet enrichi trouvé — fallback sur CSV (features limitées)")
        print("      → Lancer d'abord : python scripts/build_hedge_fund_features.py")
        csv_files = sorted(DATA_DIR.glob("*.csv"))
        primary_path = next(
            (f for f in csv_files if "btc" in f.stem.lower()), None
        )
        if primary_path:
            df_primary = _load_csv(primary_path)

    if df_primary is None:
        raise RuntimeError(
            "Aucune donnée BTC disponible.\n"
            "1. Vérifier data/enriched/BTCUSDT_1h_enriched.parquet\n"
            "2. Ou lancer : python scripts/build_hedge_fund_features.py"
        )

    n_cols = len(df_primary.columns)
    print(f"   Primary [{data_source}] : {primary_symbol}  "
          f"{len(df_primary):,} barres  "
          f"{df_primary['datetime'].iloc[0].date()} → {df_primary['datetime'].iloc[-1].date()}  "
          f"[{n_cols} colonnes]")

    # ── Actifs supplémentaires ────────────────────────────────────────────────
    extra_dfs: List[pd.DataFrame] = []
    loaded = 0

    # D'abord les parquets enrichis — avec sélection de colonnes pour économiser la RAM
    if data_source == "enriched":
        for path in sorted(DATA_ENRICHED_DIR.glob("*_1h_enriched.parquet")):
            sym = path.stem.replace("_1h_enriched", "")
            if sym == primary_symbol:
                continue
            if loaded >= max_extra_assets:
                break
            df = _load_enriched(sym, required_cols=required_cols)
            if df is not None and len(df) >= 5000:
                extra_dfs.append(df)
                loaded += 1

    # Compléter avec les CSV si besoin
    if loaded < max_extra_assets:
        for f in sorted(DATA_DIR.glob("*.csv")):
            if loaded >= max_extra_assets:
                break
            df = _load_csv(f)
            if df is not None and len(df) >= 5000:
                extra_dfs.append(df)
                loaded += 1

    if extra_dfs:
        print(f"   Extra   : {loaded} actifs chargés ({data_source})")

    return df_primary, extra_dfs


# ─────────────────────────────────────────────────────────────────────────────
# Préparation des features et labels
# ─────────────────────────────────────────────────────────────────────────────

def _prepare_long_features(df: pd.DataFrame, feature_candidates: List[str]) -> List[str]:
    """
    Filtre les features candidates à celles présentes dans df avec fill ≥ 75%.

    Règle de validation stricte :
      - Une feature absente du df combiné est exclue (pas de zero-fill)
      - On log le nombre de features exclues pour traçabilité
      - Les features sélectionnées ici sont les SEULES passées à _get_X
        qui lève désormais une RuntimeError sur feature absente.
    """
    available = get_available_features(df, feature_candidates, min_fill=0.75, context="LONG")
    dropped = len(feature_candidates) - len(available)
    if dropped > 0:
        print(f"   _prepare_long_features : {dropped} features exclues "
              f"(absentes du df combiné ou fill<75%) → {len(available)} retenues")
    return available


def _build_long_dataset(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    feature_list: List[str],
    use_triple_barrier: bool = False,
) -> pd.DataFrame:
    """
    Labels LONG + régime + reversal sur un DataFrame complet.
    Appelé une fois par fold sur le dataset complet avant split.

    Si use_triple_barrier=True, ajoute aussi y_long_tb (Triple Barrier).
    """
    if TARGET_COL not in df.columns:
        df = compute_label_columns(df)
    if "regime_long" not in df.columns:
        df = compute_long_regime_col(df)
    df, stats = build_labels(df, train_mask)
    if use_triple_barrier:
        df = build_triple_barrier_labels_long(df)
    return df


def _build_short_dataset(df: pd.DataFrame, train_mask: np.ndarray) -> pd.DataFrame:
    """Désactivé — SHORT_REJECTED (audit 2026-05). Ne rien faire."""
    return df


def _train_filter_stage1(
    df: pd.DataFrame, train_mask: np.ndarray, feature_list: List[str]
) -> Tuple[Optional[object], Optional[object]]:
    """Entraîne le filtre tradeable Stage 1."""
    if not _HAS_SKL or "tradeable_net" not in df.columns:
        return None, None
    feats  = [f for f in feature_list if f in df.columns]
    if not feats:
        return None, None
    X_tr   = df.loc[train_mask, feats].fillna(0.0).values.astype(np.float32)
    y_tr   = df.loc[train_mask, "tradeable_net"].values.astype(np.int32)
    valid  = np.isfinite(X_tr).all(axis=1)
    X_tr, y_tr = X_tr[valid], y_tr[valid]
    if len(y_tr) < 100 or y_tr.sum() < 10:
        return None, None
    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X_tr)
    clf    = HistGradientBoostingClassifier(
        max_iter=200, max_depth=4, min_samples_leaf=20,
        class_weight="balanced", random_state=42,
    )
    clf.fit(X_sc, y_tr)
    return clf, scaler


# ─────────────────────────────────────────────────────────────────────────────
# Backtest d'un fold
# ─────────────────────────────────────────────────────────────────────────────

def _backtest_fold_long(
    df_test:    pd.DataFrame,
    fleet:      TRMFleetLongV4,
    thresholds: Dict[str, float],
    feature_list: List[str],
    filter_clf=None,
    filter_scaler=None,
    filter_features: Optional[List[str]] = None,
    filter_thr: float = 0.50,
    cost_pct:   float = COST_PCT,
) -> Dict:
    n = len(df_test)
    if n == 0:
        return {"n_trades": 0, "pf": 0.0, "expectancy": 0.0, "wr": 0.0}

    ones   = np.ones(n, dtype=bool)
    p_all  = fleet.predict(df_test, ones)
    ctx_all = classify_context_v4(df_test)

    # Filtre Stage 1
    if filter_clf is not None and filter_scaler is not None and filter_features:
        ff_avail = [f for f in filter_features if f in df_test.columns]
        if ff_avail:
            X_f = df_test[ff_avail].fillna(0.0).values.astype(np.float32)
            p_f = filter_clf.predict_proba(filter_scaler.transform(X_f))[:, 1]
            tradeable = p_f >= filter_thr
        else:
            tradeable = ones
    else:
        tradeable = ones

    # Gate NO_LONG
    if "regime_long" in df_test.columns:
        no_long = df_test["regime_long"].values == "NO_LONG"
        tradeable = tradeable & ~no_long

    rets = df_test[TARGET_COL].fillna(0.0).values if TARGET_COL in df_test.columns \
           else np.zeros(n, dtype=np.float64)

    trade_rets: List[float] = []
    for i in range(n):
        if not tradeable[i]:
            continue
        ctx = str(ctx_all[i])
        thr = thresholds.get(ctx, thresholds.get("general", 0.54))
        if p_all[i] >= thr:
            net = float(rets[i]) - cost_pct
            trade_rets.append(net)

    return _compute_metrics(trade_rets, "long")


def _backtest_fold_short(
    df_test:     pd.DataFrame,
    fleet:       object,
    thresholds:  Dict[str, float],
    feature_list: List[str],
    ret_col:     str   = "future_ret_short_4h",
    cost_short:  float = COST_SHORT,
) -> Dict:
    """Désactivé — SHORT_REJECTED (audit 2026-05). Retourne zéro."""
    return {"n_trades": 0, "pf": 0.0, "expectancy": 0.0, "wr": 0.0}


def _compute_metrics(trade_rets: List[float], side: str) -> Dict:
    if not trade_rets:
        return {
            "n_trades": 0, "pf": 0.0, "expectancy": 0.0, "wr": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "max_drawdown": 0.0, "total_pnl": 0.0,
        }
    arr   = np.array(trade_rets, dtype=np.float64)
    wins  = arr[arr > 0]
    losses= arr[arr < 0]
    gw    = float(wins.sum())  if len(wins)   else 0.0
    gl    = float(abs(losses.sum())) if len(losses) else 0.0
    pf    = gw / max(gl, 1e-9)
    wr    = len(wins) / len(arr)

    # Drawdown
    equity = np.cumprod(1.0 + arr * 0.01)   # position size 1% equity
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / np.maximum(peak, 1e-9)
    max_dd = float(abs(dd.min())) * 100.0

    return {
        "n_trades":    len(arr),
        "pf":          round(pf, 3),
        "expectancy":  round(float(arr.mean()) * 100.0, 4),
        "wr":          round(wr, 3),
        "avg_win":     round(float(wins.mean())   if len(wins)   else 0.0, 5),
        "avg_loss":    round(float(losses.mean()) if len(losses) else 0.0, 5),
        "max_drawdown":round(max_dd, 2),
        "total_pnl":   round(float(arr.sum()), 5),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward par fold
# ─────────────────────────────────────────────────────────────────────────────

def run_fold(
    df_primary: pd.DataFrame,
    extra_dfs:  List[pd.DataFrame],
    test_year:  int,
    feature_list_long:  List[str],
    feature_list_short: List[str],
    filter_features:    List[str],
    run_short:          bool = False,    # désactivé — SHORT_REJECTED
    use_triple_barrier: bool = False,    # Triple Barrier labels pour le training LONG
    run_regime:         bool = True,     # Régime allocator v5 (hedge)
) -> Dict:
    """
    Un fold complet :
      train  ≤ test_year - 2
      val    = test_year - 1
      test   = test_year  (BTC uniquement)
    """
    years = df_primary["datetime"].dt.year.values
    train_mask = years <= (test_year - 2)
    val_mask   = years == (test_year - 1)
    test_mask  = years == test_year

    if train_mask.sum() < 2000 or val_mask.sum() < 500 or test_mask.sum() < 500:
        return {"year": test_year, "skip": True, "reason": "insufficient_data"}

    print(f"\n  ── Fold {test_year}  "
          f"[train ≤{test_year-2} : {train_mask.sum():,}] "
          f"[val {test_year-1} : {val_mask.sum():,}] "
          f"[test {test_year} : {test_mask.sum():,}]")

    # ── 1. Construire les labels sur le dataset COMPLET (anti-leakage) ────────
    df_primary_labeled = _build_long_dataset(
        df_primary, train_mask, feature_list_long,
        use_triple_barrier=use_triple_barrier,
    )

    # ── 2. Multi-actif train : concaténer BTC + altcoins ─────────────────────
    # Règle de compatibilité features : un actif extra est inclus SEULEMENT
    # si il a ≥ 70% des features sélectionnées. Sinon exclu (pas de zero-fill).
    MIN_FEATURE_COVERAGE = 0.70
    df_btc_train = df_primary_labeled.loc[train_mask].copy()   # BTC seul — pour feature selection
    dfs_train = [df_btc_train]
    n_extras_included = 0
    for df_extra in extra_dfs:
        yrs_extra = df_extra["datetime"].dt.year.values
        train_extra = yrs_extra <= (test_year - 2)
        if train_extra.sum() < 500:
            continue

        # Vérifier la couverture features avant de labelliser
        n_feat_present = sum(1 for f in feature_list_long if f in df_extra.columns)
        coverage = n_feat_present / max(len(feature_list_long), 1)
        if coverage < MIN_FEATURE_COVERAGE:
            continue   # actif incompatible — exclure sans zero-fill

        try:
            df_ex_lab = _build_long_dataset(
                df_extra, train_extra, feature_list_long,
                use_triple_barrier=use_triple_barrier,
            )
            dfs_train.append(df_ex_lab.loc[train_extra].copy())
            n_extras_included += 1
        except Exception:
            continue

    if n_extras_included > 0:
        print(f"   Multi-actif : {n_extras_included} actifs compatibles inclus dans train")
    else:
        print(f"   Multi-actif : aucun actif extra compatible (coverage ≥{MIN_FEATURE_COVERAGE:.0%}) — BTC seul")

    df_train_combined = pd.concat(dfs_train, ignore_index=True)

    # ── 3. Sélection de la colonne label LONG ────────────────────────────────
    # Si Triple Barrier activé et colonne présente → utiliser y_long_tb pour training
    long_label_col = "y_long"
    if use_triple_barrier and "y_long_tb" in df_train_combined.columns:
        n_tb_pos = int((df_train_combined["y_long_tb"] == 1).sum())
        n_tb_neg = int((df_train_combined["y_long_tb"] == 0).sum())
        if n_tb_pos >= 50 and n_tb_neg >= 50:
            long_label_col = "y_long_tb"
            print(f"   Triple Barrier : label=y_long_tb "
                  f"profit={n_tb_pos:,}  stop={n_tb_neg:,}")
        else:
            print(f"   Triple Barrier : trop peu d'exemples ({n_tb_pos}+{n_tb_neg})"
                  " → fallback y_long")

    # ── 4. SMOTE augmentation sur le train LONG ───────────────────────────────
    n_pos_long = int((df_train_combined[long_label_col] == 1).sum()) \
                 if long_label_col in df_train_combined.columns else 0
    if _HAS_SMOTE and n_pos_long < SMOTE_THRESHOLD and n_pos_long >= 50:
        try:
            _feat_smote = [f for f in feature_list_long if f in df_train_combined.columns]
            df_train_combined = augment_positives(
                df_train_combined,
                features=_feat_smote,
                label_col=long_label_col,
                multiplier=min(3, max(1, SMOTE_THRESHOLD // max(n_pos_long, 1))),
            )
        except Exception as e:
            print(f"   ⚠  SMOTE LONG échoué : {e}")

    train_mask_combined = np.ones(len(df_train_combined), dtype=bool)

    # ── 5. Features filtrées aux colonnes disponibles ─────────────────────────
    # Feature selection basée sur BTC seul : évite que les altcoins sparse
    # (débuts de série avec MTF NaN) ne fassent chuter le fill rate des features
    # BTC valides → perte de 90→51 features sur les premiers folds.
    feat_long  = _prepare_long_features(df_btc_train, feature_list_long)
    feat_short = [f for f in feature_list_short if f in df_train_combined.columns]
    feat_filter= [f for f in filter_features    if f in df_train_combined.columns]

    # ── 6. Filtre Stage 1 ─────────────────────────────────────────────────────
    filter_clf, filter_scaler = _train_filter_stage1(
        df_train_combined, train_mask_combined, feat_filter
    )

    # ── 7. Entraîner TRM LONG v4 ──────────────────────────────────────────────
    df_val_btc  = df_primary_labeled.loc[val_mask].copy()
    val_mask_btc= np.ones(len(df_val_btc), dtype=bool)

    fleet_long = TRMFleetLongV4(features=feat_long)
    fleet_long.train(
        df_train_combined, train_mask_combined,
        df_val_btc=df_val_btc,            # val BTC séparé (pour AUC val réel)
        val_mask_in_btc=val_mask_btc,
        label_col=long_label_col,
    )

    # ── 8. Calibration seuils LONG sur val BTC ────────────────────────────────
    ret_val = df_val_btc[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val_btc.columns \
              else np.zeros(len(df_val_btc))
    filter_p_val = np.ones(len(df_val_btc), dtype=np.float32)
    if filter_clf is not None and filter_scaler is not None and feat_filter:
        ff_avail = [f for f in feat_filter if f in df_val_btc.columns]
        if ff_avail:
            Xf = df_val_btc[ff_avail].fillna(0.0).values.astype(np.float32)
            filter_p_val = filter_clf.predict_proba(filter_scaler.transform(Xf))[:, 1]

    thr_long = calibrate_context_thresholds_v4(
        fleet_long, df_val_btc,
        filter_p=filter_p_val, filter_thr=0.50,
        ret_val=ret_val, cost_pct=COST_PCT,
    )
    adapt_thr = fleet_long.adaptive_threshold()
    thr_long  = {k: max(v, adapt_thr) for k, v in thr_long.items()}

    # ── 9. Backtest LONG sur test ──────────────────────────────────────────────
    df_test = df_primary_labeled.loc[test_mask].copy()
    result_long = _backtest_fold_long(
        df_test, fleet_long, thr_long, feat_long,
        filter_clf, filter_scaler, feat_filter,
    )

    # ── 10. Régime Allocator v5 ───────────────────────────────────────────────
    # Remplace TRM SHORT (SHORT_REJECTED) — deux mécanismes :
    #   A. Macro-régime BEAR → taille LONG × 0.65 (réduction drawdown)
    #   B. Funding Harvest Signal → short rare haute précision (20-40/an)
    result_regime: Dict = {
        "bear_pct": 0.0, "bull_pct": 0.0,
        "size_mult_mean": 1.0, "dd_reduction_est_pct": 0.0,
        "harvest_n": 0, "harvest_pf": 0.0, "harvest_wr": 0.0,
    }
    if run_regime and _HAS_REGIME_ALLOCATOR:
        try:
            result_regime = run_regime_fold(df_test, cost_short=COST_SHORT)
            print(f"  [{test_year}] RÉGIME  "
                  f"BEAR={result_regime['bear_pct']:.1f}%  "
                  f"BULL={result_regime['bull_pct']:.1f}%  "
                  f"sizing_moyen={result_regime['size_mult_mean']:.3f}  "
                  f"DD_est=-{result_regime['dd_reduction_est_pct']:.1f}%  "
                  f"harvest n={result_regime['harvest_n']} PF={result_regime['harvest_pf']:.2f}")
        except Exception as _e_reg:
            print(f"  [{test_year}] ⚠  Regime allocator erreur : {_e_reg}")

    # ── 11. Verdict par fold ──────────────────────────────────────────────────
    def fold_status(res: Dict, deploy_pf: float) -> str:
        n, pf = res["n_trades"], res["pf"]
        if n < MIN_TRADES_PER_FOLD:
            return "NO_TRADES"
        dd = res.get("max_drawdown", 0.0)
        if pf < CATASTROPHIC_PF or dd > 20.0:
            return "CATASTROPHIC"
        if pf >= deploy_pf and n >= MIN_TRADES_PER_FOLD:
            return "OK"
        return "WEAK"

    status_long = fold_status(result_long, DEPLOY_PF_LONG)

    # Comparaison B&H
    bh = 0.0
    close_col = "Close" if "Close" in df_test.columns else "close"
    prices = df_test[close_col].dropna()
    if len(prices) > 1:
        bh = (float(prices.iloc[-1]) - float(prices.iloc[0])) / float(prices.iloc[0]) * 100

    print(f"  [{test_year}] LONG  [{status_long:^12}]  "
          f"n={result_long['n_trades']:4d}  "
          f"PF={result_long['pf']:.3f}  "
          f"E={result_long['expectancy']:+.4f}%  "
          f"WR={result_long['wr']:.0%}  "
          f"DD={result_long.get('max_drawdown', 0):.1f}%  "
          f"B&H={bh:+.0f}%")

    return {
        "year":           test_year,
        "skip":           False,
        "long":           result_long,
        "regime":         result_regime,
        "status_long":    status_long,
        "bh_pct":         round(bh, 2),
        "label_col":      long_label_col,
        "fleet_long_auc_mean": fleet_long._fleet_auc_mean,
        "thr_long_sample": {k: thr_long.get(k, 0.54) for k in list(thr_long.keys())[:3]},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Verdict global
# ─────────────────────────────────────────────────────────────────────────────

def _global_verdict(fold_results: List[Dict]) -> Dict:
    valid = [f for f in fold_results if not f.get("skip")]

    # ── LONG ─────────────────────────────────────────────────────────────────
    long_results = [f["long"] for f in valid]
    ok_long   = sum(1 for f in valid if f["status_long"] == "OK")
    cata_long = sum(1 for f in valid if f["status_long"] == "CATASTROPHIC")
    pf_long   = sorted([r["pf"] for r in long_results if r["n_trades"] >= MIN_TRADES_PER_FOLD])
    pf_med_long  = float(np.median(pf_long)) if pf_long else 0.0
    n_total_long = sum(r["n_trades"] for r in long_results)

    if cata_long > 0:
        verdict_long = "NOT_DEPLOYABLE"
        reason_long  = [f"catastrophic: {cata_long} fold(s)"]
    elif ok_long < MIN_FOLDS_OK_LONG:
        verdict_long = "NOT_DEPLOYABLE"
        reason_long  = [f"only_{ok_long}/{len(valid)}_folds_ok (need {MIN_FOLDS_OK_LONG})"]
    elif pf_med_long < DEPLOY_PF_LONG:
        verdict_long = "NOT_DEPLOYABLE"
        reason_long  = [f"pf_median={pf_med_long:.3f} < {DEPLOY_PF_LONG}"]
    elif n_total_long < MIN_TRADES_TOTAL_LONG:
        verdict_long = "NOT_DEPLOYABLE"
        reason_long  = [f"total_trades={n_total_long} < {MIN_TRADES_TOTAL_LONG}"]
    else:
        verdict_long = "DEPLOYABLE"
        reason_long  = []

    # ── RÉGIME ALLOCATOR ─────────────────────────────────────────────────────
    regime_results = [f.get("regime", {}) for f in valid]
    bear_pcts      = [r.get("bear_pct", 0.0)  for r in regime_results]
    dd_ests        = [r.get("dd_reduction_est_pct", 0.0) for r in regime_results]
    harvest_totals = sum(r.get("harvest_n", 0) for r in regime_results)
    harvest_pfs    = [r.get("harvest_pf", 0.0) for r in regime_results if r.get("harvest_n", 0) > 0]

    return {
        "long": {
            "verdict":     verdict_long,
            "folds_ok":    ok_long,
            "folds_total": len(valid),
            "cata":        cata_long,
            "pf_median":   round(pf_med_long, 3),
            "n_trades":    n_total_long,
            "reasons":     reason_long,
        },
        "regime": {
            "bear_pct_mean":        round(float(np.mean(bear_pcts))  if bear_pcts  else 0.0, 1),
            "dd_reduction_est_pct": round(float(np.mean(dd_ests))    if dd_ests    else 0.0, 1),
            "harvest_total_trades": harvest_totals,
            "harvest_pf_mean":      round(float(np.mean(harvest_pfs)) if harvest_pfs else 0.0, 3),
            "note":                 "SHORT_REJECTED — hedge uniquement via sizing + funding harvest",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Affichage du rapport final
# ─────────────────────────────────────────────────────────────────────────────

def _print_final_report(fold_results: List[Dict], verdict: Dict) -> None:
    print("\n" + "=" * 72)
    print("VERDICT FINAL — TRM FLEET v5 LONG + RÉGIME ALLOCATOR")
    print("=" * 72)

    # Tableau LONG par fold
    print(f"\n  {'Année':^6} {'LONG Status':^14} {'N':>5} {'PF':>6} {'WR':>5} "
          f"{'DD%':>5}  {'BEAR%':>6} {'Harvest':>8}")
    print("  " + "-" * 66)
    for f in fold_results:
        if f.get("skip"):
            print(f"  [{f['year']}] SKIP — {f.get('reason', '')}")
            continue
        l = f["long"]
        r = f.get("regime", {})
        lbl = f.get("label_col", "y_long")
        tb_tag = " (TB)" if lbl == "y_long_tb" else ""
        print(
            f"  [{f['year']}] {f['status_long']:^14} {l['n_trades']:5d} "
            f"{l['pf']:6.3f} {l['wr']:5.0%} "
            f"{l.get('max_drawdown', 0):5.1f}%"
            f"  {r.get('bear_pct', 0):5.1f}%"
            f"  n={r.get('harvest_n', 0):3d} PF={r.get('harvest_pf', 0):.2f}"
            f"{tb_tag}"
        )

    # Verdict LONG
    vl = verdict["long"]
    icon_l = "✓ DEPLOYABLE" if vl["verdict"] == "DEPLOYABLE" else "✗ NOT_DEPLOYABLE"
    print(f"\n  LONG  : {icon_l}")
    print(f"    Folds OK    : {vl['folds_ok']}/{vl['folds_total']}")
    print(f"    Catastroph. : {vl['cata']}")
    print(f"    PF médian   : {vl['pf_median']:.3f}")
    print(f"    Total trades: {vl['n_trades']}")
    if vl["reasons"]:
        print(f"    Raisons     : {vl['reasons']}")

    # Régime Allocator
    vr = verdict.get("regime", {})
    print(f"\n  HEDGE : Régime Allocator v5  (SHORT_REJECTED → hedge uniquement)")
    print(f"    BEAR moyen     : {vr.get('bear_pct_mean', 0):.1f}% du temps")
    print(f"    DD réd. est.   : -{vr.get('dd_reduction_est_pct', 0):.1f}% (BEAR×0.65)")
    h_n  = vr.get('harvest_total_trades', 0)
    h_pf = vr.get('harvest_pf_mean', 0.0)
    if h_n > 0:
        print(f"    Funding Harvest: {h_n} trades, PF moyen={h_pf:.3f}")
    else:
        print(f"    Funding Harvest: 0 trades (conditions non réunies sur période)")

    print("=" * 72)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-Forward Unifié TRM v5 LONG + Régime Hedge")
    parser.add_argument("--symbol",         type=str, default=PRIMARY_SYMBOL,
                        help="Actif primaire (BTC)")
    parser.add_argument("--folds",          type=str, default=None,
                        help="Folds séparés par virgule, ex: 2022,2023,2024,2025")
    parser.add_argument("--max-assets",     type=int, default=30,
                        help="Nombre max d'altcoins pour le train multi-actif")
    parser.add_argument("--triple-barrier", action="store_true",
                        help="Utiliser Triple Barrier (Lopez de Prado) pour les labels LONG")
    parser.add_argument("--no-regime",      action="store_true",
                        help="Désactiver le Régime Allocator v5")
    args = parser.parse_args()

    print("=" * 72)
    print("WALK-FORWARD UNIFIÉ v5 — TRM FLEET LONG + RÉGIME ALLOCATOR")
    print("=" * 72)
    print(f"  LONG  : {len(TEMPORAL_HORIZONS_V4)} horizons × {len(MOVEMENT_ARCHETYPES_V4)} archétypes = 100 TRM")
    print(f"  HEDGE : Régime Allocator v5 (BEAR sizing 0.65 + Funding Harvest)")
    label_mode = "Triple Barrier (Lopez de Prado)" if args.triple_barrier else "Quantile 8h (défaut)"
    print(f"  Labels: {label_mode}")
    print(f"  Coûts : LONG={COST_PCT*10000:.0f}bps  SHORT={COST_SHORT*10000:.0f}bps")
    print(f"  Dépl. : LONG≥{MIN_FOLDS_OK_LONG}/n folds OK, PF≥{DEPLOY_PF_LONG}")
    print(f"  SHORT : DÉSACTIVÉ — SHORT_REJECTED (audit 2026-05)")
    print()

    # ── Passe 1 : charger BTC complet pour découvrir les features disponibles ──
    # (chargement toutes colonnes sur BTC seul — ~900 MB temporaire)
    print("   Passe 1 : découverte features sur BTC enrichi …")
    df_primary_full, _ = load_multi_asset_data(
        primary_symbol=args.symbol, max_extra_assets=0  # BTC seul pour la découverte
    )
    if df_primary_full is None:
        print("   ✗  Impossible de charger les données. Vérifier data/")
        sys.exit(1)

    _cands_long = list(dict.fromkeys(FEATURES_INST_LONG + FEATURES_LONG))
    _cands_filt = list(dict.fromkeys(list(FEATURES_INST_FILTER) + list(FEATURES_COMMON)))

    # SHORT pipeline désactivé — pas de feature selection SHORT
    feat_short_cand: List[str] = []

    feat_long_cand   = get_available_features(df_primary_full, _cands_long, min_fill=0.75, context="LONG_CAND")
    feat_filter_cand = get_available_features(df_primary_full, _cands_filt, min_fill=0.75, context="FILTER_CAND")

    print(f"\n  Features sélectionnées (fill≥75%) :")
    print(f"    LONG   : {len(feat_long_cand)}")
    print(f"    FILTER : {len(feat_filter_cand)}")
    print(f"    SHORT  : {len(feat_short_cand)}")
    if len(feat_long_cand) < 20:
        sys.exit("   ✗  Trop peu de features LONG (<20). Relancer build_hedge_fund_features.py")

    # Libérer le DataFrame complet — on va recharger avec sélection de colonnes
    del df_primary_full
    import gc; gc.collect()

    # ── Passe 2 : recharger TOUS les actifs avec sélection de colonnes ─────────
    # Seulement les ~100-120 colonnes nécessaires → ~40× moins de RAM
    _required_cols = list(set(feat_long_cand) | set(feat_filter_cand) | set(feat_short_cand))
    print(f"\n   Passe 2 : chargement {len(_required_cols)} colonnes × tous actifs …")
    df_primary, extra_dfs = load_multi_asset_data(
        primary_symbol=args.symbol,
        max_extra_assets=args.max_assets,
        required_cols=_required_cols,
    )

    # Folds
    if args.folds:
        test_years = [int(y) for y in args.folds.split(",")]
    else:
        all_years = sorted(df_primary["datetime"].dt.year.unique())
        test_years = [y for y in all_years if y >= 2020]

    # Walk-forward
    fold_results: List[Dict] = []
    for ty in test_years:
        result = run_fold(
            df_primary=df_primary,
            extra_dfs=extra_dfs,
            test_year=ty,
            feature_list_long=feat_long_cand,
            feature_list_short=feat_short_cand,
            filter_features=feat_filter_cand,
            run_short=False,
            use_triple_barrier=args.triple_barrier,
            run_regime=not args.no_regime,
        )
        fold_results.append(result)

    # Verdict global
    verdict = _global_verdict(fold_results)
    _print_final_report(fold_results, verdict)

    # Sauvegarde
    report = {
        "config": {
            "version":        "v5",
            "n_trm_long":     100,
            "short_status":   "SHORT_REJECTED_2026-05",
            "hedge_mode":     "regime_allocator_v5",
            "horizons":       [h.key for h in TEMPORAL_HORIZONS_V4],
            "archetypes":     [m.key for m in MOVEMENT_ARCHETYPES_V4],
            "label_mode":     "triple_barrier" if args.triple_barrier else "quantile_8h",
            "cost_long_bps":  COST_PCT * 10000,
            "cost_short_bps": COST_SHORT * 10000,
        },
        "folds":   fold_results,
        "verdict": verdict,
    }
    out_path = REPORT_DIR / "walk_forward_unified_results.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Rapport JSON → {out_path}")


if __name__ == "__main__":
    main()
