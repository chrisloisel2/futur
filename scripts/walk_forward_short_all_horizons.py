#!/usr/bin/env python3
"""
walk_forward_short_all_horizons.py — Walk-forward SHORT multi-horizons
=======================================================================

Teste TOUS les horizons [1h, 2h, 3h, 4h, 6h, 8h, 12h] sur BTC + ETH.

3 fixes structurels vs pipeline précédente :
  Fix 1 — Labels moins rares    : quantile 0.80 (top 20%) au lieu de 0.84
  Fix 2 — Calibration étendue   : si val < 15 trades, ajoute dernière année de train
  Fix 3 — Gate bull fort        : ret_30d > +22% log + EMA50>EMA200 → NO_SHORT absolu

Folds :
  F1 : train=2019-2020, val=2021, test=2022  (val=bull peak → test=crash)
  F2 : train=2019-2021, val=2022, test=2023  (val=crash   → test=recovery)
  F3 : train=2019-2022, val=2023, test=2024  (val=recovery → test=bull)

Résultat : matrice horizon × fold × asset → horizons les plus prometteurs

Usage :
  python3 scripts/walk_forward_short_all_horizons.py
  python3 scripts/walk_forward_short_all_horizons.py --symbols BTC --horizons 4 8
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

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "short_all_horizons"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Paramètres globaux ──────────────────────────────────────────────────────

ALL_HORIZONS  = [1, 2, 3, 4, 6, 8, 12]   # horizons en heures
QUANTILE_SHORT = 0.80                      # Fix 1 : top 20% (était 0.84)
COST_PCT       = 0.0015                    # 15 bps stress
MIN_POS_TRAIN  = 60                        # labels SHORT min pour entraîner
MIN_TRADES_VAL = 12                        # trades min sur val pour calibration directe
MIN_TRADES_EXT = 6                         # seuil pour calibration étendue
PF_PASS        = 1.30
PF_WEAK        = 1.00

FOLDS = [
    {"train": [2019, 2020],             "val": 2021, "test": 2022},
    {"train": [2019, 2020, 2021],       "val": 2022, "test": 2023},
    {"train": [2019, 2020, 2021, 2022], "val": 2023, "test": 2024},
]

# ─── Colonnes à charger (ciblées — évite OOM) ────────────────────────────────

_LOAD_COLS = [
    "timestamp",
    "open", "high", "low", "close", "volume", "taker_buy_base",
    "rsi_14", "stoch_rsi_k", "stoch_rsi_d", "stoch_rsi_diff",
    "willr_14", "cci_20", "mfi_14", "cmf_21",
    "adx_14", "di_diff_14",
    "macd_hist", "macd_line", "squeeze_on", "squeeze_mom", "squeeze_mom_sign",
    "bb_pctb_20", "bb_width_20",
    "vwap_dist_60m", "vwap_dist_240m", "vwap_dist_1440m",
    "funding_rate", "funding_accel",
    "oi_sum", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h",
    "global_long_short_ratio", "top_trader_lsr", "lsr_z_1d",
    "taker_buy_ratio", "taker_buy_sell_ratio",
    "fear_greed",
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist",
    "ichi_senkou_a_dist", "ichi_senkou_b_dist",
    "obv_z_1h", "obv_z_4h",
    "session_asia", "session_europe", "session_us",
    "btc_spy_corr_1d", "eth_btc_ret_1d",
    "oi_price_div_1h", "basis", "basis_z_1d",
]

# ─── Imports pipeline ────────────────────────────────────────────────────────

from ai.level_0.short_features import compute_all_short_features, FEATURES_SHORT_GAMECHANGER  # noqa


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CHARGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def load_symbol(symbol: str, years: List[int]) -> pd.DataFrame:
    import pyarrow.parquet as pq
    frames = []
    for y in years:
        path = DATA_DIR / f"{y}_{symbol}USDT_features.parquet"
        if not path.exists():
            continue
        available = set(pq.ParquetFile(path).schema.names)
        cols = [c for c in _LOAD_COLS if c in available]
        df = pd.read_parquet(path, columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"Aucun parquet pour {symbol}")
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    df = df.set_index("timestamp")
    agg = {}
    for src, dst, how in [
        ("open",  "Open",   "first"), ("high",  "High",  "max"),
        ("low",   "Low",    "min"),   ("close", "Close", "last"),
        ("volume","Volume", "sum"),
    ]:
        if src in df.columns:
            agg[dst] = pd.NamedAgg(src, how)
    if "taker_buy_base" in df.columns:
        agg["taker_buy_base_asset_volume"] = pd.NamedAgg("taker_buy_base", "sum")

    h_ohlcv = df.resample("1h").agg(**agg)
    other   = [c for c in df.select_dtypes(include=[np.number]).columns
               if c not in {"open","high","low","close","volume","taker_buy_base"}]
    h_other = df[other].resample("1h").last()
    h = pd.concat([h_ohlcv, h_other], axis=1).dropna(subset=["Close"])
    h.index.name = "datetime"
    return h.reset_index()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════════════════════

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=max(span // 4, 5)).mean()

def _rsi(c: pd.Series, p: int = 14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False, min_periods=p).mean()
    l = (-d).clip(lower=0).ewm(span=p, adjust=False, min_periods=p).mean()
    return 100 - 100 / (1 + g / l.clip(lower=1e-9))

def _atr(hi: pd.Series, lo: pd.Series, cl: pd.Series, p: int = 14) -> pd.Series:
    tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False, min_periods=p).mean()

def _zs(s: pd.Series, w: int) -> pd.Series:
    mu  = s.rolling(w, min_periods=w // 2).mean()
    sig = s.rolling(w, min_periods=w // 2).std()
    return (s - mu) / sig.clip(lower=1e-9)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df   = df.copy()
    c    = df["Close"]
    hi   = df["High"]
    lo   = df["Low"]
    vol  = df["Volume"]
    logc = np.log(c.clip(lower=1e-9))

    # ── EMA / régime (toujours recalculés sur barres 1h) ─────────────────────
    e50  = _ema(c, 50)
    e200 = _ema(c, 200)
    df["ema_50"]            = e50
    df["ema_200"]           = e200
    df["ema_spread_50_200"] = (e50 - e200) / e200.clip(lower=1e-9)
    df["dist_ema_50"]       = (c - e50)    / e50.clip(lower=1e-9)
    df["dist_ema_200"]      = (c - e200)   / e200.clip(lower=1e-9)

    # ── RSI / ATR ─────────────────────────────────────────────────────────────
    if "rsi_14" not in df.columns:
        df["rsi_14"] = _rsi(c, 14)
    atr14 = _atr(hi, lo, c, 14)
    df["atr_14"]     = atr14
    df["atr_pct_14"] = atr14 / c.clip(lower=1e-9)

    # ── Momentum multi-fenêtres ────────────────────────────────────────────────
    for w in [1, 2, 3, 4, 6, 8, 12, 24, 72, 168, 720]:
        df[f"mom_logret_{w}"] = logc - logc.shift(w)
    # alias attendus
    df["mom_logret_4"]   = logc - logc.shift(4)
    df["mom_logret_12"]  = logc - logc.shift(12)
    df["mom_logret_24"]  = logc - logc.shift(24)
    df["mom_logret_72"]  = logc - logc.shift(72)
    df["mom_logret_168"] = logc - logc.shift(168)

    # ── Réalisé vol ───────────────────────────────────────────────────────────
    r1h = logc.diff()
    for w in [6, 12, 24, 48, 72, 168]:
        df[f"rv_{w}"] = r1h.rolling(w, min_periods=w//2).std() * np.sqrt(w)
    df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].clip(lower=1e-9)
    df["rv_ratio_12_48"] = df["rv_12"] / df["rv_48"].clip(lower=1e-9)

    # ── Bollinger ─────────────────────────────────────────────────────────────
    mu20 = c.rolling(20, min_periods=10).mean()
    sg20 = c.rolling(20, min_periods=10).std()
    bw   = (4 * sg20).clip(lower=1e-9)
    df["boll_width_20"] = bw / mu20.clip(lower=1e-9)
    df["boll_pos_20"]   = (c - (mu20 - 2*sg20)) / bw

    # ── Microstructure ────────────────────────────────────────────────────────
    rng = (hi - lo).clip(lower=1e-9)
    df["close_in_bar"]       = (c - lo) / rng
    df["intrabar_range_pct"] = rng / c.clip(lower=1e-9)
    df["upper_wick_pct"]     = (hi - c) / rng

    # ── Volume ────────────────────────────────────────────────────────────────
    df["vol_ratio_24"]   = vol / vol.rolling(24, min_periods=12).mean().clip(lower=1e-9)
    df["trades_ratio_24"] = df["vol_ratio_24"]   # proxy

    # ── Eff. directionnelle ───────────────────────────────────────────────────
    for w in [12, 24]:
        net = (logc - logc.shift(w)).abs()
        tot = r1h.abs().rolling(w, min_periods=w//2).sum()
        df[f"eff_ratio_{w}"] = net / tot.clip(lower=1e-9)

    # ── Z-score clôture ───────────────────────────────────────────────────────
    df["zscore_close_24"] = _zs(c, 24)

    # ── Temporel ─────────────────────────────────────────────────────────────
    hr  = df["datetime"].dt.hour
    dow = df["datetime"].dt.dayofweek
    df["hour_sin"] = np.sin(2*np.pi*hr/24)
    df["hour_cos"] = np.cos(2*np.pi*hr/24)
    df["dow_sin"]  = np.sin(2*np.pi*dow/7)
    df["dow_cos"]  = np.cos(2*np.pi*dow/7)

    # ── VWAP intraday ─────────────────────────────────────────────────────────
    if "vwap_dist_60m" not in df.columns:
        df["_date"] = df["datetime"].dt.date
        tp = (hi + lo + c) / 3
        cum_tp  = (tp * vol).groupby(df["_date"]).cumsum()
        cum_vol = vol.groupby(df["_date"]).cumsum().clip(lower=1e-9)
        vwap    = cum_tp / cum_vol
        df.drop(columns=["_date"], inplace=True)
        df["vwap"]         = vwap
        df["vwap_dist_60m"]= (c - vwap) / vwap.clip(lower=1e-9)
    df["above_vwap_4h"]  = (c > df.get("vwap", c)).astype(float)
    df["above_vwap_12h"] = df["above_vwap_4h"]
    df["distance_vwap"]  = df.get("vwap_dist_60m", pd.Series(0.0, index=df.index))
    df["dist_vwap"]      = df["distance_vwap"]

    # ── Local high/low ────────────────────────────────────────────────────────
    df["local_high_24"]           = hi.rolling(24, min_periods=12).max()
    df["local_low_24"]            = lo.rolling(24,  min_periods=12).min()
    df["dist_from_local_high_24"] = (c - df["local_high_24"]) / df["local_high_24"].clip(lower=1e-9)

    # ── Z-scores macro sur barres horaires ───────────────────────────────────
    macro_map = [
        ("funding_rate",           "funding_rate_z_24",              24),
        ("funding_rate",           "funding_rate_z_72",              72),
        ("global_long_short_ratio","global_ls_longShortRatio_z_24",  24),
        ("global_long_short_ratio","global_ls_longShortRatio_z_72",  72),
        ("oi_sum",                 "oihist_sumOpenInterest_z_24",     24),
        ("oi_sum",                 "oihist_sumOpenInterest_z_72",     72),
        ("fear_greed",             "fear_greed_value_z_24",           24),
        ("fear_greed",             "fear_greed_value_z_72",           72),
        ("taker_buy_sell_ratio",   "taker_ls_buySellRatio_z_24",      24),
    ]
    for src, dst, w in macro_map:
        if src in df.columns:
            df[dst] = _zs(pd.to_numeric(df[src], errors="coerce"), w)

    if "taker_buy_ratio" in df.columns:
        df["taker_buy_ratio_base"] = pd.to_numeric(df["taker_buy_ratio"], errors="coerce")
    if "taker_buy_sell_ratio" in df.columns:
        df["taker_ls_imbalance"] = pd.to_numeric(df["taker_buy_sell_ratio"], errors="coerce") - 1.0
    if "funding_rate" in df.columns:
        fr = pd.to_numeric(df["funding_rate"], errors="coerce")
        df["funding_accel_24"] = fr.diff(24)
        df["funding_accel_72"] = fr.diff(72)
        df["funding_extreme_positive"] = (df.get("funding_rate_z_24", pd.Series(0.0, index=df.index)) > 2.0).astype(float)

    # ── Gamechanger SHORT (OHLCV + macro si disponible) ───────────────────────
    df = compute_all_short_features(df)

    return df


# ─── Liste features finale ───────────────────────────────────────────────────

_FEATURES_BASE = [
    "dist_ema_50", "ema_spread_50_200", "dist_ema_200",
    "rsi_14", "atr_pct_14",
    "stoch_rsi_k", "stoch_rsi_d", "stoch_rsi_diff",
    "willr_14", "cci_20", "mfi_14", "cmf_21",
    "adx_14", "di_diff_14",
    "mom_logret_4", "mom_logret_12", "mom_logret_24", "mom_logret_72", "mom_logret_168",
    "rv_6", "rv_12", "rv_24", "rv_48", "rv_72", "rv_168",
    "rv_ratio_24_72", "rv_ratio_12_48",
    "boll_width_20", "boll_pos_20", "bb_pctb_20", "bb_width_20",
    "squeeze_on", "squeeze_mom", "squeeze_mom_sign",
    "close_in_bar", "intrabar_range_pct", "upper_wick_pct",
    "vol_ratio_24", "eff_ratio_12", "eff_ratio_24", "zscore_close_24",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "session_asia", "session_europe", "session_us",
    "above_vwap_4h", "dist_vwap", "vwap_dist_60m", "vwap_dist_240m", "vwap_dist_1440m",
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72",
    "funding_accel_24", "funding_accel_72", "funding_accel",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    "oi_sum", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h", "oi_price_div_1h",
    "global_long_short_ratio", "global_ls_longShortRatio_z_24", "global_ls_longShortRatio_z_72",
    "top_trader_lsr", "lsr_z_1d",
    "taker_buy_ratio", "taker_buy_sell_ratio", "taker_ls_buySellRatio_z_24", "taker_ls_imbalance",
    "fear_greed", "fear_greed_value_z_24", "fear_greed_value_z_72",
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    "obv_z_1h", "obv_z_4h",
    "macd_hist", "macd_line",
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist", "ichi_senkou_a_dist", "ichi_senkou_b_dist",
    "btc_spy_corr_1d", "eth_btc_ret_1d",
    "basis", "basis_z_1d",
]


def get_features(df: pd.DataFrame) -> List[str]:
    candidates = _FEATURES_BASE + FEATURES_SHORT_GAMECHANGER
    seen, out = set(), []
    for f in candidates:
        if f in seen or f not in df.columns:
            continue
        seen.add(f)
        if df[f].isna().mean() < 0.60:
            out.append(f)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LABELS MULTI-HORIZONS
# ═══════════════════════════════════════════════════════════════════════════════

def add_forward_returns(df: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    """Calcule future_ret_{H}h et max_1h_ret_{2H}h pour chaque horizon H."""
    df   = df.copy()
    logc = np.log(df["Close"].values.astype(np.float64))
    n    = len(logc)
    r1h  = np.diff(logc, prepend=np.nan)
    r1h_safe = np.where(np.isnan(r1h), 0.0, r1h)

    for H in horizons:
        fwd = np.full(n, np.nan)
        fwd[:n-H] = logc[H:] - logc[:n-H]
        df[f"future_ret_{H}h"] = fwd

        # max sur fenêtre 2H (anti-squeeze : max 1h-ret parmi les 2H suivantes)
        W   = 2 * H
        mx  = np.full(n, np.nan)
        shifted = r1h_safe[1:]
        if len(shifted) >= W:
            from numpy.lib.stride_tricks import sliding_window_view
            wins = sliding_window_view(shifted, window_shape=W)
            valid = wins.shape[0]
            mx[:valid] = wins.max(axis=1)
        df[f"max_ret_{W}h"] = mx

    return df


def build_labels_h(
    df: pd.DataFrame,
    H: int,
    train_mask: np.ndarray,
    quantile: float = QUANTILE_SHORT,
    cost_pct: float = COST_PCT,
    nrev_factor: float = 0.40,
    gray_factor: float = 0.15,
) -> pd.DataFrame:
    """
    Construit y_short_{H}h et regime_short_{H}h pour un horizon H donné.
    Fix 1 intégré : quantile=0.80 par défaut.
    """
    fwd_col = f"future_ret_{H}h"
    rev_col = f"max_ret_{2*H}h"
    lbl_col = f"y_short_{H}h"

    if fwd_col not in df.columns:
        raise RuntimeError(f"Colonne manquante : {fwd_col}")

    df   = df.copy()
    ret  = df[fwd_col].values.astype(np.float64)

    # Seuil calibré sur train uniquement
    ret_train = np.abs(ret[train_mask & np.isfinite(ret)])
    thr_raw   = float(np.quantile(ret_train, quantile))
    thr       = thr_raw + cost_pct
    df.attrs[f"thr_short_{H}h"] = thr

    raw_short = ret < -thr

    # Filtre non-retournement (anti-squeeze)
    if rev_col in df.columns:
        rev = df[rev_col].values.astype(np.float64)
        no_rev = rev < thr_raw * nrev_factor
        pos    = raw_short & no_rev
        gray   = raw_short & ~no_rev
    else:
        pos  = raw_short
        gray = np.zeros(len(ret), dtype=bool)

    y = np.zeros(len(ret), dtype=np.int8)
    y[pos]  = 1
    y[gray] = -1   # gray zone → exclus du training

    # Gray zone supplémentaire (frontière)
    thr_lo = thr * (1 + gray_factor)
    border = (ret < -thr) & (ret > -thr_lo) & (y == 1)
    y[border] = -1

    df[lbl_col] = y
    return df


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gate régime avec les 3 fixes :
    - EMA gate (lente, existante)
    - Momentum gate (rapide, conditionnelle au macro-bear)
    - Fix 3 : bull fort (ret_30d > +22% log + EMA50>EMA200) → NO_SHORT absolu
    """
    df = df.copy()

    above50  = df["dist_ema_50"]       > 0
    dc_bull  = df["ema_spread_50_200"] > 0   # EMA50 > EMA200
    rsi_bull = df["rsi_14"]            > 55

    # Gate EMA classique
    gate_ema = above50 & dc_bull & rsi_bull

    # Macro-bear confirmé (neutralise la momentum gate)
    macro_bear = (~dc_bull)
    if "mom_logret_72" in df.columns:
        macro_bear = macro_bear & (df["mom_logret_72"] < -0.05)

    # Momentum gate rapide (ret_7d, ret_3d) — désactivée en macro-bear confirmé
    ret7d = df.get("mom_logret_168", pd.Series(0.0, index=df.index))
    ret3d = df.get("mom_logret_72",  pd.Series(0.0, index=df.index))
    gate_momentum = ((ret7d > 0.08) | (ret3d > 0.05)) & (~macro_bear)

    # Fix 3 — Bull fort absolu (indépendant du death cross)
    ret30d = df.get("mom_logret_720", pd.Series(0.0, index=df.index))
    gate_bull_strong = (ret30d > 0.22) & dc_bull   # +22% log ≈ +25% en 30j + EMA50>EMA200

    no_short = gate_ema | gate_momentum | gate_bull_strong

    # SHORTABLE : death cross + RSI bearish + pas en NO_SHORT
    rsi_bear    = df["rsi_14"] < 48
    dist24      = df.get("dist_from_local_high_24", pd.Series(-0.05, index=df.index))
    shortable_s = (~above50) & (~dc_bull) & rsi_bear
    shortable_x = (~above50) & (df["rsi_14"] < 42) & (dist24 < -0.015)
    shortable   = (shortable_s | shortable_x) & (~no_short)

    df["regime_short"] = np.where(no_short, "NO_SHORT",
                         np.where(shortable, "SHORTABLE", "NEUTRAL"))
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TRAINING
# ═══════════════════════════════════════════════════════════════════════════════

