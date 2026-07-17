"""
data_pipeline/derivatives_positioning.py — Archivage positioning dérivés Binance
================================================================================

Les endpoints `futures/data/*` de Binance (ratios top-trader, ratio global,
taker buy/sell, open interest historique) ne conservent qu'environ 30 jours
d'historique. Ce module les archive en continu dans le lake parquet local
(`data/raw/`), au format large : une ligne par (symbol, timestamp) avec les
colonnes des cinq endpoints fusionnées.

Il archive aussi un snapshot quotidien d'univers (perpétuels USDT-M classés
par volume 24 h) — nécessaire pour construire des univers point-in-time côté
recherche cross-sectionnelle.

Usage programmatique :
    from data_pipeline.derivatives_positioning import archive_positioning
    archive_positioning(root=Path("data/raw"), top_n=40)

CLI : scripts/archive_derivatives.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from data_pipeline.http import PublicHTTPClient
from data_pipeline.normalization import normalize_symbol
from data_pipeline.storage import write_partitioned_parquet

FAPI_BASE = "https://fapi.binance.com"

SOURCE_POSITIONING = "binance_futures_positioning"
SOURCE_UNIVERSE = "binance_futures_universe"
MARKET_TYPE = "futures_um"

# Rétention observée des endpoints futures/data : ~30 jours.
RETENTION_DAYS = 30
PAGE_LIMIT = 500

PERIOD_MINUTES = {
    "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240,
    "6h": 360, "12h": 720, "1d": 1440,
}

# endpoint -> (chemin, renommage des colonnes API -> colonnes lake)
POSITIONING_ENDPOINTS: Dict[str, Dict[str, str]] = {
    "topLongShortAccountRatio": {
        "longShortRatio": "ls_ratio_top_accounts",
        "longAccount": "top_accounts_long_pct",
        "shortAccount": "top_accounts_short_pct",
    },
    "topLongShortPositionRatio": {
        "longShortRatio": "ls_ratio_top_positions",
        "longAccount": "top_positions_long_pct",
        "shortAccount": "top_positions_short_pct",
    },
    "globalLongShortAccountRatio": {
        "longShortRatio": "ls_ratio_global",
        "longAccount": "global_long_pct",
        "shortAccount": "global_short_pct",
    },
    "takerlongshortRatio": {
        "buySellRatio": "taker_buy_sell_ratio",
        "buyVol": "taker_buy_vol",
        "sellVol": "taker_sell_vol",
    },
    "openInterestHist": {
        "sumOpenInterest": "oi",
        "sumOpenInterestValue": "oi_value",
    },
}


def _fetch_endpoint_series(
    client: PublicHTTPClient,
    endpoint: str,
    symbol: str,
    period: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Pagine un endpoint futures/data sur [start_ms, end_ms] par pas de PAGE_LIMIT."""
    step_ms = PERIOD_MINUTES[period] * 60_000 * PAGE_LIMIT
    rename = POSITIONING_ENDPOINTS[endpoint]
    rows: List[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + step_ms - 1, end_ms)
        payload = client.get_json(
            f"{FAPI_BASE}/futures/data/{endpoint}",
            params={
                "symbol": symbol,
                "period": period,
                "limit": PAGE_LIMIT,
                "startTime": cursor,
                "endTime": chunk_end,
            },
        )
        if isinstance(payload, list):
            rows.extend(payload)
        cursor = chunk_end + 1
    if not rows:
        return pd.DataFrame(columns=["timestamp", *rename.values()])
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns=rename)
    keep = ["timestamp", *[c for c in rename.values() if c in frame.columns]]
    frame = frame[keep]
    for col in rename.values():
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")


