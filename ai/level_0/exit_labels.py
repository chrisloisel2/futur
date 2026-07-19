"""
ai/level_0/exit_labels.py — Labels et features pour le modèle de sortie de position
======================================================================================

Principe :
  Pour chaque barre t0 où y_long == 1 (signal d'entrée long), on génère des
  samples d'entraînement pour décider QUAND sortir la position.

Label y_exit[t0, k] = 1 si sortir à t0+k est OPTIMAL dans la fenêtre restante :
  (1) net_now >= best_future - 5bps  (optimal ou quasi-optimal)
  OU (2) log(close[t0+k] / close[t0]) < -0.025  (stop-loss dur -2.5%)
  OU (3) k == MAX_HOLD-1              (dernière barre : toujours 1)

où :
  net_now     = log(close[t0+k] / close[t0]) - cost_pct
  best_future = max(net de chaque barre future restante), -inf si k = MAX_HOLD-1

Anti-leakage :
  - generate_exit_samples() n'utilise que les barres t0 avec train_mask[t0]=True
  - Les features de marché sont calculées à t0+k (barre courante, pas future)
  - Les features de position sont construites depuis les barres t0..t0+k (historique)
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ai.level_0.constants import COST_PCT

# ─────────────────────────────────────────────────────────────────────────────
# Listes de features
# ─────────────────────────────────────────────────────────────────────────────

EXIT_POSITION_FEATURES: List[str] = [
    "bars_held",
    "bars_remaining",
    "bars_frac",
    "unrealized_ret",
    "unrealized_ret_bps",
    "max_ret_so_far",
    "min_ret_so_far",
    "drawdown_from_peak",
    "recovery_from_trough",
    "is_profitable",
    "pnl_velocity_1",
    "pnl_velocity_3",
    "pnl_normalized",
    "entry_rsi",
    "entry_adx",
    "entry_trend_score",
    "entry_momentum_score",
    "entry_close_position_in_range",
]

EXIT_MARKET_FEATURES: List[str] = [
    "return_5",
    "return_10",
    "log_return_5",
    "log_return_10",
    "realized_vol_20",
    "atr_pct_20",
    "bb_width_20",
    "bb_percent_b_20",
    "close_position_in_range",
    "body_to_range",
    "high_low_range_pct",
    "distance_ema_20",
    "distance_ema_50",
    "ema_21_50_spread",
    "ema_slope_20",
    "macd_hist",
    "macd_hist_slope",
    "rsi_13",
    "rsi_20",
    "stoch_k_20",
    "adx_20",
    "di_spread_20",
    "choppiness_20",
    "efficiency_ratio_20",
    "volume_ratio_20",
    "cmf_20",
    "obv_slope_20",
    "trend_score",
    "momentum_score",
    "volatility_score",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "return_50",
    "distance_ema_200",
    "regression_slope_50",
    "donchian_position_20",
    "di_plus_20",
    "mtf_4h_adx_20",
    "mtf_4h_rsi_10",
    "mtf_4h_return_5",
    "mtf_1d_return_5",
    "mtf_1d_rsi_5",
]

EXIT_ALL_FEATURES: List[str] = EXIT_POSITION_FEATURES + EXIT_MARKET_FEATURES


# ─────────────────────────────────────────────────────────────────────────────
# Générateur de samples d'entraînement
# ─────────────────────────────────────────────────────────────────────────────

def generate_exit_samples(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    symbol: str,
    cost_pct: float = COST_PCT,
    tolerance_bps: int = 5,
    hard_sl_pct: float = 0.025,
    max_hold: int = 8,
) -> pd.DataFrame:
    """
    Génère les samples d'entraînement pour le modèle de sortie.

    Pour chaque barre t0 où y_long==1 ET train_mask[t0]==True :
      Pour k = 1..max_hold-1 :
        → 1 sample = features de position (state k) + features marché à t0+k + y_exit

    Paramètres
    ----------
    df          : enriched parquet d'un symbol (doit contenir 'y_long' et 'close')
    train_mask  : masque booléen des barres d'entraînement (len == len(df))
    symbol      : nom du symbol (ajouté comme colonne pour le pooling multi-asset)
    cost_pct    : coût round-trip (10 bps)
    tolerance_bps : tolérance pour "quasi-optimal" (5 bps)
    hard_sl_pct : stop-loss dur en absolu (2.5%)
    max_hold    : fenêtre max de hold en barres

    Retourne
    --------
    pd.DataFrame avec EXIT_ALL_FEATURES + ['y_exit', 'symbol', 't0', 'k']
    """
    if "y_long" not in df.columns:
        raise ValueError("df doit contenir 'y_long' — appeler compute_label_columns() + build_labels() avant")

    close_col = "close" if "close" in df.columns else "Close"
    close = df[close_col].values.astype(np.float64)
    y_long = df["y_long"].values

    # Résilience vs NaN dans la colonne rv pour pnl_normalized
    rv_col = "realized_volatility_20"
    if rv_col in df.columns:
        rv_arr = df[rv_col].fillna(0.02).values.astype(np.float64)
    else:
        rv_arr = np.full(len(df), 0.02)

    # Features d'entrée au niveau t0 (fingerprint)
    def _safe_col(col, default=0.0):
        if col in df.columns:
            return df[col].fillna(default).values.astype(np.float64)
        return np.full(len(df), default)

    entry_rsi_arr     = _safe_col("rsi_14", 50.0)
    entry_adx_arr     = _safe_col("adx_20", 20.0)
    entry_trend_arr   = _safe_col("trend_score", 0.0)
    entry_mom_arr     = _safe_col("momentum_score", 0.0)
    entry_cpr_arr     = _safe_col("close_position_in_range", 0.5)

    # Identifier les barres t0 valides :
    # y_long==1 ET train_mask ET il reste au moins max_hold barres après t0
    n = len(df)
    tol = tolerance_bps * 1e-4

    records = []
    t0_indices = np.where(
        (y_long == 1) & train_mask & (np.arange(n) + max_hold < n)
    )[0]

    for t0 in t0_indices:
        c0 = close[t0]
        if c0 <= 0:
            continue

        # Rendements log depuis l'entrée pour chaque barre de la fenêtre
        log_rets = np.log(close[t0 + 1 : t0 + max_hold + 1] / c0)  # shape (max_hold,)

        # Calcul des net P&L pour toutes les barres de la fenêtre
        net_rets = log_rets - cost_pct  # net_pnl[k-1] = net_ret à la barre k

        for k in range(1, max_hold):
            idx_k = t0 + k
            if idx_k >= n:
                break

            unrealized_ret = log_rets[k - 1]
            net_now = net_rets[k - 1]

            # best_future = max(net_rets[k..max_hold-1]) — futur de la barre k+1 à max_hold
            if k < max_hold - 1:
                best_future = float(np.max(net_rets[k:]))  # net_rets[k] = barre k+1
            else:
                best_future = -np.inf  # dernière barre → toujours sortir

            # Calcul du label y_exit
            is_last_bar   = (k == max_hold - 1)
            is_hard_sl    = (unrealized_ret < -hard_sl_pct)
            is_near_optim = (net_now >= best_future - tol)

            y_exit = int(is_last_bar or is_hard_sl or is_near_optim)

            # ── Features de position ──────────────────────────────────────────
            # max/min ret sur j=1..k
            rets_so_far = log_rets[:k]  # shape (k,)
            max_ret   = float(np.max(rets_so_far))
            min_ret   = float(np.min(rets_so_far))

            # pnl_velocity_1 : variation entre la barre k et k-1
            if k >= 2:
                pnl_vel1 = unrealized_ret - float(log_rets[k - 2])
            else:
                pnl_vel1 = unrealized_ret

            # pnl_velocity_3 : variation entre barre k et max(1, k-3)
            ref_k3    = max(1, k - 3)
            pnl_vel3  = unrealized_ret - float(log_rets[ref_k3 - 1])

            rv_k = float(rv_arr[idx_k])
            pnl_norm = unrealized_ret / max(rv_k, 1e-6)

            pos_feats = {
                "bars_held":               float(k),
                "bars_remaining":          float(max_hold - k),
                "bars_frac":               k / max_hold,
                "unrealized_ret":          unrealized_ret,
                "unrealized_ret_bps":      unrealized_ret * 10_000,
                "max_ret_so_far":          max_ret,
                "min_ret_so_far":          min_ret,
                "drawdown_from_peak":      unrealized_ret - max_ret,
                "recovery_from_trough":    unrealized_ret - min_ret,
                "is_profitable":           float(unrealized_ret > cost_pct),
                "pnl_velocity_1":          pnl_vel1,
                "pnl_velocity_3":          pnl_vel3,
                "pnl_normalized":          pnl_norm,
                "entry_rsi":               float(entry_rsi_arr[t0]),
                "entry_adx":               float(entry_adx_arr[t0]),
                "entry_trend_score":       float(entry_trend_arr[t0]),
                "entry_momentum_score":    float(entry_mom_arr[t0]),
                "entry_close_position_in_range": float(entry_cpr_arr[t0]),
            }

            # ── Features marché à t0+k ────────────────────────────────────────
            mkt_feats = {}
            for feat in EXIT_MARKET_FEATURES:
                if feat in df.columns:
                    val = df.iloc[idx_k][feat]
                    mkt_feats[feat] = float(val) if pd.notna(val) else 0.0
                else:
                    mkt_feats[feat] = 0.0

            record = {
                **pos_feats,
                **mkt_feats,
                "y_exit": y_exit,
                "symbol": symbol,
                "t0": t0,
                "k": k,
            }
            records.append(record)

    if not records:
        return pd.DataFrame(columns=EXIT_ALL_FEATURES + ["y_exit", "symbol", "t0", "k"])

    return pd.DataFrame(records)


# ─── Helper runtime : état de position pour l'inférence live ─────────────────

def compute_position_state(
    bars_held:     int,
    entry_price:   float,
    current_price: float,
    max_price:     float,
    min_price:     float,
    prev_price:    float,
    price_3ago:    float,
    rv_24:         float,
    entry_bar:     dict,
    max_hold:      int   = 8,
    cost_pct:      float = COST_PCT,
) -> dict:
    """
    Calcule les 18 features de position pour l'inférence live.

    Paramètres
    ----------
    bars_held     : entier, nombre de barres 1h écoulées depuis l'entrée
    entry_price   : prix de clôture à l'entrée
    current_price : prix courant (dernière barre)
    max_price     : prix max observé depuis l'entrée (suivi dans trades.csv)
    min_price     : prix min observé depuis l'entrée (suivi dans trades.csv)
    prev_price    : close de la barre précédente (pour pnl_velocity_1)
    price_3ago    : close de 3 barres avant (pour pnl_velocity_3)
    rv_24         : réalisée vol 24h (de last_bar du parquet)
    entry_bar     : dict (ou pd.Series) des features de la barre d'entrée
    """
    def _lc(p: float) -> float:
        return np.log(max(float(p), 1e-9))

    cur_ret  = _lc(current_price) - _lc(entry_price)
    max_ret  = _lc(max(max_price, current_price)) - _lc(entry_price)
    min_ret  = _lc(min(min_price, current_price)) - _lc(entry_price)
    vel1     = cur_ret - (_lc(prev_price)  - _lc(entry_price)) if prev_price  > 0 else cur_ret
    vel3     = cur_ret - (_lc(price_3ago)  - _lc(entry_price)) if price_3ago  > 0 else cur_ret
    rv       = max(float(rv_24), 1e-6)

    def _ef(key: str, default: float = 0.0) -> float:
        v = entry_bar.get(key, default) if entry_bar else default
        try:
            f = float(v)
            return f if np.isfinite(f) else default
        except Exception:
            return default

    return {
        "bars_held":                   float(bars_held),
        "bars_remaining":              float(max(0, max_hold - bars_held)),
        "bars_frac":                   float(bars_held) / max(max_hold, 1),
        "unrealized_ret":              cur_ret,
        "unrealized_ret_bps":          cur_ret * 10_000,
        "max_ret_so_far":              max_ret,
        "min_ret_so_far":              min_ret,
        "drawdown_from_peak":          cur_ret - max_ret,
        "recovery_from_trough":        cur_ret - min_ret,
        "is_profitable":               float(cur_ret > cost_pct),
        "pnl_velocity_1":              vel1,
        "pnl_velocity_3":              vel3,
        "pnl_normalized":              cur_ret / rv,
        "entry_rsi":                   _ef("rsi_14",    50.0),
        "entry_adx":                   _ef("adx_20",   20.0),
        "entry_trend_score":           _ef("trend_score",    0.0),
        "entry_momentum_score":        _ef("momentum_score", 0.0),
        "entry_close_position_in_range": _ef("close_position_in_range", 0.5),
    }
