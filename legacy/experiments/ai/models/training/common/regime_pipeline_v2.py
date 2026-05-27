# =============================================================================
# regime_pipeline_v2.py
# =============================================================================
# Pipeline complet : features causales → labels régime/confidence → TF dataset
# pour EventClassifier (OHLCV BTC 1h Binance).
#
# Garanties :
#   - Aucune fuite temporelle (rolling causal, labels forward H=12)
#   - Scaler fit sur train uniquement
#   - Labels déterministes, reproductibles, thresholds stockés
#   - Features stationnaires, numériquement stables
#   - Confidence non-tautologique (amplitude × path_clarity)
#
# Usage minimal :
#   from regime_pipeline_v2 import make_tf_datasets, PipelineConfig
#   ds_train, ds_val, ds_test, meta = make_tf_datasets("data/BTCUSD_1h_Binance.csv")
# =============================================================================

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import tensorflow as tf

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG
# =============================================================================

@dataclass(frozen=True)
class PipelineConfig:
    # ── Horizon forward (barres) ─────────────────────────────────────────────
    # H=12 : 12h pour BTC 1h. Capte les régimes intrajournaliers/overnight.
    # Assez long pour séparer signal/bruit, assez court pour garder la validité
    # locale des labels. 24h : trop de variance sur BTC. 6h : trop bruité.
    horizon: int = 12

    # ── Longueur de séquence ─────────────────────────────────────────────────
    # TCN 3 layers, dilation 1/2/4, kernel 3 → réceptivité = 1+2*(1+2+4) = 15.
    # L=64 donne ~2.7 jours de contexte horaire.
    seq_len: int = 64

    # ── Features ─────────────────────────────────────────────────────────────
    ema_spans: Tuple[int, ...] = (8, 21, 55)
    rsi_period: int = 14
    atr_period: int = 14
    rv_windows: Tuple[int, ...] = (3, 6, 12, 24)
    zscore_window: int = 48          # fenêtre z-score causal
    vol_ema_span: int = 24           # EMA volume de référence

    # ── Labels ───────────────────────────────────────────────────────────────
    # UP   = fwd_ret > ret_hi  ET  path_clarity > clarity_thresh
    # DOWN = fwd_ret < ret_lo  ET  path_clarity > clarity_thresh
    # CHOP = tout le reste (move faible, bruité, ou direction ambiguë)
    #
    # Le filtre path_clarity élimine les faux UP/DOWN (monte puis repart).
    # Distribution typique BTC 1h H=12 : CHOP≈60%, UP≈20%, DOWN≈20%.
    ret_lo_q:    float = 0.25    # quantile fwd_ret train → seuil DOWN
    ret_hi_q:    float = 0.75    # quantile fwd_ret train → seuil UP
    clarity_q:   float = 0.35    # quantile path_clarity train → filtre qualité chemin
    trade_abs_q: float = 0.60    # quantile |fwd_sharpe| train → seuil tradeable

    # ── Split temporel ───────────────────────────────────────────────────────
    train_frac: float = 0.70
    val_frac: float = 0.15
    # test_frac = 1 - 0.70 - 0.15 = 0.15

    # ── Robustesse numérique ─────────────────────────────────────────────────
    feature_clip: float = 10.0
    eps: float = 1e-8


# Noms des 3 régimes — return-forward quantile split
REGIME_NAMES: Dict[int, str] = {
    0: "CHOP",
    1: "UP",
    2: "DOWN",
}
N_REGIMES: int = 3


# =============================================================================
# COLONNES BINANCE
# =============================================================================

_COL_RENAME = {
    "Open time":                    "open_time",
    "Close time":                   "close_time",
    "Open":                         "open",
    "High":                         "high",
    "Low":                          "low",
    "Close":                        "close",
    "Volume":                       "volume",
    "Quote asset volume":           "quote_vol",
    "Number of trades":             "n_trades",
    "Taker buy base asset volume":  "taker_buy_vol",
    "Taker buy quote asset volume": "taker_buy_quote_vol",
}

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]
_ALL_INPUT_COLS = [
    "open", "high", "low", "close", "volume",
    "quote_vol", "n_trades", "taker_buy_vol", "taker_buy_quote_vol",
]


# =============================================================================
# SECTION 1 — CHARGEMENT
# =============================================================================

def load_csv(path: str) -> pd.DataFrame:
    """
    Charge un CSV Binance OHLCV 1h.
    - Renomme les colonnes en snake_case
    - Trie chronologiquement
    - Coerce les types numériques
    - Supprime les lignes OHLCV invalides
    """
    df = pd.read_csv(path)

    # Renommage flexible (supporte colonnes déjà renommées)
    df = df.rename(columns={k: v for k, v in _COL_RENAME.items() if k in df.columns})
    df = df.drop(columns=["Ignore", "close_time"], errors="ignore")

    # Parse + tri chronologique
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"])
        df = df.sort_values("open_time").reset_index(drop=True)

    # Coercion numérique
    for c in _ALL_INPUT_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Nettoyage strict des anomalies OHLCV
    df = df.dropna(subset=_OHLCV_COLS).reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)
    df = df[df["high"] >= df["low"]].reset_index(drop=True)
    df = df[df["high"] >= df["close"]].reset_index(drop=True)
    df = df[df["low"] <= df["close"]].reset_index(drop=True)
    df = df[df["volume"] > 0].reset_index(drop=True)

    logger.info("Loaded %d rows from %s", len(df), path)
    return df


# =============================================================================
# SECTION 2 — FEATURE ENGINEERING (CAUSAL)
# =============================================================================

def _ema(s: pd.Series, span: int) -> pd.Series:
    """EMA causale avec min_periods pour éviter les NaN de début de série."""
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(s: pd.Series, period: int) -> pd.Series:
    """
    RSI de Wilder, entièrement causal.
    alpha = 1/period (smoothing de Wilder). Retourne [0, 100].
    """
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    return 100.0 - (100.0 / (1.0 + rs))


