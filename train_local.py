#!/usr/bin/env python3
"""
train_local.py — entraînement local robuste sur CSV enrichi
=========================================================

Objectifs de cette version :
- arrêter de relabeliser Level 0 avec une logique externe incohérente
- utiliser directement les labels du CSV enrichi pour Level 0 et Level 1
- remplacer le Level 0 linéaire fragile par un vrai modèle tabulaire non linéaire
- construire de vraies features de fenêtre pour Level 0
- ajouter des logs de diagnostic complets et sauvegarder les artefacts même en cas d'échec du gate
- conserver un Level 1 séquentiel compatible avec le pipeline existant

Hypothèses d'entrée :
- le CSV a été produit par build_binance_features.py
- il contient au minimum :
  - les colonnes de FEATURE_KEYS
  - label_regime_3
  - label_tradeable
  - future_ret_h / future_rv_h / future_dd_h

Usage :
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv --skip-event
    python ~/futur/train_local.py --data ~/futur/data/ --out ~/futur/runs/local
    python ~/futur/train_local.py --data ~/futur/data/BTCUSD_1h_features.csv --years 2022,2023,2024
"""
from __future__ import annotations

import json
import sys
import time
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import warnings
import numpy as np
import pandas as pd

from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import f1_score, precision_recall_fscore_support, confusion_matrix
warnings.filterwarnings("ignore", category=ConvergenceWarning)

FUTUR = Path(__file__).parent
sys.path.insert(0, str(FUTUR / "ai" / "models"))

from training.common.scaler import RobustScaler, ReservoirSampler
from level_1.Event_Classifier import EventClassifier, EventClassifierConfig

import tensorflow as tf

# ── GPU setup ────────────────────────────────────────────────────────────────
gpus = tf.config.list_physical_devices("GPU")
if gpus:
    try:
        tf.config.set_logical_device_configuration(
            gpus[0], [tf.config.LogicalDeviceConfiguration(memory_limit=6144)]
        )
        from tensorflow.keras import mixed_precision
        mixed_precision.set_global_policy("mixed_float16")
        print(f"✅ GPU : {gpus[0].name}  |  FP16 mixed precision")
    except RuntimeError as e:
        print(f"⚠  GPU config : {e}")
else:
    print("⚠  Pas de GPU détecté — CPU utilisé")


# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CFG:
    lookback: int = 128
    horizon: int = 12
    stride: int = 4

    train_frac: float = 0.80
    val_frac: float = 0.10

    batch_size: int = 128
    epochs: int = 60

    lr: float = 3e-4
    min_lr: float = 5e-6
    weight_decay: float = 1e-4
    clip_norm: float = 1.0

    scaler_sample_max: int = 250_000

    reduce_lr_patience: int = 3
    reduce_lr_factor: float = 0.5
    early_stop_patience: int = 6
    min_delta: float = 1e-4

    n_regimes: int = 3  # 0=bear 1=neutral 2=bull
    seed: int = 1337

    # Level 0 gate
    min_bull_recall: float = 0.25
    min_macro_f1: float = 0.40

    # Level 0 model params
    l0_learning_rate: float = 0.05
    l0_max_iter: int = 500
    l0_max_depth: int = 6
    l0_min_samples_leaf: int = 40
    l0_l2: float = 1e-3


# ═════════════════════════════════════════════════════════════════════════════
# NOTE DATASET — data/bundle_btc/features_merged.parquet
# ─────────────────────────────────────────────────────────────────────────────
# Source     : Binance Vision klines BTCUSDT 1m (mensuel + quotidien)
# Couverture : 2017-08-17 → 2026-04-16  |  4 548 799 barres 1m  |  123 colonnes
# Format     : parquet zstd float32, ~640 MB sur disque
#
# Ce script travaille à 1h : _bundle_parquet_to_1h_local rééchantillonne le
# bundle et recalcule tous les FEATURE_KEYS directement (sans dépendance externe).
#
# Mapping bundle → architecture Level 0 / Level 1 (ce script) :
#   Level 0 (Regime Classifier — HistGradientBoosting sur fenêtres 128 barres) :
#     Input : vecteur ~180 features par fenêtre (snapshot + agrégats multi-fenêtres)
#     Colonnes utilisées : FEATURE_KEYS (36 cols 1h)
#     Label : label_regime_3 (0=bear / 1=neutral / 2=bull) sur future_ret_h
#   Level 1 (Event Classifier — TCN 4 couches, d_model=128) :
#     Input : séquence (128, 36) — 128 barres 1h × FEATURE_KEYS
#     Label : même label_regime_3 filtré par label_tradeable
#
# Colonnes bundle IGNORÉES ici (opportunité d'extension Level 0/1) :
#   funding_rate_z_*, oihist_sumOpenInterest_z_*, global_ls_longShortRatio_z_*,
#   fear_greed_value, news_count_roll_*
#   → Ces signaux macro existent sur l'ensemble du dataset et pourraient
#     enrichir FEATURE_KEYS pour améliorer la détection de régime (bear market
#     structurel vs consolidation).  Ajouter ~8-10 cols augmenterait le signal
#     du Level 0 sans risque d'overfitting sur la fenêtre de 128 barres.
#
# Split temporel :
#   train ≤ 2022  (~43k barres 1h)  |  val = 2023  |  test ≥ 2024
# ═════════════════════════════════════════════════════════════════════════════

FEATURE_KEYS = [
    "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
    "ret", "log_ret", "hl_log_range", "co_log_ret",
    "rv_12", "rv_24", "rv_72", "rv_168",
    "rv_ratio_12_48", "rv_ratio_24_72",
    "ema_20", "dist_ema_20", "ema_50", "dist_ema_50",
    "ema_200", "dist_ema_200",
    "ema_spread_20_50", "ema_spread_50_200",
    "rsi_14", "atr_14", "atr_pct_14", "cci_20",
    "boll_pos_20", "boll_width_20",
    "taker_buy_ratio_base", "delta_taker_pressure",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]

