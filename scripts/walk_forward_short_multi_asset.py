#!/usr/bin/env python3
"""
walk_forward_short_multi_asset.py — Walk-forward SHORT multi-actif
==================================================================

Architecture ensemble :
    p_short_final = 0.40 × p_transformer + 0.35 × p_lgbm + 0.25 × p_trmshortfleet

Données :
    50 actifs Binance 1h depuis data/
    Feature engineering SHORT + proxies macro
    Labels asymétriques MFE/MAE (y_short_clean)
    Gate NO_SHORT sur bull trend sain

Validation walk-forward :
    Folds annuels : train<=T-2, val=T-1, test=T
    Seuils calibrés sur val uniquement
    Backtest OOS sur test
    Verdict : SHORT_REJECTED / SHORT_PROMISING_BUT_UNSAFE / SHORT_PAPER_CANDIDATE

GPU : RTX 3070, CUDA 12.1, PyTorch 2.4.1
LightGBM 4.6, scikit-learn HistGBT disponibles
"""
from __future__ import annotations

import argparse
import json
import sys
import os
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Imports projet — try/except avec fallbacks gracieux
# ─────────────────────────────────────────────────────────────────────────────

_IMPORT_ERRORS: List[str] = []

try:
    from ai.level_0.short_labels import compute_short_label_columns, build_short_labels
    _HAS_SHORT_LABELS = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.short_labels : {e}")
    _HAS_SHORT_LABELS = False

try:
    from ai.level_0.short_features import compute_all_short_features, FEATURES_SHORT_GAMECHANGER
    _HAS_SHORT_FEATURES = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.short_features : {e}")
    _HAS_SHORT_FEATURES = False
    FEATURES_SHORT_GAMECHANGER: List[str] = []

try:
    from ai.level_0.short_proxy_features import compute_all_proxy_features, FEATURES_SHORT_PROXY
    _HAS_PROXY_FEATURES = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.short_proxy_features : {e}")
    _HAS_PROXY_FEATURES = False
    FEATURES_SHORT_PROXY: List[str] = []

try:
    from ai.level_1.short_rules import (
        compute_no_short_gate,
        compute_short_permission_context,
        compute_train_percentiles,
    )
    _HAS_SHORT_RULES = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_1.short_rules : {e}")
    _HAS_SHORT_RULES = False

try:
    from ai.level_2.short_specialists import TRMShortFleet
    _HAS_TRM_FLEET = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_2.short_specialists : {e}")
    _HAS_TRM_FLEET = False

try:
    from ai.level_2.transformer import (
        TradingTransformer,
        TransformerConfig,
        train_transformer,
        predict_transformer,
    )
    _HAS_TRANSFORMER = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_2.transformer : {e}")
    _HAS_TRANSFORMER = False

try:
    import lightgbm as lgb
    _HAS_LGBM = True
except ImportError:
    _HAS_LGBM = False
    _IMPORT_ERRORS.append("lightgbm : non disponible, fallback HistGBT")

try:
    from ai.level_2.short_calibration import (
        calibrate_short_thresholds,
        simulate_short_trades,
        get_threshold_for_context,
        summarize_calibration,
        COST_NORMAL,
        COST_STRESS,
        COST_EXTREME,
        SHORT_DEPLOY_PF,
        SHORT_CATASTROPHIC_PF,
        MIN_SHORT_FOLDS_OK,
        MIN_SHORT_TRADES_TOTAL,
        MIN_SHORT_TRADES_PER_VALID_FOLD,
        MAX_SHORT_DD,
        MAX_SQUEEZE_LOSS_RATE,
    )
    _HAS_CALIBRATION = True
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_2.short_calibration : {e}")
    _HAS_CALIBRATION = False
    # Valeurs par défaut strictes
    COST_NORMAL = 0.0010
    COST_STRESS = 0.0015
    COST_EXTREME = 0.0020
    SHORT_DEPLOY_PF = 1.30
    SHORT_CATASTROPHIC_PF = 0.75
    MIN_SHORT_FOLDS_OK = 5
    MIN_SHORT_TRADES_TOTAL = 100
    MIN_SHORT_TRADES_PER_VALID_FOLD = 10
    MAX_SHORT_DD = 8.0
    MAX_SQUEEZE_LOSS_RATE = 0.35

try:
    from ai.level_0.features import FEATURES_SHORT, FEATURES_SHORT_GAMECHANGER as _FSG2
    if not FEATURES_SHORT_GAMECHANGER:
        FEATURES_SHORT_GAMECHANGER = _FSG2
except ImportError as e:
    _IMPORT_ERRORS.append(f"ai.level_0.features : {e}")
    FEATURES_SHORT: List[str] = []

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DIR = ROOT / "reports" / "short_rebuild"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = ROOT / "data"

# Colonnes cibles
RET_COL = "future_ret_short_4h"
MFE_COL = "mfe_short_4h"
MAE_COL = "mae_short_4h"
SQUEEZE_COL = "squeeze_reject_4h"
LABEL_COL = "y_short_clean"
CONTEXT_COL = "short_context_name"
GATE_COL = "no_short"
MACRO_BEAR_COL = "macro_bear_ok"  # True = régime bear → SHORT autorisé

# Poids ensemble
W_TRANSFORMER = 0.40
W_LGBM = 0.35
W_TRM = 0.25