def _true_range(df: pd.DataFrame) -> pd.Series:
    """True range causal : max(H-L, |H-C_prev|, |L-C_prev|)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def _causal_zscore(s: pd.Series, window: int, eps: float = 1e-8) -> pd.Series:
    """
    Z-score causal : (x - rolling_mean) / rolling_std.
    Fenêtre backward uniquement → aucune fuite.
    """
    mp = max(window // 2, 2)
    m = s.rolling(window, min_periods=mp).mean()
    std = s.rolling(window, min_periods=mp).std()
    return (s - m) / (std + eps)


def compute_features(df: pd.DataFrame, cfg: PipelineConfig = PipelineConfig()) -> pd.DataFrame:
    """
    Calcule les 28 features causales à partir des colonnes OHLCV Binance.

    Propriétés :
      - Toutes les features utilisent uniquement des données passées (causal)
      - Toutes sont stationnaires ou normalisées (aucun prix absolu)
      - Numériquement stables (eps, clipping minimal)
      - NaN en début de série gérés par min_periods (ne pas fillna ici,
        ils seront traités après scaling)

    Input  : df avec [open, high, low, close, volume, quote_vol,
                      n_trades, taker_buy_vol, taker_buy_quote_vol]
    Output : df + colonnes features
    """
    eps = cfg.eps
    df = df.copy()

    # ── Log return 1 barre ────────────────────────────────────────────────────
    log_close = np.log(df["close"].clip(lower=eps))
    df["ret_1"] = log_close.diff(1)

    # ── Log returns multi-échelle (h bars) ────────────────────────────────────
    # diff(h) sur log_close = log(close[t]/close[t-h]) — causal par construction
    for h in (3, 6, 12):
        df[f"ret_{h}"] = log_close.diff(h)

    # ── Volatilité réalisée : rolling std de ret_1 ───────────────────────────
    for w in cfg.rv_windows:
        df[f"rv_{w}"] = df["ret_1"].rolling(w, min_periods=max(w // 2, 2)).std()

    # ── Amplitude haute-basse normalisée par close ───────────────────────────
    df["hl_pct"] = (df["high"] - df["low"]) / (df["close"] + eps)

    # ── ATR(14) normalisé % ───────────────────────────────────────────────────
    tr = _true_range(df)
    df["atr_pct"] = (
        tr.rolling(cfg.atr_period, min_periods=cfg.atr_period // 2).mean()
        / (df["close"] + eps)
    )

    # ── Distance price/EMA : (close - EMA_k) / close ─────────────────────────
    for span in cfg.ema_spans:
        ema = _ema(df["close"], span)
        df[f"dist_ema{span}"] = (df["close"] - ema) / (df["close"] + eps)

    # ── Pente EMA courte vs EMA longue ────────────────────────────────────────
    ema_s = _ema(df["close"], cfg.ema_spans[0])
    ema_l = _ema(df["close"], cfg.ema_spans[1])
    df["ema_slope"] = (ema_s - ema_l) / (df["close"] + eps)

    # ── RSI normalisé vers [-1, 1] ────────────────────────────────────────────
    df["rsi_norm"] = (_rsi(df["close"], cfg.rsi_period) - 50.0) / 50.0

    # ── Volume relatif vs EMA ─────────────────────────────────────────────────
    vol_ema = _ema(df["volume"], cfg.vol_ema_span)
    df["vol_rel"] = df["volume"] / (vol_ema + eps)

    # ── Ratio taker buy / volume total ───────────────────────────────────────
    df["taker_buy_ratio"] = df["taker_buy_vol"] / (df["volume"] + eps)

    # ── Imbalance quote (pression acheteuse nette) ────────────────────────────
    # Centré à 0 : >0 = plus d'achats que de ventes en valeur
    df["taker_imbalance"] = (
        df["taker_buy_quote_vol"] / (df["quote_vol"] + eps) - 0.5
    )

    # ── Ratio de volatilité (compression/expansion) ───────────────────────────
    df["rv_ratio"] = df["rv_3"] / (df["rv_24"] + eps)
    df["rv_expansion"] = df["rv_6"] / (df["rv_24"] + eps)

    # ── Structure de la bougie ────────────────────────────────────────────────
    candle_range = (df["high"] - df["low"]).clip(lower=eps)
    df["body_ratio"] = (df["close"] - df["open"]).abs() / candle_range
    df["close_pos"] = (df["close"] - df["low"]) / candle_range
    df["upper_wick"] = (df["high"] - df[["close", "open"]].max(axis=1)) / candle_range
    df["lower_wick"] = (df[["close", "open"]].min(axis=1) - df["low"]) / candle_range

    # ── Z-scores causaux (fenêtre 48h) ───────────────────────────────────────
    df["ret_zscore"] = _causal_zscore(df["ret_1"], cfg.zscore_window)
    df["hl_zscore"] = _causal_zscore(df["hl_pct"], cfg.zscore_window)
    df["atr_zscore"] = _causal_zscore(df["atr_pct"], cfg.zscore_window)

    # ── Activité marché relative ──────────────────────────────────────────────
    trades_ema = _ema(df["n_trades"], cfg.vol_ema_span)
    df["trades_rel"] = df["n_trades"] / (trades_ema + eps)

    return df


# Ordre fixe — NE PAS MODIFIER sans re-fitter le scaler et recréer les tenseurs
FEATURE_COLS: List[str] = [
    # Returns (4)
    "ret_1", "ret_3", "ret_6", "ret_12",
    # Realized vol (4)
    "rv_3", "rv_6", "rv_12", "rv_24",
    # Range / ATR (2)
    "hl_pct", "atr_pct",
    # EMA distances + slope (4)
    "dist_ema8", "dist_ema21", "dist_ema55", "ema_slope",
    # Momentum (1)
    "rsi_norm",
    # Volume / microstructure (3)
    "vol_rel", "taker_buy_ratio", "taker_imbalance",
    # Régime de vol (2)
    "rv_ratio", "rv_expansion",
    # Structure bougie (4)
    "body_ratio", "close_pos", "upper_wick", "lower_wick",
    # Z-scores causaux (3)
    "ret_zscore", "hl_zscore", "atr_zscore",
    # Activité (1)
    "trades_rel",
]
N_FEATURES: int = len(FEATURE_COLS)   # 28


# =============================================================================
# SECTION 3 — GÉNÉRATION DE LABELS
# =============================================================================

def _forward_stats_vectorized(
    log_ret: np.ndarray,
    H: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Statistiques vectorisées du chemin futur. O(N×H) mais entièrement numpy.

    Pour t ∈ [0, N-H-1] :
      fwd_ret[t]      = log(close[t+H]/close[t]) = sum(log_ret[t+1:t+H+1])
      fwd_rv[t]       = std(log_ret[t+1:t+H+1])
      max_exc[t]      = max |cumsum(log_ret[t+1:t+H+1])|
      path_clarity[t] = |fwd_ret[t]| / max_exc[t]   ∈ [0,1]
                        1.0 = chemin monotone, ~0 = aller-retour total

    Les H dernières positions sont NaN (pas de futur disponible).
    Pour 71K barres × H=12 → 852K éléments : ~3 MB, négligeable.
    """
    N = len(log_ret)
    valid_N = N - H

    # indices[t, h] = t+1+h → fenêtre future de H barres à partir de t
    t_idx = np.arange(valid_N)[:, None]   # [valid_N, 1]
    h_idx = np.arange(H)[None, :]         # [1, H]
    indices = t_idx + h_idx + 1           # [valid_N, H]

    forward_mat = log_ret[indices]        # [valid_N, H]
    cum_paths = np.cumsum(forward_mat, axis=1)  # [valid_N, H]

    fwd_ret_v = cum_paths[:, -1]
    fwd_rv_v = np.std(forward_mat, axis=1)
    max_exc_v = np.max(np.abs(cum_paths), axis=1)
    path_clarity_v = np.abs(fwd_ret_v) / (max_exc_v + 1e-12)

    nan_pad = np.full(H, np.nan, dtype=np.float64)
    return (
        np.concatenate([fwd_ret_v, nan_pad]),
        np.concatenate([fwd_rv_v, nan_pad]),
        np.concatenate([path_clarity_v, nan_pad]),
        np.concatenate([max_exc_v, nan_pad]),
    )


