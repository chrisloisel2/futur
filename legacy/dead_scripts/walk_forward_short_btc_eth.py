#!/usr/bin/env python3
"""
walk_forward_short_btc_eth.py — Walk-forward SHORT ciblé BTC + ETH
====================================================================

Validation de la pipeline SHORT avec le patch momentum gate.

Architecture :
  - Données : BTC + ETH 1-min parquets → resample 1h
  - Features : OHLCV-based (EMA, RSI, momentum, gamechanger SHORT)
  - Modèle : HistGradientBoostingClassifier (natif NaN, rapide)
  - Gate : compute_regime_col() avec momentum gate conditionnelle (patch)
  - Folds : train<=T-2, val=T-1, test=T

Folds testés :
  F1 : train=2019-2020, val=2021, test=2022  (val=bull peak, test=crash)
  F2 : train=2019-2021, val=2022, test=2023  (val=crash, test=recovery)
  F3 : train=2019-2022, val=2023, test=2024  (val=recovery, test=sideways/bull)

Verdict par fold : PASS (PF>=1.30) / WEAK (PF 1.0-1.30) / FAIL (PF<1.0)
Verdict global : SHORT_PROMISING si >=2/3 folds PASS sur les 2 actifs

Usage :
  python3 scripts/walk_forward_short_btc_eth.py [--symbols BTC ETH] [--cost-bps 15]
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
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "short_rebuild_btc_eth"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Paramètres ──────────────────────────────────────────────────────────────

HORIZON_H  = 8       # prédiction 8h
QUANTILE   = 0.84    # top 16% des mouvements (TRADEABLE_QUANTILE_SHORT)
COST_PCT   = 0.0015  # 15 bps stress (funding + slippage)

FOLDS = [
    {"train": list(range(2019, 2021)), "val": 2021, "test": 2022},
    {"train": list(range(2019, 2022)), "val": 2022, "test": 2023},
    {"train": list(range(2019, 2023)), "val": 2023, "test": 2024},
]

MIN_POS_TRAIN    = 80    # labels SHORT min dans train pour entraîner
MIN_TRADES_VAL   = 8     # trades min sur val pour calibrer
PF_PASS          = 1.30
PF_WEAK          = 1.00

# ─── Imports projet ──────────────────────────────────────────────────────────

from ai.level_0.labels import compute_label_columns, build_labels, compute_regime_col
from ai.level_0.feature_engineering import compute_long_features
from ai.level_0.short_features import compute_all_short_features

# ─── Helpers : prix ──────────────────────────────────────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=max(span // 4, 10)).mean()

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    d   = close.diff()
    g   = d.clip(lower=0)
    l   = (-d).clip(lower=0)
    ag  = g.ewm(span=period, adjust=False, min_periods=period).mean()
    al  = l.ewm(span=period, adjust=False, min_periods=period).mean()
    rs  = ag / al.clip(lower=1e-9)
    return 100 - (100 / (1 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False, min_periods=period).mean()


# ─── Chargement ──────────────────────────────────────────────────────────────

# Colonnes ciblées à charger depuis les parquets 1-min
# On ne charge que ce dont on a besoin → évite OOM sur 250 cols × 2.5M lignes
_LOAD_COLS = [
    "timestamp",
    # OHLCV
    "open", "high", "low", "close", "volume", "taker_buy_base",
    # RSI / oscillateurs pré-calculés
    "rsi_14", "stoch_rsi_k", "stoch_rsi_d", "stoch_rsi_diff",
    "willr_14", "cci_20", "mfi_14", "cmf_21",
    "adx_14", "di_diff_14",
    # MACD, squeeze
    "macd_hist", "macd_line", "squeeze_on", "squeeze_mom", "squeeze_mom_sign",
    # Bollinger
    "bb_pctb_20", "bb_width_20",
    # VWAP
    "vwap_dist_60m", "vwap_dist_240m", "vwap_dist_1440m",
    # Funding / macro
    "funding_rate", "funding_accel",
    "oi_sum", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h",
    "global_long_short_ratio", "top_trader_lsr", "lsr_z_1d",
    "taker_buy_ratio", "taker_buy_sell_ratio",
    "fear_greed",
    # Returns bruts
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    # Ichimoku
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist",
    "ichi_senkou_a_dist", "ichi_senkou_b_dist",
    # Volume
    "obv_z_1h", "obv_z_4h",
    # Misc
    "session_asia", "session_europe", "session_us",
    "btc_spy_corr_1d", "eth_btc_ret_1d",
    # OI price divergence
    "oi_price_div_1h",
    # Basis (funding proxy)
    "basis", "basis_z_1d",
]


def load_symbol(symbol: str, years: List[int]) -> pd.DataFrame:
    frames = []
    for y in years:
        path = DATA_DIR / f"{y}_{symbol}USDT_features.parquet"
        if not path.exists():
            print(f"    [skip] {path.name}")
            continue
        import pyarrow.parquet as pq
        available = set(pq.ParquetFile(path).schema.names)
        cols      = [c for c in _LOAD_COLS if c in available]
        df = pd.read_parquet(path, columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"Aucun parquet trouvé pour {symbol}")
    out = pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    return out


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    df = df.set_index("timestamp")

    # Colonnes OHLCV : agrégation spécifique
    ohlcv_agg = {}
    if "open"   in df.columns: ohlcv_agg["Open"]   = pd.NamedAgg("open",   "first")
    if "high"   in df.columns: ohlcv_agg["High"]   = pd.NamedAgg("high",   "max")
    if "low"    in df.columns: ohlcv_agg["Low"]    = pd.NamedAgg("low",    "min")
    if "close"  in df.columns: ohlcv_agg["Close"]  = pd.NamedAgg("close",  "last")
    if "volume" in df.columns: ohlcv_agg["Volume"] = pd.NamedAgg("volume", "sum")
    if "taker_buy_base" in df.columns:
        ohlcv_agg["taker_buy_base_asset_volume"] = pd.NamedAgg("taker_buy_base", "sum")

    h_ohlcv = df.resample("1h").agg(**ohlcv_agg)

    # Toutes les autres colonnes numériques : last() (valeur la plus récente dans l'heure)
    other_cols = [c for c in df.select_dtypes(include=[np.number]).columns
                  if c not in {"open", "high", "low", "close", "volume",
                                "taker_buy_base", "quote_volume", "n_trades"}]
    h_other = df[other_cols].resample("1h").last()

    h = pd.concat([h_ohlcv, h_other], axis=1).dropna(subset=["Close"])
    h.index.name = "datetime"
    return h.reset_index()


# ─── Feature engineering ─────────────────────────────────────────────────────

def _zs(s: pd.Series, w: int) -> pd.Series:
    """Z-score rolling local sur w barres."""
    mu  = s.rolling(w, min_periods=w // 2).mean()
    sig = s.rolling(w, min_periods=w // 2).std()
    return (s - mu) / sig.clip(lower=1e-9)


def add_base_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Complète le DataFrame 1h avec les features nécessaires à la pipeline.
    Les parquets ont déjà 250+ colonnes utiles (funding, OI, L/S, RSI, etc.).
    Cette fonction ajoute uniquement ce qui est absent ou doit être recalculé
    sur les barres horaires (EMA50/200, momentum, z-scores manquants).
    """
    df    = df.copy()
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]
    logc  = np.log(close.clip(lower=1e-9))

    # ── EMA et régime (toujours recalculés sur 1h) ───────────────────────────
    ema50  = _ema(close, 50)
    ema200 = _ema(close, 200)
    df["ema_50"]            = ema50
    df["ema_200"]           = ema200
    df["ema_spread_50_200"] = (ema50 - ema200) / ema200.clip(lower=1e-9)
    df["dist_ema_50"]       = (close - ema50)  / ema50.clip(lower=1e-9)
    df["dist_ema_200"]      = (close - ema200) / ema200.clip(lower=1e-9)

    # ── RSI 14h (recalculé sur barres horaires si absent) ─────────────────────
    if "rsi_14" not in df.columns:
        df["rsi_14"] = _rsi(close, 14)

    # ── ATR 14h ───────────────────────────────────────────────────────────────
    atr14 = _atr(high, low, close, 14)
    df["atr_14"]     = atr14
    df["atr_pct_14"] = atr14 / close.clip(lower=1e-9)

    # ── Momentum horaire (gate + gamechanger) ─────────────────────────────────
    df["mom_logret_4"]   = logc - logc.shift(4)
    df["mom_logret_12"]  = logc - logc.shift(12)
    df["mom_logret_24"]  = logc - logc.shift(24)
    df["mom_logret_72"]  = logc - logc.shift(72)
    df["mom_logret_168"] = logc - logc.shift(168)

    # ── Réalisé vol horaire ───────────────────────────────────────────────────
    ret1h = logc.diff()
    for w in [12, 24, 48, 72, 168]:
        df[f"rv_{w}"] = ret1h.rolling(w, min_periods=w // 2).std() * np.sqrt(w)
    df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].clip(lower=1e-9)

    # ── Bollinger (horaire) ───────────────────────────────────────────────────
    mu20   = close.rolling(20, min_periods=10).mean()
    sg20   = close.rolling(20, min_periods=10).std()
    boll_w = (4 * sg20).clip(lower=1e-9)
    df["boll_width_20"] = boll_w / mu20.clip(lower=1e-9)
    df["boll_pos_20"]   = (close - (mu20 - 2 * sg20)) / boll_w

    # ── Microstructure ────────────────────────────────────────────────────────
    bar_range = (high - low).clip(lower=1e-9)
    df["close_in_bar"]       = (close - low) / bar_range
    df["intrabar_range_pct"] = bar_range / close.clip(lower=1e-9)
    df["upper_wick_pct"]     = (high - close) / bar_range

    # ── Volume ratio ─────────────────────────────────────────────────────────
    df["vol_ratio_24"] = vol / vol.rolling(24, min_periods=12).mean().clip(lower=1e-9)

    # ── Efficacité directionnelle ─────────────────────────────────────────────
    for w in [12, 24]:
        net = (logc - logc.shift(w)).abs()
        tot = ret1h.abs().rolling(w, min_periods=w // 2).sum()
        df[f"eff_ratio_{w}"] = net / tot.clip(lower=1e-9)

    # ── Z-score clôture ───────────────────────────────────────────────────────
    df["zscore_close_24"] = _zs(close, 24)

    # ── Temporel ─────────────────────────────────────────────────────────────
    hour = df["datetime"].dt.hour
    dow  = df["datetime"].dt.dayofweek
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"]  = np.sin(2 * np.pi * dow  / 7)
    df["dow_cos"]  = np.cos(2 * np.pi * dow  / 7)

    # ── VWAP intraday ─────────────────────────────────────────────────────────
    if "vwap_dist_60m" not in df.columns:
        df["date"] = df["datetime"].dt.date
        typical    = (high + low + close) / 3
        cum_tp_vol = (typical * vol).groupby(df["date"]).cumsum()
        cum_vol    = vol.groupby(df["date"]).cumsum().clip(lower=1e-9)
        vwap       = cum_tp_vol / cum_vol
        df.drop(columns=["date"], inplace=True)
        df["vwap"]         = vwap
        df["vwap_dist_60m"]= (close - vwap) / vwap.clip(lower=1e-9)
    df["above_vwap_4h"]  = (close > (df.get("vwap", close))).astype(float)
    df["above_vwap_12h"] = df["above_vwap_4h"]
    df["distance_vwap"]  = df.get("vwap_dist_60m", pd.Series(0.0, index=df.index))
    df["dist_vwap"]      = df["distance_vwap"]

    # ── Local high/low ────────────────────────────────────────────────────────
    df["local_high_24"]           = high.rolling(24, min_periods=12).max()
    df["local_low_24"]            = low.rolling(24,  min_periods=12).min()
    df["dist_from_local_high_24"] = (close - df["local_high_24"]) / df["local_high_24"].clip(lower=1e-9)

    # ── Z-scores macro (nécessaires pour compute_all_short_features) ──────────
    # Ces features sont dans le parquet à 1-min ; après resample(.last()),
    # elles ont des valeurs valides. On recalcule les z-scores sur barres horaires.
    for col_src, col_dst, w in [
        ("funding_rate",          "funding_rate_z_24",              24),
        ("funding_rate",          "funding_rate_z_72",              72),
        ("global_long_short_ratio","global_ls_longShortRatio_z_24", 24),
        ("global_long_short_ratio","global_ls_longShortRatio_z_72", 72),
        ("oi_sum",                "oihist_sumOpenInterest_z_24",    24),
        ("oi_sum",                "oihist_sumOpenInterest_z_72",    72),
        ("fear_greed",            "fear_greed_value_z_24",          24),
        ("fear_greed",            "fear_greed_value_z_72",          72),
        ("taker_buy_sell_ratio",  "taker_ls_buySellRatio_z_24",     24),
        ("taker_buy_ratio",       "taker_buy_ratio_base",           1),   # copie directe
    ]:
        if col_src not in df.columns:
            continue
        s = pd.to_numeric(df[col_src], errors="coerce")
        if w == 1:
            df[col_dst] = s
        else:
            df[col_dst] = _zs(s, w)

    # Alias pour compute_all_short_features
    if "taker_buy_sell_ratio" in df.columns:
        df["taker_ls_imbalance"] = pd.to_numeric(df["taker_buy_sell_ratio"], errors="coerce") - 1.0
    if "funding_rate" in df.columns:
        df["funding_accel_24"] = pd.to_numeric(df["funding_rate"], errors="coerce").diff(24)
        df["funding_accel_72"] = pd.to_numeric(df["funding_rate"], errors="coerce").diff(72)

    return df


# ─── Feature list finale ─────────────────────────────────────────────────────

FEATURES_BASE = [
    # Régime / EMA
    "dist_ema_50", "ema_spread_50_200", "dist_ema_200",
    # Oscillateurs
    "rsi_14", "atr_pct_14",
    "stoch_rsi_k", "stoch_rsi_d", "stoch_rsi_diff",
    "willr_14", "cci_20", "mfi_14", "cmf_21",
    "adx_14", "di_diff_14",
    # Momentum horaire
    "mom_logret_4", "mom_logret_12", "mom_logret_24", "mom_logret_72", "mom_logret_168",
    # Volatilité
    "rv_12", "rv_24", "rv_48", "rv_72", "rv_168", "rv_ratio_24_72",
    "boll_width_20", "boll_pos_20", "bb_pctb_20", "bb_width_20",
    "squeeze_on", "squeeze_mom", "squeeze_mom_sign",
    # Microstructure
    "close_in_bar", "intrabar_range_pct", "upper_wick_pct",
    "vol_ratio_24", "eff_ratio_12", "eff_ratio_24", "zscore_close_24",
    # Temporel
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "session_asia", "session_europe", "session_us",
    # VWAP
    "above_vwap_4h", "dist_vwap", "vwap_dist_60m", "vwap_dist_240m", "vwap_dist_1440m",
    # Macro / dérivés
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72",
    "funding_z_7d", "funding_z_30d", "funding_accel",
    "oi_sum", "oi_z_1d", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h",
    "global_long_short_ratio", "global_ls_longShortRatio_z_24", "global_ls_longShortRatio_z_72",
    "top_trader_lsr", "top_trader_z_1d", "lsr_z_1d",
    "taker_buy_ratio", "taker_buy_sell_ratio", "taker_ls_buySellRatio_z_24",
    "taker_ls_imbalance",
    "fear_greed", "fear_greed_value_z_24", "fear_greed_value_z_72",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    # Returns raw parquet (différentes fenêtres)
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    # Corr cross-asset
    "eth_btc_ret_1d", "btc_spy_corr_1d",
    # Volume / OBV
    "obv_z_1h", "obv_z_4h",
    # MACD
    "macd_hist", "macd_line",
    # Ichimoku distances
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist",
    "ichi_senkou_a_dist", "ichi_senkou_b_dist",
]

from ai.level_0.short_features import FEATURES_SHORT_GAMECHANGER  # noqa


def get_feature_list(df: pd.DataFrame) -> List[str]:
    """Retourne les features disponibles (base + gamechanger non-NaN à >50%)."""
    candidates = FEATURES_BASE + FEATURES_SHORT_GAMECHANGER
    avail = []
    for f in candidates:
        if f not in df.columns:
            continue
        frac_nan = df[f].isna().mean()
        if frac_nan < 0.50:
            avail.append(f)
    return avail


# ─── Labels ──────────────────────────────────────────────────────────────────

def build(df: pd.DataFrame, train_mask: np.ndarray) -> Tuple[pd.DataFrame, Dict]:
    """Délègue à la factory labels canonique."""
    df = compute_label_columns(df)
    df, stats = build_labels(df, train_mask)
    df = compute_regime_col(df)  # gate avec momentum patch
    return df, stats


# ─── PnL simulation ──────────────────────────────────────────────────────────

def _pf(pnl: np.ndarray) -> float:
    wins = pnl[pnl > 0].sum()
    loss = abs(pnl[pnl < 0].sum())
    return wins / loss if loss > 1e-9 else float("inf")


def backtest_fold(
    df_test: pd.DataFrame,
    clf: HistGradientBoostingClassifier,
    features: List[str],
    threshold: float,
    cost_pct: float = COST_PCT,
) -> Dict:
    """Backtest bar-à-bar sur df_test avec gate régime."""
    trade_pnls: List[float] = []
    trade_info: List[Dict]  = []

    for _, row in df_test.iterrows():
        if row.get("regime_short", "NEUTRAL") == "NO_SHORT":
            continue
        if row["y_short"] == -1:      # gray zone — exclue
            continue
        fwd = row.get("future_ret_8h")
        if pd.isna(fwd):
            continue

        x = np.array([[row.get(f, np.nan) for f in features]], dtype=np.float64)
        try:
            p = clf.predict_proba(x)[0, 1]
        except Exception:
            continue

        if p < threshold:
            continue

        pnl = -fwd - cost_pct
        trade_pnls.append(pnl)
        trade_info.append({
            "ts":    str(row["datetime"]),
            "p":     round(p, 4),
            "fwd":   round(fwd, 5),
            "pnl":   round(pnl, 5),
            "win":   pnl > 0,
        })

    if not trade_pnls:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan,
                "max_dd": np.nan, "squeeze_rate": np.nan}

    pnl_arr = np.array(trade_pnls)
    cum     = np.cumsum(pnl_arr)
    peak    = np.maximum.accumulate(cum)
    dd      = (cum - peak).min()

    # squeeze rate : trades qui gagnent momentanément mais finissent perdants
    # (proxy : MAE non disponible ici → on utilise la fraction de trades très négatifs)
    squeeze_like = (pnl_arr < -0.005).mean()

    return {
        "n":            len(pnl_arr),
        "pf":           round(_pf(pnl_arr), 3),
        "wr":           round((pnl_arr > 0).mean(), 3),
        "exp":          round(float(pnl_arr.mean()), 5),
        "max_dd":       round(float(dd), 5),
        "squeeze_rate": round(float(squeeze_like), 3),
        "trades":       trade_info[:20],  # premiers 20 pour debug
    }