def fetch_positioning_wide(
    client: PublicHTTPClient,
    symbol: str,
    *,
    period: str = "5m",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """Fusionne les cinq endpoints positioning en un frame large indexé timestamp."""
    now = datetime.now(timezone.utc)
    end = end or now
    floor = now - timedelta(days=RETENTION_DAYS) + timedelta(hours=1)
    start = max(start or floor, floor)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if start_ms >= end_ms:
        return pd.DataFrame()

    merged: Optional[pd.DataFrame] = None
    for endpoint in POSITIONING_ENDPOINTS:
        frame = _fetch_endpoint_series(client, endpoint, symbol, period, start_ms, end_ms)
        if frame.empty:
            continue
        merged = frame if merged is None else merged.merge(frame, on="timestamp", how="outer")
    if merged is None:
        return pd.DataFrame()
    merged.insert(1, "symbol", normalize_symbol(symbol))
    return merged.sort_values("timestamp").reset_index(drop=True)


def latest_stored_timestamp(
    root: Path,
    symbol: str,
    *,
    source: str = SOURCE_POSITIONING,
    interval: str = "5m",
) -> Optional[pd.Timestamp]:
    """Timestamp max déjà archivé, en ne lisant que la partition la plus récente."""
    base = root / source / MARKET_TYPE / normalize_symbol(symbol) / interval
    files = sorted(base.glob("year=*/month=*/data.parquet"))
    if not files:
        return None
    frame = pd.read_parquet(files[-1], columns=["timestamp"])
    if frame.empty:
        return None
    return pd.to_datetime(frame["timestamp"], utc=True).max()


def archive_symbol_positioning(
    client: PublicHTTPClient,
    root: Path,
    symbol: str,
    *,
    period: str = "5m",
) -> int:
    """Archive incrémentalement le positioning d'un symbole. Retourne le nb de lignes écrites."""
    last = latest_stored_timestamp(root, symbol, interval=period)
    start = None
    if last is not None:
        # Recouvrement d'une barre pour absorber les valeurs partielles de la dernière barre.
        start = last.to_pydatetime() - timedelta(minutes=PERIOD_MINUTES[period])
    frame = fetch_positioning_wide(client, symbol, period=period, start=start)
    if frame.empty:
        return 0
    write_partitioned_parquet(
        frame,
        root=root,
        source=SOURCE_POSITIONING,
        market_type=MARKET_TYPE,
        symbol=symbol,
        interval=period,
        dedupe_keys=["symbol", "timestamp"],
    )
    return len(frame)


def fetch_universe(client: PublicHTTPClient, *, top_n: int = 40) -> pd.DataFrame:
    """Perpétuels USDT-M en TRADING, classés par volume quote 24 h décroissant."""
    info = client.get_json(f"{FAPI_BASE}/fapi/v1/exchangeInfo")
    perps = {
        item["symbol"]
        for item in info.get("symbols", [])
        if item.get("contractType") == "PERPETUAL"
        and item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
    }
    tickers = client.get_json(f"{FAPI_BASE}/fapi/v1/ticker/24hr")
    rows = [
        {
            "symbol": item["symbol"],
            "quote_volume_24h": float(item.get("quoteVolume", 0.0)),
            "last_price": float(item.get("lastPrice", 0.0)),
        }
        for item in tickers
        if item.get("symbol") in perps
    ]
    frame = pd.DataFrame(rows).sort_values("quote_volume_24h", ascending=False)
    frame = frame.head(top_n).reset_index(drop=True)
    frame.insert(0, "rank", frame.index + 1)
    frame["timestamp"] = pd.Timestamp.now(tz="UTC").floor("D")
    return frame


def archive_universe_snapshot(client: PublicHTTPClient, root: Path, *, top_n: int = 80) -> pd.DataFrame:
    """Archive le snapshot d'univers du jour (écrase la ligne du jour si relancé)."""
    frame = fetch_universe(client, top_n=top_n)
    # ensure_raw_schema écrase la colonne `symbol` par le symbole de partition :
    # on garde la liste des membres sous `member_symbol`.
    stored = frame.rename(columns={"symbol": "member_symbol"})
    write_partitioned_parquet(
        stored,
        root=root,
        source=SOURCE_UNIVERSE,
        market_type=MARKET_TYPE,
        symbol="GLOBAL",
        interval="1d",
        dedupe_keys=["member_symbol", "timestamp"],
    )
    return frame


def archive_positioning(
    root: Path,
    *,
    top_n: int = 40,
    extra_symbols: Sequence[str] = (),
    period: str = "5m",
    universe_top_n: int = 80,
    client: Optional[PublicHTTPClient] = None,
    log: bool = True,
) -> Dict[str, int]:
    """
    Point d'entrée principal : snapshot d'univers + archivage positioning
    des top_n symboles (plus extra_symbols). Retourne {symbol: lignes écrites}.
    """
    client = client or PublicHTTPClient(rate_limit_per_minute=150)
    universe = archive_universe_snapshot(client, root, top_n=universe_top_n)
    symbols = list(universe["symbol"].head(top_n))
    for sym in extra_symbols:
        clean = normalize_symbol(sym)
        if clean not in symbols:
            symbols.append(clean)

    written: Dict[str, int] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            n = archive_symbol_positioning(client, root, sym, period=period)
        except Exception as exc:  # un symbole en échec ne doit pas bloquer les autres
            if log:
                print(f"  [{i}/{len(symbols)}] {sym}: ERREUR {exc}")
            written[sym] = -1
            continue
        written[sym] = n
        if log:
            print(f"  [{i}/{len(symbols)}] {sym}: {n} lignes")
    return written
