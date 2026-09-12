#!/usr/bin/env python3
"""Gate : ni le spot ni les contrats n'exposent de date de cotation. Les instruments sont rendus
avec first_listed_ts=None ; l'existence du marche reste un fait utile, sa date ne l'est pas.
Une date peut etre obtenue a la demande par la premiere bougie (`first_candle_ts`), un appel par symbole."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from data_lake.collectors.venue_clients import QUOTES, VenueError, http_json, instrument, ms_to_iso

VENUE = "gate"
SPOT = "https://api.gateio.ws/api/v4/spot/currency_pairs"
FUTURES = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
CANDLES = "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=%s&interval=1d&from=1451606400&limit=1"
HAS_NATIVE_LISTING_TIME = False


def fetch_instruments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for x in http_json(SPOT) or []:
            base, quote = x.get("base"), x.get("quote")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            out.append(instrument(VENUE, "spot", x.get("id") or "%s_%s" % (base, quote), base, quote, x.get("trade_status"), None, None, x))
    except VenueError:
        pass
    try:
        for x in http_json(FUTURES) or []:
            name = x.get("name") or ""
            parts = name.split("_")
            base, quote = (parts[0] if parts else None), (parts[1] if len(parts) > 1 else "USDT")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            out.append(instrument(VENUE, "perp", name, base, quote, "delisting" if x.get("in_delisting") else "trading", None, None, x))
    except VenueError:
        pass
    return out


def first_candle_ts(pair: str) -> Optional[str]:
    """Premiere bougie journaliere connue d'une paire spot : une date de premiere cotation par le marche lui-meme.
    Un appel par symbole : reserve aux actifs qu'aucune autre place n'a resolus."""
    try:
        rows = http_json(CANDLES % pair)
    except VenueError:
        return None
    if not rows:
        return None
    first = rows[0]
    t = first[0] if isinstance(first, list) else first.get("t")
    try:
        return ms_to_iso(int(float(t)) * 1000)
    except (TypeError, ValueError):
        return None