def train(df_tr: pd.DataFrame, features: List[str], lbl_col: str) -> Optional[HistGradientBoostingClassifier]:
    valid = (df_tr[lbl_col] >= 0) & df_tr[lbl_col].notna()
    sub   = df_tr.loc[valid]
    y     = sub[lbl_col].values.astype(np.int32)
    n_pos = (y == 1).sum()
    if n_pos < MIN_POS_TRAIN:
        return None

    avail = [f for f in features if f in sub.columns]
    X = sub[avail].values.astype(np.float64)
    n_neg = (y == 0).sum()
    w = np.where(y == 1, n_neg / max(n_pos, 1), 1.0)

    clf = HistGradientBoostingClassifier(
        max_iter=500, max_depth=4, learning_rate=0.04,
        min_samples_leaf=15, l2_regularization=1.0, random_state=42,
    )
    clf.fit(X, y, sample_weight=w)
    return clf


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CALIBRATION (Fix 2)
# ═══════════════════════════════════════════════════════════════════════════════

def calibrate(
    df_val: pd.DataFrame,
    clf: HistGradientBoostingClassifier,
    features: List[str],
    lbl_col: str,
    df_ext: Optional[pd.DataFrame] = None,  # Fix 2 : données supplémentaires
) -> Tuple[float, int]:
    """
    Sweep de seuils sur val. Sélectionne PF>=1.05, WR>=0.48, n>=MIN_TRADES_VAL.
    Fix 2 : si val trop petite, étend avec la dernière année de train.
    """
    def _candidates(df_sub: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        valid = (df_sub["regime_short"] != "NO_SHORT") & (df_sub[lbl_col] >= 0)
        fwd_col = lbl_col.replace("y_short_", "future_ret_")  # y_short_4h → future_ret_4h
        if fwd_col not in df_sub.columns:
            return np.array([]), np.array([])
        valid &= df_sub[fwd_col].notna()
        sub = df_sub.loc[valid]
        if len(sub) < 2:
            return np.array([]), np.array([])
        avail = [f for f in features if f in sub.columns]
        X = sub[avail].values.astype(np.float64)
        p = clf.predict_proba(X)[:, 1]
        fwds = sub[fwd_col].values
        return p, fwds

    p_val, fwds_val = _candidates(df_val)

    # Fix 2 : si pas assez de données val, étendre
    if len(p_val) < MIN_TRADES_EXT and df_ext is not None:
        p_ext, fwds_ext = _candidates(df_ext)
        p_val   = np.concatenate([p_val,   p_ext])
        fwds_val= np.concatenate([fwds_val, fwds_ext])

    if len(p_val) < MIN_TRADES_EXT:
        return 0.72, 0

    best_thr, best_score = 0.72, -1.0
    for thr in np.arange(0.50, 0.91, 0.02):
        mask = p_val >= thr
        n    = mask.sum()
        if n < MIN_TRADES_EXT:
            continue
        pnl  = -fwds_val[mask] - COST_PCT
        wins = pnl[pnl > 0]
        loss = abs(pnl[pnl < 0].sum())
        pf   = wins.sum() / loss if loss > 1e-9 else float("inf")
        wr   = (pnl > 0).mean()
        score = pf * np.sqrt(n)
        if pf >= 1.05 and wr >= 0.48 and n >= MIN_TRADES_EXT and score > best_score:
            best_score = score
            best_thr   = round(thr, 2)

    return best_thr, int(len(p_val))


# ═══════════════════════════════════════════════════════════════════════════════
# 6. BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════

def backtest(
    df_test: pd.DataFrame,
    clf: HistGradientBoostingClassifier,
    features: List[str],
    lbl_col: str,
    threshold: float,
) -> Dict:
    fwd_col = lbl_col.replace("y_short_", "future_ret_")
    avail   = [f for f in features if f in df_test.columns]
    pnls    = []

    for _, row in df_test.iterrows():
        if row.get("regime_short", "NEUTRAL") == "NO_SHORT":
            continue
        if row.get(lbl_col, -1) == -1:
            continue
        fwd = row.get(fwd_col)
        if pd.isna(fwd):
            continue
        x = np.array([[row.get(f, np.nan) for f in avail]], dtype=np.float64)
        try:
            p = clf.predict_proba(x)[0, 1]
        except Exception:
            continue
        if p < threshold:
            continue
        pnls.append(-fwd - COST_PCT)

    if not pnls:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan, "max_dd": np.nan}

    arr  = np.array(pnls)
    wins = arr[arr > 0]
    loss = abs(arr[arr < 0].sum())
    pf   = wins.sum() / loss if loss > 1e-9 else float("inf")
    cum  = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    dd   = float((cum - peak).min())

    return {
        "n":      len(arr),
        "pf":     round(float(pf), 3),
        "wr":     round(float((arr > 0).mean()), 3),
        "exp":    round(float(arr.mean()), 5),
        "max_dd": round(dd, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. WALK-FORWARD PAR ACTIF
# ═══════════════════════════════════════════════════════════════════════════════

def run_symbol(symbol: str, horizons: List[int], all_years: List[int]) -> Dict:
    print(f"\n{'═'*65}")
    print(f"  {symbol}USDT — walk-forward SHORT multi-horizons")
    print(f"{'═'*65}")

    print("  [1] Chargement + resample 1h...")
    raw = load_symbol(symbol, all_years)
    df  = resample_1h(raw)
    print(f"       {len(df):,} barres horaires")

    print("  [2] Feature engineering...")
    df = add_features(df)
    features = get_features(df)
    print(f"       {len(features)} features disponibles (dont macro)")

    print("  [3] Forward returns (tous horizons)...")
    df = add_forward_returns(df, horizons)

    print("  [4] Régime (patch EMA + momentum + bull_fort)...")
    df = add_regime(df)
    ns = (df["regime_short"] == "NO_SHORT").sum()
    sh = (df["regime_short"] == "SHORTABLE").sum()
    print(f"       NO_SHORT={ns/len(df):.1%}  SHORTABLE={sh/len(df):.1%}")

    years = df["datetime"].dt.year

    # Résultats par horizon
    hz_results: Dict[int, List[Dict]] = {H: [] for H in horizons}

    for H in horizons:
        lbl_col   = f"y_short_{H}h"
        fwd_col   = f"future_ret_{H}h"
        print(f"\n  ── Horizon {H}h ────────────────────────────────────────")

        # Masque global train (2019-2021) pour calibrer le seuil une fois
        global_train_mask = years.isin([2019, 2020, 2021]).values
        df = build_labels_h(df, H, global_train_mask)
        thr = df.attrs.get(f"thr_short_{H}h", 0.0)
        n_pos_tot = int((df[lbl_col] == 1).sum())
        print(f"     thr={thr:.4f} ({thr*100:.2f}%)  y_short=1: {n_pos_tot} ({n_pos_tot/len(df):.2%})")

        for fi, fold in enumerate(FOLDS):
            tr_yrs   = fold["train"]
            val_yr   = fold["val"]
            tst_yr   = fold["test"]

            # Masque fold
            tr_mask  = years.isin(tr_yrs).values
            val_mask = (years == val_yr).values
            tst_mask = (years == tst_yr).values

            if not (tr_mask.any() and val_mask.any() and tst_mask.any()):
                hz_results[H].append({"fold": fi+1, "test_year": tst_yr, "status": "SKIP"})
                continue

            df_tr  = df.loc[tr_mask].reset_index(drop=True)
            df_val = df.loc[val_mask].reset_index(drop=True)
            df_tst = df.loc[tst_mask].reset_index(drop=True)

            # Recompute labels with fold-specific train mask
            fold_train_mask = np.zeros(len(df), dtype=bool)
            fold_train_mask[tr_mask] = True
            df = build_labels_h(df, H, fold_train_mask)
            df_tr  = df.loc[tr_mask].reset_index(drop=True)
            df_val = df.loc[val_mask].reset_index(drop=True)
            df_tst = df.loc[tst_mask].reset_index(drop=True)

            n_pos_tr = int((df_tr[lbl_col] == 1).sum())

            # Entraînement
            clf = train(df_tr, features, lbl_col)
            if clf is None:
                print(f"     F{fi+1} test={tst_yr}: n_pos_train={n_pos_tr} < {MIN_POS_TRAIN} → SKIP")
                hz_results[H].append({"fold": fi+1, "test_year": tst_yr, "status": "NO_SIGNAL",
                                      "n_pos_train": n_pos_tr})
                continue

            # Val AUC
            valid_val = (df_val[lbl_col] >= 0) & df_val[fwd_col].notna()
            sub_val   = df_val.loc[valid_val]
            auc = np.nan
            if len(sub_val) >= 10 and sub_val[lbl_col].nunique() > 1:
                avail = [f for f in features if f in sub_val.columns]
                Xv    = sub_val[avail].values.astype(np.float64)
                pv    = clf.predict_proba(Xv)[:, 1]
                auc   = roc_auc_score(sub_val[lbl_col].values, pv)

            # Calibration (Fix 2 : extension si val petite)
            # Dernière année de train comme extension
            last_tr_yr = max(tr_yrs)
            df_ext     = df.loc[(years == last_tr_yr).values].reset_index(drop=True) if last_tr_yr != val_yr else None
            threshold, n_cal = calibrate(df_val, clf, features, lbl_col, df_ext)

            # Backtest
            res = backtest(df_tst, clf, features, lbl_col, threshold)

            verdict = ("NO_TRADES" if res["n"] == 0
                       else "PASS" if res["pf"] >= PF_PASS
                       else "WEAK" if res["pf"] >= PF_WEAK
                       else "FAIL")

            print(f"     F{fi+1} test={tst_yr}: "
                  f"n_tr={n_pos_tr:3d}  AUC={auc:.3f}  thr={threshold:.2f}  "
                  f"n={res['n']:3d}  PF={res['pf']:.3f}  WR={res['wr']:.1%}  → {verdict}")

            hz_results[H].append({
                "fold": fi+1, "test_year": tst_yr,
                "n_pos_train": n_pos_tr,
                "val_auc": round(auc, 4) if not np.isnan(auc) else None,
                "threshold": threshold, "n_cal": n_cal,
                **res,
                "verdict": verdict,
            })

    return {"symbol": symbol, "n_features": len(features), "horizons": hz_results}


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RAPPORT SYNTHÈSE
# ═══════════════════════════════════════════════════════════════════════════════

def print_summary(all_results: Dict[str, Dict]) -> None:
    horizons = sorted({H for r in all_results.values() for H in r["horizons"]})
    symbols  = list(all_results.keys())

    print(f"\n{'='*75}")
    print("  SYNTHÈSE — MATRIX HORIZONS × ACTIFS")
    print(f"{'='*75}")
    print(f"  {'H':>3}  ", end="")
    for sym in symbols:
        print(f"  {'':.<20}", end="")
    print()

    header = f"  {'H':>3}h  "
    for sym in symbols:
        header += f"  {'['+sym+']':^24}"
    print(header)

    subh = f"  {'':>4}  "
    for _ in symbols:
        subh += f"  {'F1':>6} {'F2':>6} {'F3':>6} {'PASS':>4}"
    print(subh)
    print(f"  {'─'*70}")

    best: Dict[str, Dict] = {}   # meilleur par actif

    for H in horizons:
        row = f"  {H:>3}h  "
        for sym in symbols:
            hz = all_results[sym]["horizons"].get(H, [])
            fold_pf = []
            n_pass  = 0
            for r in hz:
                pf = r.get("pf", np.nan)
                v  = r.get("verdict", "?")
                fold_pf.append(pf)
                if v == "PASS":
                    n_pass += 1
            while len(fold_pf) < 3:
                fold_pf.append(np.nan)
            row += "  "
            for pf in fold_pf:
                row += f" {pf:>6.3f}" if not np.isnan(pf) else f"  {'—':>5}"
            row += f" {'✓'*n_pass if n_pass else '':>4}"

            # Mettre à jour le meilleur pour cet actif
            if n_pass > best.get(sym, {}).get("n_pass", -1):
                best[sym] = {"H": H, "n_pass": n_pass, "fold_pf": fold_pf}
        print(row)

    print(f"\n  {'─'*70}")
    print("  MEILLEURS HORIZONS PAR ACTIF")
    for sym, b in best.items():
        pf_str = " / ".join(f"{p:.3f}" if not np.isnan(p) else "—" for p in b["fold_pf"])
        print(f"    {sym:6s} → {b['H']}h   PASS={b['n_pass']}/3   PF=[{pf_str}]")

    print(f"\n  LÉGENDE : PASS = PF ≥ {PF_PASS}  WEAK = PF ≥ {PF_WEAK}  FAIL = PF < {PF_WEAK}")


# ═══════════════════════════════════════════════════════════════════════════════
# 9. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols",  nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--horizons", nargs="+", type=int, default=ALL_HORIZONS)
    parser.add_argument("--cost-bps", type=int,  default=15)
    args = parser.parse_args()

    global COST_PCT
    COST_PCT = args.cost_bps / 10_000

    all_years = sorted({y for f in FOLDS for y in f["train"] + [f["val"], f["test"]]})

    print("=" * 65)
    print("  WALK-FORWARD SHORT — TOUS HORIZONS — BTC + ETH")
    print("=" * 65)
    print(f"  Horizons : {args.horizons}h")
    print(f"  Actifs   : {args.symbols}")
    print(f"  Coût     : {args.cost_bps} bps stress")
    print(f"  Fix 1    : quantile={QUANTILE_SHORT} (top {(1-QUANTILE_SHORT)*100:.0f}%)")
    print(f"  Fix 2    : calibration étendue si val < {MIN_TRADES_EXT} trades")
    print(f"  Fix 3    : gate bull fort (ret_30d > 22% log + EMA50>EMA200)")

    all_results: Dict[str, Dict] = {}
    for sym in args.symbols:
        try:
            all_results[sym] = run_symbol(sym, args.horizons, all_years)
        except Exception as e:
            print(f"  [ERREUR] {sym}: {e}")
            import traceback; traceback.print_exc()

    print_summary(all_results)

    out = REPORT_DIR / "all_horizons_results.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Résultats → {out}")


if __name__ == "__main__":
    main()
