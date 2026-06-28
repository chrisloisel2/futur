"""
src/institutional/engines/legacy_bridge.py
─────────────────────────────────────────────────────────────────────────────
Pont vers l'alpha legacy prouvé (ai/level_*, scripts paper). On NE modifie pas
le cœur TRM : on charge les modèles persistés et on appelle .predict().

Centralise :
  - configuration sys.path / configure_project_imports (pour joblib.load)
  - chargement OHLCV+features enrichis (data/enriched/{ASSET}_1h_enriched.parquet)
  - chargement des fleets TRM persistés (reports/paper_trading/.models)
  - calcul du régime long (NO_LONG / NEUTRAL / LONGABLE)

⚠️ FRONTIÈRE D'ANTI-LEAKAGE : les fleets persistés sont entraînés sur données
≤ TRAIN_END (2025). Tout backtest TRM doit donc utiliser une fenêtre ≥ 2026
pour rester hors-échantillon (cf. règle "ne jamais gonfler un backtest").
"""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parents[3]
ENRICHED_DIR = ROOT / "data" / "enriched"
MODELS_DIR = ROOT / "reports" / "paper_trading" / ".models"
TRAIN_END_TAG = "2025"          # tag des fichiers modèles {ASSET}_{TAG}.pkl
TRAIN_END_BOUNDARY = pd.Timestamp("2026-01-01", tz="UTC")  # OOS strictement après

_IMPORTS_READY = False


def _ensure_imports() -> None:
    """Met ROOT sur sys.path et configure les imports projet (idempotent)."""
    global _IMPORTS_READY
    if _IMPORTS_READY:
        return
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from core.settings import configure_project_imports
        configure_project_imports()
    except Exception as e:  # pragma: no cover
        logger.debug("configure_project_imports indisponible: %s", e)
    _IMPORTS_READY = True


def load_enriched(
    asset: str,
    required_cols: Optional[List[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Charge l'OHLCV+features enrichi 1h pour un asset (slim, colonnes ciblées)."""
    path = ENRICHED_DIR / f"{asset}_1h_enriched.parquet"
    if not path.exists():
        logger.warning("enriched absent: %s", path)
        return None
    _always = {
        "datetime", "open", "high", "low", "close", "Close", "volume",
        "dist_ema_50", "dist_ema_200", "dist_ema_20", "ema_spread_50_200",
        "rsi_14", "mom_logret_72", "mom_logret_168", "ema_spread_20_50", "rv_24",
        "realized_volatility_14", "realized_volatility_20",
        "realized_volatility_50", "realized_volatility_100",
    }
    try:
        if required_cols is not None:
            import pyarrow.parquet as pq
            avail = set(pq.ParquetFile(path).schema_arrow.names)
            cols = list((set(required_cols) | _always) & avail)
            df = pd.read_parquet(path, columns=cols)
        else:
            df = pd.read_parquet(path)
    except Exception as e:
        logger.warning("load enriched %s échec: %s", asset, e)
        return None

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    if "Close" not in df.columns and "close" in df.columns:
        df["Close"] = df["close"]
    _rv_map = {"rv_12": "realized_volatility_14", "rv_24": "realized_volatility_20",
               "rv_48": "realized_volatility_50", "rv_72": "realized_volatility_50",
               "rv_168": "realized_volatility_100"}
    for tgt, src in _rv_map.items():
        if tgt not in df.columns and src in df.columns:
            df[tgt] = df[src]
    if "rv_ratio_24_72" not in df.columns and "rv_24" in df.columns and "rv_72" in df.columns:
        df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].replace(0.0, np.nan)
    df = df.sort_values("datetime").reset_index(drop=True)
    if start is not None:
        df = df[df["datetime"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["datetime"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


@lru_cache(maxsize=16)
def load_trm_fleet(asset: str, train_end_tag: str = TRAIN_END_TAG):
    """Charge un TRMFleetLongV4 persisté (joblib). None si absent."""
    _ensure_imports()
    import joblib
    path = MODELS_DIR / f"{asset}_{train_end_tag}.pkl"
    if not path.exists():
        logger.warning("fleet TRM absent: %s", path)
        return None
    try:
        return joblib.load(path)
    except Exception as e:
        logger.warning("load fleet %s échec: %s", asset, e)
        return None


@lru_cache(maxsize=16)
def load_return_predictor(asset: str, train_end_tag: str = TRAIN_END_TAG):
    """Charge le ReturnPredictor persisté si présent. None sinon."""
    _ensure_imports()
    import joblib
    path = MODELS_DIR / f"{asset}_{train_end_tag}_return_pred.pkl"
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


def compute_regime_long(df: pd.DataFrame) -> pd.Series:
    """Calcule regime_long (NO_LONG/NEUTRAL/LONGABLE). 'UNKNOWN' si indisponible."""
    _ensure_imports()
    try:
        from ai.level_0.labels import compute_long_regime_col
        out = compute_long_regime_col(df.copy())
        if "regime_long" in out.columns:
            return out["regime_long"].astype(str)
    except Exception as e:
        logger.debug("compute_regime_long indisponible: %s", e)
    return pd.Series(["UNKNOWN"] * len(df), index=df.index)


def annualized_vol_from_rv(rv_hourly: float) -> float:
    """Convertit une vol horaire en vol annualisée bornée [0.1, 3.0]."""
    if rv_hourly is None or not np.isfinite(rv_hourly) or rv_hourly <= 0:
        return 0.5
    return float(np.clip(rv_hourly * np.sqrt(8760.0), 0.1, 3.0))
