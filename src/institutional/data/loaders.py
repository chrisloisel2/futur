"""
src/institutional/data/loaders.py
─────────────────────────────────────────────────────────────────────────────
Loaders pour la structure data_out/{year}/raw/ existante.

Charge, normalise et resample les données brutes vers des DataFrames
canoniques 1h indexés UTC. Aucune feature n'est calculée ici.

Schéma de sortie canonique (OHLCV 1h) :
    index     : pd.DatetimeIndex UTC
    open, high, low, close : float
    volume    : float (en base asset)
    asset     : str
    source    : str ("enriched" | "futures" | "spot")

PRIORITÉ DE SOURCE (tous actifs) :
    1. data/enriched/{ASSET}_1h_enriched.parquet   ← préféré (1h, OHLCV complet)
    2. data_out/{year}/raw/ (BTC uniquement)        ← fallback 1min → resample

ATTENTION : binance_eth/bnb/sol.parquet ne contiennent QUE des séries de
clôture (2 colonnes) — pas des OHLCV. Ces fichiers ne sont jamais utilisés
pour la construction de features.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── Configuration des chemins ───────────────────────────────────────────────

DATA_ROOT = Path(__file__).parents[3] / "data_out"
ENRICHED_ROOT = Path(__file__).parents[3] / "data" / "enriched"

# mapping asset → fichier pour les alts (dans data_out/{year}/raw/)
ALT_FILE_MAP: Dict[str, str] = {
    "ETHUSDT": "binance_eth.parquet",
    "BNBUSDT": "binance_bnb.parquet",
    "SOLUSDT": "binance_sol.parquet",
}

FUTURES_FILE = "binance_futures_klines.parquet"
SPOT_FILE = "binance_spot.parquet"
FUNDING_FILE = "binance_funding.parquet"
METRICS_FILE = "binance_metrics.parquet"

AVAILABLE_YEARS = list(range(2019, 2027))


# ─── Chargement brut ─────────────────────────────────────────────────────────

def _load_years(
    years: List[int],
    filename: str,
    asset_col: Optional[str] = None,
) -> pd.DataFrame:
    """Charge et concatène un fichier parquet sur plusieurs années."""
    frames = []
    for y in years:
        path = DATA_ROOT / str(y) / "raw" / filename
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            if asset_col:
                df["asset"] = asset_col
            frames.append(df)
        except Exception as e:
            logger.warning(f"Impossible de charger {path}: {e}")
    if not frames:
        raise FileNotFoundError(f"Aucun fichier trouvé pour {filename} dans {years}")
    return pd.concat(frames, ignore_index=True)


def _normalize_timestamps(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame:
    """Normalise les timestamps en UTC, déduplique, trie."""
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.sort_values(ts_col).drop_duplicates(subset=[ts_col], keep="first")
    df = df.set_index(ts_col).sort_index()
    return df


def _resample_ohlcv_to_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample données minute → 1h via règles OHLCV standard."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "quote_volume" in df.columns:
        agg["quote_volume"] = "sum"
    if "n_trades" in df.columns:
        agg["n_trades"] = "sum"
    if "taker_buy_quote" in df.columns:
        agg["taker_buy_quote"] = "sum"

    resampled = df.resample("1H").agg(agg)
    return resampled.dropna(subset=["close"])


# ─── Loaders publics ─────────────────────────────────────────────────────────

def load_btc_futures_1h(
    start: str,
    end: str,
    years: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Charge BTCUSDT futures klines à 1h entre start et end.

    Sources (par ordre de priorité) :
      1. data/enriched/BTCUSDT_1h_enriched.parquet (couverture 2017-2026)
      2. data_out/{year}/raw/binance_futures_klines.parquet
    """
    # Priorité : enriched data si disponible (meilleure couverture)
    enriched_path = ENRICHED_ROOT / "BTCUSDT_1h_enriched.parquet"
    if enriched_path.exists():
        try:
            df = pd.read_parquet(enriched_path, columns=["datetime", "open", "high", "low", "close", "volume"])
            df = df.rename(columns={"datetime": "timestamp"})
            df = _normalize_timestamps(df, "timestamp")
            df["asset"] = "BTCUSDT"
            df["source"] = "enriched"
            result = df.loc[start:end]
            if len(result) > 0:
                return result
        except Exception as e:
            logger.debug(f"Enriched BTC load failed ({e}), falling back to data_out")

    # Fallback : data_out partitionné par année
    years = years or AVAILABLE_YEARS
    raw = _load_years(years, FUTURES_FILE)
    raw = raw.rename(columns={
        "quote_volume": "quote_vol",
        "taker_buy_quote": "taker_buy",
    })
    raw = _normalize_timestamps(raw, "timestamp")

    freq = pd.tseries.frequencies.to_offset(pd.infer_freq(raw.index[:100]) or "1T")
    if freq and freq <= pd.tseries.frequencies.to_offset("5T"):
        raw = _resample_ohlcv_to_1h(raw)

    raw["asset"] = "BTCUSDT"
    raw["source"] = "futures"
    return raw.loc[start:end]


