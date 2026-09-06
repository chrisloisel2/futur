"""
trading-system/src/institutional/data/column_normalizer.py
═══════════════════════════════════════════════════════════════════════════════
Normalisation des colonnes OHLCV vers le schéma canonique.

Schéma cible :
    index     : DatetimeIndex UTC (nommé "timestamp")
    open      : float
    high      : float
    low       : float
    close     : float
    volume    : float

Variantes supportées :
    lowercase         : open, high, low, close, volume
    Capitalized       : Open, High, Low, Close, Volume
    Binance CSV       : Open time, Open, High, Low, Close, Volume
    snake_case time   : open_time, open, high, low, close, volume
    price_ prefix     : price_open, price_high, price_low, price_close
    asset-prefixed    : btc_close (close-only → error si pas OHLCV complet)
    volume fallback   : quote_asset_volume, quote_volume, base_volume

Python 3.11+ requis. Stdlib + pandas uniquement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# Mapping de colonnes
# ══════════════════════════════════════════════════════════════════════════════

# Noms reconnus pour chaque dimension OHLCV
_OPEN_ALIASES: tuple[str, ...] = (
    "open", "Open", "OPEN", "price_open", "open_price",
    "Open Price", "open_bid", "first",
)
_HIGH_ALIASES: tuple[str, ...] = (
    "high", "High", "HIGH", "price_high", "high_price",
    "High Price", "max", "MAX",
)
_LOW_ALIASES: tuple[str, ...] = (
    "low", "Low", "LOW", "price_low", "low_price",
    "Low Price", "min", "MIN",
)
_CLOSE_ALIASES: tuple[str, ...] = (
    "close", "Close", "CLOSE", "price_close", "close_price",
    "Close Price", "last", "Last", "LAST", "price", "Price",
)
_VOLUME_ALIASES: tuple[str, ...] = (
    "volume", "Volume", "VOLUME", "vol", "Vol", "VOL",
    "base_volume", "base_asset_volume",
)
_VOLUME_FALLBACKS: tuple[str, ...] = (
    "quote_asset_volume", "quote_volume", "quoteVolume",
    "taker_buy_base_asset_volume", "total_volume",
)
_TIMESTAMP_ALIASES: tuple[str, ...] = (
    "timestamp", "datetime", "date", "time", "Date", "Time",
    "open_time", "Open time", "open time", "Datetime",
    "Timestamp", "ts", "index", "Date/Time",
)

_REQUIRED_OHLCV = ("open", "high", "low", "close")
_CANONICAL_OHLCV = ("open", "high", "low", "close", "volume")


# ══════════════════════════════════════════════════════════════════════════════
# Rapport de mapping
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ColumnMappingReport:
    """
    Résultat d'une tentative de normalisation de colonnes.

    is_valid == True  → DataFrame peut être utilisé pour features/labels
    is_valid == False → pipeline bloqué, vérifier missing_required
    """

    asset:               str
    source:              str
    original_columns:    tuple[str, ...]
    mapped:              dict[str, str]         # canonical → original
    missing_required:    tuple[str, ...]        # colonnes OHLCV absentes
    volume_source:       str                    # quelle colonne utilisée pour volume
    timestamp_source:    str                    # quelle colonne utilisée pour timestamp
    is_valid:            bool
    warnings:            tuple[str, ...]

    def summary(self) -> str:
        status = "OK  " if self.is_valid else "FAIL"
        mapped_str = ", ".join(
            f"{k}←{v}" for k, v in self.mapped.items() if k != v
        ) or "no renames"
        warn_str   = f" WARN={list(self.warnings)}" if self.warnings else ""
        return (
            f"[{status}] {self.asset}/{self.source}: "
            f"orig={len(self.original_columns)} cols | {mapped_str}"
            f"{warn_str}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Fonctions publiques
# ══════════════════════════════════════════════════════════════════════════════


def infer_timestamp_column(df: pd.DataFrame) -> Optional[str]:
    """
    Détecte la colonne timestamp dans df.

    Stratégie :
        1. Cherche par nom connu (_TIMESTAMP_ALIASES)
        2. Cherche par dtype datetime
        3. Vérifie si l'index est déjà un DatetimeIndex

    Retourne le nom de la colonne, ou None si l'index convient directement.
    """
    cols_lower = {c.lower().strip(): c for c in df.columns}

    # Recherche par alias connus
    for alias in _TIMESTAMP_ALIASES:
        if alias in df.columns:
            return alias
        if alias.lower() in cols_lower:
            return cols_lower[alias.lower()]

    # Recherche par dtype datetime dans les colonnes
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col

    # L'index est déjà un DatetimeIndex → pas besoin de colonne
    if isinstance(df.index, pd.DatetimeIndex):
        return None

    return None


def infer_ohlcv_columns(df: pd.DataFrame) -> dict[str, Optional[str]]:
    """
    Infère le mapping canonical → colonne_originale.

    Retourne un dict avec les clés "open", "high", "low", "close", "volume".
    Les valeurs sont None si la colonne n'a pas été trouvée.

    Cherche d'abord les correspondances exactes, puis case-insensitive.
    """
    cols_set    = set(df.columns)
    cols_lower  = {c.lower().strip(): c for c in df.columns}

    result: dict[str, Optional[str]] = {k: None for k in _CANONICAL_OHLCV}

    def _find(aliases: tuple[str, ...]) -> Optional[str]:
        # Exact match
        for a in aliases:
            if a in cols_set:
                return a
        # Case-insensitive
        for a in aliases:
            if a.lower() in cols_lower:
                return cols_lower[a.lower()]
        return None

    result["open"]   = _find(_OPEN_ALIASES)
    result["high"]   = _find(_HIGH_ALIASES)
    result["low"]    = _find(_LOW_ALIASES)
    result["close"]  = _find(_CLOSE_ALIASES)
    result["volume"] = _find(_VOLUME_ALIASES)

    # Volume fallback
    if result["volume"] is None:
        vol_fb = _find(_VOLUME_FALLBACKS)
        if vol_fb is not None:
            result["volume"] = vol_fb

    return result


def normalize_ohlcv_columns(
    df: pd.DataFrame,
    asset:  str = "unknown",
    source: str = "unknown",
) -> tuple[pd.DataFrame, ColumnMappingReport]:
    """
    Normalise un DataFrame vers le schéma canonique OHLCV.

    Paramètres
    ----------
    df     : DataFrame brut, n'importe quel schéma de colonnes
    asset  : nom de l'actif (pour le rapport)
    source : source des données (pour le rapport)

    Retourne
    --------
    (df_normalized, ColumnMappingReport)

    df_normalized a :
        - DatetimeIndex UTC nommé "timestamp"
        - Colonnes : open, high, low, close, volume (+ colonnes originales conservées)

    Lève
    ----
    ValueError si les colonnes OHLCV minimales (open/high/low/close) sont absentes.
    """
    orig_cols = tuple(df.columns.tolist())
    warnings: list[str] = []

    # ── 1. Timestamp ──────────────────────────────────────────────────────────

    ts_col = infer_timestamp_column(df)

    if ts_col is not None:
        df = df.copy()
        if ts_col in df.columns:
            df[ts_col] = pd.to_datetime(df[ts_col], utc=True, errors="raise")
            df = df.set_index(ts_col)
        df.index.name = "timestamp"
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        df.index.name = "timestamp"
    else:
        raise ValueError(
            f"{asset}/{source}: aucune colonne timestamp détectée. "
            f"Colonnes disponibles : {list(orig_cols)}"
        )

    ts_source = ts_col or "index"

    # ── 2. OHLCV mapping ──────────────────────────────────────────────────────

    ohlcv_map = infer_ohlcv_columns(df)

    # Renommer les colonnes trouvées vers le nom canonique
    rename_map: dict[str, str] = {}
    for canonical, original in ohlcv_map.items():
        if original is not None and original != canonical and original in df.columns:
            rename_map[original] = canonical

    if rename_map:
        df = df.rename(columns=rename_map)

    # ── 3. Volume fallback warning ─────────────────────────────────────────────

    vol_src = ohlcv_map.get("volume") or "absent"
    if vol_src not in ("volume", None) and vol_src not in {"volume"}:
        warnings.append(
            f"Volume issu de la colonne de secours {vol_src!r} "
            f"— vérifier que c'est le bon volume"
        )
    if vol_src is None or (vol_src != "volume" and vol_src not in df.columns and "volume" not in df.columns):
        warnings.append("Volume absent — certaines features volume seront NaN")

    # ── 4. Vérification des colonnes obligatoires ─────────────────────────────

    missing = tuple(
        c for c in _REQUIRED_OHLCV
        if c not in df.columns
    )

    is_valid = len(missing) == 0

    if not is_valid:
        raise ValueError(
            f"{asset}/{source}: colonnes OHLCV obligatoires absentes : {list(missing)}. "
            f"Colonnes disponibles après normalisation : {list(df.columns[:10])}. "
            f"Colonnes originales : {list(orig_cols[:10])}"
        )

    # ── 5. Rapport ────────────────────────────────────────────────────────────

    report = ColumnMappingReport(
        asset=asset,
        source=source,
        original_columns=orig_cols,
        mapped={k: v or "absent" for k, v in ohlcv_map.items()},
        missing_required=missing,
        volume_source=vol_src,
        timestamp_source=ts_source,
        is_valid=is_valid,
        warnings=tuple(warnings),
    )

    return df, report


# ══════════════════════════════════════════════════════════════════════════════
# Validation seule (sans modifier le df)
# ══════════════════════════════════════════════════════════════════════════════


def validate_ohlcv_schema(
    df: pd.DataFrame,
    asset:  str = "unknown",
    source: str = "unknown",
) -> ColumnMappingReport:
    """
    Valide qu'un DataFrame a un schéma OHLCV utilisable, sans le modifier.
    Utile pour un pre-check rapide avant chargement complet.
    """
    ohlcv_map  = infer_ohlcv_columns(df)
    ts_col     = infer_timestamp_column(df)
    missing    = tuple(k for k in _REQUIRED_OHLCV if ohlcv_map.get(k) is None)
    vol_src    = ohlcv_map.get("volume") or "absent"
    ts_src     = ts_col or ("index_datetime" if isinstance(df.index, pd.DatetimeIndex) else "absent")
    warnings_l: list[str] = []

    if ts_src == "absent":
        warnings_l.append("Aucune colonne ou index timestamp détecté")
    if vol_src == "absent":
        warnings_l.append("Volume absent")
    if ohlcv_map.get("volume") in _VOLUME_FALLBACKS:
        warnings_l.append(f"Volume via fallback: {ohlcv_map['volume']!r}")

    return ColumnMappingReport(
        asset=asset,
        source=source,
        original_columns=tuple(df.columns.tolist()),
        mapped={k: v or "absent" for k, v in ohlcv_map.items()},
        missing_required=missing,
        volume_source=vol_src,
        timestamp_source=ts_src,
        is_valid=len(missing) == 0 and ts_src != "absent",
        warnings=tuple(warnings_l),
    )
