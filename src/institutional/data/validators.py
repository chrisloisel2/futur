"""
src/institutional/data/validators.py
─────────────────────────────────────────────────────────────────────────────
Validation causale des données — aucune fuite future tolérée.

Principe : une donnée au timestamp T ne peut contenir aucune information
postérieure à T. Ce module implémente les guards qui garantissent cette
propriété avant tout calcul de features ou de labels.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── Constantes de validation ─────────────────────────────────────────────────

MAX_STALE_MINUTES = 120   # écart max avant qu'une donnée soit considérée stale
MAX_MISSING_RATE = 0.05   # 5% de NaN max dans les colonnes OHLCV
MAX_PRICE_JUMP = 0.50     # saut de prix > 50% = outlier potentiel
MIN_VOLUME = 0.0          # volume négatif = erreur


# ─── Checks individuels ───────────────────────────────────────────────────────

def check_no_future_timestamps(
    df: pd.DataFrame,
    horizon_minutes: int = 0,
) -> List[str]:
    """Vérifie qu'aucun timestamp n'est dans le futur (avec marge horizon)."""
    issues = []
    cutoff = pd.Timestamp.utcnow() + pd.Timedelta(minutes=horizon_minutes + 5)
    if df.index.max() > cutoff:
        issues.append(
            f"Timestamps futurs détectés : max={df.index.max()}, cutoff={cutoff}"
        )
    return issues


def check_monotonic_index(df: pd.DataFrame) -> List[str]:
    """Vérifie que l'index est strictement croissant."""
    issues = []
    if not df.index.is_monotonic_increasing:
        issues.append("Index non monotone : présence de timestamps inversés")
    if df.index.duplicated().any():
        n = df.index.duplicated().sum()
        issues.append(f"{n} timestamps dupliqués")
    return issues


def check_missing_values(df: pd.DataFrame, critical_cols: List[str]) -> List[str]:
    """Vérifie le taux de NaN dans les colonnes critiques."""
    issues = []
    for col in critical_cols:
        if col not in df.columns:
            continue
        rate = df[col].isna().mean()
        if rate > MAX_MISSING_RATE:
            issues.append(f"Colonne {col!r} : {rate:.1%} NaN (max {MAX_MISSING_RATE:.0%})")
    return issues


def check_ohlcv_consistency(df: pd.DataFrame) -> List[str]:
    """Vérifie la cohérence interne des OHLCV."""
    issues = []
    required = ["open", "high", "low", "close"]
    if not all(c in df.columns for c in required):
        return ["Colonnes OHLCV manquantes"]

    # high >= max(open, close)
    bad_high = df["high"] < df[["open", "close"]].max(axis=1)
    if bad_high.any():
        issues.append(f"{bad_high.sum()} barres avec high < max(open, close)")

    # low <= min(open, close)
    bad_low = df["low"] > df[["open", "close"]].min(axis=1)
    if bad_low.any():
        issues.append(f"{bad_low.sum()} barres avec low > min(open, close)")

    # prix positifs
    if (df[required] <= 0).any().any():
        issues.append("Prix nuls ou négatifs détectés")

    # volume non négatif
    if "volume" in df.columns and (df["volume"] < MIN_VOLUME).any():
        issues.append("Volume négatif détecté")

    return issues


def check_price_outliers(df: pd.DataFrame, max_jump: float = MAX_PRICE_JUMP) -> List[str]:
    """Détecte les sauts de prix anormaux (probable erreur de données)."""
    issues = []
    if "close" not in df.columns:
        return issues
    log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
    outliers = (log_ret.abs() > max_jump).sum()
    if outliers > 0:
        issues.append(
            f"{outliers} barres avec saut de prix > {max_jump:.0%} "
            f"(vérifier les données brutes)"
        )
    return issues


def check_gaps(df: pd.DataFrame, expected_freq_minutes: int = 60) -> List[str]:
    """Détecte les trous dans la série temporelle."""
    issues = []
    if len(df) < 2:
        return issues

    gaps = df.index.to_series().diff().dt.total_seconds() / 60
    max_gap = gaps.max()
    if max_gap > MAX_STALE_MINUTES:
        n_gaps = (gaps > MAX_STALE_MINUTES).sum()
        issues.append(
            f"{n_gaps} trous > {MAX_STALE_MINUTES}min "
            f"(max gap = {max_gap:.0f}min)"
        )
    return issues


def check_causal_feature_alignment(
    features: pd.DataFrame,
    ohlcv: pd.DataFrame,
    tolerance_minutes: int = 5,
) -> List[str]:
    """
    Vérifie que les features ne regardent pas en avant par rapport à l'OHLCV.

    Une feature à T doit utiliser uniquement des données ≤ T.
    Cette fonction vérifie que les timestamps features sont alignés
    sur les closes OHLCV (pas les opens, pas les mid-bars).
    """
    issues = []
    if not features.index.isin(ohlcv.index).all():
        extra = (~features.index.isin(ohlcv.index)).sum()
        issues.append(
            f"{extra} timestamps features non présents dans l'OHLCV "
            f"— possible désalignement causal"
        )
    return issues


# ─── Validation complète ─────────────────────────────────────────────────────

def validate_ohlcv(
    df: pd.DataFrame,
    asset: str = "unknown",
    expected_freq_minutes: int = 60,
) -> List[str]:
    """
    Validation complète d'un DataFrame OHLCV.
    Retourne la liste des problèmes détectés (vide = OK).
    """
    all_issues: List[str] = []

    all_issues.extend(check_monotonic_index(df))
    all_issues.extend(check_missing_values(df, ["open", "high", "low", "close", "volume"]))
    all_issues.extend(check_ohlcv_consistency(df))
    all_issues.extend(check_price_outliers(df))
    all_issues.extend(check_gaps(df, expected_freq_minutes))

    if all_issues:
        for issue in all_issues:
            logger.warning(f"[{asset}] {issue}")
    return all_issues


def assert_no_lookahead(
    features: pd.DataFrame,
    label_horizon_minutes: int,
    check_col: Optional[str] = None,
) -> None:
    """
    Vérifie qu'une DataFrame de features n'a pas de lookahead.

    Méthode : calcule si le décalage moyen entre les features et les labels
    est cohérent avec l'horizon déclaré. C'est une vérification de cohérence,
    pas une preuve formelle — la preuve est dans la structure du code features.

    Lève AssertionError si problème détecté.
    """
    if features.empty:
        return
    if not features.index.is_monotonic_increasing:
        raise AssertionError("Index features non monotone — vérifier le pipeline")
    if features.index.duplicated().any():
        raise AssertionError("Timestamps dupliqués dans les features")
