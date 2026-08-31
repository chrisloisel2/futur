"""
src/institutional/engines/vol_forecast_layer/realized_vol.py
─────────────────────────────────────────────────────────────────────────────
Volatilité réalisée (RV) BTCUSDT, calculée avec EXACTEMENT la même convention
déjà établie dans ce projet (src/institutional/features/volatility.py
::realized_vol / ANNUALIZATION_FACTOR = sqrt(24*365)) -- pas une formule
inventée pour ce layer. Série de prix source : data/enriched/
BTCUSDT_1h_enriched.parquet, le MÊME fichier que
reports/edge_discovery/alpha_hunt_2026-08-30/w6_options/REPORT.md utilise
pour ses propres calculs de RV ("perp price: data/enriched/
BTCUSDT_1h_enriched.parquet (Binance, hourly...) -- chosen ... because it's
the project's canonical enriched panel").

Seules les colonnes `datetime`/`log_return_1` sont lues (projection de
colonnes via pyarrow) -- ce fichier a 4050 colonnes et pèse ~1.7GB ; le lire
intégralement est exactement le piège documenté dans
memory/project_incident_scheduler_freeze.md (timeout sur parquet 4050 col).
La projection de colonnes l'évite entièrement.

sameday_rv(day) = std(log-returns horaires dont le timestamp tombe dans le
jour calendaire `day`, UTC) * sqrt(24*365) * 100 (points de pourcentage,
même échelle que l'ATM IV / DVOL -- sanity-check fait, pas un fit : sur
2026-08-30, atm_iv_traded=34.42 vs sameday_rv calculé=30.93, même ordre de
grandeur).

Causal par construction : sameday_rv(day) n'utilise QUE les timestamps
contenus dans `day` (jamais une barre future) -- calculable en temps réel à
la clôture UTC du jour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[4]
ENRICHED_DIR = ROOT / "data" / "enriched"

ANNUALIZATION_FACTOR = np.sqrt(24 * 365)  # identique à src/institutional/features/volatility.py
MIN_HOURLY_BARS_PER_DAY = 12              # >=50% de couverture -- tolère des trous, jamais moins


def load_hourly_returns(symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Charge UNIQUEMENT datetime + log_return_1 (projection de colonnes --
    NE JAMAIS lire le fichier enrichi complet, voir docstring module).
    DataFrame vide (bonnes colonnes) si le fichier ou les colonnes sont
    absents -- fail soft, pas de crash."""
    f = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    empty = pd.DataFrame(columns=["datetime", "log_return_1"])
    if not f.exists():
        return empty
    try:
        pf = pq.ParquetFile(f)
    except Exception:
        return empty
    if "datetime" not in pf.schema.names or "log_return_1" not in pf.schema.names:
        return empty
    df = pf.read(columns=["datetime", "log_return_1"]).to_pandas()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df


def compute_daily_realized_vol(
    symbol: str = "BTCUSDT", hourly_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """DataFrame[day, sameday_rv, n_hourly_bars] -- une ligne par jour
    calendaire UTC ayant au moins MIN_HOURLY_BARS_PER_DAY barres horaires
    valides. Vide si aucune donnée.

    `hourly_df` (colonnes datetime/log_return_1) est injectable pour les
    tests (évite de dépendre du disque, même pattern que
    funding_basis_disagreement/panel.py::build_panel(quarterly=, live_daily=))."""
    df = hourly_df if hourly_df is not None else load_hourly_returns(symbol)
    if df.empty:
        return pd.DataFrame(columns=["day", "sameday_rv", "n_hourly_bars"])

    df = df.dropna(subset=["log_return_1"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["day", "sameday_rv", "n_hourly_bars"])

    df["day"] = df["datetime"].dt.floor("D")
    grp = df.groupby("day")["log_return_1"]
    n = grp.count()
    std = grp.std()
    rv = std * ANNUALIZATION_FACTOR * 100.0

    out = pd.DataFrame({
        "day": n.index,
        "n_hourly_bars": n.to_numpy(),
        "sameday_rv": rv.to_numpy(),
    })
    out = out[out["n_hourly_bars"] >= MIN_HOURLY_BARS_PER_DAY].reset_index(drop=True)
    return out.sort_values("day").reset_index(drop=True)
