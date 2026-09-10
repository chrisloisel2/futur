#!/usr/bin/env python3
"""
symbol_lifecycle.py -- de la difference d'exchangeInfo a l'evenement de cycle de vie.

Un marche nait (symbole ajoute, onboardDate pose), change de statut (PENDING_TRADING -> TRADING ->
SETTLING / BREAK / CLOSE), devient ou cesse d'etre shortable (marge spot autorisee, perp existant),
meurt (retire). L'etat par symbole est persistant ; chaque transition produit un
symbol_lifecycle_event (schema obligatoire) et, le cas echeant, un declencheur de capture.
Aucun prix, aucun signal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_lake.collectors.market_state_schema import TAPE_ROOT, make_lifecycle_event, iso_ms, now_local

STATE_PATH = TAPE_ROOT / "lifecycle" / "state.json"
DEAD_STATUSES = {"SETTLING", "CLOSE", "CLOSED", "DELISTED", "DELIVERING", "PRE_DELIVERING", "DELIVERED"}
LIVE_STATUSES = {"TRADING"}


class SymbolLifecycle:
    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = Path(state_path); self.state: Dict[str, Dict[str, Any]] = {}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())

    def _key(self, venue: str, symbol: str) -> str:
        return f"{venue}|{symbol}"

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(self.state, separators=(",", ":"), ensure_ascii=False)); tmp.replace(self.state_path)

    def apply(self, venue: str, changes: List[Dict[str, Any]], server_time: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """-> (lifecycle_events, triggers). Un changement 'baseline' (premiere photo) enregistre l'etat sans declencher."""
        events, triggers = [], []
        ex_ts = iso_ms(server_time) if server_time else None
        for c in changes:
            sym = c["symbol"]; k = self._key(venue, sym); cur = c.get("cur") or c.get("new") or {}; st = self.state.get(k)
            base = dict(base_asset=cur.get("base_asset") if isinstance(cur, dict) else None, quote_asset=cur.get("quote_asset") if isinstance(cur, dict) else None,
                        market_type=cur.get("market_type") if isinstance(cur, dict) else None, exchange_ts=ex_ts, raw=c)
            if c["type"] == "symbol_added":
                new = c["new"] or {}
                self.state[k] = {"first_seen_at": now_local(), "status": new.get("status"), "onboard_date": new.get("onboard_date"), "market_type": new.get("market_type"),
                                 "base_asset": new.get("base_asset"), "quote_asset": new.get("quote_asset"), "margin_trading_allowed": new.get("margin_trading_allowed"),
                                 "history": [{"at": now_local(), "status": new.get("status"), "baseline": c.get("baseline", False)}], "died_at": None}
                if c.get("baseline"):
                    continue
                ev = make_lifecycle_event(venue, sym, "born", old_status=None, new_status=new.get("status"), onboard_date=iso_ms(new.get("onboard_date")) if isinstance(new.get("onboard_date"), (int, float)) else new.get("onboard_date"),
                                          first_seen_at=self.state[k]["first_seen_at"], base_asset=new.get("base_asset"), quote_asset=new.get("quote_asset"), market_type=new.get("market_type"), exchange_ts=ex_ts, raw=c,
                                          detail={"contract_type": new.get("contract_type"), "filters": new.get("filters"), "permissions": new.get("permissions")})
                events.append(ev)
                if new.get("market_type") == "perp":
                    triggers.append({"trigger_type": "new_perp_listing", "venue": venue, "symbol": sym, "t0": ev["onboard_date"], "reason": f"symbol added with status {new.get('status')}", "anticipated": (new.get("status") == "PENDING_TRADING")})
                elif new.get("market_type") == "spot":
                    triggers.append({"trigger_type": "status_change", "venue": venue, "symbol": sym, "t0": None, "reason": "spot symbol added", "anticipated": False})
                continue
            if st is None:
                st = self.state[k] = {"first_seen_at": now_local(), "status": None, "onboard_date": None, "market_type": base["market_type"], "base_asset": base["base_asset"], "quote_asset": base["quote_asset"], "margin_trading_allowed": None, "history": [], "died_at": None}
            if c["type"] == "symbol_removed":
                old = c["old"] or {}
                st["died_at"] = now_local(); st["history"].append({"at": now_local(), "status": "REMOVED"})
                events.append(make_lifecycle_event(venue, sym, "died", old_status=old.get("status"), new_status="REMOVED", first_seen_at=st["first_seen_at"], onboard_date=st.get("onboard_date"),
                                                   base_asset=old.get("base_asset"), quote_asset=old.get("quote_asset"), market_type=old.get("market_type"), exchange_ts=ex_ts, raw=c))
                triggers.append({"trigger_type": "delisting_detected", "venue": venue, "symbol": sym, "t0": None, "reason": "symbol removed from exchangeInfo", "anticipated": False})
            elif c["type"] == "status_changed":
                old, new = c["old"], c["new"]; st["status"] = new; st["history"].append({"at": now_local(), "status": new})
                events.append(make_lifecycle_event(venue, sym, "status_change", old_status=old, new_status=new, first_seen_at=st["first_seen_at"], onboard_date=st.get("onboard_date"), **base))
                if new in DEAD_STATUSES:
                    st["died_at"] = now_local(); triggers.append({"trigger_type": "delisting_detected", "venue": venue, "symbol": sym, "t0": None, "reason": f"status {old} -> {new}", "anticipated": False})
                elif new in LIVE_STATUSES and old not in LIVE_STATUSES and st.get("market_type") == "perp":
                    triggers.append({"trigger_type": "new_perp_listing", "venue": venue, "symbol": sym, "t0": now_local(), "reason": f"status {old} -> {new} (market is now tradable)", "anticipated": False})
                else:
                    triggers.append({"trigger_type": "status_change", "venue": venue, "symbol": sym, "t0": None, "reason": f"status {old} -> {new}", "anticipated": False})
            elif c["type"] == "onboard_date_changed":
                old, new = c["old"], c["new"]; kind = "onboard_date_set" if old is None else "onboard_date_change"; st["onboard_date"] = new
                events.append(make_lifecycle_event(venue, sym, kind, old_status=st.get("status"), new_status=st.get("status"), first_seen_at=st["first_seen_at"],
                                                   onboard_date=iso_ms(new) if isinstance(new, (int, float)) else new, **base, detail={"old_onboard_date": old}))
                if st.get("market_type") == "perp":
                    triggers.append({"trigger_type": "onboard_date_change", "venue": venue, "symbol": sym, "t0": iso_ms(new) if isinstance(new, (int, float)) else new, "reason": f"onboardDate {old} -> {new}", "anticipated": True})
            elif c["type"] == "margin_trading_changed":
                old, new = c["old"], c["new"]; st["margin_trading_allowed"] = new
                events.append(make_lifecycle_event(venue, sym, "shortability_change", old_status=str(old), new_status=str(new), first_seen_at=st["first_seen_at"], onboard_date=st.get("onboard_date"), **base, detail={"field": "isMarginTradingAllowed"}))
            elif c["type"] in ("filters_changed", "permissions_changed", "contract_type_changed", "margin_asset_changed", "delivery_date_changed", "liquidation_fee_changed", "maint_margin_changed"):
                events.append(make_lifecycle_event(venue, sym, "filters_change", old_status=st.get("status"), new_status=st.get("status"), first_seen_at=st["first_seen_at"], onboard_date=st.get("onboard_date"), **base, detail={"change": c["type"], "old": c["old"], "new": c["new"]}))
        return events, triggers

    def summary(self) -> Dict[str, Any]:
        n = len(self.state); dead = sum(1 for s in self.state.values() if s.get("died_at")); perps = sum(1 for s in self.state.values() if s.get("market_type") == "perp")
        return {"symbols_tracked": n, "perps": perps, "dead": dead, "state_path": str(self.state_path)}
