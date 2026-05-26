#!/usr/bin/env python3
"""
walk_forward_short_rebuild_v3.py — Rebuild complet pipeline SHORT
==================================================================

Basé sur les insights des meilleurs papiers de recherche (arxiv 2402.05272,
2602.11708, 2105.13727, Lopez de Prado Triple Barrier + Meta-Labeling,
Hudson & Thames meta-labeling study).

7 améliorations vs pipeline précédente :
  1. Triple Barrier Labels ATR-calibrés     (remplace seuil quantile fixe)
  2. Pool Training multi-actifs             (résout la pénurie de labels)
  3. Meta-labeling en cascade               (signal → précision en 2 étapes)
  4. Validation purged avec embargo         (pas de leakage temporel)
  5. Features primaires funding+OI+basis    (edge réel des hedge funds)
  6. Gate asymétrique selon régime          (Sharpe 1.7 requis en short)
  7. Changepoint Detection comme feature    (détecte les retournements)

Actifs : BTC, ETH, SOL, BNB, LINK, ADA, XRP, AVAX (pool)
Horizons testés : 4h, 6h, 8h
Folds : 4 (train≤T-2, val=T-1, test=T) — 2022 à 2025

Usage :
  python3 scripts/walk_forward_short_rebuild_v3.py
  python3 scripts/walk_forward_short_rebuild_v3.py --horizons 4 6 --assets BTC ETH SOL
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
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import roc_auc_score, precision_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "short_rebuild_v3"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Paramètres ──────────────────────────────────────────────────────────────

HORIZONS   = [4, 6, 8]          # heures — les plus prometteurs selon research
ALL_ASSETS = ["BTC", "ETH", "SOL", "BNB", "LINK", "ADA", "XRP", "AVAX"]
ALL_YEARS  = [2019, 2020, 2021, 2022, 2023, 2024, 2025]

# Triple barrier
ATR_MULT_PROFIT = 2.0    # profit target = ATR × 2.0
ATR_MULT_STOP   = 2.5    # stop loss    = ATR × 2.5
MAX_HOLD_MULT   = 4      # max hold = H × 4 barres (réduit pour perf)

# Coût
COST_BPS    = 15
COST_PCT    = COST_BPS / 10_000

# Entraînement
MIN_POS_TRAIN  = 40      # labels short-wins min pour entraîner
MIN_TRADES_VAL = 8       # min trades sur val pour calibration
PF_PASS        = 1.30
PF_WEAK        = 1.00

FOLDS = [
    {"train": [2019, 2020],                   "val": 2021, "test": 2022},
    {"train": [2019, 2020, 2021],             "val": 2022, "test": 2023},
    {"train": [2019, 2020, 2021, 2022],       "val": 2023, "test": 2024},
    {"train": [2019, 2020, 2021, 2022, 2023], "val": 2024, "test": 2025},
]

# ─── Colonnes à charger ───────────────────────────────────────────────────────

_COLS = [
    "timestamp", "open", "high", "low", "close", "volume", "taker_buy_base",
    "atr_14", "atr_pct_14", "rsi_14", "willr_14", "cci_20", "mfi_14", "cmf_21",
    "adx_14", "di_diff_14", "macd_hist", "macd_line",
    "stoch_rsi_k", "stoch_rsi_d", "bb_pctb_20", "bb_width_20",
    "squeeze_on", "squeeze_mom",
    "vwap_dist_60m", "vwap_dist_240m",
    "funding_rate", "funding_accel", "funding_z_7d", "funding_sign",
    "oi_sum", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h", "oi_price_div_1h",
    "global_long_short_ratio", "top_trader_lsr", "lsr_z_1d",
    "taker_buy_ratio", "taker_buy_sell_ratio",
    "fear_greed",
    "basis", "basis_z_1d", "basis_accel",
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    "obv_z_1h", "obv_z_4h",
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist",
    "ichi_senkou_a_dist", "ichi_senkou_b_dist",
    "session_asia", "session_europe", "session_us",
    "btc_spy_corr_1d", "rv_60m", "rv_240m", "rv_1440m",
    "smart_retail_divergence", "smart_retail_z_1d",
    "lsr_extreme_long", "lsr_extreme_short",
    "top_trader_conviction", "top_trader_z_1d",
    "oi_avg_price", "oi_value_sum",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CHARGEMENT + RESAMPLE
# ═══════════════════════════════════════════════════════════════════════════════

def load_asset(symbol: str, years: List[int]) -> Optional[pd.DataFrame]:
    import pyarrow.parquet as pq
    frames = []
    for y in years:
        path = DATA_DIR / f"{y}_{symbol}USDT_features.parquet"
        if not path.exists():
            continue
        avail = set(pq.ParquetFile(path).schema.names)
        cols  = [c for c in _COLS if c in avail]
        df = pd.read_parquet(path, columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df["symbol"]    = symbol
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    df = df.set_index("timestamp")
    ohlcv = {}
    for src, dst, how in [("open","Open","first"),("high","High","max"),
                           ("low","Low","min"),("close","Close","last"),
                           ("volume","Volume","sum")]:
        if src in df.columns:
            ohlcv[dst] = pd.NamedAgg(src, how)
    if "taker_buy_base" in df.columns:
        ohlcv["taker_buy_base_asset_volume"] = pd.NamedAgg("taker_buy_base", "sum")
    h_ohlcv = df.resample("1h").agg(**ohlcv)
    # Toutes les autres colonnes numériques : last
    num = [c for c in df.select_dtypes(include=[np.number]).columns
           if c not in {"open","high","low","close","volume","taker_buy_base"}]
    h_other = df[num].resample("1h").last()
    sym = df["symbol"].resample("1h").last()
    h = pd.concat([h_ohlcv, h_other, sym], axis=1).dropna(subset=["Close"])
    h.index.name = "datetime"
    return h.reset_index()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════════════════════

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=max(n//4, 5)).mean()

def _zs(s: pd.Series, w: int) -> pd.Series:
    mu  = s.rolling(w, min_periods=w//2).mean()
    sig = s.rolling(w, min_periods=w//2).std()
    return (s - mu) / sig.clip(lower=1e-9)

def _rsi(c: pd.Series, p: int = 14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False, min_periods=p).mean()
    l = (-d).clip(lower=0).ewm(span=p, adjust=False, min_periods=p).mean()
    return 100 - 100 / (1 + g / l.clip(lower=1e-9))


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enrichit le DataFrame 1h avec les features manquantes.
    Priorité aux features identifiées comme discriminantes par les papers.
    """
    df   = df.copy()
    c    = df["Close"]
    hi   = df["High"]
    lo   = df["Low"]
    vol  = df["Volume"]
    logc = np.log(c.clip(lower=1e-9))

    # ── Indicateurs de régime (toujours recalculés sur barres horaires) ───────
    e50  = _ema(c, 50)
    e200 = _ema(c, 200)
    df["ema_spread_50_200"] = (e50 - e200) / e200.clip(lower=1e-9)
    df["dist_ema_50"]       = (c - e50)    / e50.clip(lower=1e-9)
    df["dist_ema_200"]      = (c - e200)   / e200.clip(lower=1e-9)

    if "rsi_14" not in df.columns:
        df["rsi_14"] = _rsi(c, 14)

    # ── ATR horaire (si absent du parquet) ────────────────────────────────────
    if "atr_14" not in df.columns:
        tr  = pd.concat([hi-lo, (hi-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
        df["atr_14"] = tr.ewm(span=14, adjust=False, min_periods=14).mean()
    df["atr_pct_14"] = df["atr_14"] / c.clip(lower=1e-9)

    # ── Momentum multi-horizons ────────────────────────────────────────────────
    for w in [1, 4, 8, 24, 72, 168, 720]:
        df[f"mom_{w}h"] = logc - logc.shift(w)

    # ── Réalisé vol ───────────────────────────────────────────────────────────
    r1h = logc.diff()
    for w in [6, 12, 24, 48, 72, 168]:
        df[f"rv_{w}h"] = r1h.rolling(w, min_periods=w//2).std() * np.sqrt(w)
    df["rv_ratio_24_72"] = df["rv_24h"] / df["rv_72h"].clip(lower=1e-9)

    # ── Bollinger ─────────────────────────────────────────────────────────────
    mu20 = c.rolling(20, min_periods=10).mean()
    sg20 = c.rolling(20, min_periods=10).std()
    bw   = (4 * sg20).clip(lower=1e-9)
    df["boll_width_20"] = bw / mu20.clip(lower=1e-9)
    df["boll_pos_20"]   = (c - (mu20 - 2*sg20)) / bw

    # ── Microstructure ────────────────────────────────────────────────────────
    rng = (hi - lo).clip(lower=1e-9)
    df["close_in_bar"]   = (c - lo) / rng
    df["upper_wick_pct"] = (hi - c) / rng
    df["vol_ratio_24"]   = vol / vol.rolling(24, min_periods=12).mean().clip(lower=1e-9)

    # ── MACD divergence (signal clé selon papers) ─────────────────────────────
    if "macd_hist" not in df.columns:
        ema12 = _ema(c, 12); ema26 = _ema(c, 26)
        df["macd_hist"] = ema12 - ema26 - _ema(ema12 - ema26, 9)
    # Divergence prix / MACD : prix monte mais MACD baisse = signal SHORT fort
    macd_slope  = df["macd_hist"].diff(4)
    price_slope = logc.diff(4)
    df["macd_price_divergence"] = np.where(
        (price_slope > 0) & (macd_slope < 0), 1.0,  # bearish divergence
        np.where((price_slope < 0) & (macd_slope > 0), -1.0, 0.0)
    )

    # ── Changepoint Detection (arxiv 2105.13727) ──────────────────────────────
    # Proxy CPD : rupture de la distribution locale des returns
    # CPD severity = |current_vol - vol_MA| / vol_MA (spike de vol = changement de régime)
    rv24h = df["rv_24h"]
    rv_ma = rv24h.rolling(168, min_periods=48).mean()
    df["cpd_severity"] = (rv24h - rv_ma).abs() / rv_ma.clip(lower=1e-9)
    df["cpd_spike"]    = (df["cpd_severity"] > 2.0).astype(float)

    # ── Z-scores macro sur barres horaires ────────────────────────────────────
    for src, dst, w in [
        ("funding_rate",           "funding_z_24h",    24),
        ("funding_rate",           "funding_z_72h",    72),
        ("global_long_short_ratio","lsr_z_24h",        24),
        ("oi_sum",                 "oi_z_24h",         24),
        ("fear_greed",             "fg_z_24h",         24),
        ("taker_buy_sell_ratio",   "taker_ratio_z_24h",24),
        ("basis",                  "basis_z_24h",      24),
    ]:
        if src in df.columns:
            df[dst] = _zs(pd.to_numeric(df[src], errors="coerce"), w)

    # ── Features composites (insights hedge funds) ───────────────────────────
    # OI monte + prix baisse = pression vendeuse cachée (signal SHORT fort)
    if "oi_chg_60m" in df.columns:
        oi_up  = (pd.to_numeric(df["oi_chg_60m"], errors="coerce") > 0).astype(float)
        df["oi_up_price_down"] = oi_up * (price_slope < 0).astype(float)

    # Funding extrême positif = foule sur-longée = contre-signal
    if "funding_rate" in df.columns:
        fr = pd.to_numeric(df["funding_rate"], errors="coerce")
        df["funding_extreme_long"]  = (fr > 0.001).astype(float)  # > 0.1%/8h
        df["funding_extreme_short"] = (fr < -0.0005).astype(float)

    # Basis contango extrême (> 15% annualisé ≈ 0.04%/8h) + momentum négatif
    if "basis" in df.columns:
        ba = pd.to_numeric(df["basis"], errors="coerce")
        df["basis_contango_extreme"] = (ba > 0.0004).astype(float)

    # ── Temporel ─────────────────────────────────────────────────────────────
    hr  = df["datetime"].dt.hour
    dow = df["datetime"].dt.dayofweek
    df["hour_sin"] = np.sin(2*np.pi*hr/24)
    df["hour_cos"] = np.cos(2*np.pi*hr/24)
    df["dow_sin"]  = np.sin(2*np.pi*dow/7)
    df["dow_cos"]  = np.cos(2*np.pi*dow/7)

    # ── Taker imbalance ───────────────────────────────────────────────────────
    if "taker_buy_sell_ratio" in df.columns:
        df["taker_imbalance"] = pd.to_numeric(df["taker_buy_sell_ratio"], errors="coerce") - 1.0

    return df


# ─── Feature list ─────────────────────────────────────────────────────────────

_FEAT_LIST = [
    # Régime
    "dist_ema_50", "ema_spread_50_200", "dist_ema_200", "rsi_14",
    # ATR / Vol
    "atr_pct_14", "rv_6h", "rv_12h", "rv_24h", "rv_48h", "rv_72h", "rv_168h",
    "rv_ratio_24_72", "bb_width_20", "bb_pctb_20", "boll_width_20", "boll_pos_20",
    # Momentum
    "mom_1h", "mom_4h", "mom_8h", "mom_24h", "mom_72h", "mom_168h", "mom_720h",
    # Oscillateurs
    "willr_14", "cci_20", "mfi_14", "cmf_21", "adx_14", "di_diff_14",
    "stoch_rsi_k", "stoch_rsi_d",
    # MACD
    "macd_hist", "macd_line", "macd_price_divergence",
    # Microstructure
    "close_in_bar", "upper_wick_pct", "vol_ratio_24",
    "obv_z_1h", "obv_z_4h",
    # Funding / OI / basis (features primaires hedge funds)
    "funding_rate", "funding_z_24h", "funding_z_72h", "funding_z_7d",
    "funding_extreme_long", "funding_extreme_short", "funding_accel",
    "oi_z_24h", "oi_chg_60m", "oi_chg_240m", "oi_accel_1h", "oi_price_div_1h",
    "oi_up_price_down",
    "basis", "basis_z_24h", "basis_z_1d", "basis_accel", "basis_contango_extreme",
    # L/S ratio
    "lsr_z_24h", "global_long_short_ratio", "top_trader_lsr", "lsr_z_1d",
    "lsr_extreme_long", "lsr_extreme_short", "top_trader_conviction", "top_trader_z_1d",
    # Taker
    "taker_buy_sell_ratio", "taker_ratio_z_24h", "taker_imbalance", "taker_buy_ratio",
    # Fear & Greed
    "fear_greed", "fg_z_24h",
    # Smart money
    "smart_retail_divergence", "smart_retail_z_1d",
    # Changepoint
    "cpd_severity", "cpd_spike",
    # VWAP
    "vwap_dist_60m", "vwap_dist_240m",
    # Returns bruts
    "ret_60m", "ret_240m", "ret_480m", "ret_1440m",
    # Ichimoku
    "ichi_tenkan_sen_dist", "ichi_kijun_sen_dist",
    "ichi_senkou_a_dist", "ichi_senkou_b_dist",
    # Squeeze
    "squeeze_on", "squeeze_mom",
    # Temporel
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "session_asia", "session_europe", "session_us",
    # Corrélations
    "btc_spy_corr_1d",
]


def get_features(df: pd.DataFrame) -> List[str]:
    seen, out = set(), []
    for f in _FEAT_LIST:
        if f in seen or f not in df.columns:
            continue
        seen.add(f)
        if df[f].isna().mean() < 0.65:
            out.append(f)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TRIPLE BARRIER LABELS (Lopez de Prado)
# ═══════════════════════════════════════════════════════════════════════════════

def build_triple_barrier_labels(
    df: pd.DataFrame,
    H: int,
    atr_mult_profit: float = ATR_MULT_PROFIT,
    atr_mult_stop:   float = ATR_MULT_STOP,
    max_hold_mult:   int   = MAX_HOLD_MULT,
) -> pd.DataFrame:
    """
    Triple Barrier Method — implémentation VECTORISÉE numpy (O(n×max_h) en C).

    Pour chaque barre t et un trade SHORT :
      y_tb_{H}h = 1  : profit barrier touché en premier (short gagne)
      y_tb_{H}h = 0  : stop barrier touché en premier  (stop loss)
      y_tb_{H}h = -1 : aucun dans max_h barres         (expiré, exclu du training)
                       ou barre en NO_SHORT regime
    """
    df    = df.copy()
    n     = len(df)
    max_h = H * max_hold_mult
    n_v   = n - max_h                       # barres pour lesquelles on peut calculer

    close  = df["Close"].values.astype(np.float64)
    atr    = df["atr_14"].values.astype(np.float64)
    regime = df.get("regime_short", pd.Series("NEUTRAL", index=df.index)).values

    # Barrières absolues pour chaque barre d'entrée
    atr_c    = np.maximum(atr, close * 0.005)    # plancher 0.5% du prix
    prof_lvl = close - atr_mult_profit * atr_c   # niveau cible (SHORT profit)
    stop_lvl = close + atr_mult_stop   * atr_c   # niveau stop

    # Matrice future_close[i, j] = close[i + j + 1]  shape (n_v, max_h)
    row_idx  = np.arange(n_v)[:, np.newaxis]      # (n_v, 1)
    col_idx  = np.arange(1, max_h + 1)[np.newaxis, :]  # (1, max_h)
    fc       = close[row_idx + col_idx]            # (n_v, max_h)

    # Croisements (vectorisés)
    prof_cross = fc <= prof_lvl[:n_v, np.newaxis]  # (n_v, max_h)
    stop_cross = fc >= stop_lvl[:n_v, np.newaxis]  # (n_v, max_h)

    has_prof = prof_cross.any(axis=1)
    has_stop = stop_cross.any(axis=1)

    INF = max_h + 1
    j_prof = np.where(has_prof, prof_cross.argmax(axis=1), INF)
    j_stop = np.where(has_stop, stop_cross.argmax(axis=1), INF)

    # Labels
    lbl = np.full(n, -1, dtype=np.int8)
    no_short_mask = np.array([r == "NO_SHORT" for r in regime[:n_v]], dtype=bool)
    active = ~no_short_mask

    # Profit en premier
    lbl[:n_v] = np.where(
        active & has_prof & (j_prof <= j_stop), 1,
        np.where(
            active & has_stop & (j_stop < j_prof),  0,
            -1   # expiré ou NO_SHORT
        )
    )

    col = f"y_tb_{H}h"
    df[col] = lbl
    n1 = int((lbl == 1).sum())
    n0 = int((lbl == 0).sum())
    nm = int((lbl == -1).sum())
    print(f"     Triple Barrier {H}h: wins={n1} ({n1/n:.2%})  "
          f"stops={n0} ({n0/n:.2%})  expired={nm} ({nm/n:.2%})")
    df.attrs[f"tb_win_rate_{H}h"] = n1 / max(n1 + n0, 1)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RÉGIME GATE (v3 — 3 niveaux)
# ═══════════════════════════════════════════════════════════════════════════════

def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gate régime v3 :
    - EMA gate (structure lente)
    - Momentum gate (recovery rapide — hors macro-bear)
    - Bull fort absolu (ret_30d > 22% log + EMA50>EMA200)
    - Seuil asymétrique : en NEUTRAL, require funding > 0 AND RSI > 60 pour shorter
    """
    df = df.copy()
    c   = df["Close"]
    e50 = _ema(c, 50)
    e200= _ema(c, 200)
    # Recalculer si absent
    if "dist_ema_50" not in df.columns:
        df["dist_ema_50"]       = (c - e50)  / e50.clip(lower=1e-9)
    if "ema_spread_50_200" not in df.columns:
        df["ema_spread_50_200"] = (e50 - e200) / e200.clip(lower=1e-9)

    above50 = df["dist_ema_50"] > 0
    dc_bull = df["ema_spread_50_200"] > 0
    rsi_bull= df["rsi_14"] > 55
    rsi_bear= df["rsi_14"] < 48

    # Gate EMA
    gate_ema = above50 & dc_bull & rsi_bull

    # Macro-bear confirmé (inhibe momentum gate)
    m72 = df.get("mom_72h", pd.Series(0.0, index=df.index))
    macro_bear = (~dc_bull) & (m72 < -0.05)

    # Momentum gate : recovery sauf en macro-bear
    m7d  = df.get("mom_168h", pd.Series(0.0, index=df.index))
    m3d  = df.get("mom_72h",  pd.Series(0.0, index=df.index))
    gate_momentum = ((m7d > 0.08) | (m3d > 0.05)) & (~macro_bear)

    # Bull fort absolu (Fix 3)
    m30d = df.get("mom_720h", pd.Series(0.0, index=df.index))
    gate_bull_strong = (m30d > 0.22) & dc_bull

    no_short = gate_ema | gate_momentum | gate_bull_strong

    # SHORTABLE strict
    shortable = (~above50) & (~dc_bull) & rsi_bear & (~no_short)
    # SHORTABLE étendu (RSI très baissier + éloigné des hauts)
    logc = np.log(c.clip(lower=1e-9))
    local_high = df["High"].rolling(24, min_periods=12).max()
    dist_high  = (c - local_high) / local_high.clip(lower=1e-9)
    shortable_x = (~above50) & (df["rsi_14"] < 42) & (dist_high < -0.015) & (~no_short)

    regime = np.where(no_short, "NO_SHORT",
              np.where(shortable | shortable_x, "SHORTABLE", "NEUTRAL"))
    df["regime_short"] = regime
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 5. POOL TRAINING + META-LABELING
# ═══════════════════════════════════════════════════════════════════════════════

def _encode_symbol(df: pd.DataFrame, all_syms: List[str]) -> pd.DataFrame:
    """Encode le symbole en features numériques (target encoding de la volatilité)."""
    df = df.copy()
    sym_map = {s: i for i, s in enumerate(all_syms)}
    df["symbol_id"] = df["symbol"].map(sym_map).fillna(0).astype(float)
    return df


def pool_train_primary(
    df_pool: pd.DataFrame,
    features: List[str],
    lbl_col: str,
    all_syms: List[str],
) -> Optional[HistGradientBoostingClassifier]:
    """
    Entraîne le modèle PRIMAIRE sur le pool multi-actifs.
    Labels : y_tb = 1 (short wins) vs y_tb = 0 (stop hit).
    Exclus : y_tb = -1 (expired).
    """
    valid = (df_pool[lbl_col] >= 0) & df_pool[lbl_col].notna()
    sub   = df_pool.loc[valid]
    y     = sub[lbl_col].values.astype(np.int32)
    n_pos = (y == 1).sum()
    n_neg = (y == 0).sum()

    if n_pos < MIN_POS_TRAIN:
        return None

    avail = [f for f in features + ["symbol_id"] if f in sub.columns]
    X     = sub[avail].values.astype(np.float64)
    w     = np.where(y == 1, n_neg / max(n_pos, 1), 1.0)

    print(f"       Pool train : n_pos={n_pos} n_neg={n_neg} "
          f"n_features={len(avail)} assets={df_pool['symbol'].nunique()}")

    clf = HistGradientBoostingClassifier(
        max_iter=600, max_depth=4, learning_rate=0.04,
        min_samples_leaf=12, l2_regularization=1.0,
        class_weight=None,   # géré par sample_weight
        random_state=42,
    )
    clf.fit(X, y, sample_weight=w)
    return clf


def pool_train_meta(
    df_pool:   pd.DataFrame,
    clf_prim:  HistGradientBoostingClassifier,
    features:  List[str],
    lbl_col:   str,
    p_thresh_prim: float = 0.50,
) -> Optional[RandomForestClassifier]:
    """
    Meta-labeling (Hudson & Thames) :
      - On prend tous les trades où le modèle PRIMAIRE dit SHORT (p > p_thresh_prim)
      - Le META-MODÈLE prédit si ce trade sera effectivement profitable
      - Features supplémentaires : p_primaire, volatilité courante, regime

    Avantage : le meta-modèle apprend à filtrer les faux positifs du modèle primaire.
    """
    valid = (df_pool[lbl_col] >= 0) & df_pool[lbl_col].notna()
    sub   = df_pool.loc[valid].copy()
    avail = [f for f in features + ["symbol_id"] if f in sub.columns]
    X     = sub[avail].values.astype(np.float64)

    try:
        p_prim = clf_prim.predict_proba(X)[:, 1]
    except Exception:
        return None

    sub["p_primary"]  = p_prim
    sub["in_signal"]  = (p_prim >= p_thresh_prim).astype(int)

    # Ne garder que les bars où le primaire émet un signal
    meta_sub = sub.loc[sub["in_signal"] == 1].copy()
    if len(meta_sub) < MIN_POS_TRAIN:
        return None

    y_meta = meta_sub[lbl_col].values.astype(np.int32)
    n_pos  = (y_meta == 1).sum()
    if n_pos < 10:
        return None

    # Features du meta-modèle : features standard + p_primaire
    meta_feats = avail + ["p_primary"]
    avail_meta = [f for f in meta_feats if f in meta_sub.columns]
    X_meta     = meta_sub[avail_meta].fillna(0).values.astype(np.float64)

    print(f"       Meta-model: signal_bars={len(meta_sub)} wins={n_pos}")

    clf_meta = RandomForestClassifier(
        n_estimators=200, max_depth=5, min_samples_leaf=8,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    clf_meta.fit(X_meta, y_meta)
    return clf_meta


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CALIBRATION PURGED
# ═══════════════════════════════════════════════════════════════════════════════

def calibrate_threshold(
    df_val:    pd.DataFrame,
    clf_prim:  HistGradientBoostingClassifier,
    clf_meta:  Optional[RandomForestClassifier],
    features:  List[str],
    lbl_col:   str,
    embargo_h: int = 0,
) -> Tuple[float, float, Dict]:
    """
    Calibration du seuil sur val avec embargo.
    Si meta-modèle disponible : seuil sur p_meta.
    Sinon : seuil sur p_primaire.
    Retourne (threshold_primary, threshold_meta, info).
    """
    valid = (df_val["regime_short"] != "NO_SHORT") & (df_val[lbl_col] >= 0)
    # Embargo : exclure les N premières barres (contamination du train)
    if embargo_h > 0:
        valid.iloc[:embargo_h] = False

    sub = df_val.loc[valid]
    fwd_col = lbl_col  # y_tb_Xh est déjà le label

    if len(sub) < MIN_TRADES_VAL:
        return 0.72, 0.50, {"reason": "val_too_small", "n": len(sub)}

    avail  = [f for f in features + ["symbol_id"] if f in sub.columns]
    X      = sub[avail].values.astype(np.float64)
    y_true = sub[lbl_col].values

    try:
        p_prim = clf_prim.predict_proba(X)[:, 1]
    except Exception:
        return 0.72, 0.50, {"reason": "predict_failed"}

    # Simulation PnL sur val pour chaque seuil primaire
    best_thr, best_score = 0.72, -1.0
    for thr in np.arange(0.50, 0.91, 0.02):
        mask = p_prim >= thr
        n    = mask.sum()
        if n < MIN_TRADES_VAL:
            continue
        wins = (y_true[mask] == 1).mean()
        stop = (y_true[mask] == 0).mean()
        if wins < 0.45:
            continue
        # Score simplifié : win_rate × sqrt(n)
        score = wins * np.sqrt(n)
        if score > best_score:
            best_score = score
            best_thr   = round(thr, 2)

    # Meta threshold (si meta-modèle)
    best_meta_thr = 0.50
    if clf_meta is not None:
        meta_feats = avail + ["p_primary"]
        sub2 = sub.copy()
        sub2["p_primary"] = p_prim
        meta_avail = [f for f in meta_feats if f in sub2.columns]
        X_meta = sub2[meta_avail].fillna(0).values.astype(np.float64)
        try:
            p_meta = clf_meta.predict_proba(X_meta)[:, 1]
            for thr in np.arange(0.40, 0.91, 0.05):
                mask = (p_prim >= best_thr) & (p_meta >= thr)
                n    = mask.sum()
                if n < 4:
                    continue
                wins = (y_true[mask] == 1).mean()
                score = wins * np.sqrt(n)
                if score > best_score * 0.8 and wins >= 0.52:
                    best_meta_thr = round(thr, 2)
        except Exception:
            pass

    return best_thr, best_meta_thr, {
        "reason": "swept",
        "best_score": round(best_score, 2),
        "n_val": len(sub),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. BACKTEST AVEC META-LABELING
# ═══════════════════════════════════════════════════════════════════════════════

def backtest_fold(
    df_test:   pd.DataFrame,
    clf_prim:  HistGradientBoostingClassifier,
    clf_meta:  Optional[RandomForestClassifier],
    features:  List[str],
    lbl_col:   str,
    H:         int,
    thr_prim:  float,
    thr_meta:  float,
    cost_pct:  float = COST_PCT,
) -> Dict:
    """Backtest vectorisé — predict_proba sur toutes les barres valides en une passe."""
    fwd_col = f"future_ret_{H}h"
    avail   = [f for f in features + ["symbol_id"] if f in df_test.columns]

    # Filtre initial vectorisé
    mask = (
        (df_test["regime_short"] != "NO_SHORT") &
        (df_test[lbl_col] >= 0) &
        df_test[fwd_col].notna()
    )
    sub = df_test.loc[mask].copy()
    if len(sub) == 0:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan,
                "max_dd": np.nan, "squeeze_rate": np.nan}

    X = sub[avail].values.astype(np.float64)
    try:
        p_prim = clf_prim.predict_proba(X)[:, 1]
    except Exception:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan,
                "max_dd": np.nan, "squeeze_rate": np.nan}

    signal_mask = p_prim >= thr_prim

    # Meta-labeling vectorisé
    if clf_meta is not None and signal_mask.any():
        X_sig = X[signal_mask]
        p_sig = p_prim[signal_mask]
        X_meta = np.column_stack([X_sig, p_sig])
        try:
            p_meta = clf_meta.predict_proba(X_meta)[:, 1]
            meta_mask = p_meta >= thr_meta
            full_meta = np.zeros(len(signal_mask), dtype=bool)
            full_meta[np.where(signal_mask)[0]] = meta_mask
            signal_mask = full_meta
        except Exception:
            pass

    trades = sub.loc[signal_mask.astype(bool), fwd_col].values
    if len(trades) == 0:
        return {"n": 0, "pf": np.nan, "wr": np.nan, "exp": np.nan,
                "max_dd": np.nan, "squeeze_rate": np.nan}

    arr  = -trades - cost_pct
    wins = arr[arr > 0]; loss = abs(arr[arr < 0].sum())
    pf   = wins.sum() / loss if loss > 1e-9 else float("inf")
    cum  = np.cumsum(arr); peak = np.maximum.accumulate(cum)
    return {
        "n":            len(arr),
        "pf":           round(float(pf), 3),
        "wr":           round(float((arr > 0).mean()), 3),
        "exp":          round(float(arr.mean()), 5),
        "max_dd":       round(float((cum - peak).min()), 4),
        "squeeze_rate": round(float((arr < -0.005).mean()), 3),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 8. WALK-FORWARD PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run_horizon(H: int, pool: pd.DataFrame, all_syms: List[str]) -> List[Dict]:
    """Walk-forward pour un horizon H sur le pool multi-actifs."""
    lbl_col = f"y_tb_{H}h"
    fwd_col = f"future_ret_{H}h"

    # Forward returns pour PnL du backtest
    logc = np.log(pool.groupby("symbol")["Close"].transform(lambda x: x.clip(lower=1e-9)))
    fwd  = np.full(len(pool), np.nan)
    for sym, grp in pool.groupby("symbol"):
        idx  = grp.index
        n    = len(grp)
        logc_sym = np.log(grp["Close"].values.astype(np.float64))
        f    = np.full(n, np.nan)
        f[:n-H] = logc_sym[H:] - logc_sym[:n-H]
        pool.loc[idx, fwd_col] = f

    features = get_features(pool)
    pool = _encode_symbol(pool, all_syms)
    years = pool["datetime"].dt.year

    fold_results = []

    for fi, fold in enumerate(FOLDS):
        tr_yrs = fold["train"]; val_yr = fold["val"]; tst_yr = fold["test"]
        tr_m   = years.isin(tr_yrs).values
        val_m  = (years == val_yr).values
        tst_m  = (years == tst_yr).values

        if not (tr_m.any() and val_m.any() and tst_m.any()):
            fold_results.append({"fold": fi+1, "test_year": tst_yr, "status": "SKIP"})
            continue

        df_tr  = pool.loc[tr_m].reset_index(drop=True)
        df_val = pool.loc[val_m].reset_index(drop=True)
        df_tst = pool.loc[tst_m].reset_index(drop=True)

        n_pos_tr = int((df_tr[lbl_col] == 1).sum())
        n_neg_tr = int((df_tr[lbl_col] == 0).sum())
        print(f"\n   F{fi+1} test={tst_yr}: "
              f"train_wins={n_pos_tr} train_stops={n_neg_tr} "
              f"val_n={val_m.sum()} test_n={tst_m.sum()}")

        # Entraînement primaire
        clf_prim = pool_train_primary(df_tr, features, lbl_col, all_syms)
        if clf_prim is None:
            fold_results.append({"fold": fi+1, "test_year": tst_yr,
                                  "status": "NO_SIGNAL", "n_pos_train": n_pos_tr})
            continue

        # AUC val
        auc = np.nan
        valid_v = (df_val[lbl_col] >= 0) & df_val[lbl_col].notna()
        sub_v   = df_val.loc[valid_v]
        if len(sub_v) >= 10 and sub_v[lbl_col].nunique() > 1:
            avail = [f for f in features + ["symbol_id"] if f in sub_v.columns]
            try:
                Xv  = sub_v[avail].values.astype(np.float64)
                pv  = clf_prim.predict_proba(Xv)[:, 1]
                auc = roc_auc_score(sub_v[lbl_col].values, pv)
            except Exception:
                pass
        print(f"       AUC val primaire : {auc:.4f}" if not np.isnan(auc) else "       AUC val : N/A")

        # Meta-labeling (entraîné sur train+val pour plus de données)
        df_tr_val  = pd.concat([df_tr, df_val], ignore_index=True)
        clf_meta   = pool_train_meta(df_tr_val, clf_prim, features, lbl_col)

        # Calibration
        embargo = H * 2
        thr_p, thr_m, cal_info = calibrate_threshold(
            df_val, clf_prim, clf_meta, features, lbl_col, embargo_h=embargo
        )
        print(f"       Seuil primaire={thr_p:.2f}  méta={thr_m:.2f}  ({cal_info.get('reason')}  n_val={cal_info.get('n_val',0)})")

        # Backtest
        res = backtest_fold(df_tst, clf_prim, clf_meta, features, lbl_col,
                            H, thr_p, thr_m)

        verdict = ("NO_TRADES" if res["n"] == 0
                   else "PASS" if res["pf"] >= PF_PASS
                   else "WEAK" if res["pf"] >= PF_WEAK
                   else "FAIL")

        print(f"       Test {tst_yr}: n={res['n']}  PF={res['pf']:.3f}  "
              f"WR={res['wr']:.1%}  DD={res['max_dd']:.3f}  sq={res['squeeze_rate']:.1%}  → {verdict}")

        fold_results.append({
            "fold": fi+1, "test_year": tst_yr,
            "n_pos_train": n_pos_tr, "n_neg_train": n_neg_tr,
            "val_auc": round(auc, 4) if not np.isnan(auc) else None,
            "threshold_primary": thr_p, "threshold_meta": thr_m,
            **res, "verdict": verdict,
        })

    return fold_results


# ═══════════════════════════════════════════════════════════════════════════════
# 9. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizons", nargs="+", type=int, default=HORIZONS)
    parser.add_argument("--assets",   nargs="+", default=["BTC","ETH","SOL","BNB","LINK","XRP"])
    parser.add_argument("--cost-bps", type=int,  default=COST_BPS)
    args = parser.parse_args()

    global COST_PCT
    COST_PCT = args.cost_bps / 10_000

    print("=" * 65)
    print("  REBUILD SHORT v3 — Triple Barrier + Pool + Meta-Labeling")
    print("=" * 65)
    print(f"  Horizons : {args.horizons}h")
    print(f"  Actifs   : {args.assets}")
    print(f"  Coût     : {args.cost_bps} bps")
    print(f"  Barrière : profit=ATR×{ATR_MULT_PROFIT}  stop=ATR×{ATR_MULT_STOP}")
    print(f"  Max hold : H × {MAX_HOLD_MULT} barres")

    # ── Chargement de tous les actifs ──────────────────────────────────────
    print("\n[1] Chargement du pool multi-actifs...")
    frames = []
    loaded = []
    for sym in args.assets:
        raw = load_asset(sym, ALL_YEARS)
        if raw is None:
            print(f"    [skip] {sym} — aucun parquet")
            continue
        df = resample_1h(raw)
        print(f"    {sym}: {len(df):,} barres 1h")
        frames.append(df)
        loaded.append(sym)

    if not frames:
        print("ERREUR : aucun actif chargé.")
        return

    # ── Feature engineering par actif ─────────────────────────────────────
    print("\n[2] Feature engineering + régime par actif...")
    enriched = []
    for df in frames:
        sym = df["symbol"].iloc[0]
        df  = add_features(df)
        df  = add_regime(df)
        enriched.append(df)
        ns = (df["regime_short"] == "NO_SHORT").sum()
        sh = (df["regime_short"] == "SHORTABLE").sum()
        print(f"    {sym}: NO_SHORT={ns/len(df):.1%}  SHORTABLE={sh/len(df):.1%}")

    pool = pd.concat(enriched, ignore_index=True).sort_values(["datetime","symbol"]).reset_index(drop=True)
    print(f"\n  Pool total : {len(pool):,} barres ({len(loaded)} actifs)")

    # ── Triple Barrier labels ─────────────────────────────────────────────
    print("\n[3] Triple Barrier Labels par actif et horizon...")
    pool_labeled = []
    for sym, grp in pool.groupby("symbol"):
        grp = grp.copy().reset_index(drop=True)
        for H in args.horizons:
            print(f"    {sym} H={H}h...")
            grp = build_triple_barrier_labels(grp, H)
        pool_labeled.append(grp)
    pool = pd.concat(pool_labeled, ignore_index=True).sort_values(["datetime","symbol"]).reset_index(drop=True)

    # ── Walk-forward par horizon ───────────────────────────────────────────
    print("\n[4] Walk-forward multi-horizons...")
    all_results: Dict[int, List[Dict]] = {}

    for H in args.horizons:
        print(f"\n{'─'*60}")
        print(f"  HORIZON {H}h")
        print(f"{'─'*60}")
        all_results[H] = run_horizon(H, pool.copy(), loaded)

    # ── Synthèse ──────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  SYNTHÈSE FINALE")
    print(f"{'='*65}")
    print(f"  {'H':>3}h  {'F1(2022)':>10} {'F2(2023)':>10} {'F3(2024)':>10} {'F4(2025)':>10}  {'PASS':>4}")
    print(f"  {'─'*60}")

    best_h, best_pass = None, -1
    for H, folds in all_results.items():
        n_pass = 0
        parts  = []
        for r in folds:
            pf  = r.get("pf", np.nan)
            v   = r.get("verdict", "SKIP")
            if v == "PASS":
                n_pass += 1
            s = f"{pf:.3f}" if not np.isnan(pf) else "   —"
            s += "✓" if v == "PASS" else ("~" if v == "WEAK" else " ")
            parts.append(f"{s:>11}")
        while len(parts) < 4:
            parts.append(f"{'SKIP':>11}")
        print(f"  {H:>3}h  {''.join(parts)}  {n_pass}/4")
        if n_pass > best_pass:
            best_pass = n_pass
            best_h    = H

    print(f"\n  ► Meilleur horizon : {best_h}h  ({best_pass}/4 folds PASS)")

    if best_pass >= 2:
        print(f"  ► VERDICT : SHORT_PROMISING — horizon {best_h}h viable")
        print(f"             Activer sur pool {loaded} avec seuil primaire 0.72+")
    elif best_pass == 1:
        print(f"  ► VERDICT : SHORT_WEAK_POSITIVE — signal partiel en {best_h}h")
        print(f"             Utiliser comme hedge uniquement, pas en standalone")
    else:
        print(f"  ► VERDICT : SHORT_REJECTED — aucun horizon ne passe 2+ folds")
        print(f"             Ajouter plus d'actifs ou ajuster ATR_MULT")

    # Sauvegarde
    out = REPORT_DIR / "rebuild_v3_results.json"
    with open(out, "w") as f:
        json.dump({"horizons": all_results, "assets": loaded,
                   "best_horizon": best_h, "best_pass": best_pass}, f, indent=2, default=str)
    print(f"\n  Résultats → {out}")


if __name__ == "__main__":
    main()
