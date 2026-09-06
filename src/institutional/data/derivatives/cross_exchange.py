"""
src/institutional/data/derivatives/cross_exchange.py
─────────────────────────────────────────────────────────────────────────────
Néo-signaux CROSS-EXCHANGE (l'edge gratuit sans liquidations historiques).

Charge le funding de Binance/Bybit/OKX (backfill gratuit normalisé), aligne sur
la grille 8h par actif, calcule :
    cross_exchange_funding_spread = max − min (entre exchanges)
    funding_consensus            = moyenne (crowding directionnel)
    pairwise spreads (binance-bybit, binance-okx, bybit-okx)

Hypothèse d'edge : un spread de funding cross-exchange élevé = dislocation de
positionnement (un exchange sur-leveragé d'un côté) → tend à mean-revert et
signale du stress exploitable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
BACKFILL = ROOT / "data" / "derivatives_backfill"
EXCHANGES = ("binance", "bybit", "okx")
FUNDING_HOURS = (0, 8, 16)


def load_funding(exchange: str, symbol: str) -> Optional[pd.Series]:
    p = BACKFILL / exchange / "funding" / f"{symbol}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    s = df.set_index(pd.to_datetime(df["timestamp"], utc=True))["funding_rate"].sort_index()
    # normaliser sur la grille 8h
    s = s[s.index.hour.isin(FUNDING_HOURS)]
    return s[~s.index.duplicated(keep="last")]


def cross_exchange_funding(symbol: str) -> pd.DataFrame:
    """DataFrame aligné des funding par exchange + spread/consensus. Inner join (overlap)."""
    series = {}
    for ex in EXCHANGES:
        s = load_funding(ex, symbol)
        if s is not None and len(s):
            series[ex] = s
    if len(series) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(series).dropna(how="all")
    # arrondir l'index à l'heure de funding pour aligner (petits décalages de ms)
    df.index = df.index.floor("8h")
    df = df[~df.index.duplicated(keep="last")].dropna()
    if df.empty:
        return df
    cols = [c for c in EXCHANGES if c in df.columns]
    df["spread"] = df[cols].max(axis=1) - df[cols].min(axis=1)
    df["consensus"] = df[cols].mean(axis=1)
    if "binance" in cols and "bybit" in cols:
        df["binance_bybit"] = df["binance"] - df["bybit"]
    if "binance" in cols and "okx" in cols:
        df["binance_okx"] = df["binance"] - df["okx"]
    if "bybit" in cols and "okx" in cols:
        df["bybit_okx"] = df["bybit"] - df["okx"]
    return df


def funding_divergence_signal(symbol: str, z_window: int = 90) -> pd.DataFrame:
    """Ajoute spread_zscore + consensus_zscore (crowding) au panneau cross-exchange."""
    df = cross_exchange_funding(symbol)
    if df.empty:
        return df
    df["spread_zscore"] = (df["spread"] - df["spread"].rolling(z_window, min_periods=10).mean()) \
        / (df["spread"].rolling(z_window, min_periods=10).std() + 1e-12)
    df["consensus_zscore"] = (df["consensus"] - df["consensus"].rolling(z_window, min_periods=10).mean()) \
        / (df["consensus"].rolling(z_window, min_periods=10).std() + 1e-12)
    return df