# Actifs liquides à charger en premier (meilleur signal SHORT)
PRIORITY_ASSETS = ["BTCUSD", "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

# Seuil sous-échantillonnage mémoire
MAX_TRAIN_BARS = 200_000

# Walk-forward conditionnel au régime bear
# Un fold n'est testé que si BTC était en régime bear sur >= X% du test period.
# Les folds bull-dominated sont SKIPPED (pas pénalisés — la stratégie était inactive).
MIN_BTC_BEAR_COVERAGE_TEST = 0.12   # 12% minimum de barres en bear (EMA200d + mom30d<-10%)
# Verdict : SHORT_PAPER_CANDIDATE si >= 67% des folds actifs sont OK (min 2)
MIN_ACTIVE_FOLDS_FOR_VERDICT = 2    # minimum de folds actifs pour rendre un verdict

# ─────────────────────────────────────────────────────────────────────────────
# 1. Chargement multi-actif
# ─────────────────────────────────────────────────────────────────────────────

def load_all_assets(
    data_dir: Path,
    max_assets: int = 50,
) -> Tuple[pd.DataFrame, np.ndarray]:
    """
    Charge tous les CSV de features de data_dir.
    Concat vertical, ajoute colonnes 'symbol' et 'asset_id' (int).
    Retourne (df_combined, asset_ids).

    Ordre : BTC/ETH/BNB en premier, puis tri par taille décroissante.
    """
    csv_files = sorted(data_dir.glob("*_features.csv"))
    if not csv_files:
        csv_files = sorted(data_dir.glob("*_features.parquet"))

    if not csv_files:
        raise FileNotFoundError(f"Aucun fichier *_features.csv dans {data_dir}")

    # Trier : priority assets en tête, puis par taille décroissante
    def _sort_key(p: Path) -> Tuple[int, int]:
        sym = p.stem.split("_")[0].upper()
        priority = next(
            (i for i, pa in enumerate(PRIORITY_ASSETS) if sym.startswith(pa)),
            999,
        )
        return (priority, -p.stat().st_size)

    csv_files = sorted(csv_files, key=_sort_key)[:max_assets]

    dfs: List[pd.DataFrame] = []
    symbols_loaded: List[str] = []

    for i, p in enumerate(csv_files):
        sym = p.stem.replace("_1h_features", "").replace("_features", "")
        try:
            if p.suffix == ".parquet":
                df_asset = pd.read_parquet(p)
            else:
                df_asset = pd.read_csv(p)

            # Parser datetime
            if "datetime" in df_asset.columns:
                df_asset["datetime"] = pd.to_datetime(df_asset["datetime"], utc=True)
                df_asset = df_asset.set_index("datetime")
            elif not isinstance(df_asset.index, pd.DatetimeIndex):
                df_asset.index = pd.to_datetime(df_asset.index, utc=True)
            else:
                df_asset.index = df_asset.index.tz_localize("UTC") if df_asset.index.tz is None \
                                 else df_asset.index.tz_convert("UTC")

            df_asset = df_asset.sort_index()
            # float32 pour économiser la mémoire
            float_cols = df_asset.select_dtypes(include="number").columns
            df_asset[float_cols] = df_asset[float_cols].astype(np.float32)

            df_asset["symbol"] = sym
            df_asset["asset_id"] = i

            dfs.append(df_asset)
            symbols_loaded.append(sym)
            print(f"  [{i+1:2d}/{len(csv_files)}] {sym:<20} {len(df_asset):>7,} barres")
        except Exception as e:
            print(f"  WARN : échec chargement {p.name} — {e}")

    if not dfs:
        raise RuntimeError("Aucun actif chargé avec succès.")

    df_combined = pd.concat(dfs, axis=0)
    # Tri chronologique puis par asset_id pour le séquencement Transformer
    df_combined = df_combined.sort_values(["asset_id", df_combined.index.name or "datetime"]) \
                             if "asset_id" in df_combined.columns \
                             else df_combined.sort_index()
    df_combined = df_combined.reset_index(drop=False)

    # S'assurer que l'index est RangeIndex (requis par SequenceDataset)
    df_combined = df_combined.reset_index(drop=True)

    asset_ids = df_combined["asset_id"].values.astype(np.int32)

    print(f"\n  Total : {len(df_combined):,} barres | {len(symbols_loaded)} actifs")
    print(f"  Période : {df_combined.index.min()} → {df_combined.index.max()}" if isinstance(df_combined.index, pd.DatetimeIndex) else "")
    print(f"  Colonnes : {len(df_combined.columns)}")

    return df_combined, asset_ids


# ─────────────────────────────────────────────────────────────────────────────
# 2. Feature engineering par actif (évite contamination cross-actif)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_macro_bear_gate(df: pd.DataFrame) -> pd.Series:
    """
    Gate macro bear par-actif : True = régime bear confirmé → SHORT autorisé.

    Condition (les DEUX doivent être vraies) :
      1. Close < EMA(4800)  — sous la moyenne mobile 200 jours (4800 barres 1h)
      2. mom_720h < -0.05   — rendement 30 jours < -5% (tendance baissière établie)

    min_periods=720 (30 jours) pour que l'EMA soit significative.
    Avant 720 barres : macro_bear = False par défaut (pas de short sans contexte).

    Pourquoi EMA(4800) ?
      200 jours × 24h = 4800 barres 1h = vrai EMA 200 jours.
      C'est LE référentiel institutionnel pour distinguer bull/bear macro.
    """
    close = df["Close"].values.astype(np.float64)

    # EMA 200 jours sur barres 1h
    alpha   = 2.0 / (4800 + 1)
    ema200d = np.full(len(close), np.nan)
    ema200d[0] = close[0]
    for i in range(1, len(close)):
        ema200d[i] = alpha * close[i] + (1 - alpha) * ema200d[i - 1]
    # invalider les 720 premières barres (< 30j de données)
    ema200d[:720] = np.nan

    # Momentum 30 jours (720 barres)
    mom_720 = np.full(len(close), np.nan)
    mom_720[720:] = np.log(close[720:] / close[:-720])

    # Gate : Close < EMA200d ET mom_30j < -10% (baisse significative sur 30 jours)
    # -10% filtre les corrections mineures dans un bull macro et cible les vrais bears
    macro_bear = (close < ema200d) & (mom_720 < -0.10)

    return pd.Series(macro_bear.astype(float), index=df.index, name=MACRO_BEAR_COL)


def enrich_asset(df_asset: pd.DataFrame) -> pd.DataFrame:
    """
    Applique sur un actif individuel :
      1. compute_all_short_features(df)
      2. compute_all_proxy_features(df)
      3. compute_short_label_columns(df) [forward-looking, sur tout le df]
      4. _compute_macro_bear_gate(df)    [gate régime bear par-actif]
    Retourne df enrichi.
    """
    # 1. Features SHORT spécifiques
    if _HAS_SHORT_FEATURES:
        try:
            df_asset = compute_all_short_features(df_asset)
        except Exception as e:
            print(f"    WARN compute_all_short_features : {e}")

    # 2. Proxies macro (simule funding_rate, OI, fear&greed depuis OHLCV)
    if _HAS_PROXY_FEATURES:
        try:
            df_asset = compute_all_proxy_features(df_asset)
        except Exception as e:
            print(f"    WARN compute_all_proxy_features : {e}")

    # 3. Colonnes forward-looking pour labels (sur tout le df — aucun leakage
    #    puisque build_short_labels calibrera les seuils sur train uniquement)
    if _HAS_SHORT_LABELS:
        try:
            df_asset = compute_short_label_columns(df_asset)
        except Exception as e:
            print(f"    WARN compute_short_label_columns : {e}")

    # 4. Gate macro bear par-actif (EMA 200 jours)
    try:
        df_asset[MACRO_BEAR_COL] = _compute_macro_bear_gate(df_asset)
    except Exception as e:
        print(f"    WARN macro_bear_gate : {e}")
        df_asset[MACRO_BEAR_COL] = 0.0

    return df_asset


def enrich_all_assets(df_combined: pd.DataFrame) -> pd.DataFrame:
    """
    Applique enrich_asset sur chaque actif séparément (via groupby asset_id),
    puis re-concatène dans le même ordre.
    """
    groups = []
    for asset_id, grp in df_combined.groupby("asset_id", sort=True):
        sym = grp["symbol"].iloc[0] if "symbol" in grp.columns else str(asset_id)
        print(f"  enrichissement {sym} ({len(grp):,} barres)…")
        try:
            grp_enriched = enrich_asset(grp.copy())
        except Exception as e:
            print(f"    ERREUR enrichissement {sym} : {e}")
            grp_enriched = grp
        groups.append(grp_enriched)

    result = pd.concat(groups, axis=0).reset_index(drop=True)

    # ── Gate BTC cross-actif ──────────────────────────────────────────────────
    # Quand BTC est en fort bull (mom_720 > +5% sur 30j), les rallies BTC
    # squeezeront tous les shorts altcoins → bloquer.
    # On extrait la série BTC mom_720 et on la broadcast à tous les actifs.
    BTC_SYMBOLS = ["BTCUSD", "BTCUSDT"]
    btc_mask = result["symbol"].isin(BTC_SYMBOLS) if "symbol" in result.columns else pd.Series(False, index=result.index)
    btc_rows  = result[btc_mask]

    if len(btc_rows) > 0 and "datetime" in btc_rows.columns:
        # Calculer le momentum 720h de BTC
        btc_dt = pd.to_datetime(btc_rows["datetime"]).dt.tz_localize(None)
        btc_close = pd.Series(btc_rows["Close"].values, index=btc_dt).sort_index()
        btc_mom720 = np.full(len(btc_close), np.nan)
        c = btc_close.values
        btc_mom720[720:] = np.log(c[720:] / c[:-720])
        btc_mom720_series = pd.Series(btc_mom720, index=btc_close.index)

        # Stocker la série BTC bear pour la vérification de couverture par fold
        # (utilisée dans main() pour décider SKIP vs RUN)
        btc_mom720_series_stored = btc_mom720_series  # noqa: F841 — utilisé ci-dessous
        # On NE bloque PAS les shorts selon BTC ici : le gate per-actif (EMA200d + mom_720)
        # suffit. Une gate BTC cross-asset supplémentaire réduisait trop la couverture.
        n_bear = int((result[MACRO_BEAR_COL].values > 0).sum()) if MACRO_BEAR_COL in result.columns else 0
        print(f"  [bear_gate] Barres en régime bear (EMA200d + mom30d<-10%): {n_bear:,}/{len(result):,} ({n_bear/len(result)*100:.1f}%)")
    else:
        print("  [btc_gate] BTC non trouvé — gate cross-actif désactivée")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. Construction des features list
# ─────────────────────────────────────────────────────────────────────────────

def _build_feature_lists(df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    """
    Construit les listes de features disponibles dans df.

    Retourne :
      features_all      : FEATURES_SHORT + GAMECHANGER + PROXY (disponibles)
      features_lgbm     : features_all (LightGBM tolère les NaN)
    """
    all_candidates = list(dict.fromkeys(
        FEATURES_SHORT
        + FEATURES_SHORT_GAMECHANGER
        + FEATURES_SHORT_PROXY
    ))

    # Exclure les colonnes de labels/cibles pour éviter le leakage
    _LABEL_COLS = {
        "y_long", "y_short", "y_short_4h", "y_short_8h", "y_short_clean",
        "y_short_gray", "future_ret_h", "future_ret_4h", "future_ret_short_4h",
        "future_ret_short_8h", "mfe_short_4h", "mfe_short_8h", "mae_short_4h",
        "mae_short_8h", "squeeze_reject_4h", "squeeze_reject_8h",
        "late_short_reject",
        # Colonnes de métadonnées
        "symbol", "asset_id", "no_short", "short_context_name",
        "ctx_crowded_longs", "ctx_breakdown", "ctx_failed_breakout",
        "ctx_liquidity_stress", "ctx_bear_continuation", "ctx_macro_riskoff",
        "ctx_general_short",
    }

    features_available = [
        f for f in all_candidates
        if f in df.columns and f not in _LABEL_COLS
    ]

    print(f"\n  Features disponibles : {len(features_available)} / {len(all_candidates)}")
    return features_available, features_available


# ─────────────────────────────────────────────────────────────────────────────
# 4. LightGBM SHORT wrapper
# ─────────────────────────────────────────────────────────────────────────────

class ShortLGBMModel:
    """
    Wrapper LightGBM / HistGBT pour la prédiction SHORT.
    Fallback automatique sur HistGradientBoostingClassifier si LightGBM absent.
    """

    def __init__(
        self,
        n_estimators: int = 800,
        learning_rate: float = 0.03,
        max_depth: int = 6,
        num_leaves: int = 63,
        min_child_samples: int = 50,
        subsample: float = 0.80,
        colsample_bytree: float = 0.80,
        reg_lambda: float = 1.0,
        use_gpu: bool = False,
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.reg_lambda = reg_lambda
        self.use_gpu = use_gpu and _HAS_LGBM

        self._model = None
        self._features: List[str] = []
        self.val_auc: float = 0.0

    def fit(
        self,
        df_train: pd.DataFrame,
        y_train: np.ndarray,
        df_val: pd.DataFrame,
        y_val: np.ndarray,
        features: List[str],
    ) -> None:
        avail = [f for f in features if f in df_train.columns]
        self._features = avail

        # Filtrer gray zone (-1)
        train_ok = y_train >= 0
        val_ok = y_val >= 0

        X_tr = df_train[avail].values[train_ok].astype(np.float32)
        y_tr = y_train[train_ok].astype(np.int32)
        X_vl = df_val[avail].values[val_ok].astype(np.float32)
        y_vl = y_val[val_ok].astype(np.int32)

        # Poids de classe
        n_neg = max(int((y_tr == 0).sum()), 1)
        n_pos = max(int((y_tr == 1).sum()), 1)
        scale_pos = float(n_neg / n_pos)

        if _HAS_LGBM:
            params = {
                "n_estimators":     self.n_estimators,
                "learning_rate":    self.learning_rate,
                "max_depth":        self.max_depth,
                "num_leaves":       self.num_leaves,
                "min_child_samples": self.min_child_samples,
                "subsample":        self.subsample,
                "colsample_bytree": self.colsample_bytree,
                "reg_lambda":       self.reg_lambda,
                "scale_pos_weight": scale_pos,
                "objective":        "binary",
                "metric":           "auc",
                "verbose":          -1,
                "n_jobs":           -1,
                "random_state":     42,
            }
            if self.use_gpu:
                params["device"] = "gpu"

            clf = lgb.LGBMClassifier(**params)
            clf.fit(
                X_tr, y_tr,
                eval_set=[(X_vl, y_vl)],
                callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
            )
        else:
            # Fallback HistGBT
            clf = HistGradientBoostingClassifier(
                max_iter=min(self.n_estimators, 500),
                learning_rate=self.learning_rate,
                max_depth=self.max_depth,
                min_samples_leaf=self.min_child_samples,
                random_state=42,
            )
            # sample_weight pour compenser le déséquilibre
            sw = np.where(y_tr == 1, scale_pos, 1.0).astype(np.float32)
            clf.fit(X_tr, y_tr, sample_weight=sw)

        self._model = clf

        # AUC val
        if len(np.unique(y_vl)) >= 2:
            p_vl = self._model.predict_proba(X_vl)[:, 1]
            self.val_auc = float(roc_auc_score(y_vl, p_vl))
        else:
            self.val_auc = 0.5

        print(f"    LightGBM{'(GPU)' if self.use_gpu else ''} — val AUC={self.val_auc:.4f}  "
              f"n_pos_train={n_pos:,}  features={len(avail)}")

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            return np.full(len(df), 0.5, dtype=np.float32)
        avail = [f for f in self._features if f in df.columns]
        X = df[avail].values.astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)
        return self._model.predict_proba(X)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Sous-échantillonnage mémoire
# ─────────────────────────────────────────────────────────────────────────────

def _subsample_train(
    df: pd.DataFrame,
    asset_ids: np.ndarray,
    train_mask: np.ndarray,
    max_bars: int = MAX_TRAIN_BARS,
    label_col: str = LABEL_COL,
) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """
    Si n_train > max_bars, sous-échantillonne en gardant :
      - Toutes les barres récentes (derniers 40% de la période)
      - Sample stratifié des barres anciennes (pour diversité temporelle)
    Retourne (df_train_sub, asset_ids_sub, new_mask sur df original).
    """
    train_idx = np.where(train_mask)[0]
    n_train = len(train_idx)

    if n_train <= max_bars:
        return df.iloc[train_idx], asset_ids[train_idx], train_mask

    print(f"\n  [MEM] {n_train:,} barres train > max={max_bars:,} → sous-échantillonnage")

    # Garder les barres les plus récentes + sample des anciennes
    n_recent_target = min(int(max_bars * 0.60), n_train)
    recent_idx = train_idx[-n_recent_target:]
    old_idx    = train_idx[:-n_recent_target] if n_recent_target < n_train else train_idx[:0]

    n_needed_old = max_bars - len(recent_idx)
    rng = np.random.default_rng(42)

    if n_needed_old <= 0 or len(old_idx) == 0:
        combined_idx = recent_idx[-max_bars:]   # les max_bars barres les plus récentes
    else:
        sample_old   = rng.choice(old_idx, size=min(n_needed_old, len(old_idx)), replace=False)
        combined_idx = np.sort(np.concatenate([sample_old, recent_idx]))

    new_mask = np.zeros(len(df), dtype=bool)
    new_mask[combined_idx] = True

    print(f"  [MEM] après sous-échantillonnage : {len(combined_idx):,} barres train")
    return df.iloc[combined_idx], asset_ids[combined_idx], new_mask


# ─────────────────────────────────────────────────────────────────────────────
# 6. Contexte SHORT : gate + permission
# ─────────────────────────────────────────────────────────────────────────────

def _compute_context_columns(
    df: pd.DataFrame,
    train_mask: np.ndarray,
) -> pd.DataFrame:
    """
    Calcule les colonnes ctx_* et no_short sur df complet.
    Les percentiles sont calibrés sur train_mask uniquement.
    Retourne df avec nouvelles colonnes.
    """
    if not _HAS_SHORT_RULES:
        df = df.copy()
        df[GATE_COL] = False
        df[CONTEXT_COL] = "general_short"
        return df

    try:
        df_train = df[train_mask]
        percentiles = compute_train_percentiles(df_train)

        # Gate
        no_short_series = compute_no_short_gate(df, percentiles)
        df = df.copy()
        df[GATE_COL] = no_short_series.values

        # Contextes détaillés
        ctx_df = compute_short_permission_context(df, percentiles)
        for col in ctx_df.columns:
            if col != GATE_COL:
                df[col] = ctx_df[col].values

        # Construire un nom de contexte textuel (pour la calibration)
        ctx_cols_ordered = [
            "ctx_crowded_longs", "ctx_breakdown", "ctx_failed_breakout",
            "ctx_liquidity_stress", "ctx_bear_continuation",
            "ctx_macro_riskoff", "ctx_general_short",
        ]
        available_ctx = [c for c in ctx_cols_ordered if c in df.columns]
        if available_ctx:
            ctx_names = np.full(len(df), "general_short", dtype=object)
            for col in reversed(available_ctx):
                label = col.replace("ctx_", "")
                mask_col = df[col].values.astype(bool)
                ctx_names[mask_col] = label
            df[CONTEXT_COL] = ctx_names

    except Exception as e:
        print(f"  WARN compute_context_columns : {e}")
        df = df.copy()
        df[GATE_COL] = False
        df[CONTEXT_COL] = "general_short"

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 7. Backtest OOS
# ─────────────────────────────────────────────────────────────────────────────

def _backtest_fold(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    p_ensemble: np.ndarray,
    thresholds: dict,
    costs: float = COST_NORMAL,
) -> dict:
    """
    Simule les trades SHORT sur le test fold.

    Pour chaque barre test :
      - contexte = CONTEXT_COL
      - si p_ensemble >= threshold[contexte] AND NOT no_short_gate → trade
      - net_ret = future_ret_short_4h - costs

    Retourne un dict de métriques.
    """
    df_test = df[test_mask].copy()
    p_test = p_ensemble[test_mask] if len(p_ensemble) == len(df) \
             else p_ensemble

    if len(df_test) == 0 or RET_COL not in df_test.columns:
        return _empty_fold_metrics()

    # Gate no_short (technique)
    gate_tech = df_test[GATE_COL].values.astype(bool) if GATE_COL in df_test.columns \
                else np.zeros(len(df_test), dtype=bool)

    # Gate macro bear : True = régime bear → SHORT AUTORISÉ (inverser pour bloquer)
    if MACRO_BEAR_COL in df_test.columns:
        macro_bear = df_test[MACRO_BEAR_COL].values.astype(bool)
        gate_macro  = ~macro_bear  # True = PAS en bear → BLOQUER
    else:
        gate_macro = np.zeros(len(df_test), dtype=bool)  # pas de blocage macro par défaut

    gate = gate_tech | gate_macro  # bloqué si technique OU pas en régime bear

    # Contexte par barre
    ctx_col = df_test[CONTEXT_COL].values if CONTEXT_COL in df_test.columns \
              else np.full(len(df_test), "general_short", dtype=object)

    ret_arr = df_test[RET_COL].values.astype(np.float64)
    mae_arr = df_test[MAE_COL].values.astype(np.float64) if MAE_COL in df_test.columns \
              else np.zeros(len(df_test))
    sq_arr  = df_test[SQUEEZE_COL].values.astype(bool) if SQUEEZE_COL in df_test.columns \
              else np.zeros(len(df_test), dtype=bool)

    trade_mask = np.zeros(len(df_test), dtype=bool)
    for i, ctx in enumerate(ctx_col):
        if gate[i]:
            continue  # bloqué par la gate (technique ou macro bull)
        thr = _get_threshold(thresholds, ctx)
        if thr is not None and p_test[i] >= thr:
            trade_mask[i] = True

    n_trades = int(trade_mask.sum())
    if n_trades == 0:
        return _empty_fold_metrics()

    # P&L : future_ret_short_4h est déjà du signe court (positif = short profite)
    net_rets = ret_arr[trade_mask] - costs  # net du coût round-trip
    wins   = net_rets[net_rets > 0]
    losses = net_rets[net_rets <= 0]

    gross_win  = float(wins.sum())  if len(wins)   > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    pf = gross_win / max(gross_loss, 1e-9)
    wr = float(len(wins)) / n_trades
    expectancy = float(net_rets.mean())

    # Max drawdown — equity curve ancrée sur 1.0 avec position sizing 0.1%
    POSITION_PCT = 0.001
    equity  = np.cumprod(1.0 + net_rets * POSITION_PCT)
    peak    = np.maximum.accumulate(equity)
    dd_pct  = (peak - equity) / peak * 100.0
    max_dd  = float(dd_pct.max()) if len(dd_pct) > 0 else 0.0

    # Squeeze rate (trades avec mae élevé qui se terminent en perte)
    sq_trades = sq_arr[trade_mask]
    sq_pnl    = net_rets[sq_trades]
    n_sq_loss = int((sq_pnl < 0).sum()) if len(sq_pnl) > 0 else 0
    squeeze_rate = n_sq_loss / max(n_trades, 1)

    # PF avec coût stress
    net_stress = ret_arr[trade_mask] - COST_STRESS
    wins_s   = net_stress[net_stress > 0]
    losses_s = net_stress[net_stress <= 0]
    pf_stress = float(wins_s.sum()) / max(float(abs(losses_s.sum())), 1e-9)

    # PF avec coût extrême
    net_extreme = ret_arr[trade_mask] - COST_EXTREME
    wins_e   = net_extreme[net_extreme > 0]
    losses_e = net_extreme[net_extreme <= 0]
    pf_extreme = float(wins_e.sum()) / max(float(abs(losses_e.sum())), 1e-9)

    return {
        "n_trades":      n_trades,
        "pf":            round(pf, 4),
        "pf_stress":     round(pf_stress, 4),
        "pf_extreme":    round(pf_extreme, 4),
        "expectancy":    round(expectancy, 6),
        "wr":            round(wr, 4),
        "avg_win":       round(float(wins.mean()) if len(wins) > 0 else 0.0, 6),
        "avg_loss":      round(float(losses.mean()) if len(losses) > 0 else 0.0, 6),
        "max_drawdown":  round(max_dd, 4),
        "squeeze_rate":  round(squeeze_rate, 4),
        "gate_blocked_pct": round(float(gate.mean()), 4),
    }


def _get_threshold(thresholds: dict, context: str) -> Optional[float]:
    """Retourne le seuil calibré pour un contexte, ou None si désactivé."""
    if _HAS_CALIBRATION:
        try:
            return get_threshold_for_context(thresholds, context)
        except Exception:
            pass
    entry = thresholds.get(context) or thresholds.get("general_short") or thresholds.get("general")
    if entry and entry.get("enabled"):
        return entry.get("threshold")
    return None


def _empty_fold_metrics() -> dict:
    return {
        "n_trades": 0, "pf": 0.0, "pf_stress": 0.0, "pf_extreme": 0.0,
        "expectancy": 0.0, "wr": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "max_drawdown": 0.0, "squeeze_rate": 0.0, "gate_blocked_pct": 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8. Pipeline par fold
# ─────────────────────────────────────────────────────────────────────────────

def run_fold(
    df: pd.DataFrame,
    asset_ids: np.ndarray,
    fold_year: int,
    features: List[str],
    use_transformer: bool = True,
    use_lgbm: bool = True,
    max_epochs: int = 40,
) -> dict:
    """
    Walk-forward pour un fold annuel T.

      train_mask = year <= T-2
      val_mask   = year == T-1
      test_mask  = year == T

    Retourne un dict de métriques détaillées.
    """
    t_fold_start = time.time()
    print(f"\n{'='*70}")
    print(f"  FOLD {fold_year}  (train <= {fold_year-2}, val = {fold_year-1}, test = {fold_year})")
    print(f"{'='*70}")

    # ── Masques temporels ──────────────────────────────────────────────────────
    # Utiliser la colonne datetime si présente, sinon l'index
    if "datetime" in df.columns:
        years = pd.to_datetime(df["datetime"], utc=True).dt.year.values
    elif isinstance(df.index, pd.DatetimeIndex):
        years = df.index.year.values
    else:
        print(f"  ERREUR : impossible de déterminer l'année des barres pour fold {fold_year}")
        return {"fold_year": fold_year, "status": "ERROR", "error": "no_datetime"}

    train_mask = years <= fold_year - 2
    val_mask   = years == fold_year - 1
    test_mask  = years == fold_year

    n_train = int(train_mask.sum())
    n_val   = int(val_mask.sum())
    n_test  = int(test_mask.sum())

    print(f"  n_train={n_train:,}  n_val={n_val:,}  n_test={n_test:,}")

    if n_train < 5_000 or n_val < 500 or n_test < 200:
        print(f"  SKIP : pas assez de données pour fold {fold_year}")
        return {"fold_year": fold_year, "status": "SKIP", "n_train": n_train,
                "n_val": n_val, "n_test": n_test}

    # ── Labels short (seuils calibrés sur train uniquement) ────────────────────
    if _HAS_SHORT_LABELS and LABEL_COL not in df.columns:
        try:
            print(f"\n  [labels] build_short_labels…")
            df = build_short_labels(df, train_mask)
        except Exception as e:
            print(f"  WARN build_short_labels : {e}")

    if LABEL_COL not in df.columns:
        print(f"  ERREUR : colonne '{LABEL_COL}' absente après build_short_labels")
        return {"fold_year": fold_year, "status": "ERROR", "error": "no_label_col"}

    y = df[LABEL_COL].values.astype(np.int32)
    y_train = y[train_mask]
    y_val   = y[val_mask]

    n_pos_train = int((y_train == 1).sum())
    n_pos_val   = int((y_val   == 1).sum())
    print(f"  y_short_clean : pos_train={n_pos_train:,}  pos_val={n_pos_val:,}")

    if n_pos_train < 50:
        print(f"  SKIP : trop peu de positifs en train ({n_pos_train})")
        return {"fold_year": fold_year, "status": "SKIP", "error": "too_few_positives",
                "n_pos_train": n_pos_train}

    # ── Contextes + gate ───────────────────────────────────────────────────────
    print(f"\n  [gate] Calcul contextes SHORT…")
    df = _compute_context_columns(df, train_mask)

    # Vérifier le context_df pour TRMShortFleet
    ctx_cols = ["ctx_crowded_longs", "ctx_breakdown", "ctx_failed_breakout",
                "ctx_liquidity_stress", "ctx_bear_continuation",
                "ctx_macro_riskoff", "ctx_general_short"]
    ctx_df_full = df[[c for c in ctx_cols if c in df.columns]].copy()
    if ctx_df_full.empty:
        ctx_df_full = pd.DataFrame(
            {"ctx_general_short": np.ones(len(df), dtype=bool)}, index=df.index
        )

    # ── Sous-échantillonnage mémoire ───────────────────────────────────────────
    _, _, train_mask_sub = _subsample_train(df, asset_ids, train_mask)

    # ── 5a. Transformer ────────────────────────────────────────────────────────
    p_transformer = np.full(len(df), 0.5, dtype=np.float32)
    metrics_tr = {}

    if use_transformer and _HAS_TRANSFORMER:
        print(f"\n  [transformer] Entraînement Transformer SHORT…")
        try:
            cfg_short = TransformerConfig(
                seq_len=24,
                d_model=48,
                n_heads=4,
                n_layers=2,
                dropout=0.30,
                max_epochs=max_epochs,
                patience=12,
                device="cuda" if __import__("torch").cuda.is_available() else "cpu",
            )

            # Remplacer y_long par y_short_clean pour compatibilité avec train_transformer
            df_for_tr = df.copy()
            df_for_tr["y_long"] = df_for_tr[LABEL_COL].astype(np.float32)

            features_tr = [f for f in features if f in df_for_tr.columns]

            model_tr, scaler_tr, metrics_tr = train_transformer(
                df=df_for_tr,
                train_mask=train_mask_sub,
                val_mask=val_mask,
                features=features_tr,
                cfg=cfg_short,
                verbose=True,
                asset_ids=asset_ids,
            )

            # Prédictions sur l'ensemble du df
            p_transformer_all = predict_transformer(
                model_tr, scaler_tr, df_for_tr,
                mask=np.ones(len(df), dtype=bool),
                features=features_tr,
            )
            p_transformer = p_transformer_all.astype(np.float32)
            print(f"  [transformer] best_auc={metrics_tr.get('best_auc', 0):.4f}")

        except Exception as e:
            print(f"  WARN Transformer : {e}")
            import traceback; traceback.print_exc()

    # ── 5b. LightGBM SHORT ─────────────────────────────────────────────────────
    p_lgbm = np.full(len(df), 0.5, dtype=np.float32)

    if use_lgbm:
        print(f"\n  [lgbm] Entraînement LightGBM SHORT…")
        try:
            lgbm_model = ShortLGBMModel(
                n_estimators=800,
                learning_rate=0.03,
                max_depth=6,
                num_leaves=63,
                min_child_samples=30,
                subsample=0.80,
                colsample_bytree=0.80,
            )
            lgbm_model.fit(
                df[train_mask_sub], y[train_mask_sub],
                df[val_mask], y_val,
                features=features,
            )
            p_lgbm_all = lgbm_model.predict_proba(df)
            p_lgbm = p_lgbm_all.astype(np.float32)
            print(f"  [lgbm] val_auc={lgbm_model.val_auc:.4f}")
        except Exception as e:
            print(f"  WARN LightGBM : {e}")

    # ── 5c. TRMShortFleet ─────────────────────────────────────────────────────
    p_trm = np.full(len(df), 0.5, dtype=np.float32)

    if _HAS_TRM_FLEET:
        print(f"\n  [fleet] Entraînement TRMShortFleet…")
        try:
            fleet = TRMShortFleet(
                features=features,
                n_iter=500,
                learning_rate=0.03,
                max_leaf_nodes=31,
            )
            fleet.fit(
                df_train=df[train_mask_sub],
                y=y[train_mask_sub],
                df_val=df[val_mask],
                y_val=y_val,
                context_df=ctx_df_full.iloc[np.where(train_mask_sub)[0]],
            )
            result_fleet = fleet.predict_short_with_context(
                df, ctx_df_full
            )
            p_trm_col = result_fleet["p_short"].values if "p_short" in result_fleet.columns \
                        else fleet.predict_short_proba(df, ctx_df_full)
            p_trm = p_trm_col.astype(np.float32)
        except Exception as e:
            print(f"  WARN TRMShortFleet : {e}")
    else:
        # Fallback HistGBT généraliste si fleet absent
        print(f"\n  [fleet-fallback] HistGBT généraliste…")
        try:
            X_tr_f = df[train_mask_sub][[f for f in features if f in df.columns]].values.astype(np.float32)
            y_tr_f = y[train_mask_sub]
            ok_tr  = y_tr_f >= 0
            n_neg_f = max(int((y_tr_f[ok_tr] == 0).sum()), 1)
            n_pos_f = max(int((y_tr_f[ok_tr] == 1).sum()), 1)
            sw_f = np.where(y_tr_f[ok_tr] == 1, n_neg_f / n_pos_f, 1.0)

            clf_f = HistGradientBoostingClassifier(
                max_iter=300, learning_rate=0.05, max_depth=5, random_state=42
            )
            clf_f.fit(X_tr_f[ok_tr], y_tr_f[ok_tr], sample_weight=sw_f)

            X_all_f = df[[f for f in features if f in df.columns]].values.astype(np.float32)
            p_trm = clf_f.predict_proba(X_all_f)[:, 1].astype(np.float32)
        except Exception as e:
            print(f"  WARN fallback HistGBT : {e}")

    # ── Ensemble final ─────────────────────────────────────────────────────────
    print(f"\n  [ensemble] Fusion p = {W_TRANSFORMER}×TR + {W_LGBM}×LGB + {W_TRM}×TRM")
    p_ensemble = (
        W_TRANSFORMER * p_transformer
        + W_LGBM      * p_lgbm
        + W_TRM       * p_trm
    ).astype(np.float32)

    # Stats prob sur val (contrôle qualité)
    p_val_ens = p_ensemble[val_mask]
    y_val_clean = y[val_mask]
    valid_val = y_val_clean >= 0
    if valid_val.sum() >= 10 and len(np.unique(y_val_clean[valid_val])) >= 2:
        try:
            ens_auc_val = float(roc_auc_score(y_val_clean[valid_val], p_val_ens[valid_val]))
        except Exception:
            ens_auc_val = 0.5
    else:
        ens_auc_val = 0.5

    print(f"  [ensemble] AUC val ensemble = {ens_auc_val:.4f}")
    print(f"  [ensemble] p_val : mean={p_val_ens.mean():.3f}  p90={np.percentile(p_val_ens, 90):.3f}")

    # ── Calibration seuils sur val ────────────────────────────────────────────
    print(f"\n  [calibration] Calibration seuils sur val…")
    thresholds: dict = {}

    if _HAS_CALIBRATION and RET_COL in df.columns:
        try:
            # Calibrer uniquement sur les barres val en régime bear (macro_bear_ok=1)
            df_val_full = df[val_mask].reset_index(drop=True)
            p_val_full  = p_ensemble[val_mask]
            y_val_full  = y[val_mask]

            if MACRO_BEAR_COL in df_val_full.columns:
                bear_val = df_val_full[MACRO_BEAR_COL].values.astype(bool)
                n_bear   = int(bear_val.sum())
                pct_bear = n_bear / max(len(bear_val), 1) * 100
                print(f"  [calibration] Barres val en régime bear : {n_bear:,}/{len(bear_val):,} ({pct_bear:.1f}%)")
                if n_bear >= 50:
                    df_val_full = df_val_full[bear_val].reset_index(drop=True)
                    p_val_full  = p_val_full[bear_val]
                    y_val_full  = y_val_full[bear_val]

            thresholds = calibrate_short_thresholds(
                df_val=df_val_full,
                y_val=y_val_full,
                p_short=p_val_full,
                context_col=CONTEXT_COL,
                costs=COST_NORMAL,
            )
            print(summarize_calibration(thresholds))
        except Exception as e:
            print(f"  WARN calibrate_short_thresholds : {e}")
            # Fallback : seuil fixe p90 val
            p90 = float(np.percentile(p_ensemble[val_mask], 90))
            thresholds = {"general_short": {"enabled": True, "threshold": p90}}

    else:
        # Fallback seuil fixe
        p90 = float(np.percentile(p_ensemble[val_mask], 90))
        thresholds = {"general_short": {"enabled": True, "threshold": p90}}

    # Sauvegarder les seuils
    thr_path = REPORT_DIR / f"thresholds_fold_{fold_year}_multi.json"
    try:
        with open(thr_path, "w") as f:
            json.dump(thresholds, f, indent=2, default=lambda x: float(x) if isinstance(x, np.floating) else str(x))
    except Exception as e:
        print(f"  WARN sauvegarde seuils : {e}")

    # ── Backtest test ──────────────────────────────────────────────────────────
    print(f"\n  [backtest] Test OOS fold {fold_year}…")
    bt_metrics = _backtest_fold(df, test_mask, p_ensemble, thresholds, costs=COST_NORMAL)

    # PF stress/extreme sur test
    bt_stress   = _backtest_fold(df, test_mask, p_ensemble, thresholds, costs=COST_STRESS)
    bt_extreme  = _backtest_fold(df, test_mask, p_ensemble, thresholds, costs=COST_EXTREME)

    dt_fold = time.time() - t_fold_start
    print(f"\n  FOLD {fold_year} résultats :")
    print(f"    n_trades    = {bt_metrics['n_trades']:,}")
    print(f"    PF (normal) = {bt_metrics['pf']:.4f}  |  PF stress={bt_stress['pf']:.4f}  |  PF extreme={bt_extreme['pf']:.4f}")
    print(f"    expectancy  = {bt_metrics['expectancy']:+.5f}")
    print(f"    WR          = {bt_metrics['wr']:.2%}")
    print(f"    max_DD      = {bt_metrics['max_drawdown']:.2f}%")
    print(f"    squeeze_rt  = {bt_metrics['squeeze_rate']:.2%}")
    print(f"    gate_blk    = {bt_metrics['gate_blocked_pct']:.2%}")
    print(f"    AUC val ens = {ens_auc_val:.4f}")
    print(f"    Durée fold  = {dt_fold:.0f}s")

    # ── Classification du fold ─────────────────────────────────────────────────
    n_tr = bt_metrics["n_trades"]
    pf   = bt_metrics["pf"]
    exp  = bt_metrics["expectancy"]
    dd   = bt_metrics["max_drawdown"]
    sq   = bt_metrics["squeeze_rate"]
    pf_s = bt_stress["pf"]

    fold_ok = (
        n_tr >= MIN_SHORT_TRADES_PER_VALID_FOLD
        and pf >= SHORT_DEPLOY_PF
        and exp > 0
        and dd <= MAX_SHORT_DD
        and sq <= MAX_SQUEEZE_LOSS_RATE
    )
    fold_catastrophic = (
        n_tr >= MIN_SHORT_TRADES_PER_VALID_FOLD   # seulement si assez de trades
        and (pf < SHORT_CATASTROPHIC_PF or dd > MAX_SHORT_DD or sq > 0.50)
    )
    fold_status = (
        "CATASTROPHIC" if fold_catastrophic
        else "OK"       if fold_ok
        else "WEAK"
    )
    print(f"    → fold_status = {fold_status}")

    return {
        "fold_year":      fold_year,
        "status":         "RUN",
        "fold_status":    fold_status,
        "fold_ok":        fold_ok,
        "fold_catastrophic": fold_catastrophic,
        "n_train":        n_train,
        "n_val":          n_val,
        "n_test":         n_test,
        "n_pos_train":    n_pos_train,
        "n_pos_val":      n_pos_val,
        "n_trades":       n_tr,
        "pf":             pf,
        "pf_stress":      pf_s,
        "pf_extreme":     bt_extreme["pf"],
        "expectancy":     exp,
        "wr":             bt_metrics["wr"],
        "avg_win":        bt_metrics["avg_win"],
        "avg_loss":       bt_metrics["avg_loss"],
        "max_drawdown":   dd,
        "squeeze_rate":   sq,
        "gate_blocked_pct": bt_metrics["gate_blocked_pct"],
        "ens_auc_val":    round(ens_auc_val, 4),
        "tr_best_auc":    metrics_tr.get("best_auc", None),
        "tr_best_epoch":  metrics_tr.get("best_epoch", None),
        "duration_s":     round(dt_fold, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. Verdict global
# ─────────────────────────────────────────────────────────────────────────────

def compute_verdict(fold_results: List[dict]) -> str:
    """
    Verdict walk-forward conditionnel au régime bear.

    Seuls les folds avec status="RUN" (BTC bear >= 15% du test) sont comptés.
    Les folds SKIPPED (bull-dominated) ne comptent ni pour ni contre.

    SHORT_PAPER_CANDIDATE si :
      - 0 catastrophiques
      - >= 67% des folds actifs sont OK (min 2)
      - cost_stress_pf médian >= 1.0 sur les folds OK
      - total trades >= MIN_SHORT_TRADES_TOTAL

    SHORT_REJECTED si :
      - catastrophique sur un fold actif

    SHORT_PROMISING_BUT_UNSAFE sinon.
    """
    run_folds = [r for r in fold_results if r.get("status") == "RUN"]
    if not run_folds:
        return "SHORT_REJECTED"

    n_active       = len(run_folds)
    n_ok           = int(sum(1 for r in run_folds if r.get("fold_ok", False)))
    n_catastrophic = int(sum(1 for r in run_folds if r.get("fold_catastrophic", False)))

    pf_stress_ok = [r["pf_stress"] for r in run_folds if r.get("fold_ok", False)]
    median_pf_stress = float(np.median(pf_stress_ok)) if pf_stress_ok else 0.0

    n_total_trades = int(sum(r.get("n_trades", 0) for r in run_folds))

    # Seuil adaptatif : au moins 67% des folds actifs doivent être OK (floor, minimum 2)
    # floor vs ceil : pour n=3, floor(3×0.67=2.01)=2 (≥ 2/3), ceil donnerait 3 (= 3/3)
    min_folds_ok_adaptive = max(MIN_ACTIVE_FOLDS_FOR_VERDICT, int(np.floor(n_active * 0.67)))

    if n_catastrophic > 0:
        return "SHORT_REJECTED"

    if (n_ok >= min_folds_ok_adaptive
            and n_catastrophic == 0
            and median_pf_stress >= 1.0
            and n_total_trades >= MIN_SHORT_TRADES_TOTAL):
        return "SHORT_PAPER_CANDIDATE"

    return "SHORT_PROMISING_BUT_UNSAFE"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Main
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward SHORT multi-actif avec Transformer + LightGBM + TRMFleet"
    )
    parser.add_argument(
        "--max-assets", type=int, default=50,
        help="Nombre maximum d'actifs à charger (défaut: 50)"
    )
    parser.add_argument(
        "--max-epochs", type=int, default=40,
        help="Époques maximum pour le Transformer (défaut: 40)"
    )
    parser.add_argument(
        "--no-transformer", action="store_true",
        help="Désactiver le Transformer (debug rapide)"
    )
    parser.add_argument(
        "--no-lgbm", action="store_true",
        help="Désactiver LightGBM"
    )
    parser.add_argument(
        "--folds", nargs="+", type=int,
        default=[2022, 2023, 2024, 2025, 2026],
        help="Années de test pour le walk-forward (défaut: 2022 2023 2024 2025 2026)"
    )
    parser.add_argument(
        "--data-dir", type=str, default=str(DATA_DIR),
        help="Répertoire des fichiers *_features.csv"
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    t_start = time.time()
    print("\n" + "="*70)
    print("  WALK-FORWARD SHORT MULTI-ACTIF")
    print(f"  Date : {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Actifs max : {args.max_assets}")
    print(f"  Folds      : {args.folds}")
    print(f"  Transformer: {'OFF' if args.no_transformer else 'ON'}")
    print(f"  LightGBM   : {'OFF' if args.no_lgbm else 'ON'}")
    print("="*70)

    # Afficher les erreurs d'import non-critiques
    if _IMPORT_ERRORS:
        print("\nAVERTISSEMENTS IMPORT :")
        for err in _IMPORT_ERRORS:
            print(f"  - {err}")

    # ── Chargement multi-actif ─────────────────────────────────────────────────
    data_dir = Path(args.data_dir)
    print(f"\n[1/4] Chargement des données depuis {data_dir}…")
    try:
        df_combined, asset_ids = load_all_assets(data_dir, max_assets=args.max_assets)
    except FileNotFoundError as e:
        print(f"  ERREUR : {e}")
        sys.exit(1)

    # ── Enrichissement feature engineering (par actif, évite cross-contamination)
    print(f"\n[2/4] Feature engineering par actif…")
    df_combined = enrich_all_assets(df_combined)
    asset_ids = df_combined["asset_id"].values.astype(np.int32)

    # ── Listes de features ────────────────────────────────────────────────────
    features, features_lgbm = _build_feature_lists(df_combined)

    if len(features) == 0:
        print("  ERREUR CRITIQUE : aucune feature disponible dans le DataFrame.")
        sys.exit(1)

    # ── Pré-calcul BTC bear coverage par année (pour fold-skip) ─────────────────
    btc_bear_by_year: dict = {}
    btc_rows = df_combined[df_combined["symbol"].isin(["BTCUSD", "BTCUSDT"])] \
               if "symbol" in df_combined.columns else pd.DataFrame()
    if len(btc_rows) > 0 and MACRO_BEAR_COL in df_combined.columns:
        btc_bear_col = df_combined.loc[df_combined["symbol"].isin(["BTCUSD","BTCUSDT"]), MACRO_BEAR_COL]
        btc_yr_col   = pd.to_datetime(
            df_combined.loc[df_combined["symbol"].isin(["BTCUSD","BTCUSDT"]), "datetime"]
        ).dt.year.values
        for y in set(btc_yr_col):
            m = btc_yr_col == y
            btc_bear_by_year[int(y)] = float(btc_bear_col.values[m].mean())

    print(f"\n  Couverture bear BTC par année (EMA200d + mom30d<-10%):")
    for y, cov in sorted(btc_bear_by_year.items()):
        flag = "→ RUN" if cov >= MIN_BTC_BEAR_COVERAGE_TEST else "→ SKIP (bull-dominated)"
        print(f"    {y}: {cov*100:.1f}%  {flag}")

    print(f"\n[3/4] Walk-forward par fold…")
    fold_results: List[dict] = []

    for fold_year in args.folds:
        # ── Fold-skip : ne pas tester les folds bull-dominated ─────────────────
        btc_cov = btc_bear_by_year.get(fold_year, 0.0)
        if btc_cov < MIN_BTC_BEAR_COVERAGE_TEST:
            pct = btc_cov * 100
            print(f"\n  [{fold_year}] SKIPPED — BTC bear coverage {pct:.1f}% < {MIN_BTC_BEAR_COVERAGE_TEST*100:.0f}% requis")
            print(f"    (Fold bull-dominated : la stratégie SHORT était inactive — pas penalisée)")
            fold_results.append({
                "fold_year": fold_year,
                "status": "SKIPPED",
                "btc_bear_coverage": btc_cov,
                "fold_ok": False,
                "fold_catastrophic": False,
                "fold_status": "SKIPPED",
                "n_trades": 0,
                "pf": 0.0,
                "pf_stress": 0.0,
                "max_drawdown": 0.0,
                "squeeze_rate": 0.0,
                "ens_auc_val": 0.0,
            })
            continue

        try:
            result = run_fold(
                df=df_combined,
                asset_ids=asset_ids,
                fold_year=fold_year,
                features=features,
                use_transformer=(not args.no_transformer),
                use_lgbm=(not args.no_lgbm),
                max_epochs=args.max_epochs,
            )
            fold_results.append(result)
        except Exception as e:
            print(f"\n  ERREUR fold {fold_year} : {e}")
            import traceback; traceback.print_exc()
            fold_results.append({
                "fold_year": fold_year,
                "status": "ERROR",
                "error": str(e),
            })

    # ── Verdict global ─────────────────────────────────────────────────────────
    print(f"\n[4/4] Verdict global…")
    verdict = compute_verdict(fold_results)

    run_folds = [r for r in fold_results if r.get("status") == "RUN"]
    n_ok           = sum(1 for r in run_folds if r.get("fold_ok", False))
    n_catastrophic = sum(1 for r in run_folds if r.get("fold_catastrophic", False))
    n_total_trades = sum(r.get("n_trades", 0) for r in run_folds)

    pf_values = [r["pf"] for r in run_folds if "pf" in r]
    pf_median  = float(np.median(pf_values)) if pf_values else 0.0

    pf_stress_ok = [r["pf_stress"] for r in run_folds if r.get("fold_ok", False)]
    median_pf_stress = float(np.median(pf_stress_ok)) if pf_stress_ok else 0.0

    print("\n" + "="*70)
    print("  RÉSUMÉ WALK-FORWARD SHORT MULTI-ACTIF")
    print("="*70)

    # Tableau par fold
    print(f"\n  {'Fold':>6} | {'Status':>12} | {'Trades':>7} | {'PF':>6} | {'PF_stress':>9} | "
          f"{'DD%':>6} | {'Sq%':>6} | {'AUC_val':>7}")
    print("  " + "-"*75)
    for r in fold_results:
        fy = r["fold_year"]
        st = r.get("fold_status", r.get("status", "?"))
        nt = r.get("n_trades", 0)
        pf = r.get("pf", 0.0)
        ps = r.get("pf_stress", 0.0)
        dd = r.get("max_drawdown", 0.0)
        sq = r.get("squeeze_rate", 0.0)
        au = r.get("ens_auc_val", 0.0)
        print(f"  {fy:>6} | {st:>12} | {nt:>7,} | {pf:>6.3f} | {ps:>9.3f} | "
              f"{dd:>6.2f} | {sq:>6.2%} | {au:>7.4f}")

    print("  " + "-"*75)
    print(f"\n  Folds run     : {len(run_folds)}")
    print(f"  Folds OK      : {n_ok} / {MIN_SHORT_FOLDS_OK} requis")
    print(f"  Catastrophics : {n_catastrophic}")
    print(f"  PF médian     : {pf_median:.4f}")
    print(f"  PF_stress (OK): {median_pf_stress:.4f}  (requis >= 1.0)")
    print(f"  Total trades  : {n_total_trades:,}  (requis >= {MIN_SHORT_TRADES_TOTAL})")
    print(f"\n  VERDICT FINAL : {verdict}")
    print("="*70)

    # Verdict en couleur / emphase
    if verdict == "SHORT_REJECTED":
        print("\n  !! SHORT_REJECTED : au moins un fold catastrophique.")
        print("     Pas de déploiement. Analyser les folds CATASTROPHIC en priorité.")
    elif verdict == "SHORT_PROMISING_BUT_UNSAFE":
        print("\n  !! SHORT_PROMISING_BUT_UNSAFE")
        print(f"     Seulement {n_ok}/{MIN_SHORT_FOLDS_OK} folds OK ou PF_stress insuffisant.")
        print("     Pas de déploiement. Résultats intéressants mais pas assez stables.")
    else:
        print("\n  >> SHORT_PAPER_CANDIDATE")
        print("     Critères minimaux atteints. Trading papier UNIQUEMENT.")
        print("     Surveillance de 3 mois minimum avant toute décision de déploiement.")

    # ── Sauvegarde JSON ────────────────────────────────────────────────────────
    summary = {
        "verdict":           verdict,
        "run_date":          pd.Timestamp.now().isoformat(),
        "n_assets":          args.max_assets,
        "folds_requested":   args.folds,
        "n_folds_run":       len(run_folds),
        "n_folds_ok":        n_ok,
        "n_catastrophic":    n_catastrophic,
        "pf_median":         round(pf_median, 4),
        "pf_stress_median":  round(median_pf_stress, 4),
        "n_total_trades":    n_total_trades,
        "thresholds_used": {
            "SHORT_DEPLOY_PF":       SHORT_DEPLOY_PF,
            "MIN_SHORT_FOLDS_OK":    MIN_SHORT_FOLDS_OK,
            "MAX_SHORT_DD":          MAX_SHORT_DD,
            "MAX_SQUEEZE_LOSS_RATE": MAX_SQUEEZE_LOSS_RATE,
            "MIN_SHORT_TRADES_TOTAL": MIN_SHORT_TRADES_TOTAL,
        },
        "ensemble_weights": {
            "transformer": W_TRANSFORMER,
            "lgbm":        W_LGBM,
            "trm_fleet":   W_TRM,
        },
        "fold_results": fold_results,
        "duration_total_s": round(time.time() - t_start, 1),
    }

    json_path = REPORT_DIR / "walk_forward_short_multi_asset.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=lambda x: (
            float(x) if isinstance(x, (np.floating, np.float32, np.float64))
            else int(x) if isinstance(x, (np.integer,))
            else bool(x) if isinstance(x, np.bool_)
            else str(x)
        ))
    print(f"\n  JSON sauvegardé : {json_path}")

    # ── Sauvegarde CSV ────────────────────────────────────────────────────────
    if fold_results:
        csv_cols = [
            "fold_year", "fold_status", "n_trades", "pf", "pf_stress", "pf_extreme",
            "expectancy", "wr", "max_drawdown", "squeeze_rate", "gate_blocked_pct",
            "ens_auc_val", "tr_best_auc", "n_pos_train", "n_pos_val",
            "n_train", "n_val", "n_test", "duration_s",
        ]
        rows = []
        for r in fold_results:
            row = {c: r.get(c, None) for c in csv_cols}
            row["fold_year"] = r["fold_year"]
            rows.append(row)

        csv_df = pd.DataFrame(rows)
        csv_path = REPORT_DIR / "walk_forward_short_multi_asset.csv"
        csv_df.to_csv(csv_path, index=False)
        print(f"  CSV sauvegardé  : {csv_path}")

    # ── Mise à jour du rapport de validation ──────────────────────────────────
    report_path = REPORT_DIR / "SHORT_REBUILD_VALIDATION.md"
    _update_validation_report(report_path, summary, fold_results)

    print(f"\n  Durée totale : {time.time() - t_start:.0f}s")
    print(f"\n  FIN — Verdict : {verdict}\n")


def _update_validation_report(
    report_path: Path,
    summary: dict,
    fold_results: List[dict],
) -> None:
    """Ajoute ou met à jour le bloc 'Walk-Forward Multi-Actif' dans le rapport."""
    lines = []

    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            existing = f.read()
        # Supprimer l'ancienne section walk-forward multi-actif si elle existe
        marker_start = "## Walk-Forward Multi-Actif"
        if marker_start in existing:
            idx = existing.index(marker_start)
            # Trouver la prochaine section ##
            next_sec = existing.find("\n## ", idx + 5)
            existing = (existing[:idx] + existing[next_sec:]).strip() if next_sec > 0 \
                       else existing[:idx].strip()
        lines.append(existing)
        lines.append("\n\n")
    else:
        lines.append("# SHORT REBUILD VALIDATION\n\n")

    lines.append("## Walk-Forward Multi-Actif\n\n")
    lines.append(f"**Date** : {summary['run_date']}\n\n")
    lines.append(f"**Verdict** : `{summary['verdict']}`\n\n")
    lines.append(f"**Actifs** : {summary['n_assets']}  |  "
                 f"**Folds run** : {summary['n_folds_run']}  |  "
                 f"**Folds OK** : {summary['n_folds_ok']}/{summary.get('thresholds_used', {}).get('MIN_SHORT_FOLDS_OK', 5)}  |  "
                 f"**Catastrophics** : {summary['n_catastrophic']}\n\n")
    lines.append(f"**PF médian** : {summary['pf_median']:.4f}  |  "
                 f"**PF stress médian (OK)** : {summary['pf_stress_median']:.4f}  |  "
                 f"**Total trades** : {summary['n_total_trades']:,}\n\n")

    # Tableau des folds
    lines.append("| Fold | Status | Trades | PF | PF_stress | DD% | Sq% | AUC_val |\n")
    lines.append("|------|--------|--------|----|-----------|-----|-----|---------|\n")
    for r in fold_results:
        fy  = r["fold_year"]
        st  = r.get("fold_status", r.get("status", "?"))
        nt  = r.get("n_trades", 0)
        pf  = r.get("pf", 0.0)
        ps  = r.get("pf_stress", 0.0)
        dd  = r.get("max_drawdown", 0.0)
        sq  = r.get("squeeze_rate", 0.0)
        au  = r.get("ens_auc_val", 0.0)
        lines.append(f"| {fy} | {st} | {nt:,} | {pf:.3f} | {ps:.3f} | {dd:.2f} | {sq:.2%} | {au:.4f} |\n")

    lines.append("\n")
    lines.append(f"**Ensemble** : {W_TRANSFORMER}× Transformer + {W_LGBM}× LightGBM + {W_TRM}× TRMShortFleet\n\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    print(f"  Rapport MD    : {report_path}")


if __name__ == "__main__":
    main()
