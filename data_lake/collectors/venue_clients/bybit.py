#!/usr/bin/env python3
"""Bybit : `launchTime` est publie pour les perpetuels lineaires. Le spot n'a pas de date de cotation
dans instruments-info : il est rendu avec first_listed_ts=None, ce que le rapport dit explicitement."""
from __future__ import annotations

from typing import Any, Dict, List

from data_lake.collectors.venue_clients import QUOTES, http_json, instrument, ms_to_iso

VENUE = "bybit"
BASE = "https://api.bybit.com/v5/market/instruments-info?category=%s&limit=1000"
HAS_NATIVE_LISTING_TIME = True


def _page(category: str) -> List[Dict[str, Any]]:
    rows, cursor = [], None
    for _ in range(20):
        url = BASE % category + (("&cursor=" + cursor) if cursor else "")
        d = http_json(url)
        res = d.get("result") or {}
        rows.extend(res.get("list") or [])
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    return rows


def fetch_instruments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for category, market in (("linear", "perp"), ("spot", "spot")):
        for x in _page(category):
            base, quote = x.get("baseCoin"), x.get("quoteCoin")
            if not base or (quote or "").upper() not in QUOTES:
                continue
            if category == "linear" and x.get("contractType") not in ("LinearPerpetual", None):
                continue
            ts = ms_to_iso(x.get("launchTime")) if category == "linear" else None
            out.append(instrument(VENUE, market, x["symbol"], base, quote, x.get("status"), ts, "launchTime" if ts else None, x))
    return out