# ─── Calibration seuil ───────────────────────────────────────────────────────

def calibrate_threshold(
    df_val: pd.DataFrame,
    clf: HistGradientBoostingClassifier,
    features: List[str],
    cost_pct: float = COST_PCT,
) -> Tuple[float, Dict]:
    """
    Sweep de seuils sur val. Sélectionne PF>=1.05 ET WR>=50% ET n>=MIN_TRADES_VAL.
    Fallback : 0.72.
    """
    valid = (
        (df_val["regime_short"] != "NO_SHORT") &
        (df_val["y_short"] >= 0) &
        df_val["future_ret_8h"].notna()
    )
    sub = df_val.loc[valid].reset_index(drop=True)
    if len(sub) < MIN_TRADES_VAL:
        return 0.72, {"reason": "val_too_small", "n_val": len(sub)}

    X_val = sub[[f for f in features if f in sub.columns]].values.astype(np.float64)
    probas = clf.predict_proba(X_val)[:, 1]
    fwds   = sub["future_ret_8h"].values

    best_thr  = 0.72
    best_score = -1.0
    results   = []

    for thr in np.arange(0.52, 0.91, 0.02):
        mask  = probas >= thr
        n     = mask.sum()
        if n < MIN_TRADES_VAL:
            continue
        pnl   = -fwds[mask] - cost_pct
        wins  = pnl[pnl > 0]
        loss  = abs(pnl[pnl < 0].sum())
        pf    = wins.sum() / loss if loss > 1e-9 else float("inf")
        wr    = (pnl > 0).mean()
        score = pf * np.sqrt(n)

        results.append({"thr": round(thr, 2), "n": int(n), "pf": round(pf, 3),
                        "wr": round(wr, 3), "score": round(score, 2)})

        if pf >= 1.05 and wr >= 0.50 and n >= MIN_TRADES_VAL and score > best_score:
            best_score = score
            best_thr   = round(thr, 2)

    return best_thr, {"reason": "swept", "best_score": round(best_score, 2),
                      "n_candidates": len(results), "results_top5": results[:5]}


