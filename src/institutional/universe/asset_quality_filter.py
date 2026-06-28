"""
src/institutional/universe/asset_quality_filter.py
─────────────────────────────────────────────────────────────────────────────
Filtre qualité d'actif (univers 50) — un actif peut être SURVEILLÉ mais BLOQUÉ
au trading s'il n'a pas de données fiables. 50 cryptos = univers, pas 50 tradées.

  PASS  : données valides + récentes + couverture OK → éligible au ranking
  WARN  : données partielles → observe/shadow/size réduit
  BLOCK : pas de données / corrompu → aucun trade paper

Honnêteté : la plupart des 50 n'ont pas encore d'historique → BLOCK (NO_DATA).
Le collecteur live doit les accumuler avant tout trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
ENRICHED = ROOT / "data" / "enriched"


class AssetQualityStatus(str, Enum):
    PASS = "PASS"; WARN = "WARN"; BLOCK = "BLOCK"


@dataclass(frozen=True)
class AssetQualityDecision:
    symbol: str
    status: AssetQualityStatus
    reason: str
    rows: int
    data_coverage_recent: Optional[float]
    funding_available: bool
    last_bar: Optional[str]


def assess_asset(symbol: str, min_recent_coverage: float = 0.95,
                 max_staleness_days: int = 3) -> AssetQualityDecision:
    from scripts.validate_parquet_store import validate_file
    p = ENRICHED / f"{symbol}_1h_enriched.parquet"
    if not p.exists():
        return AssetQualityDecision(symbol, AssetQualityStatus.BLOCK, "NO_DATA", 0, None, False, None)
    rep = validate_file(p)
    if not rep["ok"]:
        return AssetQualityDecision(symbol, AssetQualityStatus.BLOCK,
                                    "INVALID:" + ";".join(rep["issues"])[:60], rep["rows"], None, False, None)
    import pyarrow.parquet as pq
    cols = set(pq.ParquetFile(p).schema_arrow.names)
    funding = "funding_rate" in cols
    df = pd.read_parquet(p, columns=["datetime"])
    ts = pd.to_datetime(df["datetime"], utc=True)
    last = ts.max()
    stale_days = (pd.Timestamp.now(tz="UTC") - last).days
    # couverture des 30 derniers jours
    recent = ts[ts >= last - pd.Timedelta(days=30)]
    cov = len(recent) / (30 * 24)
    if stale_days > max_staleness_days:
        return AssetQualityDecision(symbol, AssetQualityStatus.WARN, f"STALE_{stale_days}d",
                                    rep["rows"], round(cov, 3), funding, str(last))
    if cov < min_recent_coverage:
        return AssetQualityDecision(symbol, AssetQualityStatus.WARN, f"LOW_COVERAGE_{cov:.0%}",
                                    rep["rows"], round(cov, 3), funding, str(last))
    return AssetQualityDecision(symbol, AssetQualityStatus.PASS, "OK",
                                rep["rows"], round(cov, 3), funding, str(last))


def assess_universe(symbols) -> dict:
    return {s: assess_asset(s) for s in symbols}
