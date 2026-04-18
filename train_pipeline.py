#!/usr/bin/env python3
"""
train_pipeline.py — Pipeline ML noyau  (horizon = 60 min, 1 barre 1h)
======================================================================

Architecture deux branches séparées LONG / SHORT :

    CSV enrichi (71 k barres 1h)
        │
        ├─ Labels séparés (net-of-cost)
        │      y_long     : 1 si ret > thr  (opportunité long exploitable)
        │      y_short    : 1 si ret < -thr (opportunité short exploitable)
        │      tradeable_net : |ret| > thr  (filtre global stage 1)
        │
        ├─ Stage 1 — Filtre tradeable (XGBoost, partagé)
        │      → P(tradeable)  seuils séparés long/short
        │
        ├─ Stage 2 LONG — Edge model long
        │      Entraîné sur TOUS les bars, label = y_long (1 = good long)
        │      Baseline A : LogisticRegression
        │      Baseline B : XGBoost
        │      Main       : TCN (si baseline battu)
        │
        ├─ Stage 2 SHORT — Edge model short  (activable/désactivable)
        │      Entraîné sur TOUS les bars, label = y_short (1 = good short)
        │      Mêmes architectures, paramètres indépendants
        │
        └─ Backtest walk-forward  (2024-2025, net de frais)
               → LONG seul
               → SHORT seul  (si activé)
               → COMBINÉ LONG+SHORT  (long prioritaire)
               → Comparaison tabulaire

Usage :
    # Pipeline long uniquement (recommandé pour préserver l'edge)
    python train_pipeline.py --data data/BTCUSD_1h_features.csv --mode long

    # Entraîner sur les données brutes 1m Binance Vision (rééchantillonnage auto → 1h)
    python train_pipeline.py \
        --data data/datasets/binance_vision_downloads/data/spot/monthly/klines/BTCUSDT/1m \
        --mode combined

    # Tout activer
    python train_pipeline.py --data data/BTCUSD_1h_features.csv --mode combined

    # Pipeline short seul
    python train_pipeline.py --data data/BTCUSD_1h_features.csv --mode short

    # Désactiver les shorts même en mode combined
    python train_pipeline.py --data data/... --mode combined --no-short

    # Seuils explicites
    python train_pipeline.py --data data/... --mode combined \\
        --filter-thr-long 0.40 --direction-thr-long 0.52 \\
        --filter-thr-short 0.45 --direction-thr-short 0.55

Sorties dans  runs/pipeline/<run_id>/ :
    labels.json              distributions des labels (long + short)
    filter/                  modèle tradeable (XGBoost partagé)
    edge_long/               modèle long (baselines + TCN optionnel)
    edge_short/              modèle short  (si mode != long)
    backtest_long/           backtest LONG seul
    backtest_short/          backtest SHORT seul
    backtest_combined/       backtest combiné
    pipeline_summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, precision_recall_fscore_support,
    confusion_matrix, roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=UserWarning)

FUTUR = Path(__file__).parent
sys.path.insert(0, str(FUTUR))
sys.path.insert(0, str(FUTUR / "ai" / "models"))

from level_7.RiskController import RiskController, RiskConfig, RiskState  # noqa: E402

from ai.level_2.long          import train_long_model
from ai.level_2.short         import train_short_model
from ai.level_2.short_calibrate import calibrate_direction_model as calibrate_short_model
from ai.level_3 import train_specialists, SpecialistPredictor, SpecialistConfig
from ai.level_1.rules import (
    diagnose_regime_distribution, compute_regime_stats_by_year,
    REGIME_NO_SHORT,
)
from ai.level_1.bear_regime   import train_bear_regime_model
from core.labels import compute_short_reversal_col, compute_regime_col
from core.feature_engineering import compute_short_features
from backtest.engine import run_wf_backtest_short
from backtest.metrics import ShortRobustnessReport, should_deploy_short
from ai.level_0.live_features import compute_live_features, compute_macro_features, MACRO_BUNDLE_COLS
from ai.level_0.feature_engineering import compute_long_features, compute_flow_features


# ═════════════════════════════════════════════════════════════════════════════
# NOTE DATASET — data/bundle_btc/features_merged.parquet
# ─────────────────────────────────────────────────────────────────────────────
# Source     : Binance Vision klines BTCUSDT 1m (mensuel + quotidien)
# Couverture : 2017-08-17 → 2026-04-16  |  4 548 799 barres 1m  |  123 colonnes
# Format     : parquet zstd float32, ~640 MB sur disque
#
# Ce script travaille à 1h : le bundle est rééchantillonné via _raw1m_df_to_1h.
# Mapping bundle → architecture 7 niveaux :
#   Level 0 (Filter / SNAPSHOT_FEATURES)  ← OHLCV 1h rééch. + compute_live_features
#   Level 1 (Regime déterministe + ML)    ← mêmes features 1h + EMA/RSI rules
#   Level 2 (Long / Short edge scorers)   ← FEATURES_LONG/SHORT (57 cols chacun)
#   Level 3 (Specialists)                 ← idem + contextes marché
#   Level 4-5 (Comparator / Gate)         ← stubs
#   Level 6 (Meta scaler)                 ← sortie probabilités calibrées
#   Level 7 (Risk Controller)             ← taille position, SL, TP
#
# Colonnes bundle NON encore exploitées (potentiel Level 3 / Specialists) :
#   funding_rate, oihist_sumOpenInterest, global_ls_longShortRatio,
#   top_acc/top_pos_longShortRatio, taker_ls_buySellRatio, fear_greed_value
#   + leurs z-scores et dérivées (_z_24/_z_72/_z_288/_chg_1/_diff_1)
#   Ces signaux macro/sentiment sont des features de haut niveau idéales pour
#   les Specialists (Level 3) qui contextualisent les marchés haussiers/baissiers.
#
# Split temporel recommandé (hardcodé dans PipelineConfig) :
#   train ≤ 2022  (~5 ans, 43k barres 1h)
#   val   = 2023  (~8.7k barres 1h)
#   test  ≥ 2024  (~19k barres 1h incluant bull 2024-2025 et bear 2026)
# ═════════════════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTES GLOBALES (valeurs par défaut — surchargées par PipelineConfig)
# ═════════════════════════════════════════════════════════════════════════════

HORIZON_BARS   = 1           # 1 barre 1h = 60 min
COST_PCT       = 0.001       # 10 bps round-trip
TRADEABLE_QUANTILE = 0.75    # ~25 % des barres retenues
INITIAL_EQUITY = 10_000.0

TRAIN_END_YEAR = 2022
VAL_YEAR       = 2023

SNAPSHOT_FEATURES: List[str] = [
    # ── Price structure ───────────────────────────────────────────────────────
    "rv_12", "rv_24", "rv_48", "rv_72", "rv_168",
    "rv_ratio_24_72", "rv_ratio_12_48",
    "atr_pct_14", "boll_width_20",
    "boll_pos_20", "close_in_bar", "intrabar_range_pct",
    "eff_ratio_12", "eff_ratio_24",
    "zscore_close_24",
    # ── Returns & trend ──────────────────────────────────────────────────────
    "mom_logret_6", "mom_logret_12", "mom_logret_24", "mom_logret_72",
    "rsi_14", "cci_20",
    "dist_ema_20", "dist_ema_50", "dist_ema_200",
    "ema_spread_20_50", "ema_spread_50_200",
    # ── Flow ──────────────────────────────────────────────────────────────────
    "taker_buy_ratio_base", "delta_taker_pressure",
    "vol_ratio_24", "trades_ratio_24",
    "trade_intensity",     # trades/volume — retail vs institutionnel
    "vol_imbalance",       # buy-sell volume imbalance normalisée
    # ── Temporel ──────────────────────────────────────────────────────────────
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]


# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION DU PIPELINE — paramètres séparés LONG / SHORT
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    """
    Configuration complète du pipeline avec paramètres asymétriques LONG/SHORT.

    Le SHORT est plus conservateur par défaut pour préserver l'edge long :
      - seuils de décision plus élevés
      - risk_per_trade plus faible
      - max_consecutive_losses plus restrictif
    """

    # ── Labeling ──────────────────────────────────────────────────────────────
    tradeable_quantile: float = 0.70   # ~30 % des barres
    cost_pct: float = 0.001

    # ── Seuils de décision LONG ───────────────────────────────────────────────
    filter_threshold_long: float = 0.40
    direction_threshold_long: float = 0.52

    # ── Seuils de décision SHORT (plus conservateurs par défaut) ──────────────
    filter_threshold_short: float = 0.45
    direction_threshold_short: float = 0.55

    # ── Risk management LONG ──────────────────────────────────────────────────
    initial_equity: float = 10_000.0
    risk_per_trade_long: float = 0.002
    max_consecutive_losses_long: int = 3
    cooldown_bars_long: int = 2
    daily_loss_limit_pct: float = 0.02

    # ── Risk management SHORT (plus conservateur) ─────────────────────────────
    risk_per_trade_short: float = 0.001     # moitié du long
    max_consecutive_losses_short: int = 2   # stop plus tôt
    cooldown_bars_short: int = 3            # cooldown plus long

    # ── Switches ─────────────────────────────────────────────────────────────
    enable_long: bool = True
    enable_short: bool = True


def make_risk_config(cfg: PipelineConfig, side: str) -> RiskConfig:
    """Construit un RiskConfig asymétrique selon la branche long ou short."""
    if side == "long":
        return RiskConfig(
            equity=cfg.initial_equity,
            risk_per_trade=cfg.risk_per_trade_long,
            rr=1.5,
            cooldown_bars=cfg.cooldown_bars_long,
            daily_loss_limit_pct=cfg.daily_loss_limit_pct,
            max_consecutive_losses=cfg.max_consecutive_losses_long,
            atr_key="atr_14",
            rv_key="rv_24",
            stop_atr_mult=2.0,
        )
    elif side == "short":
        return RiskConfig(
            equity=cfg.initial_equity,
            risk_per_trade=cfg.risk_per_trade_short,
            rr=1.5,
            cooldown_bars=cfg.cooldown_bars_short,
            daily_loss_limit_pct=cfg.daily_loss_limit_pct,
            max_consecutive_losses=cfg.max_consecutive_losses_short,
            atr_key="atr_14",
            rv_key="rv_24",
            stop_atr_mult=2.0,
        )
    else:
        raise ValueError(f"side doit être 'long' ou 'short', reçu : {side!r}")


# ═════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ═════════════════════════════════════════════════════════════════════════════

def json_dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def profit_factor(pnl_list: List[float]) -> float:
    wins   = sum(p for p in pnl_list if p > 0)
    losses = abs(sum(p for p in pnl_list if p < 0))
    return wins / losses if losses > 0 else float("inf")


def max_drawdown(equity_curve: List[float]) -> float:
    eq = np.array(equity_curve, dtype=np.float64)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1e-9)
    return float(np.max(dd)) if len(dd) > 1 else 0.0


def sharpe_ratio(pnl_list: List[float], periods_per_year: int = 8760) -> float:
    r = np.array(pnl_list, dtype=np.float64)
    if len(r) < 2 or np.std(r) < 1e-12:
        return 0.0
    return float(np.mean(r) / np.std(r) * np.sqrt(periods_per_year))


def _backtest_metrics(pnl_list: List[float], equity_curve: List[float],
                      trades: List[Dict], n_tested: int) -> Dict:
    """Calcule les métriques standard à partir des listes de PnL et trades."""
    n_tr    = len(trades)
    wins    = sum(1 for p in pnl_list if p > 0)
    wr      = wins / max(n_tr, 1)
    initial_eq = equity_curve[0] if equity_curve else INITIAL_EQUITY
    final_eq   = equity_curve[-1] if equity_curve else initial_eq
    total_ret  = (final_eq - initial_eq) / initial_eq if equity_curve else 0.0
    avg_win  = float(np.mean([p for p in pnl_list if p > 0])) if wins > 0 else 0.0
    avg_loss = float(np.mean([p for p in pnl_list if p < 0])) if (n_tr - wins) > 0 else 0.0
    expectancy = avg_win * wr + avg_loss * (1 - wr)

    by_year: Dict[int, Dict] = {}
    for t in trades:
        yr = t["year"]
        if yr not in by_year:
            by_year[yr] = {"pnl": [], "trades": 0}
        by_year[yr]["pnl"].append(t["pnl_abs"])
        by_year[yr]["trades"] += 1
    yearly = {
        yr: {
            "trades": v["trades"],
            "pnl_sum": round(sum(v["pnl"]), 2),
            "pf": round(profit_factor(v["pnl"]), 3),
            "win_rate": round(sum(1 for p in v["pnl"] if p > 0) / max(len(v["pnl"]), 1), 3),
        }
        for yr, v in sorted(by_year.items())
    }

    return {
        "n_tested": n_tested,
        "n_trades": n_tr,
        "profit_factor": round(profit_factor(pnl_list), 4),
        "max_drawdown": round(max_drawdown(equity_curve), 4),
        "sharpe_annualized": round(sharpe_ratio(pnl_list), 4),
        "win_rate": round(wr, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "expectancy_per_trade": round(expectancy, 4),
        "initial_equity": round(initial_eq, 2),
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "by_year": {str(k): v for k, v in yearly.items()},
    }


# ═════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DES DONNÉES
# ═════════════════════════════════════════════════════════════════════════════

# Colonnes Binance Vision klines 1m (format sans header)
_BINANCE_1M_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "number_of_trades",
    "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
]


def _is_raw_1m_file(path: Path) -> bool:
    """Retourne True si le CSV est au format brut Binance klines 1m."""
    try:
        peek = pd.read_csv(path, nrows=2, header=None)
        # Les fichiers bruts Binance n'ont pas de header et la première colonne
        # est un timestamp en millisecondes (13 chiffres, > 1e12)
        return pd.to_numeric(peek.iloc[0, 0], errors="coerce") > 1e12
    except Exception:
        return False


def _raw1m_df_to_1h(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Rééchantillonne un DataFrame 1m (DatetimeIndex UTC, colonnes lowercase OHLCV)
    vers 1h et calcule toutes les features pipeline.
    Utilisé par _load_raw_1m_klines (CSV) et load_csv (bundle parquet).
    """
    for col in ["open", "high", "low", "close", "volume",
                "quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw = raw.sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]

    print("   Rééchantillonnage 1m → 1h…")
    df_1h = pd.DataFrame({
        "open":   raw["open"].resample("1h").first(),
        "high":   raw["high"].resample("1h").max(),
        "low":    raw["low"].resample("1h").min(),
        "close":  raw["close"].resample("1h").last(),
        "volume": raw["volume"].resample("1h").sum(),
        "quote_asset_volume":           raw["quote_asset_volume"].resample("1h").sum(),
        "number_of_trades":             raw["number_of_trades"].resample("1h").sum(),
        "taker_buy_base_asset_volume":  raw["taker_buy_base_asset_volume"].resample("1h").sum(),
        "taker_buy_quote_asset_volume": raw["taker_buy_quote_asset_volume"].resample("1h").sum(),
    })
    df_1h = df_1h.dropna(subset=["open", "close"])
    print(f"   {len(df_1h):,} barres 1h ({df_1h.index[0].date()} → {df_1h.index[-1].date()})")

    print("   Calcul des SNAPSHOT_FEATURES (compute_live_features)…")
    df_1h = compute_live_features(df_1h)

    hl  = df_1h["high"] - df_1h["low"]
    hpc = (df_1h["high"] - df_1h["close"].shift(1)).abs()
    lpc = (df_1h["low"]  - df_1h["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df_1h["atr_14"] = tr.ewm(span=14, adjust=False).mean().ffill().fillna(0.0)

    print("   Calcul des features LONG asymétriques (compute_long_features)…")
    df_1h = compute_long_features(df_1h)

    print("   Calcul des features flow / liquidation proxies (compute_flow_features)…")
    df_1h = compute_flow_features(df_1h)

    log_close = np.log(df_1h["close"])
    df_1h["future_ret_h"] = log_close.shift(-1) - log_close

    df_1h = df_1h.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    df_1h.index.name = "datetime"
    return df_1h


def _load_raw_1m_klines(path: Path) -> pd.DataFrame:
    """
    Charge tous les CSV bruts 1m Binance Vision depuis un répertoire (ou un fichier),
    concatène, rééchantillonne à 1h, calcule toutes les features et retourne
    un DataFrame compatible avec le reste du pipeline (même format que fetch_btc_data.py).

    Colonnes Binance attendues (sans header) :
        open_time, open, high, low, close, volume, close_time,
        quote_asset_volume, number_of_trades,
        taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore
    """
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        if not files:
            raise RuntimeError(f"Aucun CSV 1m dans {path}")
        print(f"   {len(files)} fichier(s) 1m trouvé(s) dans {path}")
    else:
        files = [path]

    frames = []
    for f in files:
        try:
            chunk = pd.read_csv(f, header=None, names=_BINANCE_1M_COLS, low_memory=False)
            ts_sample = int(chunk["open_time"].iloc[0])
            ts_unit = "us" if len(str(abs(ts_sample))) >= 16 else "ms"
            chunk["open_time"] = pd.to_datetime(
                chunk["open_time"].astype("int64"), unit=ts_unit, utc=True
            )
            frames.append(chunk)
        except Exception as e:
            print(f"   ⚠  Impossible de lire {f.name} : {e}")
    if not frames:
        raise RuntimeError("Aucune donnée 1m chargée")

    raw = pd.concat(frames, ignore_index=True)
    print(f"   {len(raw):,} barres 1m brutes chargées")

    raw = raw.set_index("open_time").sort_index()
    return _raw1m_df_to_1h(raw)


def load_csv(path_arg: str) -> pd.DataFrame:
    p = Path(path_arg)

    # ── Bundle parquet (features_merged.parquet) ──────────────────────────────
    # Format natif : 4.5M barres 1m, 123 colonnes, 2017-08 → aujourd'hui.
    # On extrait uniquement les colonnes OHLCV+taker, puis on rééchantillonne
    # vers 1h via _raw1m_df_to_1h (identique au chemin CSV brut Binance).
    if p.suffix.lower() == ".parquet":
        print(f"   Bundle parquet détecté ({p.name}) → rééchantillonnage 1m→1h…")
        _OHLCV_COLS = [
            "datetime", "open", "high", "low", "close", "volume",
            "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume",
        ]
        # Charger OHLCV + macro cols disponibles dans le bundle
        import pyarrow.parquet as _pq
        _avail = set(_pq.read_schema(p).names)
        _macro_present = [c for c in MACRO_BUNDLE_COLS if c in _avail]
        raw_1m = pd.read_parquet(p, columns=_OHLCV_COLS + _macro_present)
        raw_1m["datetime"] = pd.to_datetime(raw_1m["datetime"], utc=True, format="ISO8601")
        raw_1m = raw_1m.set_index("datetime")

        # Rééchantillonner OHLCV → 1h
        df = _raw1m_df_to_1h(raw_1m[_OHLCV_COLS[1:]])  # exclure "datetime" (déjà index)

        # Rééchantillonner macro → 1h (last value de chaque heure, puis ffill)
        if _macro_present:
            macro_1h = raw_1m[_macro_present].resample("1h").last().ffill().fillna(0.0)
            df = df.join(macro_1h, how="left")
            df[_macro_present] = df[_macro_present].ffill().fillna(0.0)

        df = compute_macro_features(df)
        _long_features_done = True
        df = df.sort_index()

    # ── Détection auto du format brut 1m Binance (CSV) ───────────────────────
    else:
        is_raw_1m = False
        if p.is_dir():
            sample_files = sorted(p.glob("*.csv"))[:3]
            if sample_files and all(_is_raw_1m_file(f) for f in sample_files):
                is_raw_1m = True
        elif p.is_file() and _is_raw_1m_file(p):
            is_raw_1m = True

        if is_raw_1m:
            print(f"   Format brut Binance 1m détecté — rééchantillonnage vers 1h…")
            df = _load_raw_1m_klines(p)
            _long_features_done = True
            df = df.sort_index()
            if "datetime" not in df.columns:
                df.index.name = "datetime"
        else:
            _long_features_done = False
            if p.is_dir():
                files = sorted(p.glob("*features*.csv")) or sorted(p.glob("*.csv"))
                if not files:
                    raise RuntimeError(f"Aucun CSV dans {p}")
                frames = [pd.read_csv(f, low_memory=False) for f in files]
                df = pd.concat(frames, ignore_index=True)
            else:
                df = pd.read_csv(p, low_memory=False)

            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df.sort_values("datetime").reset_index(drop=True)
            df = df.set_index("datetime")

    required = SNAPSHOT_FEATURES + ["Close", "future_ret_h", "atr_14", "rv_24"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError("Colonnes manquantes : " + ", ".join(missing))

    for col in SNAPSHOT_FEATURES + ["future_ret_h"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ── Features long asymétriques ────────────────────────────────────────────
    if not _long_features_done:
        print("   Calcul des features long asymétriques...")
        from core.feature_engineering import compute_long_features
        df = compute_long_features(df)

    # ── Features short asymétriques ───────────────────────────────────────────
    print("   Calcul des features short asymétriques...")
    df = compute_short_features(df)

    # ── Features flow / liquidation (si pas encore calculées) ─────────────────
    if "trade_intensity" not in df.columns:
        print("   Calcul des features flow / liquidation proxies...")
        df = compute_flow_features(df)

    # ── Colonnes non-retournement (avant le split) ────────────────────────────
    print("   Calcul future_ret_h3_min (non-retournement long)...")
    from core.labels import compute_long_reversal_col
    df = compute_long_reversal_col(df)
    print("   Calcul future_ret_h3_max (non-retournement short)...")
    df = compute_short_reversal_col(df)

    # ── Régimes déterministes ─────────────────────────────────────────────────
    print("   Calcul des régimes short et long...")
    df = compute_regime_col(df)
    from core.labels import compute_long_regime_col
    df = compute_long_regime_col(df)

    df = df.dropna(subset=SNAPSHOT_FEATURES + ["future_ret_h"]).reset_index(drop=False)
    if "datetime" not in df.columns:
        df = df.rename(columns={"index": "datetime"})
    df = df.set_index("datetime")

    print(f"   {len(df):,} barres  |  {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# LABELS — SÉPARÉS LONG / SHORT
# ═════════════════════════════════════════════════════════════════════════════

def build_labels(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    cfg: PipelineConfig,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Construit des labels séparés pour LONG et SHORT.

      tradeable_net (0/1) :
          1 si |future_ret_h| > thr_q  → mouvement couvre les frais.
          Calibré sur train uniquement (pas de leakage).

      y_long (0/1) :
          1 si future_ret_h > thr  → opportunité LONG exploitable.
          0 sinon (non-tradeable ET shorts compris comme négatifs).

      y_short (0/1) :
          1 si future_ret_h < -thr → opportunité SHORT exploitable.
          0 sinon (non-tradeable ET longs compris comme négatifs).

    Les modèles long et short sont entraînés sur TOUS les bars avec leur label
    respectif. Ceci force chaque modèle à distinguer sa direction contre le
    bruit ET contre l'autre direction — pas de symétrie implicite.
    """
    ret = df["future_ret_h"].values.astype(np.float64)

    # Seuil calibré sur train only (pas de leakage)
    thr = float(np.quantile(np.abs(ret[train_mask]), cfg.tradeable_quantile))

    tradeable = (np.abs(ret) > thr).astype(np.int32)
    y_long    = (ret >  thr).astype(np.int32)   # 1 = bon long
    y_short   = (ret < -thr).astype(np.int32)   # 1 = bon short

    df = df.copy()
    df["tradeable_net"] = tradeable
    df["y_long"]        = y_long
    df["y_short"]       = y_short

    n       = len(df)
    n_tr    = int(tradeable.sum())
    n_long  = int(y_long.sum())
    n_short = int(y_short.sum())

    stats = {
        "thr_tradeable": round(thr, 6),
        "cost_pct": cfg.cost_pct,
        "n_total": n,
        "n_tradeable": n_tr,
        "frac_tradeable": round(n_tr / n, 4),
        "n_long_opportunities": n_long,
        "n_short_opportunities": n_short,
        "frac_long_of_total": round(n_long / n, 4),
        "frac_short_of_total": round(n_short / n, 4),
        "frac_long_of_tradeable": round(n_long / max(n_tr, 1), 4),
        "frac_short_of_tradeable": round(n_short / max(n_tr, 1), 4),
    }

    print(f"   Seuil tradeable   : {thr:.4f}  ({n_tr:,} barres = {n_tr/n:.1%})")
    print(f"   Opportunités LONG : {n_long:,}  ({n_long/n:.1%} des barres)")
    print(f"   Opportunités SHORT: {n_short:,}  ({n_short/n:.1%} des barres)")
    return df, stats


# ═════════════════════════════════════════════════════════════════════════════
# SPLIT CHRONOLOGIQUE
# ═════════════════════════════════════════════════════════════════════════════

def chronological_split(df: pd.DataFrame, test_from_year: int = 2024
                        ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Supporte index DatetimeIndex (nouvelle version) ou colonne "datetime"
    if isinstance(df.index, pd.DatetimeIndex):
        years = df.index.year
    elif "datetime" in df.columns:
        years = pd.to_datetime(df["datetime"]).dt.year.values
    else:
        raise RuntimeError("chronological_split: ni index DatetimeIndex ni colonne 'datetime' trouvée")
    train_mask = np.array(years <= TRAIN_END_YEAR)
    val_mask   = np.array(years == VAL_YEAR)
    test_mask  = np.array(years >= test_from_year)
    print(f"   Train ≤{TRAIN_END_YEAR}: {train_mask.sum():,}  "
          f"Val={VAL_YEAR}: {val_mask.sum():,}  "
          f"Test ≥{test_from_year}: {test_mask.sum():,}")
    return train_mask, val_mask, test_mask


# ═════════════════════════════════════════════════════════════════════════════
# FEATURES TABULAIRES
# ═════════════════════════════════════════════════════════════════════════════

def get_X(df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
    return df.loc[mask, SNAPSHOT_FEATURES].values.astype(np.float32)


def fit_scaler(X_train: np.ndarray) -> StandardScaler:
    sc = StandardScaler()
    sc.fit(X_train)
    return sc


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1 — FILTRE TRADEABLE (partagé long/short)
# ═════════════════════════════════════════════════════════════════════════════

def _calibrate_direction_threshold(
    proba: np.ndarray,
    y_true: np.ndarray,
    min_threshold: float = 0.52,
    max_threshold: float = 0.70,     # cap — évite surapprentissage en val
    min_trades: int = 30,
) -> float:
    """
    Calibre le seuil directionnel en maximisant precision × sqrt(n_trades) sur val.
    Critère : qualité des signaux, pas volume.

    Contraintes :
      - min_threshold <= seuil <= max_threshold  (bornes)
      - au moins min_trades prédictions (stabilité statistique)
    """
    best_score, best_thr = 0.0, min_threshold
    for thr in np.arange(min_threshold, max_threshold + 0.01, 0.01):
        mask   = proba >= thr
        n_pred = int(mask.sum())
        if n_pred < min_trades:
            break  # trop restrictif au-delà
        tp    = int((mask & (y_true == 1)).sum())
        prec  = tp / max(n_pred, 1)
        score = prec * np.sqrt(n_pred)
        if score > best_score:
            best_score, best_thr = score, thr
    return best_thr


def _calibrate_filter_threshold(
    proba: np.ndarray,
    y_true: np.ndarray,
    beta: float = 1.0,
    min_threshold: float = 0.40,
    max_threshold: float = 0.55,     # cap — le filtre ne doit pas devenir trop restrictif
    min_precision: float = 0.25,
) -> float:
    """
    Calibre le seuil filtre en maximisant F-beta sur la validation.

    Contraintes dures :
      - min_threshold <= seuil <= max_threshold  (bornes — évite extrêmes)
      - precision_tradeable >= min_precision     (le filtre filtre vraiment)
      - au moins 30 prédictions positives        (stabilité statistique)

    beta=1.0 : équilibré precision/recall.
    max_threshold : évite que le filtre devienne trop restrictif (>0.55 → trop peu de trades).
    """
    best_score, best_thr = 0.0, min_threshold
    for thr in np.arange(min_threshold, max_threshold + 0.01, 0.01):
        y_pred = (proba >= thr).astype(int)
        tp = int(((y_pred == 1) & (y_true == 1)).sum())
        fp = int(((y_pred == 1) & (y_true == 0)).sum())
        fn = int(((y_pred == 0) & (y_true == 1)).sum())
        n_pos_pred = tp + fp
        if n_pos_pred < 30:
            continue
        prec = tp / max(n_pos_pred, 1)
        rec  = tp / max(tp + fn, 1)
        if prec < min_precision:
            continue
        fb = (1 + beta**2) * prec * rec / max(beta**2 * prec + rec, 1e-9)
        if fb > best_score:
            best_score, best_thr = fb, thr
    return best_thr


def train_filter_model(df: pd.DataFrame,
                       train_mask: np.ndarray,
                       val_mask: np.ndarray,
                       out_dir: Path) -> Tuple[object, object, Dict]:
    """
    Entraîne le filtre tradeable global sur y = tradeable_net.
    Ce filtre est partagé entre LONG et SHORT (premier gate commun).
    Les seuils d'activation sont ensuite séparés par branche (filter_threshold_long/short).

    CORRECTIF CRITIQUE : scale_pos_weight pour compenser le déséquilibre de classes
    (ratio ~75/25). Sans cela, le modèle prédit systématiquement "not tradeable"
    et obtient F1=0.038 comme dans les runs précédents.
    """
    print_section("STAGE 1 — FILTRE TRADEABLE  (class-balanced, seuil calibré)")

    X_train = get_X(df, train_mask)
    y_train = df.loc[train_mask, "tradeable_net"].values.astype(np.int32)
    X_val   = get_X(df, val_mask)
    y_val   = df.loc[val_mask,   "tradeable_net"].values.astype(np.int32)

    # ── Correction déséquilibre de classes (CRITIQUE) ────────────────────────
    n_neg = int((y_train == 0).sum())
    n_pos = int((y_train == 1).sum())
    spw   = n_neg / max(n_pos, 1)
    print(f"   Imbalance : {n_neg:,} not_tradeable / {n_pos:,} tradeable  →  spw={spw:.2f}")

    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=500, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.7,
            scale_pos_weight=spw,                # ← FIX CRITIQUE
            use_label_encoder=False,
            eval_metric="aucpr",                 # AUC-PR > logloss pour imbalanced
            n_jobs=-1, random_state=42,
        )
        model_name = "XGBoost"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        clf = HistGradientBoostingClassifier(
            learning_rate=0.04, max_iter=500, max_depth=5,
            min_samples_leaf=20,
            class_weight="balanced",             # ← FIX CRITIQUE
            random_state=42,
        )
        model_name = "HistGBT"

    scaler = fit_scaler(X_train)
    clf.fit(scaler.transform(X_train), y_train)

    y_proba = clf.predict_proba(scaler.transform(X_val))[:, 1] \
              if hasattr(clf, "predict_proba") else np.zeros(len(y_val))

    # ── Calibration du seuil sur la val (pas le test) ────────────────────────
    # Long  : F1.0 + floor=0.40 + precision≥30% → la calibration peut monter mais pas descendre
    # Short : F1.0 + floor=0.45 + precision≥30% → encore plus sélectif
    thr_long  = _calibrate_filter_threshold(y_proba, y_val,
                                             beta=1.0, min_threshold=0.40, min_precision=0.30)
    thr_short = _calibrate_filter_threshold(y_proba, y_val,
                                             beta=1.0, min_threshold=0.45, min_precision=0.30)
    print(f"   Seuil calibré LONG  : {thr_long:.2f}  (F1.0 + floor=0.40 + prec≥30%)")
    print(f"   Seuil calibré SHORT : {thr_short:.2f}  (F1.0 + floor=0.45 + prec≥30%)")

    # ── Métriques avec le seuil calibré long (évaluation principale) ─────────
    y_pred = (y_proba >= thr_long).astype(int)
    try:
        auc = roc_auc_score(y_val, y_proba)
    except Exception:
        auc = float("nan")
    f1   = f1_score(y_val, y_pred, average="binary", zero_division=0)
    acc  = accuracy_score(y_val, y_pred)
    _, recall, _, _ = precision_recall_fscore_support(y_val, y_pred, labels=[0, 1], zero_division=0)
    cm   = confusion_matrix(y_val, y_pred, labels=[0, 1])

    print(f"   Modèle : {model_name}")
    print(f"   Val acc={acc:.4f}  F1={f1:.4f}  AUC={auc:.4f}")
    print(f"   Recall not_tradeable={recall[0]:.3f}  tradeable={recall[1]:.3f}")
    print(f"   Confusion (seuil={thr_long:.2f}) :\n{cm}")

    if recall[1] < 0.25:
        print("   ⚠  recall_tradeable < 0.25 — filtre trop restrictif, vérifier le CSV")

    metrics = {
        "model": model_name, "val_acc": acc, "val_f1": f1, "val_auc": auc,
        "recall_not_tradeable": float(recall[0]),
        "recall_tradeable": float(recall[1]),
        "confusion_matrix": cm.tolist(),
        "scale_pos_weight": round(spw, 3),
        "calibrated_threshold_long":  round(thr_long, 3),
        "calibrated_threshold_short": round(thr_short, 3),
    }

    import pickle
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "filter_model.pkl",  "wb") as f: pickle.dump(clf, f)
    with open(out_dir / "filter_scaler.pkl", "wb") as f: pickle.dump(scaler, f)
    json_dump(out_dir / "metrics.json", metrics)

    return clf, scaler, metrics


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — MODÈLE DIRECTIONNEL  (long OU short, séparés)
# ═════════════════════════════════════════════════════════════════════════════

def _eval_directional(clf, scaler, X_val_raw: np.ndarray,
                      y_val: np.ndarray, label: str, side: str) -> Dict:
    """Évalue un modèle binaire directionnel (y=1 = opportunité côté `side`)."""
    X_sc   = scaler.transform(X_val_raw)
    y_pred = clf.predict(X_sc)
    y_proba = clf.predict_proba(X_sc)[:, 1] if hasattr(clf, "predict_proba") else y_pred.astype(float)
    acc  = accuracy_score(y_val, y_pred)
    mf1  = f1_score(y_val, y_pred, average="macro", zero_division=0)
    prec, recall, _, _ = precision_recall_fscore_support(y_val, y_pred, labels=[0, 1], zero_division=0)
    try:
        auc = roc_auc_score(y_val, y_proba)
    except Exception:
        auc = float("nan")
    cm = confusion_matrix(y_val, y_pred, labels=[0, 1]).tolist()

    pos_label = side.upper()
    print(f"   [{label:>14}]  acc={acc:.4f}  macro_F1={mf1:.4f}  AUC={auc:.4f}  "
          f"prec_{pos_label}={prec[1]:.3f}  recall_{pos_label}={recall[1]:.3f}")
    return {
        "model": label, "side": side,
        "acc": acc, "macro_f1": mf1, "auc": auc,
        f"precision_{side}": float(prec[1]),
        f"recall_{side}":    float(recall[1]),
        "confusion_matrix": cm,
    }


def train_directional_model(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    side: str,           # "long" ou "short"
    out_dir: Path,
    train_tcn: bool = True,
) -> Dict:
    """
    Entraîne un modèle binaire pour LONG ou SHORT.

    Différence clé avec l'ancien pipeline symétrique :
      - side="long"  → label = y_long  (1 = bon long, 0 = tout le reste)
      - side="short" → label = y_short (1 = bon short, 0 = tout le reste)

    Les modèles sont entraînés sur TOUS les bars (pas seulement tradeable).
    Ceci force le modèle LONG à apprendre à distinguer les vrais longs
    du bruit ET des shorts (ils sont négatifs pour lui, et vice-versa).
    """
    label_col = "y_long" if side == "long" else "y_short"
    side_up   = side.upper()

    print_section(f"STAGE 2 — EDGE MODEL {side_up}  (label={label_col}, tous les bars)")

    X_train  = get_X(df, train_mask)
    y_train  = df.loc[train_mask, label_col].values.astype(np.int32)
    X_val    = get_X(df, val_mask)
    y_val    = df.loc[val_mask,   label_col].values.astype(np.int32)

    n_tr = len(X_train)
    n_v  = len(X_val)
    pos_tr = int(y_train.sum())
    pos_v  = int(y_val.sum())

    print(f"   Train  : {n_tr:,}  ({side_up}=1: {pos_tr:,} = {pos_tr/max(n_tr,1):.1%})")
    print(f"   Val    : {n_v:,}  ({side_up}=1: {pos_v:,} = {pos_v/max(n_v,1):.1%})")

    if pos_tr < 100:
        raise RuntimeError(f"Trop peu d'exemples {side_up} ({pos_tr}) — vérifie le seuil.")

    # ratio négatif/positif pour scale_pos_weight (XGBoost)
    spw = float((y_train == 0).sum()) / max((y_train == 1).sum(), 1)

    scaler     = fit_scaler(X_train)
    all_metrics: List[Dict] = []

    # ── Baseline A : Logistic Regression ─────────────────────────────────────
    lr = LogisticRegression(
        C=0.1, class_weight="balanced",
        max_iter=1000, solver="lbfgs", random_state=42,
    )
    lr.fit(scaler.transform(X_train), y_train)
    m_lr = _eval_directional(lr, scaler, X_val, y_val, "Logistic", side)
    all_metrics.append(m_lr)

    # ── Baseline B : XGBoost ─────────────────────────────────────────────────
    try:
        from xgboost import XGBClassifier
        xgb = XGBClassifier(
            n_estimators=400, max_depth=4,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            use_label_encoder=False, eval_metric="logloss",
            scale_pos_weight=spw,
            n_jobs=-1, random_state=42,
        )
        xgb.fit(scaler.transform(X_train), y_train)
        m_xgb = _eval_directional(xgb, scaler, X_val, y_val, "XGBoost", side)
        all_metrics.append(m_xgb)
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        xgb = HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=400, max_depth=4,
            class_weight="balanced", random_state=42,
        )
        xgb.fit(scaler.transform(X_train), y_train)
        m_xgb = _eval_directional(xgb, scaler, X_val, y_val, "HistGBT", side)
        all_metrics.append(m_xgb)

    best_tab  = max(all_metrics, key=lambda m: m["macro_f1"])
    best_model = lr if best_tab["model"] == "Logistic" else xgb
    print(f"\n   Meilleur tabulaire {side_up} : {best_tab['model']}  macro_F1={best_tab['macro_f1']:.4f}")

    # ── Calibration du seuil directionnel sur val ────────────────────────────
    X_val_sc   = scaler.transform(get_X(df, val_mask))
    y_val_dir  = df.loc[val_mask, f"y_{side}"].values.astype(np.int32)
    # Exclure gray zone (y=-1)
    valid_v    = y_val_dir >= 0
    p_val_best = best_model.predict_proba(X_val_sc[valid_v])[:, 1]
    y_val_filt = y_val_dir[valid_v]
    min_dir_thr = 0.52 if side == "long" else 0.55
    dir_thr_cal = _calibrate_direction_threshold(p_val_best, y_val_filt,
                                                  min_threshold=min_dir_thr, min_trades=30)
    print(f"   Seuil directionnel calibré {side_up} : {dir_thr_cal:.2f}  (prec×√n sur val)")

    # ── TCN (uniquement pour LONG — le short peut être ajouté plus tard) ──────
    tcn_metrics = None
    if train_tcn and side == "long":
        try:
            tcn_metrics = _train_tcn_long(
                df, train_mask, val_mask, scaler, out_dir,
                tab_f1_threshold=best_tab["macro_f1"],
            )
            if tcn_metrics:
                all_metrics.append(tcn_metrics)
        except Exception as e:
            print(f"   TCN ignoré : {e}")
    elif train_tcn and side == "short":
        print(f"   TCN non entraîné pour SHORT (désactivé — activer avec --tcn-short)")

    # ── Sauvegardes ───────────────────────────────────────────────────────────
    import pickle
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "logistic.pkl",    "wb") as f: pickle.dump(lr, f)
    with open(out_dir / "xgb.pkl",         "wb") as f: pickle.dump(xgb, f)
    with open(out_dir / "scaler.pkl",      "wb") as f: pickle.dump(scaler, f)
    json_dump(out_dir / "metrics.json", {"side": side, "models": all_metrics, "best_tabular": best_tab["model"]})

    return {
        "side": side,
        "lr": lr,
        "xgb": xgb,
        "best_model": best_model,
        "scaler": scaler,
        "best_tabular": best_tab["model"],
        "best_tabular_f1": best_tab["macro_f1"],
        "all_metrics": all_metrics,
        "direction_threshold_calibrated": dir_thr_cal,
    }


# ═════════════════════════════════════════════════════════════════════════════
# TCN EDGE MODEL — LONG uniquement
# ═════════════════════════════════════════════════════════════════════════════

def _train_tcn_long(df: pd.DataFrame,
                    train_mask: np.ndarray,
                    val_mask: np.ndarray,
                    snapshot_scaler: StandardScaler,
                    out_dir: Path,
                    tab_f1_threshold: float = 0.0,
                    lookback: int = 64) -> Optional[Dict]:
    """
    Entraîne le TCN sur les fenêtres tradeables pour le pipeline LONG.
    Label : y_long (1=opportunité long, 0=opportunité short parmi tradeable).
    Retourne None si TensorFlow n'est pas disponible.
    """
    try:
        import tensorflow as tf
        from training.common.scaler import RobustScaler, ReservoirSampler
        from level_1.Event_Classifier import EventClassifier, EventClassifierConfig
    except ImportError as e:
        print(f"   TCN skipped (import manquant : {e})")
        return None

    print("\n   --- TCN Edge Model (LONG) ---")

    FEATURE_KEYS = SNAPSHOT_FEATURES
    F = len(FEATURE_KEYS)

    tradeable_arr = df["tradeable_net"].values
    y_long_arr    = df["y_long"].values

    stride = 2
    total  = max(0, len(df) - lookback)

    def get_windows(mask_idx):
        idxset = set(mask_idx)
        for i in range(0, total, stride):
            end = i + lookback
            if end - 1 not in idxset:
                continue
            if tradeable_arr[end - 1] != 1:
                continue
            y = int(y_long_arr[end - 1])
            yield i, y

    train_idx = np.where(train_mask)[0]
    val_idx   = np.where(val_mask)[0]

    train_windows = list(get_windows(train_idx))
    val_windows   = list(get_windows(val_idx))

    if len(train_windows) < 200:
        print(f"   TCN skipped : seulement {len(train_windows)} fenêtres train tradeables")
        return None

    print(f"   Fenêtres train={len(train_windows):,}  val={len(val_windows):,}")

    Xraw = df[FEATURE_KEYS].values.astype(np.float32)
    sc   = RobustScaler()
    sampler = ReservoirSampler(200_000, seed=42)
    for i, _ in train_windows[:5000]:
        sampler.add(Xraw[i:i + lookback])
    sc.fit(sampler.get())
    Xn = sc.transform(Xraw)

    def make_ds(windows, shuffle=False):
        sig = (tf.TensorSpec((lookback, F), tf.float32), tf.TensorSpec((), tf.int32))
        def gen():
            for i, y in windows:
                yield Xn[i:i + lookback].astype(np.float32), np.int32(y)
        ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
        if shuffle:
            ds = ds.shuffle(2048, seed=42)
        return ds.batch(128).prefetch(tf.data.AUTOTUNE)

    ds_train = make_ds(train_windows, shuffle=True)
    ds_val   = make_ds(val_windows)

    model_cfg = EventClassifierConfig(d_model=128, n_layers=4, n_regimes=2, dropout=0.10)
    try:
        model_cfg = EventClassifierConfig(d_model=128, n_layers=4, n_regimes=2, dropout=0.10, confidence_dropout=0.1)
    except TypeError:
        pass
    model = EventClassifier(model_cfg)

    opt = tf.keras.optimizers.AdamW(learning_rate=3e-4, weight_decay=1e-4, global_clipnorm=1.0)

    best_f1, best_ep, patience, bad = 0.0, -1, 6, 0
    print(f"\n   {'Ep':>3}  {'tr_loss':>9}  {'v_loss':>9}  {'acc':>7}  {'macro_f1':>9}  t(s)")
    print("   " + "─" * 55)

    for ep in range(40):
        ep_t0 = time.time()
        tr_loss = []
        for x, y in ds_train:
            with tf.GradientTape() as tape:
                out    = model(x, training=True)
                logits = out["regime_logits"]
                probs  = tf.nn.softmax(logits, -1)
                p_t    = tf.reduce_sum(probs * tf.one_hot(tf.cast(y, tf.int32), 2), -1)
                ce     = tf.keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True)
                loss   = tf.reduce_mean((1 - p_t) ** 2.0 * ce)
            grads = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grads, model.trainable_variables))
            tr_loss.append(float(loss.numpy()))

        v_loss, all_yhat, all_yt = [], [], []
        for x, y in ds_val:
            out    = model(x, training=False)
            logits = out["regime_logits"]
            probs  = tf.nn.softmax(logits, -1)
            p_t    = tf.reduce_sum(probs * tf.one_hot(tf.cast(y, tf.int32), 2), -1)
            ce     = tf.keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True)
            v_loss.append(float(tf.reduce_mean((1 - p_t) ** 2.0 * ce).numpy()))
            all_yhat.extend(tf.argmax(probs, -1).numpy().tolist())
            all_yt.extend(y.numpy().tolist())

        yh   = np.array(all_yhat, np.int32)
        yt   = np.array(all_yt,   np.int32)
        vacc = float((yh == yt).mean()) if len(yt) else 0.0
        mf1  = float(f1_score(yt, yh, average="macro", zero_division=0)) if len(yt) else 0.0
        ep_t = time.time() - ep_t0
        print(f"   {ep+1:>3}  {np.mean(tr_loss):>9.4f}  {np.mean(v_loss):>9.4f}  "
              f"{vacc:>6.2%}  {mf1:>9.4f}  {ep_t:.0f}")

        if mf1 > best_f1 + 1e-4:
            best_f1, best_ep, bad = mf1, ep + 1, 0
            out_dir.mkdir(parents=True, exist_ok=True)
            model.save_weights(str(out_dir / "tcn_best.weights.h5"))
        else:
            bad += 1
            if bad >= patience:
                print(f"   Early stop epoch {ep+1}")
                break

    print(f"\n   TCN best macro_F1={best_f1:.4f}  (epoch {best_ep})")
    if best_f1 > tab_f1_threshold + 0.01:
        print(f"   TCN bat le meilleur tabulaire ({tab_f1_threshold:.4f})")
    else:
        print(f"   TCN n'améliore pas significativement le tabulaire ({tab_f1_threshold:.4f})")

    import pickle
    with open(out_dir / "tcn_scaler.pkl", "wb") as f: pickle.dump(sc, f)

    return {"model": "TCN", "side": "long", "macro_f1": best_f1, "best_epoch": best_ep,
            "beats_tabular": best_f1 > tab_f1_threshold + 0.01}


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3 — ENTRAÎNEMENT DES EXPERTS PAR CONTEXTE
# ═════════════════════════════════════════════════════════════════════════════

def _train_stage3(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    enable_long: bool = True,
    enable_short: bool = True,
) -> Optional["SpecialistPredictor"]:
    """
    Entraîne les experts par contexte de marché (level_3).

    Appelé après Stage 2 (train_long_model / train_short_model).
    Le df doit déjà contenir les labels y_long, y_short et les features.

    Retourne un SpecialistPredictor prêt à l'emploi, ou None si échec.
    """
    from ai.level_3 import (
        MarketContext, train_specialists, SpecialistPredictor,
        SpecialistConfig, RouterConfig,
    )

    spec_cfg = SpecialistConfig(
        n_estimators=400,
        max_depth=4,
        min_train_samples=300,
        min_val_samples=80,
        min_auc=0.56,
        calibrate=True,
    )
    router_cfg = RouterConfig(
        default_specialist_weight=0.35,
        max_specialist_weight=0.55,
        min_expert_auc=0.56,
        min_train_samples=300,
    )

    # Contextes pertinents selon les branches activées
    contexts = []
    if enable_long:
        contexts += [
            MarketContext.TREND_LONG,
            MarketContext.BREAKOUT,
        ]
    if enable_short:
        contexts += [
            MarketContext.TREND_SHORT,
        ]
    # Contextes applicables aux deux côtés
    contexts += [
        MarketContext.MEAN_REVERSION,
        MarketContext.HIGH_VOL,
    ]

    router = train_specialists(
        df=df,
        train_mask=train_mask,
        val_mask=val_mask,
        out_dir=out_dir,
        specialist_cfg=spec_cfg,
        router_cfg=router_cfg,
        contexts=contexts,
    )

    predictor = SpecialistPredictor.from_router(router)
    return predictor


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST — branche LONG ou SHORT (mode séparé)
# ═════════════════════════════════════════════════════════════════════════════

def run_backtest_side(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    filter_model,
    filter_scaler,
    edge_model,
    edge_scaler,
    side: str,           # "long" ou "short"
    cfg: PipelineConfig,
    out_dir: Path,
    model_label: str = "",
    silent: bool = False,
    edge_features: Optional[List[str]] = None,   # features pour l'edge model (None = SNAPSHOT_FEATURES)
    regime_model=None,           # méta-modèle bear regime (short only)
    regime_scaler=None,          # scaler du regime model
    regime_features: Optional[List[str]] = None,  # features du regime model
    regime_threshold: float = 0.70,              # seuil d'activation bear regime
    specialist_predictor=None,   # SpecialistPredictor niveau 3 (optionnel)
) -> Dict:
    """
    Backtest walk-forward pour UNE seule branche (long OU short).

    side="long"  : filtre=filter_threshold_long, seuil=direction_threshold_long,
                   action=BUY, sign=+1, RiskConfig long.
    side="short" : filtre=filter_threshold_short, seuil=direction_threshold_short,
                   action=SELL, sign=-1, RiskConfig short.

    Les paramètres de risque sont asymétriques par branche (voir PipelineConfig).
    """
    filter_thr   = cfg.filter_threshold_long    if side == "long" else cfg.filter_threshold_short
    decision_thr = cfg.direction_threshold_long if side == "long" else cfg.direction_threshold_short
    action_name  = "BUY"  if side == "long" else "SELL"
    ret_sign     = +1.0   if side == "long" else -1.0
    label = model_label or side.upper()

    if not silent:
        print_section(f"BACKTEST {side.upper()} — {label}  "
                      f"(filt≥{filter_thr:.2f}  dir≥{decision_thr:.2f}  "
                      f"cost={cfg.cost_pct:.3%})")

    risk_cfg = make_risk_config(cfg, side)
    rc       = RiskController(risk_cfg)

    equity_curve   = [cfg.initial_equity]
    pnl_list: List[float] = []
    trades: List[Dict]    = []
    skipped_filter = skipped_dir = skipped_risk = n_tested = 0

    df_test = df[test_mask].reset_index(drop=False)
    # Après reset_index(drop=False), l'ancien index DatetimeIndex devient la colonne "datetime"
    if "datetime" not in df_test.columns and "index" in df_test.columns:
        df_test = df_test.rename(columns={"index": "datetime"})

    # ── Pré-calcul batch des probabilités (évite N appels predict_proba 1-sample) ──
    _edge_feats = edge_features if edge_features is not None else SNAPSHOT_FEATURES
    X_filt_all = df_test[SNAPSHOT_FEATURES].fillna(0.0).values.astype(np.float32)
    X_edge_all = df_test[_edge_feats].fillna(0.0).values.astype(np.float32)
    p_trade_all = filter_model.predict_proba(filter_scaler.transform(X_filt_all))[:, 1]
    p_side_all  = edge_model.predict_proba(edge_scaler.transform(X_edge_all))[:, 1]

    # ── Level 3 : fusion avec l'expert spécialisé (optionnel) ─────────────────
    if specialist_predictor is not None:
        try:
            _p_null = np.zeros(len(df_test), dtype=np.float64)
            _p_l2   = p_side_all.astype(np.float64)
            _routing = specialist_predictor.predict_batch(
                df_test,
                p_long_l2  = _p_l2  if side == "long"  else _p_null,
                p_short_l2 = _p_l2  if side == "short" else _p_null,
            )
            col = "p_long_final" if side == "long" else "p_short_final"
            p_side_all = _routing[col].values.astype(np.float32)
            n_expert = int(_routing["expert_used"].sum())
            if not silent:
                pct = n_expert / max(len(df_test), 1)
                print(f"   [L3] expert actif sur {pct:.1%} des barres test "
                      f"({n_expert:,}/{len(df_test):,})")
        except Exception as _e:
            if not silent:
                print(f"   [L3] specialist ignoré : {_e}")

    # ── Pré-calcul du méta-modèle de régime bear (short uniquement) ──────────
    _use_regime = (side == "short" and regime_model is not None
                   and regime_scaler is not None and regime_features)
    if _use_regime:
        _reg_feats = regime_features
        X_regime_all = df_test[_reg_feats].fillna(0.0).values.astype(np.float32)
        p_bear_all = regime_model.predict_proba(
            regime_scaler.transform(X_regime_all))[:, 1]
        skipped_regime = 0
        n_bear_activated = 0
    else:
        p_bear_all = None
        skipped_regime = 0
        n_bear_activated = 0

    for i, row in enumerate(df_test.itertuples()):
        bar_date = pd.Timestamp(row.datetime)
        day_str  = bar_date.strftime("%Y-%m-%d")
        year     = bar_date.year

        if day_str != rc.state.current_day:
            rc.reset_day(equity=rc.state.equity, day_str=day_str)

        n_tested += 1
        p_trade = float(p_trade_all[i])

        # 1. Filtre tradeable (seuil dépendant de la branche)
        if p_trade < filter_thr:
            skipped_filter += 1
            continue

        # 1b. Méta-régime bear (short uniquement) — gate AVANT l'edge model
        if _use_regime:
            p_bear = float(p_bear_all[i])
            if p_bear < regime_threshold:
                skipped_regime += 1
                continue
            n_bear_activated += 1

        # 2. Edge model — P(y_long=1) ou P(y_short=1)
        p_side = float(p_side_all[i])
        if p_side < decision_thr:
            skipped_dir += 1
            continue

        # edge_final : positif → long, négatif → short
        edge_final = p_side * ret_sign

        # 3. RiskController
        feats_dict = {"atr_14": float(row.atr_14), "rv_24": float(row.rv_24)}
        decision = rc.decide(
            price=float(row.Close),
            edge_final=edge_final,
            scale=p_side,
            bar_index=i,
            features=feats_dict,
        )
        if decision["action"] == "HOLD":
            skipped_risk += 1
            continue

        # 4. Simulation : hold 1 barre, PnL net de coût
        fut_ret     = float(row.future_ret_h) * ret_sign
        pnl_net_pct = fut_ret - cfg.cost_pct
        pnl_abs     = pnl_net_pct * decision["notional"]

        rc.on_fill_pnl(pnl_abs)
        equity_curve.append(rc.state.equity)
        pnl_list.append(pnl_abs)
        trades.append({
            "bar": i, "date": day_str, "year": year,
            "side": side,
            "action": action_name,
            "p_tradeable": round(p_trade, 4),
            "p_side": round(p_side, 4),
            "fut_ret": round(float(row.future_ret_h), 5),
            "pnl_net_pct": round(pnl_net_pct, 5),
            "pnl_abs": round(pnl_abs, 4),
            "equity": round(rc.state.equity, 2),
            "notional": round(decision["notional"], 2),
        })

    # ── Métriques ──────────────────────────────────────────────────────────
    m = _backtest_metrics(pnl_list, equity_curve, trades, n_tested)
    m.update({
        "side": side,
        "model": label,
        "filter_threshold": filter_thr,
        "direction_threshold": decision_thr,
        "cost_pct": cfg.cost_pct,
        "skipped_filter": skipped_filter,
        "skipped_direction": skipped_dir,
        "skipped_risk": skipped_risk,
        "risk_per_trade": cfg.risk_per_trade_long if side == "long" else cfg.risk_per_trade_short,
    })

    bench_all_long = float(df_test["future_ret_h"].sum())

    if _use_regime:
        m["skipped_regime"]    = skipped_regime
        m["n_bear_activated"]  = n_bear_activated
        m["regime_threshold"]  = regime_threshold
        m["regime_model_used"] = True
        if not silent:
            print(f"   Skipped régime   : {skipped_regime:,}  "
                  f"(bear_activé={n_bear_activated:,} / {n_tested:,} = "
                  f"{n_bear_activated/max(n_tested,1):.1%})")

    if not silent:
        _print_backtest_summary(m, skipped_filter, skipped_dir, skipped_risk,
                                n_tested, bench_all_long, side)

    out_dir.mkdir(parents=True, exist_ok=True)
    json_dump(out_dir / "summary.json", m)
    json_dump(out_dir / "equity_curve.json", equity_curve)
    json_dump(out_dir / "trades.json", trades[:2000])

    return m


def _print_backtest_summary(m: Dict, skipped_filter: int, skipped_dir: int,
                             skipped_risk: int, n_tested: int,
                             bench_all_long: float, side: str) -> None:
    print(f"\n   Barres testées   : {n_tested:,}")
    print(f"   Skipped filter   : {skipped_filter:,}  ({skipped_filter/max(n_tested,1):.1%})")
    print(f"   Skipped dir_thr  : {skipped_dir:,}")
    print(f"   Skipped risk     : {skipped_risk:,}")
    print(f"   Trades {side.upper():<5}      : {m['n_trades']:,}")
    print(f"\n   Capital initial  : {m['initial_equity']:,.0f}")
    print(f"   Capital final    : {m['final_equity']:,.2f}  ({m['total_return_pct']:+.2f}%)")
    print(f"   Profit factor    : {m['profit_factor']:.3f}  {'OK' if m['profit_factor'] > 1.5 else '!!'}")
    print(f"   Max drawdown     : {m['max_drawdown']:.2%}  {'OK' if m['max_drawdown'] < 0.15 else '!!'}")
    print(f"   Sharpe (annuel)  : {m['sharpe_annualized']:.3f}")
    print(f"   Win rate         : {m['win_rate']:.2%}")
    print(f"   Avg win          : {m['avg_win']:+.2f}   Avg loss : {m['avg_loss']:+.2f}")
    print(f"   Expectancy/trade : {m['expectancy_per_trade']:+.2f}")
    print(f"\n   Benchmark always-long  cum_log_ret : {bench_all_long:+.4f}")
    if m.get("by_year"):
        print(f"\n   Par année : {'an':>5}  {'trades':>6}  {'PnL':>9}  {'PF':>6}  {'WR':>7}")
        print("   " + "─" * 40)
        for yr, v in m["by_year"].items():
            print(f"   {yr}  {v['trades']:>6}  {v['pnl_sum']:>+9.2f}  "
                  f"{v['pf']:>6.3f}  {v['win_rate']:>6.1%}")


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST COMBINÉ LONG + SHORT
# ═════════════════════════════════════════════════════════════════════════════

def run_backtest_combined(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    filter_model,
    filter_scaler,
    long_model,
    long_scaler,
    short_model,
    short_scaler,
    cfg: PipelineConfig,
    out_dir: Path,
    model_label: str = "Combined",
    silent: bool = False,
    short_edge_features: Optional[List[str]] = None,  # features pour le short edge model
    long_edge_features: Optional[List[str]] = None,   # features pour le long edge model
    regime_model=None,            # méta-modèle bear regime (short gate)
    regime_scaler=None,
    regime_features: Optional[List[str]] = None,
    regime_threshold: float = 0.70,
    specialist_predictor=None,    # SpecialistPredictor niveau 3 (optionnel)
) -> Dict:
    """
    Backtest combiné LONG + SHORT en un seul pass walk-forward.

    Règle de priorité :
      1. Si signal LONG valide → trade long.
      2. Si pas de signal long ET signal SHORT valide → trade short.

    Equity partagée entre les deux branches.
    RiskControllers séparés (paramètres asymétriques).
    """
    if not silent:
        print_section(f"BACKTEST COMBINÉ (long prioritaire)  —  {model_label}")

    rc_long  = RiskController(make_risk_config(cfg, "long"))
    rc_short = RiskController(make_risk_config(cfg, "short"))

    equity        = cfg.initial_equity
    equity_curve  = [equity]
    pnl_list: List[float]  = []
    trades: List[Dict]     = []
    n_tested = 0
    counts   = {"long": 0, "short": 0, "skipped_filter": 0,
                "skipped_dir": 0, "skipped_risk": 0}

    df_test = df[test_mask].reset_index(drop=False)
    if "datetime" not in df_test.columns and "index" in df_test.columns:
        df_test = df_test.rename(columns={"index": "datetime"})

    # ── Pré-calcul batch (évite N appels predict_proba 1-sample) ─────────────
    X_filt_c   = df_test[SNAPSHOT_FEATURES].fillna(0.0).values.astype(np.float32)
    _lf = long_edge_features  if long_edge_features  is not None else SNAPSHOT_FEATURES
    _sf = short_edge_features if short_edge_features is not None else SNAPSHOT_FEATURES
    X_long_c   = df_test[_lf].fillna(0.0).values.astype(np.float32)
    X_short_c  = df_test[_sf].fillna(0.0).values.astype(np.float32)
    p_trade_c  = filter_model.predict_proba(filter_scaler.transform(X_filt_c))[:, 1]
    p_long_c   = long_model.predict_proba(long_scaler.transform(X_long_c))[:, 1]    if cfg.enable_long  else np.zeros(len(df_test))
    p_short_c  = short_model.predict_proba(short_scaler.transform(X_short_c))[:, 1] if cfg.enable_short else np.zeros(len(df_test))

    # ── Level 3 : fusion specialists (optionnel) ──────────────────────────────
    if specialist_predictor is not None:
        try:
            _routing_c = specialist_predictor.predict_batch(
                df_test,
                p_long_l2  = p_long_c.astype(np.float64),
                p_short_l2 = p_short_c.astype(np.float64),
            )
            p_long_c  = _routing_c["p_long_final"].values.astype(np.float32)
            p_short_c = _routing_c["p_short_final"].values.astype(np.float32)
            n_exp = int(_routing_c["expert_used"].sum())
            if not silent:
                print(f"   [L3] expert combiné actif sur "
                      f"{n_exp/max(len(df_test),1):.1%} des barres "
                      f"({n_exp:,}/{len(df_test):,})")
        except Exception as _e:
            if not silent:
                print(f"   [L3] specialist combiné ignoré : {_e}")

    # ── Pré-calcul du régime bear (gate short) ────────────────────────────────
    _use_regime_c = (cfg.enable_short and regime_model is not None
                     and regime_scaler is not None and regime_features)
    if _use_regime_c:
        X_regime_c = df_test[regime_features].fillna(0.0).values.astype(np.float32)
        p_bear_c   = regime_model.predict_proba(regime_scaler.transform(X_regime_c))[:, 1]
    else:
        p_bear_c = np.ones(len(df_test))  # tout activé (pas de régime)

    for i, row in enumerate(df_test.itertuples()):
        bar_date = pd.Timestamp(row.datetime)
        day_str  = bar_date.strftime("%Y-%m-%d")
        year     = bar_date.year

        # Synchronise les deux RC sur la même journée / equity
        if day_str != rc_long.state.current_day:
            rc_long.reset_day(equity=equity,  day_str=day_str)
            rc_short.reset_day(equity=equity, day_str=day_str)

        n_tested += 1
        feats  = {"atr_14": float(row.atr_14), "rv_24": float(row.rv_24)}
        p_trade = float(p_trade_c[i])

        # ── Tente le LONG en premier ──────────────────────────────────────────
        taken = False
        if cfg.enable_long and p_trade >= cfg.filter_threshold_long:
            p_long = float(p_long_c[i])
            if p_long >= cfg.direction_threshold_long:
                decision = rc_long.decide(
                    price=float(row.Close), edge_final=p_long,
                    scale=p_long, bar_index=i, features=feats,
                )
                if decision["action"] != "HOLD":
                    fut_ret     = float(row.future_ret_h)
                    pnl_net_pct = fut_ret - cfg.cost_pct
                    pnl_abs     = pnl_net_pct * decision["notional"]
                    rc_long.on_fill_pnl(pnl_abs)
                    # rc_short est indépendant : on ne le notifie PAS quand un long se prend
                    equity += pnl_abs
                    equity_curve.append(equity)
                    pnl_list.append(pnl_abs)
                    trades.append({
                        "bar": i, "date": day_str, "year": year, "side": "long",
                        "p_tradeable": round(p_trade, 4), "p_side": round(p_long, 4),
                        "fut_ret": round(fut_ret, 5),
                        "pnl_net_pct": round(pnl_net_pct, 5),
                        "pnl_abs": round(pnl_abs, 4),
                        "equity": round(equity, 2),
                        "notional": round(decision["notional"], 2),
                    })
                    counts["long"] += 1
                    taken = True
                else:
                    counts["skipped_risk"] += 1
            else:
                counts["skipped_dir"] += 1
        elif p_trade < min(cfg.filter_threshold_long, cfg.filter_threshold_short):
            counts["skipped_filter"] += 1
            continue

        # ── Tente le SHORT si le long n'a pas pris ────────────────────────────
        if not taken and cfg.enable_short and p_trade >= cfg.filter_threshold_short:
            # Méta-régime bear : gate avant l'edge model
            if _use_regime_c and float(p_bear_c[i]) < regime_threshold:
                continue
            p_short = float(p_short_c[i])
            if p_short >= cfg.direction_threshold_short:
                decision = rc_short.decide(
                    price=float(row.Close), edge_final=-p_short,
                    scale=p_short, bar_index=i, features=feats,
                )
                if decision["action"] != "HOLD":
                    fut_ret     = -float(row.future_ret_h)   # signe inversé pour short
                    pnl_net_pct = fut_ret - cfg.cost_pct
                    pnl_abs     = pnl_net_pct * decision["notional"]
                    rc_short.on_fill_pnl(pnl_abs)
                    equity += pnl_abs
                    equity_curve.append(equity)
                    pnl_list.append(pnl_abs)
                    trades.append({
                        "bar": i, "date": day_str, "year": year, "side": "short",
                        "p_tradeable": round(p_trade, 4), "p_side": round(p_short, 4),
                        "fut_ret": round(float(row.future_ret_h), 5),
                        "pnl_net_pct": round(pnl_net_pct, 5),
                        "pnl_abs": round(pnl_abs, 4),
                        "equity": round(equity, 2),
                        "notional": round(decision["notional"], 2),
                    })
                    counts["short"] += 1
                else:
                    counts["skipped_risk"] += 1
            else:
                counts["skipped_dir"] += 1

    m = _backtest_metrics(pnl_list, equity_curve, trades, n_tested)
    m.update({
        "side": "combined",
        "model": model_label,
        "n_long": counts["long"],
        "n_short": counts["short"],
        "long_pf":  round(profit_factor([t["pnl_abs"] for t in trades if t["side"] == "long"]),  4),
        "short_pf": round(profit_factor([t["pnl_abs"] for t in trades if t["side"] == "short"]), 4),
        "skipped_filter": counts["skipped_filter"],
        "skipped_direction": counts["skipped_dir"],
        "skipped_risk": counts["skipped_risk"],
    })

    if not silent:
        print(f"\n   Trades LONG  : {counts['long']:,}")
        print(f"   Trades SHORT : {counts['short']:,}")
        print(f"   Total trades : {m['n_trades']:,}")
        print(f"   Capital final: {m['final_equity']:,.2f}  ({m['total_return_pct']:+.2f}%)")
        print(f"   Profit factor: {m['profit_factor']:.3f}")
        print(f"   Max drawdown : {m['max_drawdown']:.2%}")
        print(f"   Win rate     : {m['win_rate']:.2%}")
        print(f"   Expectancy   : {m['expectancy_per_trade']:+.2f}")
        print(f"   Long PF      : {m['long_pf']:.3f}  |  Short PF : {m['short_pf']:.3f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    json_dump(out_dir / "summary.json", m)
    json_dump(out_dir / "equity_curve.json", equity_curve)
    json_dump(out_dir / "trades.json", trades[:2000])

    return m


# ═════════════════════════════════════════════════════════════════════════════
# COMPARAISON TABULAIRE LONG / SHORT / COMBINÉ
# ═════════════════════════════════════════════════════════════════════════════

def _check_short_stability(
    short_bt: Dict,
    min_pf: float = 0.80,
    min_wr: float = 0.40,
    max_bad_years: int = 1,
) -> Tuple[bool, str]:
    """
    Vérifie que le short est stable inter-années.
    Retourne (True, "ok") si stable, (False, raison) sinon.
    Un short instable est désactivé pour préserver le long.
    """
    by_year = short_bt.get("by_year", {})
    if not by_year:
        return True, "ok (pas assez d'années pour valider)"

    bad_years = []
    for yr, stats in by_year.items():
        pf = stats.get("pf", 1.0)
        wr = stats.get("win_rate", 0.5)
        if pf < min_pf or wr < min_wr:
            bad_years.append(f"{yr}(PF={pf:.2f} WR={wr:.1%})")

    if len(bad_years) > max_bad_years:
        return False, f"short instable sur {len(bad_years)} années: {', '.join(bad_years)}"
    return True, "ok"


def print_comparison_table(results: Dict[str, Optional[Dict]]) -> None:
    """
    Affiche un tableau comparatif clair.
    results = {"long": {...}, "short": {...}, "combined": {...}}
    Entrées None ignorées.
    """
    print_section("COMPARAISON LONG / SHORT / COMBINÉ")
    print(f"   {'Branche':>10}  {'Trades':>6}  {'PF':>6}  {'MDD':>7}  "
          f"{'WR':>6}  {'Return':>8}  {'Expect':>8}  {'Sharpe':>7}")
    print("   " + "─" * 74)

    for key in ("long", "short", "combined"):
        r = results.get(key)
        if r is None:
            continue
        flag = "OK" if r["profit_factor"] > 1.5 and r["max_drawdown"] < 0.15 else "  "
        n_trades = r.get("n_trades", 0)
        if key == "combined":
            trade_str = f"{n_trades:>6} (L:{r.get('n_long',0)} S:{r.get('n_short',0)})"
        else:
            trade_str = f"{n_trades:>6}"

        print(f"   {key.upper():>10}  {trade_str}  "
              f"{r['profit_factor']:>6.3f}  "
              f"{r['max_drawdown']:>6.2%}  "
              f"{r['win_rate']:>5.1%}  "
              f"{r['total_return_pct']:>+7.1f}%  "
              f"{r['expectancy_per_trade']:>+8.2f}  "
              f"{r['sharpe_annualized']:>7.3f}  {flag}")

    # Impact du short sur le combined
    long_bt  = results.get("long")
    combined = results.get("combined")
    if long_bt and combined:
        delta_ret = combined["total_return_pct"] - long_bt["total_return_pct"]
        delta_pf  = combined["profit_factor"]    - long_bt["profit_factor"]
        delta_mdd = combined["max_drawdown"]      - long_bt["max_drawdown"]
        print(f"\n   Impact SHORT sur le combined vs LONG seul :")
        print(f"     Return  : {delta_ret:+.2f}%  "
              f"PF: {delta_pf:+.3f}  "
              f"MDD: {delta_mdd:+.2%}  "
              f"({'NUISIBLE' if delta_ret < 0 or delta_pf < 0 else 'UTILE'})")


def compare_models_side(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    filter_clf,
    filter_scaler,
    directional_result: Dict,
    cfg: PipelineConfig,
    out_dir: Path,
    side: str = "",
    regime_model=None,
    regime_scaler=None,
    regime_features=None,
    regime_threshold: float = 0.70,
) -> List[Dict]:
    """Compare Logistic vs XGBoost pour une branche donnée (long ou short)."""
    side = side or directional_result["side"]
    scaler = directional_result["scaler"]
    _edge_feats = directional_result.get("features")  # None → SNAPSHOT_FEATURES
    results = []

    for name, clf in [("Logistic", directional_result["lr"]),
                      ("XGBoost",  directional_result["xgb"])]:
        bt_dir = out_dir / name
        res = run_backtest_side(
            df=df, test_mask=test_mask,
            filter_model=filter_clf, filter_scaler=filter_scaler,
            edge_model=clf, edge_scaler=scaler,
            side=side, cfg=cfg,
            out_dir=bt_dir, model_label=f"{name}_{side}",
            silent=True, edge_features=_edge_feats,
            regime_model=regime_model if side == "short" else None,
            regime_scaler=regime_scaler if side == "short" else None,
            regime_features=regime_features if side == "short" else None,
            regime_threshold=regime_threshold,
        )
        results.append(res)

    print_section(f"COMPARAISON MODÈLES — {side.upper()}")
    print(f"   {'Modèle':>14}  {'Trades':>6}  {'PF':>6}  {'MDD':>7}  {'WR':>6}  {'Return':>8}")
    print("   " + "─" * 60)
    for r in sorted(results, key=lambda x: x["profit_factor"], reverse=True):
        print(f"   {r['model']:>14}  {r['n_trades']:>6}  "
              f"{r['profit_factor']:>6.3f}  "
              f"{r['max_drawdown']:>6.2%}  "
              f"{r['win_rate']:>5.1%}  "
              f"{r['total_return_pct']:>+7.1f}%")

    out_dir.mkdir(parents=True, exist_ok=True)
    json_dump(out_dir / f"comparison_{side}.json", results)
    return results


# ═════════════════════════════════════════════════════════════════════════════
# GRID RUNNER — sweep par branche
# ═════════════════════════════════════════════════════════════════════════════

def run_grid(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    filter_clf,
    filter_scaler,
    directional_result: Dict,
    cfg: PipelineConfig,
    out_dir: Path,
) -> None:
    """
    Balaie la grille filtre × direction × coût pour une branche (long ou short).
    Les grilles long et short sont indépendantes.
    """
    side = directional_result["side"]
    scaler = directional_result["scaler"]
    _edge_feats = directional_result.get("features")  # None → SNAPSHOT_FEATURES

    print_section(f"GRID {side.upper()} — sweep filtre × direction × coût")

    filter_thrs    = [0.30, 0.35, 0.40, 0.45, 0.50]
    direction_thrs = [0.50, 0.52, 0.55, 0.58]
    cost_pcts      = [0.001, 0.0015, 0.002, 0.0025]
    models         = [("Logistic", directional_result["lr"]),
                      ("XGBoost",  directional_result["xgb"])]

    grid_results: List[Dict] = []
    total = len(filter_thrs) * len(direction_thrs) * len(cost_pcts) * len(models)
    done  = 0

    for f_thr in filter_thrs:
        for d_thr in direction_thrs:
            for c_pct in cost_pcts:
                for m_name, clf in models:
                    done += 1
                    label    = f"{m_name}_{side}_f{f_thr:.2f}_d{d_thr:.2f}_c{int(c_pct*10000)}"
                    bt_dir   = out_dir / "grid" / label

                    # Crée une config temporaire avec les seuils de la grille
                    grid_cfg = PipelineConfig(
                        tradeable_quantile=cfg.tradeable_quantile,
                        cost_pct=c_pct,
                        filter_threshold_long=f_thr   if side == "long"  else cfg.filter_threshold_long,
                        direction_threshold_long=d_thr if side == "long"  else cfg.direction_threshold_long,
                        filter_threshold_short=f_thr  if side == "short" else cfg.filter_threshold_short,
                        direction_threshold_short=d_thr if side == "short" else cfg.direction_threshold_short,
                        initial_equity=cfg.initial_equity,
                        risk_per_trade_long=cfg.risk_per_trade_long,
                        risk_per_trade_short=cfg.risk_per_trade_short,
                        max_consecutive_losses_long=cfg.max_consecutive_losses_long,
                        max_consecutive_losses_short=cfg.max_consecutive_losses_short,
                        cooldown_bars_long=cfg.cooldown_bars_long,
                        cooldown_bars_short=cfg.cooldown_bars_short,
                    )

                    res = run_backtest_side(
                        df=df, test_mask=test_mask,
                        filter_model=filter_clf, filter_scaler=filter_scaler,
                        edge_model=clf, edge_scaler=scaler,
                        side=side, cfg=grid_cfg,
                        out_dir=bt_dir, model_label=label,
                        silent=True, edge_features=_edge_feats,
                    )
                    grid_results.append(res)
                    if done % 10 == 0 or done == total:
                        print(f"   {done}/{total}  {label}  "
                              f"PF={res['profit_factor']:.3f}  "
                              f"trades={res['n_trades']}  "
                              f"ret={res['total_return_pct']:+.1f}%")

    grid_results.sort(key=lambda r: (r["profit_factor"], r["total_return_pct"]), reverse=True)

    print(f"\n   Top 10 {side.upper()} :")
    print(f"   {'Modèle':>38}  {'N':>5}  {'PF':>6}  {'MDD':>6}  {'Ret':>8}")
    print("   " + "─" * 72)
    for r in grid_results[:10]:
        print(f"   {r['model']:>38}  {r['n_trades']:>5}  "
              f"{r['profit_factor']:>6.3f}  "
              f"{r['max_drawdown']:>5.2%}  "
              f"{r['total_return_pct']:>+7.1f}%")

    out_dir.mkdir(parents=True, exist_ok=True)
    json_dump(out_dir / f"grid_results_{side}.json", grid_results)


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    ap = argparse.ArgumentParser(
        description="Pipeline ML noyau — horizon 60min, labels séparés LONG/SHORT.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data", default="data/bundle_btc/features_merged.parquet",
                    help="Bundle parquet (défaut), CSV enrichi, dossier CSV enrichis, "
                         "ou dossier de CSV bruts Binance klines 1m "
                         "(détection automatique — les données 1m sont "
                         "rééchantillonnées à 1h avant entraînement)")
    ap.add_argument("--out", default=str(FUTUR / "runs" / "pipeline"),
                    help="Dossier de sortie racine")
    ap.add_argument("--test-from", type=int, default=2024,
                    help="Première année du jeu de test")
    ap.add_argument("--skip-tcn", action="store_true",
                    help="Saute l'entraînement du TCN (long uniquement)")
    ap.add_argument("--auto-calibrate", action="store_true",
                    help="Utilise les seuils filtre calibrés sur val (recommandé) "
                         "au lieu des seuils manuels --filter-thr-*")
    ap.add_argument("--require-short-stability", action="store_true",
                    help="Désactive automatiquement le short si instable inter-années "
                         "(PF 2025 < 0.80 ou WR 2025 < 40%%)")
    ap.add_argument("--no-short-if-unstable", action="store_true",
                    help="Alias de --require-short-stability")

    # ── Mode ──────────────────────────────────────────────────────────────────
    ap.add_argument("--mode", choices=["long", "short", "combined"], default="long",
                    help="Branche(s) à entraîner et backtester. "
                         "'long' = LONG seul (recommandé). "
                         "'short' = SHORT seul. "
                         "'combined' = les deux + backtest combiné.")
    ap.add_argument("--no-short", action="store_true",
                    help="Désactive la branche SHORT même en mode combined")

    # ── Labeling ──────────────────────────────────────────────────────────────
    ap.add_argument("--tradeable-q", type=float, default=0.70,
                    help="Quantile pour le seuil de label tradeable (~30%% barres)")
    ap.add_argument("--cost", type=float, default=COST_PCT,
                    help="Coût round-trip pour le backtest")

    # ── Seuils LONG ───────────────────────────────────────────────────────────
    ap.add_argument("--filter-thr-long", type=float, default=0.40,
                    help="Seuil P(tradeable) pour la branche LONG")
    ap.add_argument("--direction-thr-long", type=float, default=0.52,
                    help="Seuil confiance direction pour la branche LONG")

    # ── Seuils SHORT (plus conservateurs par défaut) ──────────────────────────
    ap.add_argument("--filter-thr-short", type=float, default=0.45,
                    help="Seuil P(tradeable) pour la branche SHORT")
    ap.add_argument("--direction-thr-short", type=float, default=0.55,
                    help="Seuil confiance direction pour la branche SHORT")

    # ── Risk LONG ─────────────────────────────────────────────────────────────
    ap.add_argument("--risk-long", type=float, default=0.002,
                    help="Risk per trade — branche LONG")
    ap.add_argument("--max-losses-long", type=int, default=3,
                    help="Max pertes consécutives — branche LONG")
    ap.add_argument("--cooldown-long", type=int, default=2,
                    help="Barres de cooldown entre trades — branche LONG")

    # ── Risk SHORT ────────────────────────────────────────────────────────────
    ap.add_argument("--risk-short", type=float, default=0.001,
                    help="Risk per trade — branche SHORT (plus conservateur)")
    ap.add_argument("--max-losses-short", type=int, default=2,
                    help="Max pertes consécutives — branche SHORT")
    ap.add_argument("--cooldown-short", type=int, default=3,
                    help="Barres de cooldown entre trades — branche SHORT (plus conservateur)")

    # ── Options avancées ──────────────────────────────────────────────────────
    ap.add_argument("--grid", action="store_true",
                    help="Lance le sweep de grille pour la/les branche(s) active(s)")
    ap.add_argument("--compare-models", action="store_true",
                    help="Compare Logistic vs XGBoost en backtest séparé")
    ap.add_argument("--regression", action="store_true",
                    help="Mode régression PnL : XGBRegressor sur y_long_pnl/y_short_pnl. "
                         "Métriques = profit_factor, Sharpe, avg_pnl au lieu de AUC/F1.")
    ap.add_argument("--top-pct", type=float, default=0.01,
                    help="Fraction top-percentile des signaux à trader en mode --regression "
                         "(0.01 = top 1%% — objectif <1%% de signaux)")
    ap.add_argument("--margin", type=float, default=0.001,
                    help="Margin minimale de PnL prédit pour trader en mode --regression")

    return ap.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# MODE RÉGRESSION PnL — XGBRegressor + métriques profit_factor / Sharpe
# ═════════════════════════════════════════════════════════════════════════════

def train_pnl_regressor(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    test_mask: np.ndarray,
    side: str,
    out_dir: Path,
    top_pct: float = 0.01,
    margin: float = 0.001,
    fee: float = 0.001,
    feature_list: Optional[List[str]] = None,
) -> Dict:
    """
    Entraîne un XGBRegressor pour prédire le PnL net.

    Cible  : y_long_pnl  = future_ret_h - 2*fee  (si side='long')
              y_short_pnl = -future_ret_h - 2*fee (si side='short')

    Règle de trading : si pnl_pred > fee + margin ET pnl_pred ∈ top top_pct% → trade.
    Métriques primaires : profit_factor, Sharpe, avg_pnl, n_trades, trade_rate.
    """
    from core.labels_pnl import (
        build_pnl_labels, pnl_label_stats,
        top_percentile_filter, calibrate_regression_margin,
    )

    side_up   = side.upper()
    label_col = f"y_{side}_pnl"
    print_section(f"RÉGRESSION PnL — {side_up}  (XGBRegressor, top {top_pct:.1%})")

    # ── Labels PnL ────────────────────────────────────────────────────────────
    df = build_pnl_labels(df, fee=fee)
    stats = pnl_label_stats(df)
    print(f"   {label_col} : mean={stats[label_col]['mean']:+.4f}  "
          f"std={stats[label_col]['std']:.4f}  "
          f"pos={stats[label_col]['pct_positive']:.1%}")

    # ── Features ──────────────────────────────────────────────────────────────
    from ai.level_0.features import FEATURES_LONG, FEATURES_SHORT
    feats = feature_list or (FEATURES_LONG if side == "long" else FEATURES_SHORT)
    feats = [f for f in feats if f in df.columns]
    print(f"   Features disponibles : {len(feats)}/{len(feature_list or feats)}")

    X_train = df.loc[train_mask, feats].fillna(0.0).values.astype(np.float32)
    y_train = df.loc[train_mask, label_col].values.astype(np.float64)
    X_val   = df.loc[val_mask,   feats].fillna(0.0).values.astype(np.float32)
    y_val   = df.loc[val_mask,   label_col].values.astype(np.float64)
    X_test  = df.loc[test_mask,  feats].fillna(0.0).values.astype(np.float32)

    scaler = fit_scaler(X_train)
    X_tr_sc  = scaler.transform(X_train)
    X_val_sc = scaler.transform(X_val)
    X_te_sc  = scaler.transform(X_test)

    # ── XGBRegressor ─────────────────────────────────────────────────────────
    try:
        from xgboost import XGBRegressor
        reg = XGBRegressor(
            n_estimators=600, max_depth=4,
            learning_rate=0.04, subsample=0.8, colsample_bytree=0.8,
            objective="reg:squarederror",
            n_jobs=-1, random_state=42,
        )
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        reg = HistGradientBoostingRegressor(
            learning_rate=0.04, max_iter=600, max_depth=4, random_state=42,
        )

    reg.fit(X_tr_sc, y_train)

    # ── Calibration de la margin sur val ──────────────────────────────────────
    p_val = reg.predict(X_val_sc)
    margin_cal = calibrate_regression_margin(
        p_val, y_val, fee=fee, target_win_rate=0.55, min_trades=30,
    )
    print(f"   Margin calibrée sur val : {margin_cal:.4f}  (win_rate ≥ 55%)")

    # ── Backtest test ─────────────────────────────────────────────────────────
    p_test   = reg.predict(X_te_sc)
    ret_sign = +1.0 if side == "long" else -1.0
    raw_rets = df.loc[test_mask, "future_ret_h"].values.astype(np.float64)

    # Filtre dual : margin + top percentile
    mask_margin = p_test > (fee + margin_cal)
    mask_top    = top_percentile_filter(p_test, top_pct=top_pct)
    trade_mask  = mask_margin & mask_top

    n_bars      = len(p_test)
    n_trades    = int(trade_mask.sum())
    trade_rate  = n_trades / max(n_bars, 1)

    trade_rets = (raw_rets[trade_mask] * ret_sign - fee)
    wins       = int((trade_rets > 0).sum())
    win_rate   = wins / max(n_trades, 1)
    total_pnl  = float(trade_rets.sum())
    avg_pnl    = float(trade_rets.mean()) if n_trades > 0 else 0.0

    gross_wins  = float(trade_rets[trade_rets > 0].sum()) if wins > 0 else 0.0
    gross_loss  = float(abs(trade_rets[trade_rets < 0].sum())) if n_trades - wins > 0 else 1e-9
    pf = gross_wins / max(gross_loss, 1e-9)

    r = trade_rets
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(8760)) if (len(r) > 1 and np.std(r) > 1e-12) else 0.0

    eq = np.concatenate([[10_000.0], 10_000.0 * (1 + np.cumsum(trade_rets))])
    peak = np.maximum.accumulate(eq)
    mdd  = float(np.max((peak - eq) / np.maximum(peak, 1e-9)))

    print(f"\n   ── Régression {side_up} (test) ───────────────────────────────────")
    print(f"   Trades : {n_trades:,} / {n_bars:,}  ({trade_rate:.2%} des barres)")
    print(f"   PF     : {pf:.3f}   WR={win_rate:.1%}   avg_pnl={avg_pnl:+.4f}")
    print(f"   Sharpe : {sharpe:.3f}   MDD={mdd:.2%}   total_pnl={total_pnl:+.4f}")

    if pf < 1.0:
        print(f"   ⚠  PF < 1.0 — edge insuffisant ou margin/top_pct trop laxiste")

    metrics = {
        "side": side, "label": label_col, "fee": fee,
        "margin_calibrated": round(margin_cal, 5),
        "top_pct": top_pct,
        "n_bars_test": n_bars, "n_trades": n_trades, "trade_rate": round(trade_rate, 5),
        "profit_factor": round(pf, 4), "win_rate": round(win_rate, 4),
        "avg_pnl": round(avg_pnl, 6), "total_pnl": round(total_pnl, 6),
        "sharpe_annualized": round(sharpe, 4), "max_drawdown": round(mdd, 4),
        "label_stats": stats.get(label_col, {}),
        "n_features": len(feats),
    }

    import pickle
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"regressor_{side}.pkl", "wb") as f: pickle.dump(reg, f)
    with open(out_dir / f"scaler_{side}.pkl",    "wb") as f: pickle.dump(scaler, f)
    json_dump(out_dir / f"metrics_{side}.json", metrics)

    return {
        "regressor": reg, "scaler": scaler, "features": feats,
        "margin": margin_cal, "top_pct": top_pct,
        "metrics": metrics,
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    args    = parse_args()

    run_id  = time.strftime("%Y%m%d-%H%M%S")
    out     = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    # ── Config centralisée ────────────────────────────────────────────────────
    cfg = PipelineConfig(
        tradeable_quantile=args.tradeable_q,
        cost_pct=args.cost,
        filter_threshold_long=args.filter_thr_long,
        direction_threshold_long=args.direction_thr_long,
        filter_threshold_short=args.filter_thr_short,
        direction_threshold_short=args.direction_thr_short,
        risk_per_trade_long=args.risk_long,
        risk_per_trade_short=args.risk_short,
        max_consecutive_losses_long=args.max_losses_long,
        max_consecutive_losses_short=args.max_losses_short,
        cooldown_bars_long=args.cooldown_long,
        cooldown_bars_short=args.cooldown_short,
        enable_long=args.mode in ("long", "combined"),
        enable_short=args.mode in ("short", "combined") and not args.no_short,
    )

    mode = args.mode
    if args.no_short:
        mode = "long"

    print_section("PIPELINE ML — LONG / SHORT SÉPARÉS  (horizon=60min)")
    print(f"  Mode            : {mode.upper()}")
    print(f"  Data            : {args.data}")
    print(f"  Sortie          : {out}")
    print(f"  Test from       : {args.test_from}")
    print(f"  Tradeable q     : {cfg.tradeable_quantile:.2f}")
    print(f"  Cost            : {cfg.cost_pct:.4%}")
    print(f"\n  Seuils LONG     : filter={cfg.filter_threshold_long}  direction={cfg.direction_threshold_long}")
    print(f"  Seuils SHORT    : filter={cfg.filter_threshold_short}  direction={cfg.direction_threshold_short}")
    print(f"\n  Risk LONG       : {cfg.risk_per_trade_long:.3%}  max_losses={cfg.max_consecutive_losses_long}  cooldown={cfg.cooldown_bars_long}b")
    print(f"  Risk SHORT      : {cfg.risk_per_trade_short:.3%}  max_losses={cfg.max_consecutive_losses_short}  cooldown={cfg.cooldown_bars_short}b")
    print(f"\n  Short activé    : {cfg.enable_short}")
    print(f"  TCN             : {'non' if args.skip_tcn else 'oui (long uniquement)'}")
    print(f"  Grid sweep      : {'oui' if args.grid else 'non'}")
    if getattr(args, "regression", False):
        print(f"  Mode            : RÉGRESSION PnL  (top {args.top_pct:.1%}, margin={args.margin:.4f})")

    # ── Chargement ────────────────────────────────────────────────────────────
    print_section("CHARGEMENT DES DONNÉES")
    df = load_csv(args.data)

    # ── Splits ────────────────────────────────────────────────────────────────
    print_section("SPLIT CHRONOLOGIQUE")
    train_mask, val_mask, test_mask = chronological_split(df, args.test_from)

    # ── Mode régression PnL (--regression) ───────────────────────────────────
    if getattr(args, "regression", False):
        reg_out = out / "regression"
        sides = []
        if cfg.enable_long:  sides.append("long")
        if cfg.enable_short: sides.append("short")
        reg_results = {}
        for s in sides:
            reg_results[s] = train_pnl_regressor(
                df=df,
                train_mask=train_mask,
                val_mask=val_mask,
                test_mask=test_mask,
                side=s,
                out_dir=reg_out,
                top_pct=args.top_pct,
                margin=args.margin,
                fee=cfg.cost_pct,
            )
        json_dump(out / "regression_summary.json", {
            s: v["metrics"] for s, v in reg_results.items()
        })
        elapsed = time.time() - t_start
        print_section(f"DONE — régression PnL  ({elapsed:.1f}s)")
        for s, v in reg_results.items():
            m = v["metrics"]
            print(f"  {s.upper():5s}  PF={m['profit_factor']:.3f}  "
                  f"Sharpe={m['sharpe_annualized']:.3f}  "
                  f"WR={m['win_rate']:.1%}  "
                  f"trades={m['n_trades']:,}  ({m['trade_rate']:.3%})")
        return

    # ── Labels séparés LONG / SHORT ───────────────────────────────────────────
    print_section("CONSTRUCTION DES LABELS  (long / short / tradeable — asymétriques)")
    # Utiliser la factory core.labels avec paramètres asymétriques
    from core.labels import build_labels as _build_labels_core
    df, label_stats = _build_labels_core(
        df=df,
        train_mask=train_mask,
        tradeable_quantile=cfg.tradeable_quantile,
        cost_pct=cfg.cost_pct,
        use_reversal_filter=True,
        use_regime_filter=True,
    )
    json_dump(out / "labels.json", label_stats)

    # ── Diagnostic régime par split ────────────────────────────────────────────
    print_section("DIAGNOSTIC RÉGIME SHORT")
    regime_report = diagnose_regime_distribution(df, val_mask, test_mask)
    json_dump(out / "regime_distribution.json", regime_report)

    # ── Stage 1 : filtre tradeable (partagé, class-balanced) ─────────────────
    filter_clf, filter_scaler, filter_metrics = train_filter_model(
        df, train_mask, val_mask, out_dir=out / "filter"
    )

    # ── Seuils filtre calibrés sur val — toujours appliqués ─────────────────
    # La calibration a des guardes dures (floor + precision) donc c'est sûr.
    # --auto-calibrate est conservé pour compatibilité CLI mais n'a plus d'effet.
    if "calibrated_threshold_long" in filter_metrics:
        cal_thr_long  = filter_metrics["calibrated_threshold_long"]
        cal_thr_short = filter_metrics["calibrated_threshold_short"]
        print(f"\n   [filtre calibré] seuils appliqués :")
        print(f"     LONG  : {cfg.filter_threshold_long} → {cal_thr_long}")
        print(f"     SHORT : {cfg.filter_threshold_short} → {cal_thr_short}")
        from dataclasses import replace as dc_replace
        cfg = dc_replace(cfg,
                         filter_threshold_long=cal_thr_long,
                         filter_threshold_short=cal_thr_short)

    # ── Stage 2 : modèles directionnels ──────────────────────────────────────
    long_result  = None
    short_result = None

    if cfg.enable_long:
        long_result = train_long_model(
            df=df,
            train_mask=train_mask,
            val_mask=val_mask,
            out_dir=out / "long",
            train_tcn=not args.skip_tcn,
        )

    if cfg.enable_short:
        short_result = train_short_model(
            df=df,
            train_mask=train_mask,
            val_mask=val_mask,
            out_dir=out / "short",
        )

    # ── Méta-modèle de régime bear (gate short) ───────────────────────────────
    bear_regime_result = None
    if cfg.enable_short:
        print_section("META-MODÈLE RÉGIME BEAR  (gate du short)")
        from core.features import FEATURES_REGIME as _FEAT_REGIME
        try:
            bear_regime_result = train_bear_regime_model(
                df=df,
                train_mask=train_mask,
                val_mask=val_mask,
                out_dir=out / "regime",
                horizon_bars=72,       # 3 jours
                bear_threshold_pct=-0.02,
                features=_FEAT_REGIME,
            )
        except Exception as e:
            print(f"   ⚠  BearRegime échoué : {e} — short sans gate régime")

    # ── Calibration du seuil directionnel sur val (post-entraînement) ────────
    from dataclasses import replace as dc_replace
    if long_result:
        try:
            from core.preprocessing import get_X as _get_X
            from core.features import FEATURES_LONG as _FEATS_LONG_DIR
            _feats_long = long_result.get("features") or _FEATS_LONG_DIR
            X_val_long  = _get_X(df, val_mask, _feats_long) if hasattr(_get_X, "__call__") else df.loc[val_mask, _feats_long].fillna(0.0).values.astype(np.float32)
            y_val_long  = df.loc[val_mask, "y_long"].values.astype(np.int32)
            valid_l     = y_val_long >= 0
            p_val_long  = long_result["best_model"].predict_proba(
                              long_result["scaler"].transform(X_val_long[valid_l]))[:, 1]
            cal_dir_long = _calibrate_direction_threshold(
                p_val_long, y_val_long[valid_l], min_threshold=0.52, min_trades=30)
            print(f"   [direction calibré] LONG  : {cfg.direction_threshold_long} → {cal_dir_long}")
            cfg = dc_replace(cfg, direction_threshold_long=cal_dir_long)
        except Exception as e:
            print(f"   [direction calibré] LONG  : erreur ({e}) — seuil inchangé")

    # ── Stage 3 : experts par contexte de marché ──────────────────────────────
    specialist_predictor = None
    if long_result or short_result:
        print_section("STAGE 3 — EXPERTS PAR CONTEXTE DE MARCHÉ")
        try:
            specialist_predictor = _train_stage3(
                df=df,
                train_mask=train_mask,
                val_mask=val_mask,
                out_dir=out / "specialists",
                enable_long=bool(long_result),
                enable_short=bool(short_result),
            )
        except Exception as e:
            print(f"   ⚠  Stage 3 échoué : {e} — backtests sans specialists")
            specialist_predictor = None

    # ── Backtests ─────────────────────────────────────────────────────────────
    long_bt = short_bt = combined_bt = None

    if long_result:
        from core.features import FEATURES_LONG as _FEAT_LONG_BT
        long_bt = run_backtest_side(
            df=df, test_mask=test_mask,
            filter_model=filter_clf, filter_scaler=filter_scaler,
            edge_model=long_result["best_model"],
            edge_scaler=long_result["scaler"],
            side="long", cfg=cfg,
            out_dir=out / "backtest_long",
            model_label=f"{long_result.get('best_tabular', long_result.get('best_model_name', 'best'))}_long",
            edge_features=_FEAT_LONG_BT,
            specialist_predictor=specialist_predictor,
        )

    # ── Validation de stabilité short inter-années ────────────────────────────
    require_stability = args.require_short_stability or args.no_short_if_unstable
    short_disabled_reason = None

    # ── Calibration short dédiée (Étape 4) ───────────────────────────────────
    short_calibrator = None
    short_threshold_calibrated = cfg.direction_threshold_short

    if short_result:
        from core.features import FEATURES_SHORT as _FEAT_SHORT_BT
        _bear_model   = bear_regime_result["model"]   if bear_regime_result else None
        _bear_scaler  = bear_regime_result["scaler"]  if bear_regime_result else None
        _bear_feats   = bear_regime_result["features"] if bear_regime_result else None
        _bear_thr     = bear_regime_result["threshold"] if bear_regime_result else 0.70
        short_bt = run_backtest_side(
            df=df, test_mask=test_mask,
            filter_model=filter_clf, filter_scaler=filter_scaler,
            edge_model=short_result["best_model"],
            edge_scaler=short_result["scaler"],
            side="short", cfg=cfg,
            out_dir=out / "backtest_short",
            model_label=f"{short_result.get('best_tabular', short_result.get('best_model_name', 'best'))}_short",
            edge_features=_FEAT_SHORT_BT,
            regime_model=_bear_model,
            regime_scaler=_bear_scaler,
            regime_features=_bear_feats,
            regime_threshold=_bear_thr,
            specialist_predictor=specialist_predictor,
        )

        # Calibration short (Platt + sweep 0.55-0.90 + precision*sqrt(n))
        print_section("CALIBRATION SHORT ASYMÉTRIQUE")
        try:
            short_calibrator, short_cal_metrics = calibrate_short_model(
                clf=short_result["best_model"],
                scaler=short_result["scaler"],
                df=df,
                val_mask=val_mask,
                method="platt",
                filter_by_regime=True,
                out_dir=out / "short",
            )
            short_threshold_calibrated = short_cal_metrics["recommended_threshold"]
            print(f"   Seuil short calibré : {short_threshold_calibrated:.3f}")
            json_dump(out / "short_calibration.json", short_cal_metrics)
        except Exception as e:
            print(f"   ⚠  Calibration short échouée : {e}")

        # Walk-forward impitoyable (Étape 5)
        print_section("WALK-FORWARD SHORT — VALIDATION ROBUSTESSE")
        try:
            from core.features import FEATURES_SHORT as _FEATURES_SHORT
            wf_report = run_wf_backtest_short(
                df=df,
                clf=short_result["best_model"],
                scaler=short_result["scaler"],
                features=_FEATURES_SHORT,
                calibrator=short_calibrator,
                threshold=short_threshold_calibrated,
                clf_filter=filter_clf,
                scaler_filter=filter_scaler,
                filter_features=SNAPSHOT_FEATURES,
                filter_threshold=cfg.filter_threshold_short,
                base_cost_pct=cfg.cost_pct,
                initial_equity=INITIAL_EQUITY,
                n_folds=6,
                test_months=6,
                verbose=True,
                clf_regime=_bear_model,
                scaler_regime=_bear_scaler,
                features_regime=_bear_feats,
                regime_threshold=_bear_thr,
            )
            json_dump(out / "short_wf_robustness.json", wf_report.to_dict())

            # Gate de déploiement
            if not wf_report.deploy_short:
                short_disabled_reason = f"wf_rejected: {wf_report.reject_reason}"
                if require_stability or args.no_short_if_unstable:
                    print(f"\n   ⚠  SHORT DÉSACTIVÉ par walk-forward : {wf_report.reject_reason}")
                    short_result = None
                    short_bt = None
        except Exception as e:
            print(f"   ⚠  Walk-forward short échoué : {e}")
            wf_report = None

        # Stabilité inter-années legacy (si pas de WF)
        if short_result and require_stability and short_bt:
            short_ok, short_stable = _check_short_stability(short_bt)
            if not short_ok:
                print(f"\n   ⚠  SHORT DÉSACTIVÉ (legacy stability) : {short_stable}")
                short_bt = None
                short_result = None
                short_disabled_reason = short_stable

    if long_result and short_result:
        from core.features import FEATURES_SHORT as _FEAT_SHORT_COMB
        from core.features import FEATURES_LONG  as _FEAT_LONG_COMB
        combined_bt = run_backtest_combined(
            df=df, test_mask=test_mask,
            filter_model=filter_clf, filter_scaler=filter_scaler,
            long_model=long_result["best_model"],
            long_scaler=long_result["scaler"],
            short_model=short_result["best_model"],
            short_scaler=short_result["scaler"],
            cfg=cfg,
            out_dir=out / "backtest_combined",
            short_edge_features=_FEAT_SHORT_COMB,
            long_edge_features=_FEAT_LONG_COMB,
            regime_model=_bear_model,
            regime_scaler=_bear_scaler,
            regime_features=_bear_feats,
            regime_threshold=_bear_thr,
            specialist_predictor=specialist_predictor,
        )

    # ── Tableau comparatif ────────────────────────────────────────────────────
    print_comparison_table({"long": long_bt, "short": short_bt, "combined": combined_bt})

    # ── Comparaison des modèles tabulaires (optionnel) ────────────────────────
    if args.compare_models:
        if long_result:
            compare_models_side(df, test_mask, filter_clf, filter_scaler,
                                long_result, cfg, out_dir=out / "backtest_long",
                                side="long")
        if short_result:
            compare_models_side(df, test_mask, filter_clf, filter_scaler,
                                short_result, cfg, out_dir=out / "backtest_short",
                                side="short")

    # ── Grid sweep (optionnel, par branche) ───────────────────────────────────
    if args.grid:
        if long_result:
            run_grid(df, test_mask, filter_clf, filter_scaler,
                     long_result, cfg, out_dir=out)
        if short_result:
            run_grid(df, test_mask, filter_clf, filter_scaler,
                     short_result, cfg, out_dir=out)

    # ── Résumé ────────────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    summary = {
        "run_id": run_id,
        "mode": mode,
        "data": args.data,
        "test_from": args.test_from,
        "config": asdict(cfg),
        "label_stats": label_stats,
        "filter_metrics": filter_metrics,
        "regime_report": regime_report if "regime_report" in dir() else None,
        "edge_long_metrics":    long_result["all_metrics"]  if long_result  else None,
        "edge_short_metrics":   short_result["all_metrics"] if short_result else None,
        "short_calibration": {
            "threshold_calibrated": short_threshold_calibrated,
            "method": "platt",
        } if short_result else None,
        "short_wf_robustness": wf_report.to_dict() if ("wf_report" in dir() and wf_report) else None,
        "backtest_long":        long_bt,
        "backtest_short":       short_bt,
        "backtest_combined":    combined_bt,
        "short_disabled_reason": short_disabled_reason,
        "auto_calibrate_used":  True,   # filtre toujours calibré sur val
        "elapsed_sec":          round(elapsed, 1),
    }
    json_dump(out / "pipeline_summary.json", summary)

    print_section(f"Pipeline terminé en {elapsed/60:.1f} min  —  {out}")


if __name__ == "__main__":
    main()
