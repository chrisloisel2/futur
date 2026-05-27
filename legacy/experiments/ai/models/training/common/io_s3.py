from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import numpy as np
import pandas as pd

# Répertoire des données locales 1m
_LOCAL_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "ohlcv_1m"


def _load_local_year(symbol: str, year: int) -> pd.DataFrame:
    """Charge le fichier parquet local et normalise les colonnes."""
    path = _LOCAL_DATA_DIR / f"{symbol}_{year}.parquet"
    if not path.exists():
        raise RuntimeError(f"Fichier local introuvable : {path}")

    df = pd.read_parquet(path)

    # Normaliser le timestamp → colonne 'datetime'
    if "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], utc=True)
    elif "open_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    else:
        raise RuntimeError("Pas de colonne timestamp dans les données locales")

    df = df.sort_values("datetime").reset_index(drop=True)

    # Renommer les colonnes en format attendu par les scripts d'entraînement
    rename = {
        "open":         "Open",
        "high":         "High",
        "low":          "Low",
        "close":        "Close",
        "volume":       "Volume",
        "quote_volume": "Quote_Volume",
        "ret_1m":       "ret",
        "rsi_14_1m":    "rsi_14",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # Calculer les features manquantes
    if "log_ret" not in df.columns and "Close" in df.columns:
        df["log_ret"] = np.log(df["Close"] / df["Close"].shift(1)).fillna(0)

    if "rv_15" not in df.columns and "log_ret" in df.columns:
        df["rv_15"]  = df["log_ret"].rolling(15).std().fillna(0)
    if "rv_60" not in df.columns and "log_ret" in df.columns:
        df["rv_60"]  = df["log_ret"].rolling(60).std().fillna(0)
    if "rv_240" not in df.columns and "log_ret" in df.columns:
        df["rv_240"] = df["log_ret"].rolling(240).std().fillna(0)

    if "ema_20" not in df.columns and "Close" in df.columns:
        df["ema_20"]  = df["Close"].ewm(span=20,  adjust=False).mean()
    if "ema_50" not in df.columns and "Close" in df.columns:
        df["ema_50"]  = df["Close"].ewm(span=50,  adjust=False).mean()
    if "ema_200" not in df.columns and "Close" in df.columns:
        df["ema_200"] = df["Close"].ewm(span=200, adjust=False).mean()

    if "atr_14" not in df.columns and all(c in df.columns for c in ("High", "Low", "Close")):
        prev_close = df["Close"].shift(1)
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr_14"] = tr.ewm(span=14, adjust=False).mean()

    return df


def read_year_df(base: str, symbol: str, quote: str, interval: str, year: int, cols: List[str]) -> pd.DataFrame:
    # Mode local : base = "local" ou chemin ne commençant pas par s3://
    if not base.startswith("s3://"):
        df = _load_local_year(symbol, year)
        available = [c for c in cols if c in df.columns]
        keep = list(dict.fromkeys(["datetime"] + available))
        return df[keep]

    # Mode S3 — import paresseux pour ne pas crasher sans awswrangler installé
    try:
        import awswrangler as wr
    except ImportError:
        raise ImportError(
            "awswrangler non installé. Passez --s3_dataset local pour utiliser les données locales."
        )

    path = f"{base.rstrip('/')}/interval={interval}/quote={quote}/symbol={symbol}/year={year}/"
    if not wr.s3.list_objects(path):
        raise RuntimeError(f"Préfixe S3 introuvable : {path}")
    df = wr.s3.read_parquet(path, columns=cols, dataset=False)

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    elif "Open Time" in df.columns:
        df["datetime"] = pd.to_datetime(df["Open Time"], unit="ms", utc=True)
    elif "open_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    else:
        raise RuntimeError("Pas de colonne datetime")

    return df.sort_values("datetime").reset_index(drop=True)


def count_total_windows(
    base: str, symbol: str, quote: str, interval: str, years: List[int],
    lookback: int, horizon: int,
) -> int:
    total = 0
    bridge = lookback + horizon
    tail: Optional[pd.DataFrame] = None

    for y in years:
        df = read_year_df(base, symbol, quote, interval, y, ["datetime"])
        if tail is not None:
            df = pd.concat([tail, df], ignore_index=True)
        T = len(df)
        total += max(0, T - lookback - horizon)
        tail = df.iloc[-bridge:].copy() if T >= bridge else df.copy()

    return int(total)