# ── Feature packs pour ablation ──────────────────────────────────────────────
FEATURE_PACKS: Dict[str, List[str]] = {
    "all": FEATURE_KEYS,
    "price_vol": [
        "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
        "ret", "log_ret", "hl_log_range", "co_log_ret",
        "rv_12", "rv_24", "rv_72", "rv_168",
        "rv_ratio_12_48", "rv_ratio_24_72",
        "atr_14", "atr_pct_14",
        "ema_20", "dist_ema_20", "ema_50", "dist_ema_50",
        "ema_200", "dist_ema_200",
        "boll_pos_20", "boll_width_20",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ],
    "momentum": [
        "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
        "ret", "log_ret", "hl_log_range", "co_log_ret",
        "rv_12", "rv_24", "rv_72", "rv_168",
        "rv_ratio_12_48", "rv_ratio_24_72",
        "atr_14", "atr_pct_14",
        "ema_20", "dist_ema_20", "ema_50", "dist_ema_50",
        "ema_200", "dist_ema_200",
        "ema_spread_20_50", "ema_spread_50_200",
        "rsi_14", "cci_20",
        "boll_pos_20", "boll_width_20",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ],
    "flow": [
        "Open", "High", "Low", "Close", "Volume", "Quote_Volume",
        "ret", "log_ret", "hl_log_range", "co_log_ret",
        "rv_12", "rv_24", "rv_72", "rv_168",
        "rv_ratio_12_48", "rv_ratio_24_72",
        "atr_14", "atr_pct_14",
        "ema_20", "dist_ema_20", "ema_50", "dist_ema_50",
        "ema_200", "dist_ema_200",
        "ema_spread_20_50", "ema_spread_50_200",
        "rsi_14", "cci_20",
        "boll_pos_20", "boll_width_20",
        "taker_buy_ratio_base", "delta_taker_pressure",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ],
}

RET_KEY = "log_ret"
RV_KEY = "rv_24"
CLOSE_KEY = "Close"
CLASS_NAMES = ["bear", "neutral", "bull"]
CLASS_ID_TO_NAME = {0: "bear", 1: "neutral", 2: "bull"}