# ─── Training ────────────────────────────────────────────────────────────────

def train_fold(
    df_train: pd.DataFrame,
    features: List[str],
) -> Optional[HistGradientBoostingClassifier]:
    """Entraîne un HistGBT sur les labels SHORT du fold."""
    valid = (df_train["y_short"] >= 0) & df_train["future_ret_8h"].notna()
    sub   = df_train.loc[valid]

    y = sub["y_short"].values.astype(np.int32)
    n_pos = (y == 1).sum()
    if n_pos < MIN_POS_TRAIN:
        print(f"      ⚠  n_pos={n_pos} < {MIN_POS_TRAIN} — fold ignoré")
        return None

    X = sub[[f for f in features if f in sub.columns]].values.astype(np.float64)

    # Pondération pour équilibrer (SHORT rare)
    n_neg = (y == 0).sum()
    w = np.where(y == 1, n_neg / max(n_pos, 1), 1.0)

    clf = HistGradientBoostingClassifier(
        max_iter=400,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=42,
    )
    clf.fit(X, y, sample_weight=w)
    return clf


# ─── Walk-forward principal ──────────────────────────────────────────────────

def run_symbol(symbol: str, all_years: List[int]) -> Dict:
    print(f"\n{'═'*60}")
    print(f"  {symbol}USDT — walk-forward SHORT")
    print(f"{'═'*60}")

    # Chargement + resample
    print("  [1] Chargement 1-minute...")
    raw  = load_symbol(symbol, all_years)
    print(f"       {len(raw):,} barres 1-min")
    df   = resample_1h(raw)
    print(f"       {len(df):,} barres 1h après resample")

    # Features
    print("  [2] Feature engineering...")
    df = add_base_features(df)
    df = compute_all_short_features(df)   # gamechanger (NaN sur features macro)
    features = get_feature_list(df)
    print(f"       {len(features)} features disponibles")

    # Labels + régime (sur tout le dataset)
    print("  [3] Labels + régime...")
    train_mask_global = df["datetime"].dt.year.isin([2019, 2020, 2021]).values
    df, stats = build(df, train_mask_global)
    n_short = int((df["y_short"] == 1).sum())
    n_no_short = int((df["regime_short"] == "NO_SHORT").sum())
    print(f"       y_short=1 : {n_short:,}  ({n_short/len(df):.2%})")
    print(f"       regime NO_SHORT : {n_no_short:,} ({n_no_short/len(df):.1%})")

    fold_results = []

    for fi, fold in enumerate(FOLDS):
        train_years = fold["train"]
        val_year    = fold["val"]
        test_year   = fold["test"]
        print(f"\n  ── Fold {fi+1} : train={train_years}, val={val_year}, test={test_year}")

        years = df["datetime"].dt.year
        tr_mask  = years.isin(train_years).values
        val_mask = (years == val_year).values
        tst_mask = (years == test_year).values

        if not tr_mask.any() or not val_mask.any() or not tst_mask.any():
            print("     [skip] données manquantes pour ce fold")
            fold_results.append({"fold": fi + 1, "test_year": test_year, "status": "SKIP"})
            continue

        df_train = df.loc[tr_mask].reset_index(drop=True)
        df_val   = df.loc[val_mask].reset_index(drop=True)
        df_test  = df.loc[tst_mask].reset_index(drop=True)

        n_pos_tr = int((df_train["y_short"] == 1).sum())
        n_pos_val = int((df_val["y_short"] == 1).sum())
        print(f"     train n_pos={n_pos_tr}  val n_pos={n_pos_val}  test n={len(df_test)}")

        # Entraînement
        clf = train_fold(df_train, features)
        if clf is None:
            fold_results.append({"fold": fi + 1, "test_year": test_year, "status": "NO_SIGNAL"})
            continue

        # AUC val
        val_valid = (df_val["y_short"] >= 0) & df_val["future_ret_8h"].notna()
        sub_val   = df_val.loc[val_valid]
        try:
            Xv = sub_val[[f for f in features if f in sub_val.columns]].values.astype(np.float64)
            pv = clf.predict_proba(Xv)[:, 1]
            auc = roc_auc_score(sub_val["y_short"].values, pv)
        except Exception:
            auc = np.nan
        print(f"     Val AUC : {auc:.4f}" if not np.isnan(auc) else "     Val AUC : N/A")

        # Calibration seuil
        threshold, cal_info = calibrate_threshold(df_val, clf, features)
        print(f"     Threshold calibré : {threshold:.2f}  ({cal_info.get('reason','?')})")

        # Backtest test
        res = backtest_fold(df_test, clf, features, threshold)

        # Verdict
        if res["n"] == 0:
            verdict = "NO_TRADES"
        elif res["pf"] >= PF_PASS:
            verdict = "PASS"
        elif res["pf"] >= PF_WEAK:
            verdict = "WEAK"
        else:
            verdict = "FAIL"

        print(f"     Test {test_year} : n={res['n']}  PF={res['pf']:.3f}  WR={res['wr']:.1%}  "
              f"E={res['exp']:.5f}  DD={res['max_dd']:.3f}  → {verdict}")

        fold_results.append({
            "fold":       fi + 1,
            "test_year":  test_year,
            "val_auc":    round(auc, 4) if not np.isnan(auc) else None,
            "threshold":  threshold,
            "n_trades":   res["n"],
            "pf":         res["pf"],
            "wr":         res["wr"],
            "exp":        res["exp"],
            "max_dd":     res["max_dd"],
            "squeeze_rate": res["squeeze_rate"],
            "verdict":    verdict,
            "cal_info":   cal_info,
        })

    # Verdict global
    n_pass = sum(1 for r in fold_results if r.get("verdict") == "PASS")
    n_weak = sum(1 for r in fold_results if r.get("verdict") == "WEAK")
    n_fail = sum(1 for r in fold_results if r.get("verdict") in ("FAIL", "NO_TRADES"))
    n_valid = n_pass + n_weak + n_fail

    if n_valid == 0:
        global_verdict = "SKIP"
    elif n_pass >= 2:
        global_verdict = "SHORT_PROMISING"
    elif n_pass >= 1 and n_fail == 0:
        global_verdict = "SHORT_WEAK_POSITIVE"
    elif n_fail > n_pass:
        global_verdict = "SHORT_REJECTED"
    else:
        global_verdict = "SHORT_INCONCLUSIVE"

    print(f"\n  Verdict global {symbol} : {global_verdict}  "
          f"(PASS={n_pass} WEAK={n_weak} FAIL={n_fail})")

    return {
        "symbol":         symbol,
        "global_verdict": global_verdict,
        "n_pass":         n_pass,
        "n_weak":         n_weak,
        "n_fail":         n_fail,
        "folds":          fold_results,
        "n_features":     len(features),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--cost-bps", type=int, default=15)
    args = parser.parse_args()

    global COST_PCT
    COST_PCT = args.cost_bps / 10_000

    all_years = sorted({y for fold in FOLDS
                        for y in fold["train"] + [fold["val"], fold["test"]]})

    print("=" * 60)
    print("  WALK-FORWARD SHORT — BTC + ETH avec momentum gate")
    print("=" * 60)
    print(f"  Actifs : {args.symbols}")
    print(f"  Coût   : {args.cost_bps} bps")
    print(f"  Folds  : {len(FOLDS)}")
    print(f"  Horizon: {HORIZON_H}h")

    results = {}
    for sym in args.symbols:
        try:
            results[sym] = run_symbol(sym, all_years)
        except FileNotFoundError as e:
            print(f"  [ERREUR] {e}")

    # Rapport final
    print(f"\n{'='*60}")
    print("  SYNTHÈSE MULTI-ACTIF")
    print(f"{'='*60}")
    for sym, r in results.items():
        print(f"  {sym:6s}  {r['global_verdict']:<28}  "
              f"PASS={r['n_pass']} WEAK={r['n_weak']} FAIL={r['n_fail']}")

    # Sauvegarder
    out_path = REPORT_DIR / "walk_forward_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Résultats sauvegardés → {out_path}")


if __name__ == "__main__":
    main()
