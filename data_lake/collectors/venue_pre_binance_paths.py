#!/usr/bin/env python3
"""
venue_pre_binance_paths.py -- fonctions PURES : quelles fenetres PRE-Binance, quels fichiers, quels chemins.

Tout est borne par t0 = premiere minute negociee du perpetuel Binance : rien apres t0 n'est demande ici.

Borne de CLOTURE (correctif P12) : une bougie n'est "avant t0" que si elle CLOTURE au plus tard a t0, soit
open_time + intervalle <= t0. Borner sur l'ouverture seule laissait passer, pour chaque evenement, une derniere
daily qui clôturait 0,2 a 22 h APRES l'arrivee de Binance (mesure sur 136/136 evenements stockes).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "pre_binance"                      # data/* est gitignore
WINDOWS = {"30d": 30 * 86400, "14d": 14 * 86400, "7d": 7 * 86400, "3d": 3 * 86400, "24h": 86400, "6h": 6 * 3600}
#: (intervalle, portee en secondes) : journalier sur 30 j, horaire sur 3 j, 5 min sur 6 h
GRANULARITY = (("1d", WINDOWS["30d"]), ("60m", WINDOWS["3d"]), ("5m", WINDOWS["6h"]))
INTERVAL_MS = {"1d": 86_400_000, "60m": 3_600_000, "5m": 300_000}
VENUES = ("mexc", "okx", "bybit", "kucoin", "gate")
ROUTE_STATUSES = ("collected_by_this_module", "collected_by_control_venue_and_second_venue_tapes", "collected_by_second_venue_tape_when_recent")
ROUTES = {  # OKX : 1Dutc (le bar 1D est aligne sur Hong Kong, 16:00 UTC)
    "mexc": {"spot": "GET https://api.mexc.com/api/v3/klines?symbol=<BASE>USDT&interval=<iv>&startTime=&endTime= (public, Binance-like, historical)",
             "perp": "GET https://contract.mexc.com/api/v1/contract/kline/<BASE>_USDT?interval=Day1|Min60|Min5&start=&end= (public)", "status": "collected_by_this_module", "cost": "free"},
    "okx": {"spot": "GET https://www.okx.com/api/v5/market/history-candles?instId=<BASE>-USDT&bar=1Dutc|1H|5m&after=&before= (public, historical)",
            "perp": "same with instId=<BASE>-USDT-SWAP", "status": "collected_by_control_venue_and_second_venue_tapes", "cost": "free"},
    "bybit": {"spot": "GET https://api.bybit.com/v5/market/kline?category=spot&symbol=<BASE>USDT&interval=D|60|5&start=&end= (public)",
              "perp": "same with category=linear", "status": "collected_by_control_venue_and_second_venue_tapes", "cost": "free"},
    "kucoin": {"spot": "GET https://api.kucoin.com/api/v1/market/candles?symbol=<BASE>-USDT&type=1day|1hour|5min&startAt=&endAt= (public)",
               "perp": "GET https://api-futures.kucoin.com/api/v1/kline/query?symbol=<BASE>USDTM&granularity=1440|60|5&from=&to=", "status": "collected_by_control_venue_and_second_venue_tapes", "cost": "free"},
    "gate": {"spot": "GET https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=<BASE>_USDT&interval=1d|1h|5m&from=&to= (public)",
             "perp": "GET https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=<BASE>_USDT&interval=1d|1h|5m&from=&to=", "status": "collected_by_second_venue_tape_when_recent", "cost": "free",
             "limit": "only the last 10 000 candles are served (~416 days in 1h): older windows return HTTP 400"},
}


def parse_ts(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def windows(t0: str) -> Dict[str, Dict[str, str]]:
    t = parse_ts(t0)
    return {k: {"start": (t - timedelta(seconds=s)).isoformat(timespec="seconds"), "end": t.isoformat(timespec="seconds")} for k, s in WINDOWS.items()}


def requests_for(t0: str) -> List[Dict[str, object]]:
    """Les trois requetes de bougies qui couvrent toutes les fenetres : [t0 - portee, t0), jamais au-dela de t0."""
    t = parse_ts(t0); out = []
    for iv, span in GRANULARITY:
        out.append({"interval": iv, "start_ms": int((t - timedelta(seconds=span)).timestamp() * 1000), "end_ms": int(t.timestamp() * 1000)})
    return out


def closed_by(rows: List[Dict[str, object]], interval: str, end_ms: int, start_ms: Optional[int] = None) -> List[Dict[str, object]]:
    """Ne garde que les bougies qui CLOTURENT au plus tard a end_ms (open + intervalle <= end_ms) et, si start_ms est
    donne, qui ouvrent a ou apres start_ms. Une bougie qui chevauche end_ms est retiree : elle contient de l'apres."""
    iv = INTERVAL_MS[interval]
    return [r for r in rows if int(r["open_time_ms"]) + iv <= end_ms and (start_ms is None or int(r["open_time_ms"]) >= start_ms)]


def local_path(venue: str, market: str, symbol: str, interval: str, t0: str, root: Optional[Path] = None) -> Path:
    r = Path(root) if root else STORE
    day = parse_ts(t0).strftime("%Y%m%dT%H%M")
    return r / venue / market / symbol / ("%s-%s-pre%s.json" % (symbol, interval, day))


def manifest_path(venue: str, event_id: str, root: Optional[Path] = None) -> Path:
    return (Path(root) if root else STORE) / venue / "manifests" / ("%s.json" % event_id)
