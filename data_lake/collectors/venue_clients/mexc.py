#!/usr/bin/env python3
"""MEXC : `firstOpenTime` sur le spot, `createTime` sur les contrats. Deux appels groupes."""
from __future__ import annotations

from typing import Any, Dict, List

from data_lake.collectors.venue_clients import QUOTES, VenueError, http_json, instrument, ms_to_iso

VENUE = "mexc"
SPOT = "https://api.mexc.com/api/v3/exchangeInfo"
FUTURES = "https://contract.mexc.com/api/v1/contract/detail"
HAS_NATIVE_LISTING_TIME = True


def fetch_instruments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for x in http_json(SPOT).get("symbols") or []:
            base, quote = x.get("baseAsset"), x.get("quoteAsset")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            out.append(instrument(VENUE, "spot", x["symbol"], base, quote, x.get("status"), ms_to_iso(x.get("firstOpenTime")), "firstOpenTime", x))
    except VenueError:
        pass
    try:
        for x in http_json(FUTURES).get("data") or []:
            base, quote = x.get("baseCoin"), x.get("quoteCoin")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            out.append(instrument(VENUE, "perp", x["symbol"], base, quote, str(x.get("state")), ms_to_iso(x.get("createTime")), "createTime", x))
    except VenueError:
        pass
    return out
