#!/usr/bin/env python3
"""
market_state_schema.py -- schemas et ecriture append-only du market_state_tape (P4).

Trois enregistrements obligatoires :
  symbol_lifecycle_event    naissance / mort / changement de statut d'un marche
  market_state_snapshot     etat du marche a un instant (carnet, mark, index, funding, OI, volume, latence)
  triggered_window_manifest ce qui a ete capture autour d'un evenement, avec hashes et completude

Regles : tout output est append-only ; chaque enregistrement porte ts local, ts exchange (si dispo),
source et raw_hash ; aucun signal, aucun verdict, aucune jointure a un resultat alpha.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
TAPE_ROOT = ROOT / "data_lake" / "market_state"

SYMBOL_LIFECYCLE_EVENT = ["venue", "symbol", "base_asset", "quote_asset", "market_type", "old_status", "new_status",
                          "detected_at_local", "exchange_ts", "onboard_date", "first_seen_at", "raw_hash", "source"]
MARKET_STATE_SNAPSHOT = ["venue", "symbol", "ts_exchange", "ts_local", "source", "bid", "ask", "mid", "spread_bps",
                         "depth_10bps_bid_usd", "depth_10bps_ask_usd", "depth_25bps_bid_usd", "depth_25bps_ask_usd",
                         "depth_50bps_bid_usd", "depth_50bps_ask_usd", "imbalance_10bps", "mark_price", "index_price",
                         "funding_rate", "open_interest", "volume_1m", "trade_count_1m", "latency_ms", "raw_hash"]
TRIGGERED_WINDOW_MANIFEST = ["trigger_id", "trigger_type", "venue", "symbol", "start_ts", "end_ts", "reason", "prereg_link",
                             "files_written", "row_counts", "sha256", "completeness_score", "missing_fields", "no_alpha_test"]
SCHEMAS = {"symbol_lifecycle_event": SYMBOL_LIFECYCLE_EVENT, "market_state_snapshot": MARKET_STATE_SNAPSHOT,
           "triggered_window_manifest": TRIGGERED_WINDOW_MANIFEST}
TRIGGER_TYPES = ("new_perp_listing", "status_change", "onboard_date_change", "delisting_detected", "oi_spike",
                 "funding_extreme", "liquidation_burst", "manual")
LIFECYCLE_KINDS = ("born", "status_change", "onboard_date_set", "onboard_date_change", "filters_change",
                   "shortability_change", "died")


class SchemaError(ValueError):
    pass


def now_local() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def now_ns() -> int:
    return time.time_ns()


def iso_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def raw_hash(payload: Any) -> str:
    """sha256 canonique du payload brut (dict / str / bytes)."""
    if isinstance(payload, bytes):
        b = payload
    elif isinstance(payload, str):
        b = payload.encode("utf-8")
    else:
        b = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def validate(kind: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """Toutes les cles du schema doivent etre presentes (None autorise pour une valeur inconnue,
    jamais pour venue / symbol / source / raw_hash / ts local). Les cles supplementaires sont tolerees."""
    if kind not in SCHEMAS:
        raise SchemaError("unknown record kind %r" % kind)
    missing = [k for k in SCHEMAS[kind] if k not in rec]
    if missing:
        raise SchemaError("%s: missing fields %s" % (kind, missing))
    for k in ("venue", "symbol") if kind != "triggered_window_manifest" else ("venue", "symbol", "trigger_id", "trigger_type"):
        if not rec.get(k):
            raise SchemaError("%s: %s must be set" % (kind, k))
    if kind == "market_state_snapshot" and not rec.get("ts_local"):
        raise SchemaError("market_state_snapshot: ts_local must be set")
    if kind == "symbol_lifecycle_event" and not rec.get("detected_at_local"):
        raise SchemaError("symbol_lifecycle_event: detected_at_local must be set")
    if kind == "triggered_window_manifest":
        if rec.get("no_alpha_test") is not True:
            raise SchemaError("triggered_window_manifest: no_alpha_test must be true")
        if rec["trigger_type"] not in TRIGGER_TYPES:
            raise SchemaError("unknown trigger_type %r" % rec["trigger_type"])
    if not rec.get("raw_hash") and kind != "triggered_window_manifest":
        raise SchemaError("%s: raw_hash must be set" % kind)
    rec.setdefault("schema_version", SCHEMA_VERSION)
    return rec


def make_lifecycle_event(venue: str, symbol: str, kind: str, *, base_asset=None, quote_asset=None, market_type=None,
                         old_status=None, new_status=None, exchange_ts=None, onboard_date=None, first_seen_at=None,
                         raw: Any = None, source: str = "exchange_info_diff", detail: Optional[dict] = None) -> Dict[str, Any]:
    if kind not in LIFECYCLE_KINDS:
        raise SchemaError("unknown lifecycle kind %r" % kind)
    rec = {"venue": venue, "symbol": symbol, "base_asset": base_asset, "quote_asset": quote_asset, "market_type": market_type,
           "old_status": old_status, "new_status": new_status, "detected_at_local": now_local(), "exchange_ts": exchange_ts,
           "onboard_date": onboard_date, "first_seen_at": first_seen_at, "raw_hash": raw_hash(raw if raw is not None else {"venue": venue, "symbol": symbol, "kind": kind, "new_status": new_status}),
           "source": source, "event_kind": kind, "detail": detail or {}}
    return validate("symbol_lifecycle_event", rec)


def make_snapshot(venue: str, symbol: str, source: str, *, ts_exchange=None, bid=None, ask=None, depth=None, mark_price=None,
                  index_price=None, funding_rate=None, open_interest=None, volume_1m=None, trade_count_1m=None,
                  latency_ms=None, raw: Any = None, extra: Optional[dict] = None) -> Dict[str, Any]:
    depth = depth or {}
    mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None
    rec = {"venue": venue, "symbol": symbol, "ts_exchange": ts_exchange, "ts_local": now_local(), "source": source,
           "bid": bid, "ask": ask, "mid": mid, "spread_bps": ((ask - bid) / mid * 1e4) if (mid and bid is not None and ask is not None and mid > 0) else None,
           "depth_10bps_bid_usd": depth.get("10_bid"), "depth_10bps_ask_usd": depth.get("10_ask"),
           "depth_25bps_bid_usd": depth.get("25_bid"), "depth_25bps_ask_usd": depth.get("25_ask"),
           "depth_50bps_bid_usd": depth.get("50_bid"), "depth_50bps_ask_usd": depth.get("50_ask"),
           "imbalance_10bps": depth.get("imbalance_10"), "mark_price": mark_price, "index_price": index_price,
           "funding_rate": funding_rate, "open_interest": open_interest, "volume_1m": volume_1m, "trade_count_1m": trade_count_1m,
           "latency_ms": latency_ms, "raw_hash": raw_hash(raw if raw is not None else {"venue": venue, "symbol": symbol, "bid": bid, "ask": ask, "ts": ts_exchange})}
    if extra:
        rec.update(extra)
    return validate("market_state_snapshot", rec)


def make_manifest(trigger_id: str, trigger_type: str, venue: str, symbol: str, start_ts: str, end_ts: Optional[str], reason: str,
                  files_written: List[str], row_counts: Dict[str, int], sha256: Dict[str, str], completeness_score: float,
                  missing_fields: List[str], prereg_link: Optional[str] = None, extra: Optional[dict] = None) -> Dict[str, Any]:
    rec = {"trigger_id": trigger_id, "trigger_type": trigger_type, "venue": venue, "symbol": symbol, "start_ts": start_ts, "end_ts": end_ts,
           "reason": reason, "prereg_link": prereg_link, "files_written": files_written, "row_counts": row_counts, "sha256": sha256,
           "completeness_score": completeness_score, "missing_fields": missing_fields, "no_alpha_test": True, "written_at_local": now_local()}
    if extra:
        rec.update(extra)
    return validate("triggered_window_manifest", rec)


def completeness(rows: Iterable[Dict[str, Any]], fields: List[str]) -> Dict[str, Any]:
    """Part des champs renseignes (non None) par champ, score moyen, champs jamais renseignes."""
    rows = list(rows); n = len(rows)
    if n == 0:
        return {"score": 0.0, "per_field": {f: 0.0 for f in fields}, "missing_fields": list(fields), "n_rows": 0}
    per = {f: sum(1 for r in rows if r.get(f) is not None) / n for f in fields}
    return {"score": round(sum(per.values()) / len(fields), 4), "per_field": per, "missing_fields": [f for f, v in per.items() if v == 0.0], "n_rows": n}


class AppendOnlyJsonl:
    """Ecriture append-only, un fichier par (kind, venue, jour), gzip optionnel (membres concatenes :
    lisibles par data_lake.collectors.tape_io.decompress_partial meme sans trailer)."""

    def __init__(self, root: Path = TAPE_ROOT, gz: bool = False):
        self.root = Path(root); self.gz = gz; self._fh: Dict[Path, Any] = {}; self.counts: Dict[str, int] = {}

    def path_for(self, kind: str, venue: str, day: Optional[str] = None, name: Optional[str] = None) -> Path:
        day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.root / kind / f"venue={venue}" / f"date={day}" / f"{name or kind}.jsonl{'.gz' if self.gz else ''}"

    def write(self, kind: str, rec: Dict[str, Any], venue: Optional[str] = None, name: Optional[str] = None) -> Path:
        venue = venue or rec.get("venue") or "unknown"
        p = self.path_for(kind, venue, name=name)
        fh = self._fh.get(p)
        if fh is None:
            p.parent.mkdir(parents=True, exist_ok=True)
            fh = gzip.open(p, "ab") if self.gz else open(p, "ab")
            self._fh[p] = fh
        fh.write((json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8"))
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return p

    def flush(self):
        for fh in self._fh.values():
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except (OSError, ValueError, AttributeError):
                pass

    def close(self):
        self.flush()
        for fh in self._fh.values():
            fh.close()
        self._fh.clear()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