# ═════════════════════════════════════════════════════════════════════════════
# UTILS
# ═════════════════════════════════════════════════════════════════════════════
def json_dump(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def count_windows(df: pd.DataFrame, cfg: CFG) -> int:
    return max(0, len(df) - cfg.lookback - cfg.horizon)


def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    if fut_ret.size == 0:
        return 0.0, 0.0
    path = np.cumsum(fut_ret.astype(np.float64))
    r_total = float(path[-1])
    peak = np.maximum.accumulate(path)
    dd = peak - path
    max_dd = float(np.max(dd)) if dd.size else 0.0
    return r_total, max_dd


def rms_vol(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    z = x.astype(np.float64)
    return float(np.sqrt(np.mean(z * z)))


def safe_float(x: float) -> float:
    if np.isnan(x) or np.isinf(x):
        return 0.0
    return float(x)


def summarize_counts(values: np.ndarray) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, name in CLASS_ID_TO_NAME.items():
        out[name] = int((values == i).sum())
    return out


def apply_binary_mode(df: pd.DataFrame) -> pd.DataFrame:
    """
    Supprime les exemples neutres (label=1) pour ne garder que bear(0) vs bull(2).
    Remape bear→0, bull→1 pour un problème binaire propre.
    """
    df = df[df["label_regime_3"] != 1].copy().reset_index(drop=True)
    df["label_regime_3"] = (df["label_regime_3"] == 2).astype(np.int32)  # bear=0, bull=1
    counts = {0: int((df["label_regime_3"] == 0).sum()), 1: int((df["label_regime_3"] == 1).sum())}
    total = len(df)
    print(f"   [binary]  bear={counts[0]} ({counts[0]/total:.1%})  bull={counts[1]} ({counts[1]/total:.1%})  "
          f"→ {total:,} exemples conservés (neutral supprimé)")
    return df


def rebuild_label_regime_3(df: pd.DataFrame, version: str, thr: float = 0.0) -> pd.DataFrame:
    """
    Recrée label_regime_3 à partir de future_ret_h (plus directionnel).

    version="quantile" : bear ≤ q33, bull ≥ q67, neutral sinon.
                         Cible ~33/33/33 mais frontières adaptatives.
    version="threshold": bear ≤ -thr, bull ≥ +thr, neutral sinon.
                         thr auto-calibré si thr==0 pour obtenir ~25-30% extrêmes.
    """
    df = df.copy()
    ret = df["future_ret_h"].values.astype(np.float64)

    if version == "quantile":
        q33 = float(np.quantile(ret, 0.33))
        q67 = float(np.quantile(ret, 0.67))
        labels = np.ones(len(ret), dtype=np.int32)  # neutral
        labels[ret <= q33] = 0  # bear
        labels[ret >= q67] = 2  # bull
        print(f"   [label/quantile]  q33={q33:.5f}  q67={q67:.5f}")

    elif version == "threshold":
        if thr <= 0.0:
            # auto-calibre pour 25-30% dans chaque queue
            thr = float(np.quantile(np.abs(ret), 0.70))
        labels = np.ones(len(ret), dtype=np.int32)  # neutral
        labels[ret <= -thr] = 0  # bear
        labels[ret >= thr] = 2   # bull
        print(f"   [label/threshold]  thr={thr:.5f}")

    else:
        raise ValueError(f"version de label inconnue : {version!r}  (quantile | threshold)")

    df["label_regime_3"] = labels.astype(np.int32)
    counts = summarize_counts(labels)
    total = len(labels)
    print(f"   Distribution nouveau label : {counts}  "
          f"(bear={counts['bear']/total:.1%}  "
          f"neutral={counts['neutral']/total:.1%}  "
          f"bull={counts['bull']/total:.1%})")
    return df


def linear_slope(y: np.ndarray) -> float:
    if y.size < 2:
        return 0.0
    x = np.arange(y.size, dtype=np.float64)
    x = x - x.mean()
    yy = y.astype(np.float64) - float(np.mean(y))
    denom = float(np.sum(x * x))
    if denom <= 0:
        return 0.0
    return float(np.sum(x * yy) / denom)


def rolling_zscore_last(y: np.ndarray) -> float:
    if y.size < 2:
        return 0.0
    m = float(np.mean(y))
    s = float(np.std(y))
    if s <= 1e-12:
        return 0.0
    return float((float(y[-1]) - m) / s)


# ═════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════════════

def _bundle_parquet_to_1h_local(path: Path) -> pd.DataFrame:
    """
    Charge features_merged.parquet (1m, 123 cols), rééchantillonne à 1h et
    calcule tous les FEATURE_KEYS + labels requis par train_local.py.

    NOTE ARCHITECTURE — correspondance bundle → niveaux :
      • OHLCV 1m → rééchantillonné à 1h : alimentation Level 0 (Regime) & Level 1 (TCN)
      • rv_*/vol_z_* (1m) : ignorés ici, recalculés sur 1h après resample
      • funding_rate / global_ls / fear_greed (123 cols bundle) : pas encore
        utilisés par train_local — à intégrer dans FEATURE_KEYS pour exploiter
        le signal macro/sentiment (Level 3 Specialists est le bon endroit)
      • Couverture 2017-08→2026-04 : split train≤2022 / val=2023 / test≥2024
        donne ~5 ans train, 1 an val, 2+ ans test — ratio sain pour Level 0/1
    """
    import pandas as pd
    import numpy as np

    import pyarrow.parquet as _pq
    from ai.level_0.live_features import MACRO_BUNDLE_COLS

    _OHLCV_COLS = [
        "datetime", "open", "high", "low", "close", "volume",
        "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume",
    ]
    _avail = set(_pq.read_schema(path).names)
    _macro_present = [c for c in MACRO_BUNDLE_COLS if c in _avail]

    print(f"   Bundle parquet détecté ({path.name}) → resample 1m→1h…")
    raw = pd.read_parquet(path, columns=_OHLCV_COLS + _macro_present)
    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True, format="ISO8601")
    raw = raw.set_index("datetime").sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]

    # ── Resample 1m → 1h ─────────────────────────────────────────────────────
    df = pd.DataFrame({
        "Open":          raw["open"].resample("1h").first(),
        "High":          raw["high"].resample("1h").max(),
        "Low":           raw["low"].resample("1h").min(),
        "Close":         raw["close"].resample("1h").last(),
        "Volume":        raw["volume"].resample("1h").sum(),
        "Quote_Volume":  raw["quote_asset_volume"].resample("1h").sum(),
        "_taker_base":   raw["taker_buy_base_asset_volume"].resample("1h").sum(),
        "_vol_base":     raw["volume"].resample("1h").sum(),
    }).dropna(subset=["Open", "Close"])
    print(f"   {len(df):,} barres 1h ({df.index[0].date()} → {df.index[-1].date()})")

    c = df["Close"]
    h, l, o = df["High"], df["Low"], df["Open"]

    # ── Returns ───────────────────────────────────────────────────────────────
    df["ret"]          = c.pct_change()
    df["log_ret"]      = np.log(c / c.shift(1))
    df["hl_log_range"] = np.log(h) - np.log(l)
    df["co_log_ret"]   = np.log(c) - np.log(o)

    # ── Realized volatility (1h bars) ─────────────────────────────────────────
    lr = df["log_ret"]
    for w in [12, 24, 72, 168]:
        df[f"rv_{w}"] = lr.rolling(w, min_periods=max(3, w // 5)).std()
    df["rv_ratio_12_48"] = df["rv_12"] / df["rv_24"].replace(0, np.nan)
    df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].replace(0, np.nan)

    # ── EMAs & distances ──────────────────────────────────────────────────────
    for span in [20, 50, 200]:
        ema = c.ewm(span=span, adjust=False).mean()
        df[f"ema_{span}"]      = ema
        df[f"dist_ema_{span}"] = (c - ema) / ema.replace(0, np.nan)
    df["ema_spread_20_50"]  = (df["ema_20"] - df["ema_50"])  / c.replace(0, np.nan)
    df["ema_spread_50_200"] = (df["ema_50"] - df["ema_200"]) / c.replace(0, np.nan)

    # ── RSI(14) ───────────────────────────────────────────────────────────────
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # ── ATR(14) ───────────────────────────────────────────────────────────────
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df["atr_14"]     = tr.ewm(span=14, adjust=False).mean().ffill()
    df["atr_pct_14"] = df["atr_14"] / c.replace(0, np.nan)

    # ── CCI(20) ───────────────────────────────────────────────────────────────
    tp = (h + l + c) / 3
    ma = tp.rolling(20).mean()
    md = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci_20"] = (tp - ma) / (0.015 * md.replace(0, np.nan))

    # ── Bollinger(20, 2σ) ─────────────────────────────────────────────────────
    boll_mid = c.rolling(20).mean()
    boll_std = c.rolling(20).std()
    boll_up  = boll_mid + 2 * boll_std
    boll_dn  = boll_mid - 2 * boll_std
    boll_rng = (boll_up - boll_dn).replace(0, np.nan)
    df["boll_pos_20"]   = (c - boll_dn) / boll_rng
    df["boll_width_20"] = boll_rng / boll_mid.replace(0, np.nan)

    # ── Taker flow ────────────────────────────────────────────────────────────
    df["taker_buy_ratio_base"]  = df["_taker_base"] / df["_vol_base"].replace(0, np.nan)
    df["delta_taker_pressure"]  = df["taker_buy_ratio_base"].diff()
    df = df.drop(columns=["_taker_base", "_vol_base"])

    # ── Time encoding ─────────────────────────────────────────────────────────
    df["hour_sin"] = np.sin(2 * np.pi * df.index.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df.index.hour / 24)
    df["dow_sin"]  = np.sin(2 * np.pi * df.index.dayofweek / 7)
    df["dow_cos"]  = np.cos(2 * np.pi * df.index.dayofweek / 7)

    # ── Labels ────────────────────────────────────────────────────────────────
    log_c = np.log(c)
    df["future_ret_h"] = log_c.shift(-1) - log_c

    thr = df["future_ret_h"].abs().quantile(0.70)
    df["label_tradeable"] = (df["future_ret_h"].abs() > thr).astype(np.float32)

    ret_vals = df["future_ret_h"].values.astype(np.float64)
    q33 = float(np.nanquantile(ret_vals, 0.33))
    q67 = float(np.nanquantile(ret_vals, 0.67))
    lbl = np.ones(len(ret_vals), dtype=np.int32)
    lbl[ret_vals <= q33] = 0
    lbl[ret_vals >= q67] = 2
    df["label_regime_3"] = lbl

    # future_rv_h / future_dd_h : approximations (colonnes requises, non utilisées en training)
    df["future_rv_h"] = df["future_ret_h"].abs()
    df["future_dd_h"] = df["future_ret_h"].clip(upper=0).abs()

    # ── Macro features (bundle) — resample last + ffill ───────────────────────
    if _macro_present:
        macro_1h = raw[_macro_present].resample("1h").last().ffill().fillna(0.0)
        df = df.join(macro_1h, how="left")
        df[_macro_present] = df[_macro_present].ffill().fillna(0.0)

    df.index.name = "datetime"
    return df.dropna(subset=FEATURE_KEYS).reset_index()


def load_data(
    path_arg: str,
    years: Optional[List[int]] = None,
    label_version: str = "original",
    label_thr: float = 0.0,
) -> pd.DataFrame:
    p = Path(path_arg)

    # ── Bundle parquet ────────────────────────────────────────────────────────
    if p.suffix.lower() == ".parquet":
        raw = _bundle_parquet_to_1h_local(p)
    elif p.is_dir():
        files = sorted(p.glob("*features*.csv"))
        if not files:
            files = sorted(p.glob("*.csv"))
        if not files:
            raise RuntimeError(f"Aucun CSV dans {p}")
        print(f"📂 {len(files)} fichier(s) trouvé(s) dans {p}")
        frames = []
        for f in files:
            print(f"   └ {f.name}")
            frames.append(pd.read_csv(f, low_memory=False))
        raw = pd.concat(frames, ignore_index=True)
    else:
        print(f"📄 Chargement : {p.name}")
        raw = pd.read_csv(p, low_memory=False)

    required = FEATURE_KEYS + [
        "datetime", "label_regime_3", "label_tradeable",
        "future_ret_h", "future_rv_h", "future_dd_h",
    ]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise RuntimeError(
            "CSV enrichi invalide — colonnes manquantes : " + ", ".join(missing)
        )

    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True)
    df = raw.sort_values("datetime").reset_index(drop=True)

    numeric_cols = list(set(FEATURE_KEYS + ["label_regime_3", "label_tradeable", "future_ret_h", "future_rv_h", "future_dd_h"]))
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required).reset_index(drop=True)
    df["label_regime_3"] = df["label_regime_3"].astype(np.int32)
    df["label_tradeable"] = df["label_tradeable"].astype(np.float32)

    if years:
        df["_year"] = df["datetime"].dt.year
        df = df[df["_year"].isin(years)].reset_index(drop=True).drop(columns=["_year"])
        if df.empty:
            raise RuntimeError(f"Aucune donnée pour les années {years}")

    print(f"   {len(df):,} barres  |  {df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()}")

    if label_version != "original":
        print(f"   Rebuild label_regime_3  version={label_version!r}")
        df = rebuild_label_regime_3(df, version=label_version, thr=label_thr)

    return df


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 0 — WINDOW FEATURES
# ═════════════════════════════════════════════════════════════════════════════
def build_level0_window_features(df: pd.DataFrame, cfg: CFG) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """
    Transforme chaque fenêtre [i, i+lookback) en vecteur riche.
    Le label lu est celui de la dernière barre visible de la fenêtre,
    qui encode déjà le futur horizon suivant dans le CSV enrichi.
    """
    feature_matrix = df[FEATURE_KEYS].values.astype(np.float32)
    labels = df["label_regime_3"].values.astype(np.int32)
    tradeable = df["label_tradeable"].values.astype(np.float32)

    col_idx = {c: i for i, c in enumerate(FEATURE_KEYS)}

    selected_series = [
        "log_ret", "rv_24", "rv_72",
        "atr_pct_14", "rsi_14",
        "dist_ema_20", "dist_ema_50",
        "ema_spread_20_50",
        "taker_buy_ratio_base", "delta_taker_pressure",
        "Volume",
    ]

    windows = [24, 48, cfg.lookback]
    rows: List[List[float]] = []
    y: List[int] = []
    y_conf: List[float] = []
    names: List[str] = []
    names_ready = False

    max_i = count_windows(df, cfg)
    try:
        from tqdm import tqdm
        _iter = tqdm(range(0, max_i, cfg.stride), desc="   Features L0", unit="win", ncols=80)
    except ImportError:
        _iter = range(0, max_i, cfg.stride)
        print(f"   Construction features Level 0 : {max_i // cfg.stride:,} fenêtres (stride={cfg.stride})", flush=True)
    for i in _iter:
        end = i + cfg.lookback
        w = feature_matrix[i:end]
        row: List[float] = []
        row_names: List[str] = []

        # Snapshot final
        for c in [
            "rv_12", "rv_24", "rv_72", "rv_168",
            "atr_pct_14", "rsi_14", "boll_pos_20", "boll_width_20",
            "dist_ema_20", "dist_ema_50", "dist_ema_200",
            "ema_spread_20_50", "ema_spread_50_200",
            "taker_buy_ratio_base", "delta_taker_pressure",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
        ]:
            row.append(safe_float(w[-1, col_idx[c]]))
            row_names.append(f"last__{c}")

        # Aggregats multi-fenêtres
        for c in selected_series:
            s = w[:, col_idx[c]].astype(np.float64)
            for win in windows:
                ss = s[-win:] if s.size >= win else s
                row.extend([
                    safe_float(ss[-1]),
                    safe_float(np.mean(ss)),
                    safe_float(np.std(ss)),
                    safe_float(linear_slope(ss)),
                    safe_float(rolling_zscore_last(ss)),
                    safe_float(ss[-1] - ss[0]) if ss.size > 1 else 0.0,
                ])
                row_names.extend([
                    f"{c}__w{win}__last",
                    f"{c}__w{win}__mean",
                    f"{c}__w{win}__std",
                    f"{c}__w{win}__slope",
                    f"{c}__w{win}__zlast",
                    f"{c}__w{win}__delta",
                ])

        # Structure prix
        close = w[:, col_idx["Close"]].astype(np.float64)
        high = w[:, col_idx["High"]].astype(np.float64)
        low = w[:, col_idx["Low"]].astype(np.float64)
        ret = w[:, col_idx["log_ret"]].astype(np.float64)

        for win in [12, 24, 48, cfg.lookback]:
            c = close[-win:] if close.size >= win else close
            h = high[-win:] if high.size >= win else high
            l = low[-win:] if low.size >= win else low
            r = ret[-win:] if ret.size >= win else ret
            price_range = float(np.max(h) - np.min(l)) if c.size else 0.0
            denom = max(abs(float(c[-1])) if c.size else 0.0, 1e-12)
            row.extend([
                safe_float((float(c[-1]) - float(c[0])) / max(abs(float(c[0])), 1e-12)) if c.size > 1 else 0.0,
                safe_float(price_range / denom),
                safe_float(np.sum(np.abs(np.diff(c))) / max(price_range, 1e-12)) if c.size > 1 else 0.0,
                safe_float(np.mean(r)),
                safe_float(np.std(r)),
                safe_float(np.sum(r)),
            ])
            row_names.extend([
                f"price__w{win}__ret",
                f"price__w{win}__range_pct",
                f"price__w{win}__path_to_range",
                f"price__w{win}__ret_mean",
                f"price__w{win}__ret_std",
                f"price__w{win}__ret_sum",
            ])

        if not names_ready:
            names = row_names
            names_ready = True

        rows.append(row)
        y.append(int(labels[end - 1]))
        y_conf.append(float(tradeable[end - 1]))

    X = np.asarray(rows, dtype=np.float32)
    y_arr = np.asarray(y, dtype=np.int32)
    y_conf_arr = np.asarray(y_conf, dtype=np.float32)
    return X, y_arr, y_conf_arr, names


