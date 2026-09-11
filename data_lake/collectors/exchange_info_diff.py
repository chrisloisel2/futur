#!/usr/bin/env python3
"""
exchange_info_diff.py -- l'exchangeInfo comme flux d'evenements.

Binance ne publie pas l'historique des statuts : on le fabrique en photographiant exchangeInfo
(USDS-M futures et spot) et en ne gardant que les DIFFERENCES : symbole ajoute / retire, statut,
onboardDate, type de contrat, actif de marge, filtres (tick, lot, notionnel min), permissions,
marge autorisee (spot). Chaque photo dont le hash change est archivee (append-only).
Aucun prix, aucun signal.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data_lake.collectors.market_state_schema import TAPE_ROOT, raw_hash, now_local

ENDPOINTS = {"binance_um": "https://fapi.binance.com/fapi/v1/exchangeInfo", "binance_spot": "https://api.binance.com/api/v3/exchangeInfo"}
SNAP_DIR = TAPE_ROOT / "exchange_info"
WATCHED_FILTERS = {"PRICE_FILTER": ("tickSize",), "LOT_SIZE": ("minQty", "stepSize"), "MIN_NOTIONAL": ("notional", "minNotional")}   # MARKET_LOT_SIZE.maxQty est recalcule en continu par Binance spot : bruit, pas un evenement


def fetch(venue: str, timeout: int = 30) -> Dict[str, Any]:
    url = ENDPOINTS[venue]
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "futur-market-state"}), timeout=timeout) as r:
                return json.loads(r.read())
        except HTTPError as e:
            if e.code in (418, 429):
                time.sleep(20 * (attempt + 1)); continue
            time.sleep(2 * (attempt + 1))
        except (URLError, TimeoutError, OSError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("exchangeInfo unavailable for %s" % venue)


def _filters(sym: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for f in sym.get("filters", []):
        keys = WATCHED_FILTERS.get(f.get("filterType"))
        if keys:
            for k in keys:
                if k in f:
                    out[f"{f['filterType']}.{k}"] = f[k]
    return out


def normalize(payload: Dict[str, Any], venue: str) -> Dict[str, Dict[str, Any]]:
    """symbol -> vue plate et stable (ordre des cles fixe) de ce qu'on surveille."""
    out = {}
    for s in payload.get("symbols", []):
        if venue == "binance_um":
            rec = {"status": s.get("status"), "onboard_date": s.get("onboardDate"), "delivery_date": s.get("deliveryDate"),
                   "contract_type": s.get("contractType"), "margin_asset": s.get("marginAsset"), "quote_asset": s.get("quoteAsset"),
                   "base_asset": s.get("baseAsset"), "market_type": "perp" if s.get("contractType") == "PERPETUAL" else "futures",
                   "permissions": sorted(s.get("permissionSets", []) or []) if isinstance(s.get("permissionSets"), list) else s.get("permissionSets"),
                   "liquidation_fee": s.get("liquidationFee"), "maint_margin_percent": s.get("maintMarginPercent"),
                   "market_take_bound": s.get("marketTakeBound"), "max_move_order_limit": s.get("maxMoveOrderLimit"),
                   "underlying_type": s.get("underlyingType"), "filters": _filters(s), "margin_trading_allowed": None}
        else:
            rec = {"status": s.get("status"), "onboard_date": None, "delivery_date": None, "contract_type": "SPOT", "margin_asset": None,
                   "quote_asset": s.get("quoteAsset"), "base_asset": s.get("baseAsset"), "market_type": "spot",
                   "permissions": sorted({p for ps in (s.get("permissionSets") or []) for p in ps}) if s.get("permissionSets") else sorted(s.get("permissions", []) or []),
                   "liquidation_fee": None, "maint_margin_percent": None, "market_take_bound": None, "max_move_order_limit": None,
                   "underlying_type": None, "filters": _filters(s), "margin_trading_allowed": s.get("isMarginTradingAllowed"),
                   "spot_trading_allowed": s.get("isSpotTradingAllowed")}
        out[s["symbol"]] = rec
    return out


def diff(prev: Optional[Dict[str, Dict[str, Any]]], cur: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Liste de changements atomiques entre deux vues normalisees. prev=None => tout est 'symbol_added' (baseline)."""
    changes: List[Dict[str, Any]] = []
    prev = prev or {}
    for sym, c in cur.items():
        p = prev.get(sym)
        if p is None:
            changes.append({"type": "symbol_added", "symbol": sym, "old": None, "new": c, "baseline": not bool(prev)}); continue
        for field, ctype in (("status", "status_changed"), ("onboard_date", "onboard_date_changed"), ("contract_type", "contract_type_changed"),
                             ("margin_asset", "margin_asset_changed"), ("permissions", "permissions_changed"), ("filters", "filters_changed"),
                             ("margin_trading_allowed", "margin_trading_changed"), ("delivery_date", "delivery_date_changed"),
                             ("liquidation_fee", "liquidation_fee_changed"), ("maint_margin_percent", "maint_margin_changed")):
            if p.get(field) != c.get(field):
                changes.append({"type": ctype, "symbol": sym, "old": p.get(field), "new": c.get(field), "cur": c})
    for sym, p in prev.items():
        if sym not in cur:
            changes.append({"type": "symbol_removed", "symbol": sym, "old": p, "new": None})
    return changes


class ExchangeInfoWatcher:
    """Photographie, compare, archive (append-only) ; conserve la derniere vue normalisee sur disque."""

    def __init__(self, venue: str, root: Path = SNAP_DIR):
        self.venue = venue; self.dir = Path(root) / f"venue={venue}"; self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "last_view.json"
        self.prev: Optional[Dict[str, Dict[str, Any]]] = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        self.last_hash = raw_hash(self.prev) if self.prev is not None else None

    def poll(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload if payload is not None else fetch(self.venue)
        cur = normalize(payload, self.venue); h = raw_hash(cur)
        server_ts = payload.get("serverTime")
        if h == self.last_hash:
            return {"changed": False, "hash": h, "n_symbols": len(cur), "changes": [], "server_time": server_ts}
        changes = diff(self.prev, cur)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ")
        snap = self.dir / f"snapshot_{ts}_{h[:12]}.json"
        snap.write_text(json.dumps({"venue": self.venue, "captured_at_local": now_local(), "server_time": server_ts, "hash": h, "raw_hash": raw_hash(payload),
                                    "n_symbols": len(cur), "view": cur}, separators=(",", ":"), ensure_ascii=False))
        with open(self.dir / "changes.jsonl", "a", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps({"venue": self.venue, "detected_at_local": now_local(), "server_time": server_ts, "snapshot": snap.name, **c}, ensure_ascii=False, default=str) + "\n")
        self.state_path.write_text(json.dumps(cur, separators=(",", ":"), ensure_ascii=False))
        self.prev, self.last_hash = cur, h
        return {"changed": True, "hash": h, "n_symbols": len(cur), "changes": changes, "server_time": server_ts, "snapshot": str(snap)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", default="binance_um", choices=list(ENDPOINTS)); ap.add_argument("--once", action="store_true"); ap.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args(); w = ExchangeInfoWatcher(a.venue)
    while True:
        r = w.poll(); print(json.dumps({**{k: v for k, v in r.items() if k != "changes"}, "n_changes": len(r["changes"]), "types": sorted({c["type"] for c in r["changes"]})}, default=str), flush=True)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
