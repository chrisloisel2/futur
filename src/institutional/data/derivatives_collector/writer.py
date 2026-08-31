"""
src/institutional/data/derivatives_collector/writer.py
─────────────────────────────────────────────────────────────────────────────
Writer APPEND-ONLY partitionné + MANIFEST (Phase 1, production).

    data/derivatives_raw/exchange=binance/market=usdm/stream=<s>/symbol=<sym>/date=<d>/
        part-*.parquet
        part-*.manifest.json

Règles (leçon corruption enriched) : jamais de réécriture/monolithe live ; chaque
flush = nouveau fichier immutable ; écriture atomique + validation magic bytes ;
chaque part a un manifest (sha256, schema, rows, ts, latence, validation_status).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.data.atomic_parquet import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = ROOT / "data" / "derivatives_raw"
COLLECTOR_VERSION = "v1.1"


def _partition_dir(stream: str, symbol: str, ts: datetime,
                   exchange: str = "binance", market: str = "usdm") -> Path:
    d = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return (RAW_ROOT / f"exchange={exchange}" / f"market={market}"
            / f"stream={stream}" / f"symbol={symbol}" / f"date={d}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_records(stream: str, symbol: str, records: List[Dict],
                  exchange: str = "binance", market: str = "usdm") -> Optional[Path]:
    """Écrit un lot dans une partition immutable + manifest. Retourne le chemin parquet."""
    if not records:
        return None
    now = datetime.now(timezone.utc)
    part_dir = _partition_dir(stream, symbol, now, exchange=exchange, market=market)
    part_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    base = f"part-{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    part = part_dir / f"{base}.parquet"
    atomic_write_parquet(df, part)

    # manifest
    ts_col = "timestamp" if "timestamp" in df.columns else None
    lat = df["latency_ms"] if "latency_ms" in df.columns else pd.Series(dtype=float)
    manifest = {
        "partition_id": f"{exchange}/{market}/{stream}/{symbol}/{now.strftime('%Y-%m-%d')}/{base}",
        "exchange": exchange, "market": market, "stream": stream, "symbol": symbol,
        "rows": int(len(df)),
        "start_ts": int(df[ts_col].min()) if ts_col else None,
        "end_ts": int(df[ts_col].max()) if ts_col else None,
        "sha256": _sha256(part),
        "schema_sha256": hashlib.sha256(",".join(sorted(df.columns)).encode()).hexdigest()[:16],
        "collector_version": COLLECTOR_VERSION,
        "written_at": now.isoformat(),
        "latency_p50_ms": float(np.nanpercentile(lat, 50)) if len(lat) else None,
        "latency_p99_ms": float(np.nanpercentile(lat, 99)) if len(lat) else None,
        "validation_status": "PASS",
    }
    (part_dir / f"{base}.manifest.json").write_text(json.dumps(manifest, indent=2))
    return part
