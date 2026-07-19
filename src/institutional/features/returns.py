"""
src/institutional/features/returns.py
─────────────────────────────────────────────────────────────────────────────
Features de rendement — toutes causales (calculées sur données passées uniquement).

Toutes les fenêtres sont en barres (1 barre = 1h par défaut).
Aucun scaler global — les z-scores utilisent des statistiques rolling.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd


def log_returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Log-return sur `periods` barres (causal — utilise shift interne à pct_change)."""
    return np.log(close / close.shift(periods))


def compute_return_features(
    df: pd.DataFrame,
    close_col: str = "close",
    horizons_h: Optional[List[int]] = None,
    zscore_windows: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Calcule toutes les features de rendement.

    Horizons (en barres 1h) :
        1, 4, 8, 12, 24, 48, 72, 168 (= 1h, 4h, 8h, 12h, 1d, 2d, 3d, 7d)

    Z-scores rolling :
        60, 240 barres

    Returns
    -------
    DataFrame avec nouvelles colonnes (pas de modification en place)
    """
    if horizons_h is None:
        horizons_h = [1, 4, 8, 12, 24, 48, 72, 168]
    if zscore_windows is None:
        zscore_windows = [60, 240]

    close = df[close_col]
    out = pd.DataFrame(index=df.index)

    # Log returns bruts
    for h in horizons_h:
        col = f"log_ret_{h}h"
        out[col] = log_returns(close, h)

    # Cumulative log returns (= somme des log returns horaires)
    # Alias nommés pour la lisibilité
    out["log_ret_1d"] = log_returns(close, 24)
    out["log_ret_3d"] = log_returns(close, 72)
    out["log_ret_7d"] = log_returns(close, 168)

    # Z-scores rolling (causal : rolling sur fenêtre passée)
    for w in zscore_windows:
        base = out["log_ret_1h"] if "log_ret_1h" in out.columns else log_returns(close, 1)
        roll_mean = base.rolling(w, min_periods=w // 2).mean()
        roll_std = base.rolling(w, min_periods=w // 2).std()
        out[f"ret_zscore_{w}h"] = (base - roll_mean) / (roll_std + 1e-9)

    # Momentum (sign et magnitude)
    out["momentum_sign_1d"] = np.sign(out["log_ret_1d"])
    out["momentum_sign_3d"] = np.sign(out["log_ret_3d"])
    out["momentum_sign_7d"] = np.sign(out["log_ret_7d"])

    return out


def compute_excess_return(
    asset_close: pd.Series,
    benchmark_close: pd.Series,
    horizon_h: int = 24,
) -> pd.Series:
    """
    Rendement excédentaire d'un actif par rapport à son benchmark.
    Utilisé pour les features cross-sectional et relative value.
    """
    asset_ret = log_returns(asset_close, horizon_h)
    bench_ret = log_returns(benchmark_close, horizon_h)
    return asset_ret - bench_ret


def compute_trend_consistency(
    close: pd.Series,
    window: int = 20,
    horizon_h: int = 1,
) -> pd.Series:
    """
    Score de consistance de tendance : fraction de barres dans le sens de la tendance
    sur les `window` dernières barres.

    Range : [0, 1] où 1 = tendance parfaitement consistante dans un sens.
    Causal : utilise uniquement les `window` barres précédentes.
    """
    ret = log_returns(close, horizon_h)
    direction = np.sign(ret)
    # fraction de barres positives (si majorité positive) ou négatives
    frac_pos = direction.rolling(window, min_periods=window // 2).apply(
        lambda x: (x > 0).mean(), raw=True
    )
    # Transformer en [0, 1] symétrique autour de 0.5
    # score = |frac_pos - 0.5| × 2 → 0 = aucune tendance, 1 = tendance parfaite
    return (frac_pos - 0.5).abs() * 2