def _load_from_enriched(
    asset: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """
    Charge l'actif depuis data/enriched/{ASSET}_1h_enriched.parquet.
    Retourne None si le fichier n'existe pas ou si le résultat est vide.

    Source canonique préférée pour TOUS les actifs :
      - Données déjà à 1h (pas de resampling)
      - OHLCV complet (open/high/low/close/volume)
      - Couverture 2017-2026+
    """
    enriched_path = ENRICHED_ROOT / f"{asset}_1h_enriched.parquet"
    if not enriched_path.exists():
        return None
    try:
        df = pd.read_parquet(
            enriched_path,
            columns=["datetime", "open", "high", "low", "close", "volume"],
        )
        df = df.rename(columns={"datetime": "timestamp"})
        df = _normalize_timestamps(df, "timestamp")
        df["asset"]  = asset
        df["source"] = "enriched"
        result = df.loc[start:end]
        if len(result) == 0:
            logger.warning(f"Enriched {asset}: aucune barre entre {start} et {end}")
            return None
        return result
    except Exception as e:
        logger.warning(f"Enriched {asset}: échec chargement ({e})")
        return None


def load_asset_1h(
    asset: str,
    start: str,
    end: str,
    years: Optional[List[int]] = None,
    source: str = "futures",
) -> pd.DataFrame:
    """
    Charge OHLCV 1h pour un actif donné.

    ORDRE DE PRIORITÉ :
        1. data/enriched/{ASSET}_1h_enriched.parquet  (toujours préféré)
        2. Pour BTC uniquement : data_out/{year}/raw/binance_futures_klines.parquet

    NOTE : binance_eth/bnb/sol.parquet ne contiennent pas d'OHLCV complet
    (seulement une série de clôture) et ne sont JAMAIS utilisés.
    """
    years = years or AVAILABLE_YEARS

    # ── 1. Enriched en priorité pour TOUS les actifs ──────────────────────────
    df = _load_from_enriched(asset, start, end)
    if df is not None:
        logger.info(f"  {asset}: enriched OK ({len(df)} barres)")
        return df

    # ── 2. BTC fallback : data_out futures klines (1min → 1h) ────────────────
    if asset == "BTCUSDT":
        logger.info(f"  {asset}: enriched absent, fallback data_out")
        return load_btc_futures_1h(start, end, years)

    # ── 3. Aucune source disponible ───────────────────────────────────────────
    enriched_path = ENRICHED_ROOT / f"{asset}_1h_enriched.parquet"
    raise FileNotFoundError(
        f"Aucune source OHLCV pour {asset}.\n"
        f"  Attendu : {enriched_path}\n"
        f"  Note    : binance_eth/bnb/sol.parquet = séries de clôture uniquement, "
        f"pas des OHLCV. Placer le fichier enriched dans data/enriched/."
    )


def load_funding(
    start: str,
    end: str,
    years: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Charge les funding rates BTC (8h) entre start et end.
    Colonnes : funding_rate, funding_mark_price
    """
    years = years or AVAILABLE_YEARS
    raw = _load_years(years, FUNDING_FILE)
    raw = _normalize_timestamps(raw, "timestamp")
    return raw.loc[start:end]


def load_metrics(
    start: str,
    end: str,
    years: Optional[List[int]] = None,
    resample_to: str = "1H",
) -> pd.DataFrame:
    """
    Charge open interest + long/short ratio + taker ratio (5m → 1h par défaut).
    Colonnes : oi_sum, oi_value_sum, top_trader_lsr, global_long_short_ratio, taker_buy_sell_ratio
    """
    years = years or AVAILABLE_YEARS
    raw = _load_years(years, METRICS_FILE)
    raw = _normalize_timestamps(raw, "timestamp")

    if resample_to:
        agg = {
            "oi_sum": "last",
            "oi_value_sum": "last",
            "top_trader_lsr": "mean",
            "top_trader_lsr_sum": "mean",
            "global_long_short_ratio": "mean",
            "taker_buy_sell_ratio": "mean",
        }
        raw = raw.resample(resample_to).agg({k: v for k, v in agg.items() if k in raw.columns})

    return raw.loc[start:end]


def load_all_assets(
    assets: List[str],
    start: str,
    end: str,
    years: Optional[List[int]] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Charge le OHLCV 1h pour une liste d'actifs.
    Retourne un dict asset → DataFrame.
    """
    result = {}
    for asset in assets:
        try:
            result[asset] = load_asset_1h(asset, start, end, years)
            logger.info(f"  {asset}: {len(result[asset])} barres 1h chargées")
        except FileNotFoundError as e:
            logger.warning(f"  {asset}: {e}")
    return result


def load_universe_panel(
    assets: List[str],
    start: str,
    end: str,
    years: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Charge tous les actifs et les combine en un panel (MultiIndex asset/timestamp).
    Utile pour les features cross-sectional.
    """
    frames = []
    ohlcvs = load_all_assets(assets, start, end, years)
    for asset, df in ohlcvs.items():
        df = df.copy()
        df["asset"] = asset
        frames.append(df)

    if not frames:
        raise ValueError(f"Aucun actif chargé pour {assets}")

    panel = pd.concat(frames)
    panel.index.name = "timestamp"
    return panel.sort_index()
