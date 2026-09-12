#!/usr/bin/env python3
"""
venue_clients -- un client par place, une seule question : quand ce marche a-t-il ete cote pour la premiere fois ?

Chaque client expose `fetch_instruments()` et rend une liste d'enregistrements au meme schema :

    venue, market_type (spot|perp), symbol, base, quote, status, first_listed_ts (ISO UTC ou None),
    first_listed_source (le champ d'ou vient la date), raw_hash

Aucun prix n'est demande, aucun ordre n'est passe, aucun signal n'est produit. Ce sont des metadonnees
de cycle de vie : l'existence d'un marche et sa date de naissance.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INSTRUMENT_FIELDS = ("venue", "market_type", "symbol", "base", "quote", "status", "first_listed_ts", "first_listed_source", "raw_hash")
QUOTES = ("USDT", "USDC", "USD")
UA = {"User-Agent": "futur-cross-venue/1.0", "Accept": "application/json"}


class VenueError(RuntimeError):
    pass


def http_json(url: str, timeout: int = 30, retries: int = 3) -> Any:
    last = None
    for a in range(retries):
        try:
            with urlopen(Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (400, 404):
                raise VenueError(last)
            time.sleep(1 + a)
        except Exception as e:                       # URLError, IncompleteRead, ssl, json...
            last = "%s: %s" % (type(e).__name__, str(e)[:60]); time.sleep(1 + a)
    raise VenueError(last or "unreachable")


def ms_to_iso(v: Any) -> Optional[str]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    n = n // 1000 if n > 10 ** 14 else n
    if n < 10 ** 11 or n > 10 ** 13 * 2:
        return None
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def raw_hash(o: Any) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()


def instrument(venue: str, market_type: str, symbol: str, base: str, quote: str, status: Optional[str],
               first_listed_ts: Optional[str], first_listed_source: Optional[str], raw: Any) -> Dict[str, Any]:
    return {"venue": venue, "market_type": market_type, "symbol": symbol, "base": (base or "").upper(), "quote": (quote or "").upper(),
            "status": status, "first_listed_ts": first_listed_ts, "first_listed_source": first_listed_source, "raw_hash": raw_hash(raw)}


def registry() -> Dict[str, Any]:
    from data_lake.collectors.venue_clients import okx, bybit, gate, mexc, kucoin
    return {"okx": okx, "bybit": bybit, "gate": gate, "mexc": mexc, "kucoin": kucoin}
