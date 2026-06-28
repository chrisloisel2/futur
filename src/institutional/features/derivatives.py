"""
src/institutional/features/derivatives.py
─────────────────────────────────────────────────────────────────────────────
Features dérivées : funding, basis, open interest.

Ces features sont spécifiques aux marchés crypto futures et constituent
un avantage informationnel important pour BTC/ETH.

Toutes sont causales : computed at T using data ≤ T.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


ANNUALIZATION_FUNDING = 365 * 3  # funding 8h → annualisé (3 fundings/jour × 365)


# ─── Funding Rate ─────────────────────────────────────────────────────────────

def funding_cumulative(
    funding_rate: pd.Series,
    windows_8h: Optional[list] = None,
) -> pd.DataFrame:
    """
    Funding cumulé sur plusieurs fenêtres.

    funding_rate est en 8h. Les fenêtres sont en nombre de périodes 8h.
    """
    if windows_8h is None:
        windows_8h = [1, 3, 9, 27]   # 8h, 24h, 72h, 9j

    out = pd.DataFrame(index=funding_rate.index)
    for w in windows_8h:
        h = w * 8
        out[f"funding_cum_{h}h"] = funding_rate.rolling(w, min_periods=1).sum()

    return out


def funding_zscore(funding_rate: pd.Series, window: int = 90) -> pd.Series:
    """Z-score rolling du funding rate (causal)."""
    mu = funding_rate.rolling(window, min_periods=window // 2).mean()
    sigma = funding_rate.rolling(window, min_periods=window // 2).std()
    return (funding_rate - mu) / (sigma + 1e-9)


def funding_slope(funding_rate: pd.Series, lag: int = 3) -> pd.Series:
    """
    Variation de la tendance du funding sur `lag` périodes.
    Positive = funding en hausse (marché de plus en plus long).
    """
    return funding_rate - funding_rate.shift(lag)


def funding_annualized(funding_rate: pd.Series) -> pd.Series:
    """Funding rate 8h → taux annualisé (pour comparaison avec basis)."""
    return funding_rate * ANNUALIZATION_FUNDING


def compute_funding_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features funding.
    Suppose que df contient une colonne 'funding_rate' (à 1h par as-of join).
    """
    out = pd.DataFrame(index=df.index)
    if "funding_rate" not in df.columns:
        return out

    fr = df["funding_rate"]

    # Z-score
    out["funding_zscore"] = funding_zscore(fr)

    # Slope
    out["funding_slope_3p"] = funding_slope(fr, 3)

    # Annualisé
    out["funding_ann"] = funding_annualized(fr)

    # Cumulatif (sur données horaires — approx)
    for h in [8, 24, 72]:
        out[f"funding_cum_{h}h"] = fr.rolling(h, min_periods=1).sum()

    # Signe persistant (tous positifs sur 24h = marché très long)
    out["funding_all_pos_24h"] = (fr.rolling(24, min_periods=12).min() > 0).astype(float)
    out["funding_all_neg_24h"] = (fr.rolling(24, min_periods=12).max() < 0).astype(float)

    return out


# ─── Open Interest ────────────────────────────────────────────────────────────

def oi_change(oi_sum: pd.Series, periods: int = 1) -> pd.Series:
    """Variation relative de l'open interest."""
    return (oi_sum - oi_sum.shift(periods)) / (oi_sum.shift(periods) + 1e-9)


def oi_zscore(oi_sum: pd.Series, window: int = 168) -> pd.Series:
    """Z-score rolling de l'OI."""
    mu = oi_sum.rolling(window, min_periods=window // 2).mean()
    sigma = oi_sum.rolling(window, min_periods=window // 2).std()
    return (oi_sum - mu) / (sigma + 1e-9)


def price_oi_divergence(close: pd.Series, oi_sum: pd.Series, window: int = 24) -> pd.Series:
    """
    Divergence prix/OI : corrélation rolling de leurs z-scores.
    < 0 = divergence potentielle (signal de retournement).
    """
    price_ret = np.log(close / close.shift(1))
    oi_ret = oi_change(oi_sum)

    pr_z = (price_ret - price_ret.rolling(window, min_periods=12).mean()) / (
        price_ret.rolling(window, min_periods=12).std() + 1e-9
    )
    oi_z = (oi_ret - oi_ret.rolling(window, min_periods=12).mean()) / (
        oi_ret.rolling(window, min_periods=12).std() + 1e-9
    )

    # Corrélation rolling
    return pr_z.rolling(window, min_periods=12).corr(oi_z)


def compute_oi_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule toutes les features open interest."""
    out = pd.DataFrame(index=df.index)
    if "oi_sum" not in df.columns:
        return out

    oi = df["oi_sum"]

    out["oi_change_1h"] = oi_change(oi, 1)
    out["oi_change_4h"] = oi_change(oi, 4)
    out["oi_change_24h"] = oi_change(oi, 24)
    out["oi_zscore_168h"] = oi_zscore(oi)

    if "close" in df.columns:
        out["price_oi_div_24h"] = price_oi_divergence(df["close"], oi, 24)

    # Long/short ratio (si disponible)
    if "global_long_short_ratio" in df.columns:
        lsr = df["global_long_short_ratio"]
        mu = lsr.rolling(168, min_periods=84).mean()
        sigma = lsr.rolling(168, min_periods=84).std()
        out["lsr_zscore"] = (lsr - mu) / (sigma + 1e-9)
        out["lsr_extreme_long"] = (out["lsr_zscore"] > 2.0).astype(float)
        out["lsr_extreme_short"] = (out["lsr_zscore"] < -2.0).astype(float)

    return out


# ─── Basis ────────────────────────────────────────────────────────────────────

def basis_annualized(basis: pd.Series) -> pd.Series:
    """Basis en fraction → annualisé (horizon 8h approximatif)."""
    return basis * ANNUALIZATION_FUNDING


def basis_zscore(basis: pd.Series, window: int = 168) -> pd.Series:
    """Z-score rolling du basis."""
    mu = basis.rolling(window, min_periods=window // 2).mean()
    sigma = basis.rolling(window, min_periods=window // 2).std()
    return (basis - mu) / (sigma + 1e-9)


def compute_basis_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les features basis (futures - spot) / spot.
    Suppose la colonne 'basis' dans df (calculée par asof_join.build_master_frame).
    """
    out = pd.DataFrame(index=df.index)
    if "basis" not in df.columns:
        return out

    basis = df["basis"]
    out["basis"] = basis
    out["basis_ann"] = basis_annualized(basis)
    out["basis_zscore_168h"] = basis_zscore(basis)
    out["basis_abs"] = basis.abs()

    # Convergence : basis qui se resserre vers 0
    basis_slope = basis - basis.shift(24)
    out["basis_conv"] = -(basis * basis_slope).apply(np.sign)  # +1 si convergence

    return out