# ═════════════════════════════════════════════════════════════════════════════
# SCALER
# ═════════════════════════════════════════════════════════════════════════════
def fit_scaler_from_matrix(X_train: np.ndarray, cfg: CFG) -> RobustScaler:
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    for i in range(len(X_train)):
        sampler.add(X_train[i:i+1])
    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("Pas assez de données pour le scaler.")
    sc = RobustScaler()
    sc.fit(Xfit)
    return sc


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 0 — REGIME CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════
def train_regime_classifier(df: pd.DataFrame, cfg: CFG, out_dir: Path):
    print("\n" + "=" * 70)
    print("LEVEL 0 — REGIME CLASSIFIER  (HistGradientBoosting)")
    print("=" * 70)

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support,
        confusion_matrix,
    )

    X_all, y_all, y_conf_all, feature_names = build_level0_window_features(df, cfg)
    total = len(X_all)
    n_train = int(total * cfg.train_frac)
    n_val = int(total * cfg.val_frac)

    X_train = X_all[:n_train]
    y_train = y_all[:n_train]
    X_val = X_all[n_train:n_train + n_val]
    y_val = y_all[n_train:n_train + n_val]

    print(f"   Fenêtres totales : {total:,}  |  train : {n_train:,}  val : {n_val:,}")
    print(f"   Distribution train  : {summarize_counts(y_train)}")
    print(f"   Distribution val    : {summarize_counts(y_val)}")
    print(f"   Nb features fenêtre : {X_train.shape[1]:,}")

    scaler = fit_scaler_from_matrix(X_train, cfg)
    X_train_sc = scaler.transform(X_train).astype(np.float32)
    X_val_sc = scaler.transform(X_val).astype(np.float32)

    print("   Entraînement HistGradientBoosting ...")
    t0 = time.time()
    clf = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=cfg.l0_learning_rate,
        max_iter=cfg.l0_max_iter,
        max_depth=cfg.l0_max_depth,
        min_samples_leaf=cfg.l0_min_samples_leaf,
        l2_regularization=cfg.l0_l2,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=cfg.seed,
    )
    clf.fit(X_train_sc, y_train)
    elapsed = time.time() - t0
    print(f"   Entraîné en {elapsed:.1f}s")

    y_pred = clf.predict(X_val_sc)
    y_proba = clf.predict_proba(X_val_sc)

    acc = float(accuracy_score(y_val, y_pred))
    macro_f1 = float(f1_score(y_val, y_pred, average="macro"))
    weighted_f1 = float(f1_score(y_val, y_pred, average="weighted"))

    precision, recall, f1, support = precision_recall_fscore_support(
        y_val, y_pred, labels=[0, 1, 2], zero_division=0
    )
    per_class_recall = {CLASS_ID_TO_NAME[i]: float(r) for i, r in enumerate(recall)}
    per_class_precision = {CLASS_ID_TO_NAME[i]: float(p) for i, p in enumerate(precision)}
    per_class_f1 = {CLASS_ID_TO_NAME[i]: float(v) for i, v in enumerate(f1)}
    per_class_support = {CLASS_ID_TO_NAME[i]: int(v) for i, v in enumerate(support)}

    cm = confusion_matrix(y_val, y_pred, labels=[0, 1, 2])
    cm_norm = cm.astype(float) / np.maximum(cm.sum(axis=1, keepdims=True), 1.0)

    pred_dist = summarize_counts(y_pred)
    bull_recall = per_class_recall.get("bull", 0.0)
    gate_passed = (bull_recall >= cfg.min_bull_recall) and (macro_f1 >= cfg.min_macro_f1)

    # Feature importance permutation-free proxy from boosting importances if unavailable -> skip
    diagnostics = {
        "accuracy": acc,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "per_class_recall": per_class_recall,
        "per_class_precision": per_class_precision,
        "per_class_f1": per_class_f1,
        "per_class_support": per_class_support,
        "pred_distribution_val": pred_dist,
        "true_distribution_val": summarize_counts(y_val),
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_normalized": cm_norm.tolist(),
        "bull_recall": bull_recall,
        "min_bull_recall": cfg.min_bull_recall,
        "min_macro_f1": cfg.min_macro_f1,
        "gate_passed": gate_passed,
        "n_features": int(X_train.shape[1]),
        "feature_names": feature_names,
        "model": {
            "type": "HistGradientBoostingClassifier",
            "learning_rate": cfg.l0_learning_rate,
            "max_iter": cfg.l0_max_iter,
            "max_depth": cfg.l0_max_depth,
            "min_samples_leaf": cfg.l0_min_samples_leaf,
            "l2_regularization": cfg.l0_l2,
        },
    }

    print(f"   Accuracy val        : {acc:.4f}")
    print(f"   Macro F1 val        : {macro_f1:.4f}")
    print(f"   Recall val          : {per_class_recall}")
    print(f"   Distribution prédite: {pred_dist}")
    print("   Confusion val (normalisée) :")
    print(np.array2string(cm_norm, precision=3, suppress_small=True))

    regime_dir = out_dir / "regime_classifier"
    regime_dir.mkdir(parents=True, exist_ok=True)

    import pickle
    with open(regime_dir / "model.pkl", "wb") as f:
        pickle.dump(clf, f)
    with open(regime_dir / "window_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    json_dump(regime_dir / "metrics.json", diagnostics)

    if not gate_passed:
        reasons = []
        if bull_recall < cfg.min_bull_recall:
            reasons.append(f"BULL_RECALL {bull_recall:.3f} < {cfg.min_bull_recall:.3f}")
        if macro_f1 < cfg.min_macro_f1:
            reasons.append(f"MACRO_F1 {macro_f1:.3f} < {cfg.min_macro_f1:.3f}")
        raise ValueError(
            "LEVEL 0 GATE FAILED (val) : " + " | ".join(reasons) + " — modèle rejeté."
        )

    print(f"   Sauvegardé : {regime_dir}")
    return clf, diagnostics


# ═════════════════════════════════════════════════════════════════════════════
# LEVEL 1 — EVENT CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════
def iter_windows(
    df: pd.DataFrame,
    cfg: CFG,
    scaler: RobustScaler,
    start: int,
    end: int,
    feature_keys: Optional[List[str]] = None,
):
    keys = feature_keys if feature_keys is not None else FEATURE_KEYS
    Xraw = df[keys].values.astype(np.float32)
    Xn = scaler.transform(Xraw)
    regime_arr = df["label_regime_3"].values.astype(np.int32)
    tradeable_arr = df["label_tradeable"].values.astype(np.float32)

    max_i = count_windows(df, cfg)
    for i in range(start, min(end, max_i), cfg.stride):
        Xw = Xn[i:i + cfg.lookback]
        regime = int(regime_arr[i + cfg.lookback - 1])
        y_conf = float(tradeable_arr[i + cfg.lookback - 1])
        yield (Xw.astype(np.float32), np.int32(regime), np.float32(y_conf))


def _make_tf_dataset(
    df: pd.DataFrame, cfg: CFG, scaler: RobustScaler,
    start: int, end: int, shuffle: bool = False,
    feature_keys: Optional[List[str]] = None,
) -> tf.data.Dataset:
    keys = feature_keys if feature_keys is not None else FEATURE_KEYS
    F = len(keys)
    sig = (
        tf.TensorSpec((cfg.lookback, F), tf.float32),
        tf.TensorSpec((), tf.int32),
        tf.TensorSpec((), tf.float32),
    )

    def gen():
        yield from iter_windows(df, cfg, scaler, start, end, feature_keys=keys)

    ds = tf.data.Dataset.from_generator(gen, output_signature=sig)
    if shuffle:
        ds = ds.shuffle(2048, seed=cfg.seed, reshuffle_each_iteration=True)
    return ds.batch(cfg.batch_size).prefetch(tf.data.AUTOTUNE)


def unpack_event_output(out):
    if not isinstance(out, dict):
        raise TypeError(f"EventClassifier doit retourner un dict, reçu: {type(out)}")
    if "regime_logits" not in out:
        raise KeyError(f"Clé 'regime_logits' absente. Clés disponibles: {list(out.keys())}")

    regime_logits = out["regime_logits"]

    if "regime_probs" in out:
        regime_probs = out["regime_probs"]
    else:
        regime_probs = tf.nn.softmax(regime_logits, axis=-1)

    if "confidence" in out:
        confidence = out["confidence"]
    elif "conf" in out:
        confidence = out["conf"]
    elif "tradeability" in out:
        confidence = out["tradeability"]
    elif "tradeable" in out:
        confidence = out["tradeable"]
    else:
        confidence = tf.reduce_max(regime_probs, axis=-1, keepdims=True)

    if "entropy" in out:
        entropy = out["entropy"]
    else:
        entropy = -tf.reduce_sum(regime_probs * tf.math.log(regime_probs + 1e-9), axis=-1, keepdims=True)

    return regime_logits, regime_probs, confidence, entropy


def focal_loss(y_true, logits, gamma: float = 2.0):
    """Focal loss multiclasse sparse (labels entiers)."""
    n_classes = logits.shape[-1]
    probs = tf.nn.softmax(logits, axis=-1)
    y_oh = tf.one_hot(tf.cast(y_true, tf.int32), n_classes)
    p_t = tf.reduce_sum(probs * y_oh, axis=-1)
    ce_t = tf.keras.losses.sparse_categorical_crossentropy(y_true, logits, from_logits=True)
    return tf.reduce_mean((1.0 - p_t) ** gamma * ce_t)


def _val_eval(model, ds_val):
    reg_loss = []
    all_yhat, all_ytrue = [], []

    for x, y_reg, _ in ds_val:
        out = model(x, training=False)
        logits, regime_probs, _, _ = unpack_event_output(out)

        reg_loss.append(float(focal_loss(y_reg, logits).numpy()))

        yhat = tf.argmax(regime_probs, axis=-1).numpy()
        ytrue = y_reg.numpy()
        all_yhat.extend(yhat.tolist())
        all_ytrue.extend(ytrue.tolist())

    all_yhat = np.array(all_yhat, dtype=np.int32)
    all_ytrue = np.array(all_ytrue, dtype=np.int32)

    if len(all_ytrue) == 0:
        return {
            "val_reg_loss": 0.0,
            "regime_acc": 0.0,
            "macro_f1": 0.0,
            "recall_bear": 0.0,
            "recall_bull": 0.0,
            "pred_dist": {},
            "confusion_matrix": [],
        }

    regime_acc = float((all_yhat == all_ytrue).mean())
    macro_f1 = float(f1_score(all_ytrue, all_yhat, average="macro", zero_division=0))

    _, recall, _, _ = precision_recall_fscore_support(
        all_ytrue, all_yhat, labels=[0, 1, 2], zero_division=0
    )
    recall_bear = float(recall[0])
    recall_bull = float(recall[2])

    pred_dist = summarize_counts(all_yhat)
    cm = confusion_matrix(all_ytrue, all_yhat, labels=[0, 1, 2]).tolist()

    return {
        "val_reg_loss": float(np.mean(reg_loss)) if reg_loss else 0.0,
        "regime_acc": regime_acc,
        "macro_f1": macro_f1,
        "recall_bear": recall_bear,
        "recall_bull": recall_bull,
        "pred_dist": pred_dist,
        "confusion_matrix": cm,
    }


def train_event_classifier(
    df: pd.DataFrame,
    cfg: CFG,
    out_dir: Path,
    feature_keys: Optional[List[str]] = None,
    stress_split: bool = False,
    n_regimes_override: Optional[int] = None,
):
    print("\n" + "=" * 70)
    print("LEVEL 1 — EVENT CLASSIFIER  (TCN TensorFlow/Keras)")
    print("=" * 70)

    np.random.seed(cfg.seed)
    tf.random.set_seed(cfg.seed)

    if "label_regime_3" not in df.columns or "label_tradeable" not in df.columns:
        raise RuntimeError(
            "label_regime_3 / label_tradeable absents du CSV — "
            "relance build_binance_features.py pour générer le CSV enrichi."
        )

    keys = feature_keys if feature_keys is not None else FEATURE_KEYS
    n_cls = n_regimes_override if n_regimes_override is not None else cfg.n_regimes
    print(f"   Feature pack : {len(keys)} features  |  n_regimes={n_cls}")

    total = count_windows(df, cfg)

    if stress_split:
        # Split dur chronologique : train ≤2023 / val=2024 / test=2025+
        years = df["datetime"].dt.year.values
        row_years = years[cfg.lookback - 1:]          # année de la dernière barre visible
        train_mask = row_years <= 2023
        val_mask   = row_years == 2024
        train_indices = np.where(train_mask)[0]
        val_indices   = np.where(val_mask)[0]
        n_train = len(train_indices)
        n_val   = len(val_indices)
        # Pour les générateurs on a besoin de plages contiguës — on restreint le df
        train_start = int(train_indices[0])  if n_train > 0 else 0
        train_end   = int(train_indices[-1]) + 1 if n_train > 0 else 0
        val_start   = int(val_indices[0])    if n_val   > 0 else train_end
        val_end     = int(val_indices[-1])   + 1 if n_val > 0 else train_end
        print(f"   [stress-split]  train≤2023 : {n_train:,}  val=2024 : {n_val:,}")
    else:
        n_train = int(total * cfg.train_frac)
        n_val   = int(total * cfg.val_frac)
        train_start, train_end = 0, n_train
        val_start,   val_end   = n_train, n_train + n_val

    print(f"   Total fenêtres : {total:,}  |  train {n_train:,}  val {n_val:,}")

    # Scaler ajusté sur train uniquement (colonnes du pack sélectionné)
    print("   Ajustement du scaler ...", end=" ", flush=True)
    X_train_scaler = df[keys].values.astype(np.float32)[: n_train + cfg.lookback]
    sampler = ReservoirSampler(cfg.scaler_sample_max, seed=cfg.seed)
    for i in range(max(0, len(X_train_scaler) - cfg.lookback)):
        sampler.add(X_train_scaler[i:i + cfg.lookback])
    Xfit = sampler.get()
    if Xfit.size == 0:
        raise RuntimeError("Pas assez de données pour ajuster le scaler du Level 1.")
    scaler = RobustScaler()
    scaler.fit(Xfit)
    print("OK")

    ds_train = _make_tf_dataset(df, cfg, scaler, train_start, train_end, shuffle=True, feature_keys=keys)
    ds_val   = _make_tf_dataset(df, cfg, scaler, val_start,   val_end,   shuffle=False, feature_keys=keys)

    try:
        model_cfg = EventClassifierConfig(
            d_model=128,
            n_layers=4,
            n_regimes=n_cls,
            dropout=0.10,
            confidence_dropout=0.1,
        )
    except TypeError:
        model_cfg = EventClassifierConfig(
            d_model=128,
            n_layers=4,
            n_regimes=n_cls,
            dropout=0.10,
        )
    model = EventClassifier(model_cfg)

    # Diagnostic : affiche les clés réellement renvoyées par ce modèle local
    try:
        _dummy_batch = next(iter(ds_train.take(1)))
        _dummy_out = model(_dummy_batch[0][:1], training=False)
        if isinstance(_dummy_out, dict):
            print(f"   Clés de sortie modèle : {list(_dummy_out.keys())}")
        else:
            print(f"   Sortie modèle non-dict : {type(_dummy_out)}")
    except Exception as _e:
        print(f"   (diagnostic ignoré : {_e})")

    opt = tf.keras.optimizers.AdamW(
        learning_rate=cfg.lr,
        weight_decay=cfg.weight_decay,
        global_clipnorm=cfg.clip_norm,
    )

    event_dir = out_dir / "event_classifier"
    event_dir.mkdir(parents=True, exist_ok=True)
    log_path = event_dir / "log.jsonl"

    best_score = -1e18
    best_epoch = -1
    bad = 0

    print()
    print(
        f"{'Ep':>3}  "
        f"{'tr_focal':>10}  "
        f"{'v_focal':>10}  "
        f"{'acc':>7}  "
        f"{'macroF1':>8}  "
        f"{'bear_r':>7}  "
        f"{'bull_r':>7}  "
        f"{'score':>8}  {'lr':>9}  t(s)"
    )
    print("─" * 90)

    with open(log_path, "a", buffering=1, encoding="utf-8") as log_f:
        for ep in range(cfg.epochs):
            ep_t0 = time.time()
            tr_reg_loss = []

            for _, (x, y_reg, _) in enumerate(ds_train, start=1):
                with tf.GradientTape() as tape:
                    out = model(x, training=True)
                    regime_logits, _, _, _ = unpack_event_output(out)
                    loss = focal_loss(y_reg, regime_logits)
                    # Connecte les heads auxiliaires au graph pour éviter les warnings
                    for aux_key in ("fwd_ret_pred", "confidence", "conf", "tradeability"):
                        if aux_key in out:
                            loss = loss + 0.0 * tf.reduce_mean(out[aux_key])

                grads = tape.gradient(loss, model.trainable_variables)
                opt.apply_gradients(zip(grads, model.trainable_variables))
                tr_reg_loss.append(float(loss.numpy()))

            v = _val_eval(model, ds_val)
            lr = float(
                opt.learning_rate.numpy()
                if hasattr(opt.learning_rate, "numpy")
                else cfg.lr
            )

            # Macro-F1 comme métrique principale (plus honnête que accuracy seule)
            val_score = v["macro_f1"] - v["val_reg_loss"] * 0.10

            ep_time = time.time() - ep_t0
            print(
                f"{ep+1:>3}  "
                f"{np.mean(tr_reg_loss):>10.4f}  "
                f"{v['val_reg_loss']:>10.4f}  "
                f"{v['regime_acc']:>6.2%}  "
                f"{v['macro_f1']:>8.4f}  "
                f"{v['recall_bear']:>6.2%}  "
                f"{v['recall_bull']:>6.2%}  "
                f"{val_score:>8.4f}  {lr:.2e}  {ep_time:.0f}"
            )

            # Confusion matrix toutes les 5 epochs
            if (ep + 1) % 5 == 0 and v["confusion_matrix"]:
                cm = np.array(v["confusion_matrix"])
                cm_norm = cm.astype(float) / np.maximum(cm.sum(axis=1, keepdims=True), 1.0)
                pred_dist = v.get("pred_dist", {})
                print(f"     pred dist : {pred_dist}")
                print(f"     confusion (norm) bear/neutral/bull :")
                print(np.array2string(cm_norm, precision=3, suppress_small=True, prefix="       "))

            row = {
                "epoch": ep + 1,
                "train_reg_loss": float(np.mean(tr_reg_loss)) if tr_reg_loss else 0.0,
                **{k: v[k] for k in v if k != "confusion_matrix"},
                "val_score": float(val_score),
                "lr": lr,
                "epoch_time_sec": float(ep_time),
                "confusion_matrix": v.get("confusion_matrix", []),
            }
            log_f.write(json.dumps(row, ensure_ascii=False) + "\n")

            if ep > 0 and (ep % cfg.reduce_lr_patience == 0) and val_score <= best_score:
                new_lr = max(lr * cfg.reduce_lr_factor, cfg.min_lr)
                opt.learning_rate.assign(new_lr)
                print(f"     → lr réduit à {new_lr:.2e}")

            if val_score > best_score + cfg.min_delta:
                best_score = val_score
                best_epoch = ep + 1
                bad = 0
                model.save_weights(str(event_dir / "best.weights.h5"))
            else:
                bad += 1
                if bad >= cfg.early_stop_patience:
                    print(f"\n   Early stop à l'epoch {ep+1}  (patience={cfg.early_stop_patience})")
                    break

    model.save_weights(str(event_dir / "final.weights.h5"))

    try:
        with open(event_dir / "scaler.pkl", "wb") as f:
            import pickle
            pickle.dump(scaler, f)
    except Exception as e:
        print(f"⚠  Sauvegarde scaler Level 1 impossible : {e}")

    summary = {
        "best_val_score": float(best_score),
        "best_epoch": int(best_epoch),
        "cfg": {
            "lookback": cfg.lookback,
            "horizon": cfg.horizon,
            "batch_size": cfg.batch_size,
            "epochs": cfg.epochs,
            "lr": cfg.lr,
            "min_lr": cfg.min_lr,
            "weight_decay": cfg.weight_decay,
            "clip_norm": cfg.clip_norm,
            "n_regimes": cfg.n_regimes,
        },
    }
    json_dump(event_dir / "summary.json", summary)

    print(f"\n   Best val_score : {best_score:.4f}  (epoch {best_epoch})")
    print(f"   Sauvegardé    : {event_dir}")
    return model, summary


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def parse_args():
    ap = argparse.ArgumentParser(
        description="Entraîne Level 0 + Level 1 sur des CSV Binance enrichis."
    )
    ap.add_argument(
        "--data",
        default="data/bundle_btc/features_merged.parquet",
        help="Bundle parquet (défaut), CSV enrichi ou dossier de CSV enrichis",
    )
    ap.add_argument(
        "--out",
        default=str(FUTUR / "runs" / "local"),
        help=f"Dossier de sortie (défaut : {FUTUR}/runs/local)",
    )
    ap.add_argument(
        "--years",
        default=None,
        help="Années à utiliser, ex : 2021,2022,2023",
    )
    ap.add_argument(
        "--skip-regime",
        action="store_true",
        help="Saute l'entraînement du Regime Classifier (Level 0)",
    )
    ap.add_argument(
        "--skip-event",
        action="store_true",
        help="Saute l'entraînement de l'Event Classifier (Level 1)",
    )
    ap.add_argument(
        "--label",
        default="original",
        choices=["original", "quantile", "threshold"],
        help=(
            "Version du label_regime_3 : "
            "original=CSV brut, "
            "quantile=bear≤q33/bull≥q67, "
            "threshold=bear≤-thr/bull≥+thr (thr auto si --label-thr=0)"
        ),
    )
    ap.add_argument(
        "--label-thr",
        type=float,
        default=0.0,
        help="Seuil pour --label=threshold (0=auto-calibré sur quantile 0.70 des |ret|)",
    )
    ap.add_argument(
        "--feature-pack",
        default="all",
        choices=list(FEATURE_PACKS.keys()),
        help="Pack de features à utiliser pour le Level 1 (ablation)",
    )
    ap.add_argument(
        "--binary",
        action="store_true",
        help="Mode binaire bear vs bull : supprime les exemples neutral avant l'entraînement",
    )
    ap.add_argument(
        "--stress-split",
        action="store_true",
        help="Split dur chronologique : train≤2023 / val=2024 (test=2025+ ignoré)",
    )
    return ap.parse_args()


def main():
    t_start = time.time()
    args = parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else None
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / run_id
    out.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("ML TRAINING PIPELINE — LOCAL CSV")
    print("=" * 70)
    print(f"  Data         : {args.data}")
    print(f"  Sortie       : {out}")
    print(f"  Label        : {args.label}" + (f"  thr={args.label_thr}" if args.label == "threshold" else ""))
    print(f"  Feature pack : {args.feature_pack}")
    if args.binary:
        print("  Mode         : binaire (bear vs bull, neutral supprimé)")
    if args.stress_split:
        print("  Split        : stress (train≤2023 / val=2024)")
    if years:
        print(f"  Années       : {years}")

    df = load_data(args.data, years, label_version=args.label, label_thr=args.label_thr)

    if args.binary:
        df = apply_binary_mode(df)

    feature_keys = FEATURE_PACKS[args.feature_pack]
    n_regimes = 2 if args.binary else CFG().n_regimes
    cfg = CFG()

    pipeline_summary: Dict[str, object] = {
        "run_id": run_id,
        "data": args.data,
        "years": years,
        "label_version": args.label,
        "feature_pack": args.feature_pack,
        "n_features": len(feature_keys),
        "binary_mode": args.binary,
        "stress_split": args.stress_split,
        "n_regimes": n_regimes,
        "n_rows": int(len(df)),
        "date_start": str(df["datetime"].iloc[0]),
        "date_end": str(df["datetime"].iloc[-1]),
        "cfg": {
            "lookback": cfg.lookback,
            "horizon": cfg.horizon,
            "stride": cfg.stride,
            "train_frac": cfg.train_frac,
            "val_frac": cfg.val_frac,
            "batch_size": cfg.batch_size,
            "epochs": cfg.epochs,
            "lr": cfg.lr,
            "min_lr": cfg.min_lr,
            "weight_decay": cfg.weight_decay,
            "clip_norm": cfg.clip_norm,
            "n_regimes": cfg.n_regimes,
            "min_bull_recall": cfg.min_bull_recall,
            "min_macro_f1": cfg.min_macro_f1,
        },
        "level0": None,
        "level1": None,
    }

    if not args.skip_regime:
        try:
            _, l0_diag = train_regime_classifier(df, cfg, out)
            pipeline_summary["level0"] = {
                "status": "ok",
                "metrics": l0_diag,
            }
        except ValueError as e:
            print(f"\n❌  {e}")
            print("   Pipeline continue malgré l'échec du gate Level 0.")
            metrics_path = out / "regime_classifier" / "metrics.json"
            recovered_metrics = None
            if metrics_path.exists():
                with open(metrics_path, "r", encoding="utf-8") as f:
                    recovered_metrics = json.load(f)
            pipeline_summary["level0"] = {
                "status": "gate_failed",
                "error": str(e),
                "metrics": recovered_metrics,
            }
        except Exception as e:
            print(f"\n❌  Erreur Level 0 : {e}")
            pipeline_summary["level0"] = {
                "status": "error",
                "error": str(e),
            }

    if not args.skip_event:
        try:
            _, l1_summary = train_event_classifier(
                df, cfg, out,
                feature_keys=feature_keys,
                stress_split=args.stress_split,
                n_regimes_override=n_regimes,
            )
            pipeline_summary["level1"] = {
                "status": "ok",
                "summary": l1_summary,
            }
        except Exception as e:
            print(f"\n❌  Erreur Level 1 : {e}")
            pipeline_summary["level1"] = {
                "status": "error",
                "error": str(e),
            }

    elapsed = time.time() - t_start
    pipeline_summary["elapsed_sec"] = float(elapsed)
    json_dump(out / "pipeline_summary.json", pipeline_summary)

    print("\n" + "=" * 70)
    print(f"✅  Pipeline terminé en {elapsed/60:.1f} min")
    print(f"   Résultats : {out}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