def compute_labels(
    df: pd.DataFrame,
    cfg: PipelineConfig = PipelineConfig(),
    train_end_idx: Optional[int] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Génère les labels régime (3 classes), tradeable (binaire) et fwd_ret_norm (régression).

    ── Régime (3 classes) ────────────────────────────────────────────────────
      0 CHOP : tout ce qui n'est pas UP ou DOWN (move faible, bruité, ambigu)
      1 UP   : fwd_ret > ret_hi  ET  path_clarity > clarity_thresh
      2 DOWN : fwd_ret < ret_lo  ET  path_clarity > clarity_thresh

    Le filtre path_clarity est critique : il retire les "faux UP/DOWN" où le
    prix monte fortement puis repart (path_clarity bas = retour total élevé).
    Résultat : UP/DOWN = moves directionnels propres → apprenables par les
    features de momentum/EMA/RSI. CHOP capture tout le reste.

    Distribution typique BTC 1h H=12 : CHOP≈58%, UP≈21%, DOWN≈21%.
    Pondération inverse fréquence nécessaire (CHOP dominant).

    ── Tradeable (binaire) ───────────────────────────────────────────────────
    Cible pour une tête séparée : "y-a-t-il quelque chose à exploiter ici ?"
      tradeable=1 si |fwd_sharpe| > sharpe_thresh
      tradeable=0 sinon
    Le fwd_sharpe = fwd_ret/(fwd_rv*sqrt(H)) mesure l'amplitude risk-adjusted.
    Prédit indépendamment du régime → permet un filtrage double en production.

    ── fwd_ret_norm (régression auxiliaire) ─────────────────────────────────
    Signal continu pour améliorer le gradient et la calibration :
      fwd_ret_norm = tanh(fwd_sharpe / 2.0) ∈ (-1, 1)
    Sharpe=2 → 0.76, Sharpe=-2 → -0.76. Gère les outliers naturellement.

    ── Seuils (train only, stockés pour inférence) ───────────────────────────
    Tous calculés sur valid_train_idx uniquement. Aucune fuite vers val/test.

    Returns :
      df         : df augmenté (+ regime, tradeable, fwd_ret_norm, cols debug _*)
      thresholds : dict complet pour inférence et audit
    """
    H = cfg.horizon
    N = len(df)
    eps = cfg.eps

    if "ret_1" not in df.columns:
        df = compute_features(df, cfg)

    log_ret = df["ret_1"].fillna(0.0).values.astype(np.float64)

    # ── Stats forward vectorisées ────────────────────────────────────────────
    # fwd_ret, fwd_rv, path_clarity ∈ [0,1], max_exc
    fwd_ret, fwd_rv, path_clarity, _ = _forward_stats_vectorized(log_ret, H)

    # Sharpe forward (pour tradeable et régression auxiliaire)
    fwd_sharpe = fwd_ret / (fwd_rv * np.sqrt(H) + eps)  # [N]

    # ── Seuils sur TRAIN uniquement ───────────────────────────────────────────
    if train_end_idx is None:
        train_end_idx = int(N * cfg.train_frac)

    train_mask = (
        (np.arange(N) >= H)
        & (np.arange(N) < min(train_end_idx, N - H))
        & ~np.isnan(fwd_ret)
        & ~np.isnan(fwd_rv)
    )
    train_idx = np.where(train_mask)[0]

    fwd_ret_tr   = fwd_ret[train_idx]
    clarity_tr   = path_clarity[train_idx]
    abs_sharpe_tr = np.abs(fwd_sharpe[train_idx])

    ret_lo        = float(np.quantile(fwd_ret_tr,   cfg.ret_lo_q))
    ret_hi        = float(np.quantile(fwd_ret_tr,   cfg.ret_hi_q))
    clarity_thresh = float(np.quantile(clarity_tr,   cfg.clarity_q))
    sharpe_thresh  = float(np.quantile(abs_sharpe_tr, cfg.trade_abs_q))

    logger.info(
        "Label thresholds (train only): ret_lo=%.4f  ret_hi=%.4f  "
        "clarity=%.3f  sharpe_thresh=%.3f",
        ret_lo, ret_hi, clarity_thresh, sharpe_thresh,
    )

    # ── Masque valide global ─────────────────────────────────────────────────
    valid_mask = (
        (np.arange(N) >= H)
        & (np.arange(N) < N - H)
        & ~np.isnan(fwd_ret)
        & ~np.isnan(fwd_rv)
    )
    t_arr = np.where(valid_mask)[0]
    fr  = fwd_ret[t_arr]
    pc  = path_clarity[t_arr]
    fsh = fwd_sharpe[t_arr]

    # ── Regime ───────────────────────────────────────────────────────────────
    regime = np.full(N, -1, dtype=np.int32)
    labels_arr = np.zeros(len(t_arr), dtype=np.int32)   # CHOP par défaut
    clean = pc > clarity_thresh
    labels_arr = np.where((fr > ret_hi) & clean, 1, labels_arr)  # UP
    labels_arr = np.where((fr < ret_lo) & clean, 2, labels_arr)  # DOWN
    regime[t_arr] = labels_arr

    # ── Tradeable ────────────────────────────────────────────────────────────
    tradeable_arr = (np.abs(fsh) > sharpe_thresh).astype(np.int32)
    tradeable = np.full(N, -1, dtype=np.int32)
    tradeable[t_arr] = tradeable_arr

    # ── Régression : fwd_ret normalisé ───────────────────────────────────────
    # tanh(Sharpe/2) : Sharpe=2→0.76, Sharpe=4→0.96, robuste aux outliers
    fwd_ret_norm_arr = np.tanh(fsh / 2.0).astype(np.float32)
    fwd_ret_norm = np.full(N, np.nan, dtype=np.float32)
    fwd_ret_norm[t_arr] = fwd_ret_norm_arr

    df = df.copy()
    df["regime"]       = regime
    df["tradeable"]    = tradeable
    df["fwd_ret_norm"] = fwd_ret_norm
    # colonnes debug (ne pas inclure dans FEATURE_COLS)
    df["_fwd_ret"]      = fwd_ret
    df["_fwd_rv"]       = fwd_rv
    df["_fwd_sharpe"]   = fwd_sharpe
    df["_path_clarity"] = path_clarity

    thresholds = {
        "ret_lo":        ret_lo,
        "ret_hi":        ret_hi,
        "clarity_thresh": clarity_thresh,
        "sharpe_thresh": sharpe_thresh,
        "horizon":       H,
    }
    return df, thresholds


def label_distribution(df: pd.DataFrame, split_name: str = "") -> Dict:
    """Statistiques de distribution des labels pour audit."""
    valid = df[df["regime"] >= 0]
    N = len(valid)
    if N == 0:
        return {"n_valid": 0, "warning": "No valid labels"}

    rc = valid["regime"].value_counts().sort_index().to_dict()
    n_tradeable = int((valid["tradeable"] == 1).sum()) if "tradeable" in valid.columns else 0

    stats: Dict = {
        "split": split_name,
        "n_valid": N,
        "n_invalid": len(df) - N,
        "regime": {REGIME_NAMES[k]: int(v) for k, v in rc.items() if k in REGIME_NAMES},
        "regime_pct": {REGIME_NAMES[k]: round(v / N, 4) for k, v in rc.items() if k in REGIME_NAMES},
        "tradeable_pct": round(n_tradeable / N, 4),
    }

    counts = list(rc.values())
    if counts:
        ratio = max(counts) / (min(counts) + 1)
        if ratio > 8:
            stats["warning_imbalance"] = f"Ratio={ratio:.1f}"

    return stats


# =============================================================================
# SECTION 4 — NETTOYAGE NUMÉRIQUE
# =============================================================================

def _clean_array(X: np.ndarray, clip_val: float) -> np.ndarray:
    """
    Remplace NaN/±Inf → 0, puis clip à [-clip_val, clip_val].
    Appliqué APRÈS scaling pour garantir des entrées bornées au modèle.
    """
    X = np.nan_to_num(X, nan=0.0, posinf=clip_val, neginf=-clip_val)
    return np.clip(X, -clip_val, clip_val)


# =============================================================================
# SECTION 5 — SPLIT TEMPOREL
# =============================================================================

def temporal_split(
    df: pd.DataFrame,
    cfg: PipelineConfig = PipelineConfig(),
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split chronologique strict. Aucun shuffle.
    val et test sont strictement postérieurs au train.
    """
    N = len(df)
    train_end = int(N * cfg.train_frac)
    val_end = int(N * (cfg.train_frac + cfg.val_frac))

    train = df.iloc[:train_end].copy()
    val = df.iloc[train_end:val_end].copy()
    test = df.iloc[val_end:].copy()

    logger.info(
        "Temporal split: train=%d, val=%d, test=%d",
        len(train), len(val), len(test),
    )
    return train, val, test


# =============================================================================
# SECTION 6 — SCALER (FIT SUR TRAIN UNIQUEMENT)
# =============================================================================

class RobustFeatureScaler:
    """
    RobustScaler basé sur médiane/MAD.

    Doit être fit sur les données TRAIN uniquement.
    Transforme val et test avec les mêmes paramètres (pas de fuite).

    Formule : (x - median) / (1.4826 × MAD)
    Le facteur 1.4826 normalise MAD pour être cohérent avec std (distribution N(0,1)).
    """

    def __init__(self) -> None:
        self.median_: Optional[np.ndarray] = None
        self.mad_: Optional[np.ndarray] = None
        self.feature_names_: Optional[List[str]] = None

    def fit(
        self,
        X: np.ndarray,
        feature_names: Optional[List[str]] = None,
    ) -> "RobustFeatureScaler":
        X = np.asarray(X, dtype=np.float64)
        self.median_ = np.nanmedian(X, axis=0)
        mad = np.nanmedian(np.abs(X - self.median_), axis=0)
        self.mad_ = np.maximum(mad, 1e-6)
        self.feature_names_ = feature_names
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.median_ is None:
            raise RuntimeError("RobustFeatureScaler not fitted.")
        X = np.asarray(X, dtype=np.float64)
        return (X - self.median_) / (1.4826 * self.mad_)

    def fit_transform(
        self, X: np.ndarray, feature_names: Optional[List[str]] = None
    ) -> np.ndarray:
        return self.fit(X, feature_names).transform(X)

    def to_dict(self) -> Dict:
        if self.median_ is None:
            raise RuntimeError("Not fitted.")
        return {
            "median": self.median_.tolist(),
            "mad": self.mad_.tolist(),
            "feature_names": self.feature_names_,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "RobustFeatureScaler":
        sc = cls()
        sc.median_ = np.array(d["median"], dtype=np.float64)
        sc.mad_ = np.array(d["mad"], dtype=np.float64)
        sc.feature_names_ = d.get("feature_names")
        return sc

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "RobustFeatureScaler":
        with open(path) as f:
            return cls.from_dict(json.load(f))


# =============================================================================
# SECTION 7 — WINDOWING EN SÉQUENCES
# =============================================================================

def make_sequences(
    feat_scaled:  np.ndarray,   # [N, F]   features scalées
    regime:       np.ndarray,   # [N]      int32, -1 = invalide
    tradeable:    np.ndarray,   # [N]      int32, -1 = invalide
    fwd_ret_norm: np.ndarray,   # [N]      float32, NaN = invalide
    seq_len:      int,
    clip_val:     float = 10.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Fenêtrage glissant en séquences [L, F].

    Seules les positions où regime >= 0 ET tradeable >= 0 ET fwd_ret_norm non-NaN
    génèrent une séquence (garantit cohérence entre les trois labels).

    Invariant causal :
      features[start:end+1] ← données jusqu'à end (inclus)
      labels[end]           ← données de end+1 à end+H (futur)
      → Aucune fuite.

    Output :
      X            : [M, L, F]  float32
      y_regime     : [M]        int32      (0=CHOP, 1=UP, 2=DOWN)
      y_tradeable  : [M]        float32    (0/1)
      y_fwd_ret    : [M]        float32    tanh(Sharpe/2) ∈ (-1,1)
    """
    N, _ = feat_scaled.shape
    L = seq_len

    X_list, yr_list, yt_list, yf_list = [], [], [], []

    for end in range(L - 1, N):
        r = int(regime[end])
        t = int(tradeable[end])
        f = float(fwd_ret_norm[end])
        if r < 0 or t < 0 or np.isnan(f):
            continue

        window = _clean_array(feat_scaled[end - L + 1: end + 1].copy(), clip_val)
        X_list.append(window)
        yr_list.append(r)
        yt_list.append(float(t))
        yf_list.append(f)

    if len(X_list) == 0:
        raise ValueError(
            "No valid sequences generated. "
            "Check that labels are assigned (regime >= 0) for this split."
        )

    X           = np.stack(X_list, axis=0).astype(np.float32)  # [M, L, F]
    y_regime    = np.array(yr_list, dtype=np.int32)             # [M]
    y_tradeable = np.array(yt_list, dtype=np.float32)           # [M]
    y_fwd_ret   = np.array(yf_list, dtype=np.float32)           # [M]

    return X, y_regime, y_tradeable, y_fwd_ret


# =============================================================================
# SECTION 8 — CLASS WEIGHTS
# =============================================================================

def compute_class_weights(y: np.ndarray, n_classes: int) -> np.ndarray:
    """
    Poids balancés : w_c = N / (n_classes × count_c).
    Classes absentes → w = 1.0 (sécurité).
    """
    N = len(y)
    weights = np.ones(n_classes, dtype=np.float32)
    for c in range(n_classes):
        cnt = int(np.sum(y == c))
        if cnt > 0:
            weights[c] = N / (n_classes * cnt)
    return weights


# =============================================================================
# SECTION 9 — CONSTRUCTION DES TF DATASETS
# =============================================================================

def make_tf_datasets(
    csv_path: str,
    cfg: PipelineConfig = PipelineConfig(),
    batch_size: int = 256,
    shuffle_train: bool = True,
    seed: int = 42,
) -> Tuple[tf.data.Dataset, tf.data.Dataset, tf.data.Dataset, Dict]:
    """
    Pipeline complet : CSV → tf.data.Dataset.

    Étapes :
      1. load_csv           → DataFrame nettoyé
      2. compute_features   → 28 features causales
      3. compute_labels     → régime 3-classes (seuils quantile sur train only)
      4. temporal_split     → train / val / test
      5. RobustFeatureScaler→ fit sur train, transform val et test
      6. make_sequences     → fenêtres [L, F] avec labels valides
      7. tf.data.Dataset    → batch + prefetch (+ shuffle train)

    Format des batches :
      (x, {"regime": yr})
      où x ~ [B, L, F], yr ~ [B] int32 (0=CHOP, 1=UP, 2=DOWN)

    Returns :
      ds_train, ds_val, ds_test : tf.data.Dataset
      meta : dict avec stats, scaler, feature_names, thresholds
    """
    # ── 1. Load ───────────────────────────────────────────────────────────────
    df = load_csv(csv_path)
    N = len(df)

    # ── 2. Features (calculées sur le df entier pour que les rolling windows
    #   en début de val/test bénéficient du contexte train) ────────────────────
    df = compute_features(df, cfg)

    # ── 3. Labels ─────────────────────────────────────────────────────────────
    train_end_idx = int(N * cfg.train_frac)
    df, thresholds = compute_labels(df, cfg, train_end_idx=train_end_idx)

    # ── 4. Split ──────────────────────────────────────────────────────────────
    train_df, val_df, test_df = temporal_split(df, cfg)

    # ── 5. Scaling ────────────────────────────────────────────────────────────
    def _feat(split_df: pd.DataFrame) -> np.ndarray:
        return split_df[FEATURE_COLS].values.astype(np.float64)

    scaler = RobustFeatureScaler()
    X_tr_s = scaler.fit_transform(_feat(train_df), FEATURE_COLS)
    X_v_s = scaler.transform(_feat(val_df))
    X_te_s = scaler.transform(_feat(test_df))

    # ── 6. Labels bruts par split ─────────────────────────────────────────────
    def _raw_labels(split_df: pd.DataFrame):
        return (
            split_df["regime"].values.astype(np.int32),
            split_df["tradeable"].values.astype(np.int32),
            split_df["fwd_ret_norm"].values.astype(np.float32),
        )

    yr_tr, yt_tr, yf_tr = _raw_labels(train_df)
    yr_v,  yt_v,  yf_v  = _raw_labels(val_df)
    yr_te, yt_te, yf_te = _raw_labels(test_df)

    # ── 7. Windowing ──────────────────────────────────────────────────────────
    L, clip = cfg.seq_len, cfg.feature_clip

    X_tr, yr_tr, yt_tr, yf_tr = make_sequences(X_tr_s, yr_tr, yt_tr, yf_tr, L, clip)
    X_v,  yr_v,  yt_v,  yf_v  = make_sequences(X_v_s,  yr_v,  yt_v,  yf_v,  L, clip)
    X_te, yr_te, yt_te, yf_te = make_sequences(X_te_s, yr_te, yt_te, yf_te, L, clip)

    # ── 8. Class weights (inverse fréquence, nécessaire : CHOP dominant) ─────
    class_weights = compute_class_weights(yr_tr, n_classes=N_REGIMES)

    # ── 9. TF datasets ────────────────────────────────────────────────────────
    def _to_ds(
        X: np.ndarray,
        yr: np.ndarray,
        yt: np.ndarray,
        yf: np.ndarray,
        shuffle: bool = False,
    ) -> tf.data.Dataset:
        labels = {
            "regime":      tf.constant(yr,          dtype=tf.int32),
            "tradeable":   tf.constant(yt[:, None], dtype=tf.float32),  # [M,1]
            "fwd_ret_norm": tf.constant(yf[:, None], dtype=tf.float32), # [M,1]
        }
        x_ds = tf.data.Dataset.from_tensor_slices(tf.constant(X, dtype=tf.float32))
        l_ds = tf.data.Dataset.from_tensor_slices(labels)
        ds = tf.data.Dataset.zip((x_ds, l_ds))

        if shuffle:
            ds = ds.shuffle(
                buffer_size=min(len(X), 20_000), seed=seed,
                reshuffle_each_iteration=True,
            )
        return ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)

    ds_train = _to_ds(X_tr, yr_tr, yt_tr, yf_tr, shuffle=shuffle_train)
    ds_val   = _to_ds(X_v,  yr_v,  yt_v,  yf_v)
    ds_test  = _to_ds(X_te, yr_te, yt_te, yf_te)

    # ── 10. Metadata ──────────────────────────────────────────────────────────
    meta: Dict = {
        "thresholds":   thresholds,
        "class_weights": class_weights.tolist(),
        "scaler":        scaler.to_dict(),
        "n_features":    N_FEATURES,
        "feature_cols":  FEATURE_COLS,
        "seq_len":       L,
        "n_regimes":     N_REGIMES,
        "regime_names":  {str(k): v for k, v in REGIME_NAMES.items()},
        "label_stats": {
            "train": label_distribution(train_df, "train"),
            "val":   label_distribution(val_df,   "val"),
            "test":  label_distribution(test_df,  "test"),
        },
        "n_sequences": {
            "train": int(len(X_tr)),
            "val":   int(len(X_v)),
            "test":  int(len(X_te)),
        },
        # Arrays numpy pour sanity checks
        "_arrays": {
            "X_tr": X_tr, "yr_tr": yr_tr, "yt_tr": yt_tr,
            "X_v":  X_v,  "yr_v":  yr_v,
            "X_te": X_te,
        },
    }

    return ds_train, ds_val, ds_test, meta


# =============================================================================
# SECTION 10 — FONCTIONS DE LOSS (PAS de sous-classe Keras Loss)
# =============================================================================
#
# POURQUOI DES FONCTIONS ET PAS DES CLASSES tf.keras.losses.Loss ?
#
# tf.keras.losses.Loss.__call__ a la signature :
#   __call__(self, y_true, y_pred, sample_weight=None)
#
# Si on appelait  self._rl(y_true, logits, entropy)  avec une classe Loss,
# Keras interpréterait `entropy` comme `sample_weight`. Le scalaire de loss
# serait alors multiplié par entropy [B, 1], produisant un tenseur [B, 1]
# au lieu d'un scalaire. La GradientTape somme implicitement ce tenseur
# (× batch_size), les gradients explosent, le modèle collapse sur REVERSAL
# (entropy → 0) et la loss affichée tombe à ~0 — contradiction totale
# avec l'accuracy.
#
# Solution : fonctions Python/TF pures, appelées directement dans _step().
# Aucune ambiguïté, aucun effet de bord Keras.

def _regime_loss_fn(
    y_true:          tf.Tensor,
    logits:          tf.Tensor,
    class_weights_tf: Optional[tf.Tensor] = None,
    label_smoothing: float = 0.05,
) -> tf.Tensor:
    """
    Cross-entropy sparse avec label smoothing + pondération par classe optionnelle.

    y_true : [B]    int32   — indices (0 CHOP, 1 UP, 2 DOWN)
    logits : [B, R] float32 — logits bruts, PAS de softmax avant

    label_smoothing=0.05 : réduit la confiance sur les labels (financier = bruité)
      → améliore la calibration, réduit l'entropie artificielle.
    class_weights_tf : [R] float32 — poids inverse fréquence (nécessaire si CHOP dominant)
    """
    y = tf.cast(tf.reshape(y_true, [-1]), tf.int32)
    n_cls = tf.shape(logits)[-1]

    if label_smoothing > 0.0:
        # Soft targets : (1-ε)*one_hot + ε/n_classes
        y_oh = tf.one_hot(y, n_cls, dtype=tf.float32)          # [B, R]
        y_sm = y_oh * (1.0 - label_smoothing) + label_smoothing / tf.cast(n_cls, tf.float32)
        log_probs = tf.nn.log_softmax(logits, axis=-1)          # [B, R]
        ce = -tf.reduce_sum(y_sm * log_probs, axis=-1)          # [B]
    else:
        ce = tf.keras.losses.sparse_categorical_crossentropy(
            y, logits, from_logits=True
        )                                                        # [B]

    if class_weights_tf is not None:
        sample_w = tf.gather(class_weights_tf, y)               # [B]
        return tf.reduce_mean(ce * sample_w)
    return tf.reduce_mean(ce)


def _trade_loss_fn(y_true: tf.Tensor, pred: tf.Tensor) -> tf.Tensor:
    """
    BCE pour la tête tradeability.
    y_true : [B,1] float32 — 0=not tradeable, 1=tradeable
    pred   : [B,1] float32 — sortie sigmoid
    """
    y = tf.cast(tf.reshape(y_true, [-1, 1]), tf.float32)
    p = tf.cast(pred, tf.float32)
    return tf.reduce_mean(tf.keras.losses.binary_crossentropy(y, p))


def _reg_loss_fn(y_true: tf.Tensor, pred: tf.Tensor) -> tf.Tensor:
    """
    MSE pour la tête régression (fwd_ret_norm).
    y_true : [B,1] float32 — tanh(Sharpe/2) ∈ (-1,1)
    pred   : [B,1] float32 — sortie tanh du modèle
    """
    y = tf.cast(tf.reshape(y_true, [-1, 1]), tf.float32)
    p = tf.cast(pred, tf.float32)
    return tf.reduce_mean(tf.square(y - p))


# =============================================================================
# SECTION 11 — WRAPPER D'ENTRAÎNEMENT
# =============================================================================

def build_trainable_model(
    event_classifier,
    class_weights:      np.ndarray,
    regime_loss_weight: float = 1.0,
    trade_loss_weight:  float = 0.30,
    reg_loss_weight:    float = 0.15,
    label_smoothing:    float = 0.05,
    learning_rate:      float = 3e-4,
) -> tf.keras.Model:
    """
    Encapsule EventClassifier dans un tf.keras.Model avec custom train_step.

    Losses :
      total = regime_w * CE_weighted + trade_w * BCE + reg_w * MSE
      - CE avec label smoothing 0.05 + pondération inverse fréquence (CHOP dominant)
      - BCE pour tradeability (binaire)
      - MSE pour régression fwd_ret_norm ∈ (-1,1)

    Gradient clipping : norme globale ≤ 1.0.

    Métriques exposées : regime_loss, trade_loss, reg_loss, total_loss, regime_acc, trade_acc

    Format de batch : (x, {"regime": yr, "tradeable": yt, "fwd_ret_norm": yf})
    """
    cw_tf = tf.constant(class_weights, dtype=tf.float32)
    rlw   = float(regime_loss_weight)
    tlw   = float(trade_loss_weight)
    glw   = float(reg_loss_weight)
    ls    = float(label_smoothing)

    class _Wrapper(tf.keras.Model):

        def __init__(self, clf):
            super().__init__(name="event_classifier_trainer")
            self.clf   = clf
            self._m_rl = tf.keras.metrics.Mean(name="regime_loss")
            self._m_tl = tf.keras.metrics.Mean(name="trade_loss")
            self._m_gl = tf.keras.metrics.Mean(name="reg_loss")
            self._m_tt = tf.keras.metrics.Mean(name="total_loss")
            self._m_ra = tf.keras.metrics.SparseCategoricalAccuracy(name="regime_acc")
            self._m_ta = tf.keras.metrics.BinaryAccuracy(name="trade_acc", threshold=0.5)

        @property
        def metrics(self):
            return [self._m_rl, self._m_tl, self._m_gl, self._m_tt, self._m_ra, self._m_ta]

        def call(self, x, training=False):
            return self.clf(x, training=training)

        def _compute_losses(self, x, labels, training: bool):
            out    = self(x, training=training)
            r_loss = _regime_loss_fn(
                labels["regime"], out["regime_logits"], cw_tf, ls
            )
            t_loss = _trade_loss_fn(labels["tradeable"], out["tradeability"])
            g_loss = _reg_loss_fn(labels["fwd_ret_norm"], out["fwd_ret_pred"])
            total  = rlw * r_loss + tlw * t_loss + glw * g_loss
            return total, r_loss, t_loss, g_loss, out

        def train_step(self, data):
            x, labels = data[0], data[1]
            with tf.GradientTape() as tape:
                total, r_loss, t_loss, g_loss, out = self._compute_losses(
                    x, labels, training=True
                )
            grads = tape.gradient(total, self.trainable_variables)
            grads = [tf.clip_by_norm(g, 1.0) if g is not None else g for g in grads]
            self.optimizer.apply_gradients(zip(grads, self.trainable_variables))

            self._m_rl.update_state(r_loss)
            self._m_tl.update_state(t_loss)
            self._m_gl.update_state(g_loss)
            self._m_tt.update_state(total)
            self._m_ra.update_state(labels["regime"],    out["regime_probs"])
            self._m_ta.update_state(labels["tradeable"], out["tradeability"])
            return {m.name: m.result() for m in self.metrics}

        def test_step(self, data):
            x, labels = data[0], data[1]
            total, r_loss, t_loss, g_loss, out = self._compute_losses(
                x, labels, training=False
            )
            self._m_rl.update_state(r_loss)
            self._m_tl.update_state(t_loss)
            self._m_gl.update_state(g_loss)
            self._m_tt.update_state(total)
            self._m_ra.update_state(labels["regime"],    out["regime_probs"])
            self._m_ta.update_state(labels["tradeable"], out["tradeability"])
            return {m.name: m.result() for m in self.metrics}

    wrapper = _Wrapper(event_classifier)
    wrapper.compile(
        optimizer=tf.keras.optimizers.Adam(
            learning_rate=learning_rate,
            clipnorm=1.0,
        )
    )
    return wrapper


# =============================================================================
# SECTION 11b — OVERFIT TEST (diagnostic rapide)
# =============================================================================

def overfit_test(
    event_classifier,
    seq_len: int = 64,
    n_features: int = 28,
    batch_size: int = 32,
    n_steps: int = 200,
    learning_rate: float = 1e-3,
) -> Dict:
    """
    Vérifie que la loss est correcte en sur-apprenant 1 seul batch fixe.

    Un modèle sain DOIT :
      - Partir d'une regime_loss ≈ log(3) ≈ 1.099 (random init sur 3 classes uniformes)
      - Atteindre regime_acc ≈ 100% sur ce batch en < 200 steps
      - regime_loss → proche de 0 SI ET SEULEMENT SI acc → 100%

    Returns :
      dict avec loss_init, loss_final, acc_final, verdict
    """
    import time

    rng = np.random.default_rng(42)
    x_np  = rng.standard_normal((batch_size, seq_len, n_features)).astype(np.float32)
    yr_np = rng.integers(0, N_REGIMES, size=(batch_size,), dtype=np.int32)
    yt_np = rng.integers(0, 2, size=(batch_size, 1)).astype(np.float32)
    yf_np = np.tanh(rng.standard_normal((batch_size, 1)).astype(np.float32) / 2.0)

    x = tf.constant(x_np)
    labels = {
        "regime":       tf.constant(yr_np, dtype=tf.int32),
        "tradeable":    tf.constant(yt_np, dtype=tf.float32),
        "fwd_ret_norm": tf.constant(yf_np, dtype=tf.float32),
    }

    opt = tf.keras.optimizers.Adam(learning_rate=learning_rate)

    # Loss initiale (avant tout update) — uniquement regime_loss pour le verdict
    out0 = event_classifier(x, training=False)
    loss_init = float(_regime_loss_fn(labels["regime"], out0["regime_logits"]).numpy())

    # Boucle d'overfit sur la loss totale (comme en vrai training)
    t0 = time.time()
    for _ in range(n_steps):
        with tf.GradientTape() as tape:
            out    = event_classifier(x, training=True)
            r_loss = _regime_loss_fn(labels["regime"],       out["regime_logits"])
            t_loss = _trade_loss_fn( labels["tradeable"],    out["tradeability"])
            g_loss = _reg_loss_fn(   labels["fwd_ret_norm"], out["fwd_ret_pred"])
            total  = r_loss + 0.30 * t_loss + 0.15 * g_loss

        grads = tape.gradient(total, event_classifier.trainable_variables)
        grads = [tf.clip_by_norm(g, 1.0) if g is not None else g for g in grads]
        opt.apply_gradients(zip(grads, event_classifier.trainable_variables))

    # Loss et accuracy finales
    out_f      = event_classifier(x, training=False)
    loss_final = float(_regime_loss_fn(labels["regime"], out_f["regime_logits"]).numpy())
    preds      = tf.argmax(out_f["regime_probs"], axis=-1).numpy()
    acc_final  = float(np.mean(preds == yr_np))
    elapsed    = time.time() - t0

    # Attendu : log(N_REGIMES) ≈ log(3) = 1.099 pour random init uniforme
    expected_init = float(np.log(N_REGIMES))
    init_ok = 0.1 < loss_init < expected_init * 3.0
    acc_ok = acc_final > 0.90
    loss_acc_consistent = not (loss_final < 0.1 and acc_final < 0.5)

    verdict = "PASS" if (acc_ok and loss_acc_consistent) else "FAIL"

    sep = "─" * 62
    print(f"\n{sep}")
    print(f"OVERFIT TEST (1 batch, {n_steps} steps)")
    print(sep)
    print(f"  loss_init  = {loss_init:.4f}  (attendu ≈ {expected_init:.3f} = log({N_REGIMES}))")
    print(f"  loss_final = {loss_final:.4f}")
    print(f"  acc_final  = {acc_final:.1%}  (doit atteindre > 90%)")
    print(f"  elapsed    = {elapsed:.1f}s")
    print(f"  {'✓ PASS' if verdict == 'PASS' else '✗ FAIL  ← bug détecté'}")
    if not init_ok:
        print(f"  ⚠ loss_init {loss_init:.4f} hors plage (0.1 – {expected_init * 3.0:.2f})")
        print(f"    → possible softmax avant logits ou mauvaise shape")
    if not acc_ok:
        print(f"  ⚠ acc_final {acc_final:.1%} < 90% après {n_steps} steps")
        print(f"    → modèle ne sur-apprend pas → gradient bloqué ou loss cassée")
    if not loss_acc_consistent:
        print(f"  ⚠ loss ≈ 0 mais acc < 50% → loss ne correspond pas à l'accuracy")
    print(sep)

    return {
        "verdict": verdict,
        "loss_init": loss_init,
        "loss_final": loss_final,
        "acc_final": acc_final,
        "expected_init": expected_init,
        "n_steps": n_steps,
    }


# =============================================================================
# SECTION 12 — SANITY CHECKS
# =============================================================================

def sanity_checks(
    meta: Dict,
    thresholds: Optional[Dict] = None,
) -> None:
    """
    Vérifie la cohérence complète du pipeline.
    Lit les arrays numpy depuis meta["_arrays"] (produit par make_tf_datasets).
    Lève AssertionError si quelque chose est critique.
    """
    sep = "─" * 62
    arrays = meta["_arrays"]
    X_tr  = arrays["X_tr"]
    X_v   = arrays["X_v"]
    X_te  = arrays["X_te"]
    yr_tr = arrays["yr_tr"]
    yr_v  = arrays["yr_v"]
    yt_tr = arrays.get("yt_tr")   # tradeable (may be absent in legacy meta)
    cfg_seq_len = meta["seq_len"]
    cfg_n_feat  = meta["n_features"]
    thr = thresholds or meta.get("thresholds", {})

    print(sep)
    print("SANITY CHECKS")
    print(sep)

    # 1. Shapes
    for name, arr in [("train", X_tr), ("val", X_v), ("test", X_te)]:
        assert arr.ndim == 3, f"Expected 3D for {name}"
        assert arr.shape[1] == cfg_seq_len, \
            f"seq_len mismatch {name}: {arr.shape[1]} ≠ {cfg_seq_len}"
        assert arr.shape[2] == cfg_n_feat, \
            f"n_features mismatch {name}: {arr.shape[2]} ≠ {cfg_n_feat}"
    print(f"[OK] Shapes   train={X_tr.shape}  val={X_v.shape}  test={X_te.shape}")

    # 2. Pas de NaN/Inf
    for name, arr in [("train", X_tr), ("val", X_v), ("test", X_te)]:
        n_bad = int(np.isnan(arr).sum() + np.isinf(arr).sum())
        assert n_bad == 0, f"NaN/Inf dans {name}: {n_bad}"
    print("[OK] Aucun NaN/Inf dans les features")

    # 3. Plages des labels
    assert 0 <= yr_tr.min() and yr_tr.max() <= N_REGIMES - 1, \
        f"Label out of range: [{yr_tr.min()}, {yr_tr.max()}] pour {N_REGIMES} classes"
    print(f"[OK] Plages des labels valides (0–{N_REGIMES-1})")

    # 4. Distribution des régimes (CHOP attendu ≈55-65%, UP/DOWN ≈17-22% chacun)
    print("\nDistribution régimes (train) :")
    for c in range(N_REGIMES):
        pct = float((yr_tr == c).mean())
        bar = "█" * max(1, int(pct * 36))
        print(f"  {REGIME_NAMES[c]:8s} {pct:5.1%}  {bar}")

    chop_pct = float((yr_tr == 0).mean())
    if chop_pct > 0.80:
        print(f"  ⚠  CHOP > 80% ({chop_pct:.1%}) — clarity_thresh peut-être trop élevé")
    dir_pct = 1.0 - chop_pct
    if dir_pct < 0.20:
        print(f"  ⚠  UP+DOWN < 20% ({dir_pct:.1%}) — trop peu de signaux directionnels")

    # 4b. Distribution tradeability
    if yt_tr is not None:
        trade_pct = float(yt_tr.mean())
        print(f"\nTradeable (train)  : {trade_pct:.1%} positifs")
        if trade_pct < 0.25:
            print(f"  ⚠  Moins de 25% de samples tradeable — sharpe_thresh peut-être trop strict")

    # 5. Baselines naïves à battre
    majority = int(np.bincount(yr_tr).argmax())
    val_majority_acc = float((yr_v == majority).mean())
    uniform_baseline = 1.0 / N_REGIMES
    print(f"\nBaselines (val) :")
    print(f"  Classe majoritaire '{REGIME_NAMES[majority]}' : {val_majority_acc:.2%}")
    print(f"  Aléatoire uniforme :  {uniform_baseline:.2%}")
    print(f"  → Model doit dépasser {val_majority_acc:.2%} sur regime_acc")

    # 6. Seuils
    if thr:
        print(f"\nSeuils labels :")
        for k, v in thr.items():
            if k != "horizon":
                print(f"  {k:<15s} = {v:.4f}")
            else:
                print(f"  {k:<15s} = {v}")

    # 7. Résumé
    print(f"\nFeatures : {cfg_n_feat}")
    print(f"Séquences : train={len(X_tr):,}  val={len(X_v):,}  test={len(X_te):,}")
    print(sep)


# =============================================================================
# SECTION 14 — CALLBACKS
# =============================================================================

def build_callbacks(out_dir: str, patience: int = 8) -> list:
    """
    Callbacks standard pour l'entraînement de l'EventClassifier.
      - EarlyStopping sur val_regime_acc (tâche principale)
      - ReduceLROnPlateau sur val_regime_loss
      - ModelCheckpoint (meilleurs poids, save_weights_only)
      - CSVLogger
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    return [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_regime_acc",
            patience=patience,
            restore_best_weights=True,
            mode="max",
            verbose=1,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_regime_loss",
            mode="min",
            factor=0.5,
            patience=max(patience // 2, 3),
            min_lr=1e-5,
            verbose=1,
        ),
        tf.keras.callbacks.ModelCheckpoint(
            filepath=str(Path(out_dir) / "best_weights.weights.h5"),
            monitor="val_regime_acc",
            save_best_only=True,
            save_weights_only=True,
            mode="max",
            verbose=0,
        ),
        tf.keras.callbacks.CSVLogger(
            str(Path(out_dir) / "training_log.csv"),
            append=False,
        ),
    ]


# =============================================================================
# SECTION 15 — CALLBACK DE VALIDATION DÉTAILLÉE (PAR CLASSE)
# =============================================================================

class ValDetailCallback(tf.keras.callbacks.Callback):
    """
    Callback qui tourne à la fin de chaque epoch sur le val set complet
    et affiche les métriques détaillées par classe.

    Ce qu'il affiche à chaque epoch :
      Régime :
        - Recall, Precision, F1 par classe
        - Matrice de confusion compacte (ligne = vrai, col = prédit)
        - Macro-F1 et accuracy pondérée
        - Comparaison avec la baseline majoritaire
      Tradeability :
        - Accuracy binaire à seuil 0.50 / 0.55 / 0.60
        - Precision à seuil 0.55
      Exploitation (joint) :
        - Regime_acc sur samples tradeable uniquement
        - P(UP)>0.60 precision / P(DOWN)>0.60 precision
        - Signal coverage (fraction samples avec signal clair)
      Général :
        - Mean entropy + fwd_ret_pred correlation
        - Distribution des prédictions de régime
        - LR courante

    Fichiers produits dans out_dir :
      val_detail_log.jsonl  — une ligne JSON par epoch (pour analyse offline)
    """

    def __init__(
        self,
        ds_val: tf.data.Dataset,
        out_dir: str,
        log_every: int = 1,           # logguer toutes les N epochs
        majority_class: int = 0,      # classe majoritaire (baseline)
        n_regimes: int = N_REGIMES,
    ) -> None:
        super().__init__()
        self.ds_val = ds_val
        self.log_path = str(Path(out_dir) / "val_detail_log.jsonl")
        self.log_every = log_every
        self.majority_class = majority_class
        self.n_regimes = n_regimes
        self._best_macro_f1: float = 0.0
        self._best_epoch: int = 0

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        # Tronquer le fichier de log au démarrage
        open(self.log_path, "w").close()

    # ── Collecte des prédictions sur le val set ───────────────────────────────

    def _collect(self):
        """
        Itère sur ds_val (training=False) et retourne :
          yr_true    : [N]  vrais labels régime
          yr_pred    : [N]  argmax(regime_probs)
          regime_probs : [N, R] probabilités de régime
          yt_true    : [N]  vrais labels tradeable (0/1)
          trade_pred : [N]  tradeability prédite ∈ (0,1)
          fwd_true   : [N]  vraie valeur fwd_ret_norm
          fwd_pred   : [N]  fwd_ret_pred
          entropy    : [N]  entropie prédite
        """
        yr_true, yr_pred, regime_probs_ = [], [], []
        yt_true, trade_pred = [], []
        fwd_true, fwd_pred = [], []
        entropy = []

        for batch in self.ds_val:
            x      = batch[0]
            labels = batch[1]
            out    = self.model(x, training=False)

            yr_true.append(labels["regime"].numpy().flatten())
            probs = out["regime_probs"].numpy()          # [B, R]
            regime_probs_.append(probs)
            yr_pred.append(np.argmax(probs, axis=-1))
            entropy.append(out["entropy"].numpy().flatten())

            yt_true.append(labels["tradeable"].numpy().flatten())
            trade_pred.append(out["tradeability"].numpy().flatten())

            fwd_true.append(labels["fwd_ret_norm"].numpy().flatten())
            fwd_pred.append(out["fwd_ret_pred"].numpy().flatten())

        return (
            np.concatenate(yr_true).astype(np.int32),
            np.concatenate(yr_pred).astype(np.int32),
            np.concatenate(regime_probs_, axis=0).astype(np.float32),
            np.concatenate(yt_true).astype(np.int32),
            np.concatenate(trade_pred).astype(np.float32),
            np.concatenate(fwd_true).astype(np.float32),
            np.concatenate(fwd_pred).astype(np.float32),
            np.concatenate(entropy).astype(np.float32),
        )

    # ── Métriques par classe ──────────────────────────────────────────────────

    @staticmethod
    def _per_class_metrics(
        yr_true: np.ndarray,
        yr_pred: np.ndarray,
        n_classes: int,
    ) -> List[Dict]:
        """Recall, Precision, F1, Support par classe — numpy pur, pas de sklearn."""
        results = []
        for c in range(n_classes):
            tp = int(((yr_pred == c) & (yr_true == c)).sum())
            fp = int(((yr_pred == c) & (yr_true != c)).sum())
            fn = int(((yr_pred != c) & (yr_true == c)).sum())
            support = int((yr_true == c).sum())

            recall = tp / (tp + fn + 1e-9)
            precision = tp / (tp + fp + 1e-9)
            f1 = 2 * precision * recall / (precision + recall + 1e-9)

            results.append({
                "class": c,
                "name": REGIME_NAMES[c],
                "recall": round(recall, 4),
                "precision": round(precision, 4),
                "f1": round(f1, 4),
                "support": support,
                "tp": tp, "fp": fp, "fn": fn,
            })
        return results

    @staticmethod
    def _confusion_matrix(
        yr_true: np.ndarray,
        yr_pred: np.ndarray,
        n_classes: int,
    ) -> np.ndarray:
        cm = np.zeros((n_classes, n_classes), dtype=np.int32)
        for t, p in zip(yr_true, yr_pred):
            cm[t, p] += 1
        return cm

    # ── Affichage ─────────────────────────────────────────────────────────────

    def _print_report(
        self,
        epoch: int,
        per_class: List[Dict],
        cm: np.ndarray,
        yr_true: np.ndarray,
        yr_pred: np.ndarray,
        regime_probs: np.ndarray,
        yt_true: np.ndarray,
        trade_pred: np.ndarray,
        fwd_true: np.ndarray,
        fwd_pred: np.ndarray,
        entropy: np.ndarray,
        lr: float,
    ) -> None:
        N = len(yr_true)
        n = self.n_regimes
        sep = "─" * 72

        macro_f1 = float(np.mean([m["f1"] for m in per_class]))
        overall_acc = float((yr_true == yr_pred).mean())
        baseline_acc = float((yr_true == self.majority_class).mean())

        mark = " ★" if macro_f1 > self._best_macro_f1 else ""

        print(f"\n{sep}")
        print(
            f"  VAL  epoch={epoch:3d}   n={N:,}   "
            f"acc={overall_acc:.3f}   macro-F1={macro_f1:.3f}{mark}"
            f"   baseline={baseline_acc:.3f}   lr={lr:.2e}"
        )
        print(sep)

        # ── Tableau par classe ────────────────────────────────────────────────
        print(
            f"  {'REGIME':<15s}  {'Recall':>7}  {'Precision':>9}  "
            f"{'F1':>6}  {'Support':>8}  {'TP/FN':>10}"
        )
        print(f"  {'─'*15}  {'─'*7}  {'─'*9}  {'─'*6}  {'─'*8}  {'─'*10}")
        for m in per_class:
            fn_str = f"{m['tp']:5d}/{m['fn']:5d}"
            flag = " ←" if m["recall"] < 0.25 else ("   " if m["recall"] >= 0.50 else " ~")
            print(
                f"  {m['name']:<15s}  {m['recall']:>7.1%}  {m['precision']:>9.1%}  "
                f"{m['f1']:>6.3f}  {m['support']:>8,}  {fn_str:>10}{flag}"
            )
        print(f"  {'─'*15}  {'─'*7}  {'─'*9}  {'─'*6}  {'─'*8}  {'─'*10}")
        print(
            f"  {'MACRO':15s}  {'':>7}  {'':>9}  {macro_f1:>6.3f}"
            f"  {'':>8}  gain={overall_acc - baseline_acc:+.3f}"
        )

        # ── Distribution des prédictions ──────────────────────────────────────
        pred_dist = [float((yr_pred == c).mean()) for c in range(n)]
        print(f"\n  Prédictions : " + "  ".join(
            f"{REGIME_NAMES[c]}={pred_dist[c]:.1%}" for c in range(n)
        ))

        # ── Matrice de confusion compacte ─────────────────────────────────────
        cm_norm = cm.astype(float) / (cm.sum(axis=1, keepdims=True) + 1e-9)
        names_short = [REGIME_NAMES[c][:4].upper() for c in range(n)]
        print(f"\n  Confusion (normalisée lignes = recall) :")
        header = "        " + "".join(f"{s:>7s}" for s in names_short)
        print(f"  {header}")
        for i in range(n):
            row = "  ".join(
                f"\033[1m{cm_norm[i,j]:5.1%}\033[0m" if i == j
                else f"{cm_norm[i,j]:5.1%}"
                for j in range(n)
            )
            print(f"  {names_short[i]:>6s} | {row}")

        # ── Tradeability ──────────────────────────────────────────────────────
        print(f"\n  Tradeability (label positif = {float(yt_true.mean()):.1%} du val set) :")
        for thr_t in (0.50, 0.55, 0.60):
            t_pred_bin = (trade_pred >= thr_t).astype(np.int32)
            t_acc = float((t_pred_bin == yt_true).mean())
            tp_t  = int(((t_pred_bin == 1) & (yt_true == 1)).sum())
            fp_t  = int(((t_pred_bin == 1) & (yt_true == 0)).sum())
            prec_t = tp_t / (tp_t + fp_t + 1e-9)
            cov_t  = float(t_pred_bin.mean())
            print(
                f"    thr={thr_t:.2f}  acc={t_acc:.3f}  "
                f"prec={prec_t:.3f}  coverage={cov_t:.1%}"
            )

        # ── Signal exploitation joint (regime + tradeability) ─────────────────
        # UP = classe 1, DOWN = classe 2
        UP_IDX, DOWN_IDX = 1, 2
        p_up   = regime_probs[:, UP_IDX]
        p_down = regime_probs[:, DOWN_IDX]
        trade_bin = (trade_pred >= 0.55).astype(bool)

        signal_up   = (p_up   > 0.60) & trade_bin
        signal_down = (p_down > 0.60) & trade_bin
        signal_any  = signal_up | signal_down
        coverage    = float(signal_any.mean())

        def _prec(mask, true_class):
            if mask.sum() == 0:
                return float("nan")
            return float((yr_true[mask] == true_class).mean())

        prec_up   = _prec(signal_up,   UP_IDX)
        prec_down = _prec(signal_down, DOWN_IDX)

        # Regime acc on tradeable samples
        trade_mask = yt_true.astype(bool)
        acc_on_trade = float((yr_true[trade_mask] == yr_pred[trade_mask]).mean()) \
            if trade_mask.sum() > 0 else float("nan")

        print(f"\n  Exploitation (P>0.60 AND trade>0.55) :")
        print(f"    Signal coverage : {coverage:.1%}")
        print(f"    P(UP)>0.60   prec={prec_up:.3f}   n={signal_up.sum():,}")
        print(f"    P(DOWN)>0.60 prec={prec_down:.3f}   n={signal_down.sum():,}")
        print(f"    Regime acc on tradeable samples : {acc_on_trade:.3f}  "
              f"(n={trade_mask.sum():,})")

        # ── fwd_ret_pred corrélation ──────────────────────────────────────────
        if len(fwd_true) > 10:
            corr = float(np.corrcoef(fwd_true, fwd_pred)[0, 1])
            print(f"\n  fwd_ret_pred  corr={corr:.3f}  "
                  f"pred_std={float(fwd_pred.std()):.3f}  "
                  f"true_std={float(fwd_true.std()):.3f}")

        # ── Entropy ───────────────────────────────────────────────────────────
        max_ent = float(np.log(n))
        mean_ent = float(entropy.mean())
        print(f"\n  Entropy  mean={mean_ent:.3f}  max_theoretical={max_ent:.3f}  "
              f"ratio={mean_ent/max_ent:.1%}")

        print(sep)

    # ── Hook principal ────────────────────────────────────────────────────────

    def on_epoch_end(self, epoch: int, logs: Optional[Dict] = None) -> None:
        # epoch est 0-indexé dans Keras → afficher epoch+1
        ep = epoch + 1

        if ep % self.log_every != 0:
            return

        # LR courante
        try:
            lr = float(tf.keras.backend.get_value(self.model.optimizer.lr))
        except Exception:
            lr = float("nan")

        # Collecte
        (yr_true, yr_pred, regime_probs,
         yt_true, trade_pred,
         fwd_true, fwd_pred,
         entropy) = self._collect()

        # Métriques
        per_class = self._per_class_metrics(yr_true, yr_pred, self.n_regimes)
        cm        = self._confusion_matrix(yr_true, yr_pred, self.n_regimes)
        macro_f1  = float(np.mean([m["f1"] for m in per_class]))

        # Affichage
        self._print_report(
            epoch=ep,
            per_class=per_class,
            cm=cm,
            yr_true=yr_true,
            yr_pred=yr_pred,
            regime_probs=regime_probs,
            yt_true=yt_true,
            trade_pred=trade_pred,
            fwd_true=fwd_true,
            fwd_pred=fwd_pred,
            entropy=entropy,
            lr=lr,
        )

        # Tracking du meilleur epoch
        if macro_f1 > self._best_macro_f1:
            self._best_macro_f1 = macro_f1
            self._best_epoch = ep

        # Métriques exploitation pour JSON
        UP_IDX, DOWN_IDX = 1, 2
        p_up   = regime_probs[:, UP_IDX]
        p_down = regime_probs[:, DOWN_IDX]
        trade_bin = (trade_pred >= 0.55).astype(bool)
        signal_any = ((p_up > 0.60) | (p_down > 0.60)) & trade_bin
        coverage   = float(signal_any.mean())
        trade_acc  = float(((trade_pred >= 0.50) == yt_true.astype(bool)).mean())

        # ── Sauvegarde JSON (une ligne par epoch) ─────────────────────────────
        record = {
            "epoch": ep,
            "lr": round(lr, 8),
            "overall_acc":   round(float((yr_true == yr_pred).mean()), 4),
            "macro_f1":      round(macro_f1, 4),
            "trade_acc":     round(trade_acc, 4),
            "signal_coverage": round(coverage, 4),
            "per_class":     per_class,
            "mean_entropy":  round(float(entropy.mean()), 4),
            "pred_dist": {
                REGIME_NAMES[c]: round(float((yr_pred == c).mean()), 4)
                for c in range(self.n_regimes)
            },
            "val_keras_logs": {k: round(float(v), 6) for k, v in (logs or {}).items()},
        }

        with open(self.log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def on_train_end(self, logs: Optional[Dict] = None) -> None:
        print(
            f"\n  Best val macro-F1 = {self._best_macro_f1:.4f}  "
            f"at epoch {self._best_epoch}"
        )


def build_val_detail_callback(
    ds_val: tf.data.Dataset,
    out_dir: str,
    majority_class: int = 0,
    log_every: int = 1,
) -> ValDetailCallback:
    """
    Construit le callback de validation détaillée.

    Args :
      ds_val         : dataset val (format produit par make_tf_datasets)
      out_dir        : répertoire de sortie (pour val_detail_log.jsonl)
      majority_class : classe majoritaire du train (pour baseline)
      log_every      : logguer toutes les N epochs (1 = chaque epoch)

    Usage dans le script d'entraînement :
      val_cb = build_val_detail_callback(ds_val, out_dir, majority_class=0)
      callbacks = build_callbacks(out_dir) + [val_cb]
      model.fit(..., callbacks=callbacks)
    """
    return ValDetailCallback(
        ds_val=ds_val,
        out_dir=out_dir,
        log_every=log_every,
        majority_class=majority_class,
        n_regimes=N_REGIMES,
    )
