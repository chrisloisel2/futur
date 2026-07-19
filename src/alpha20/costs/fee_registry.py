"""
src/alpha20/costs/fee_registry.py — coûts RÉELS par compte/instrument (étape 2).

Aucune hypothèse codée en dur : `effective_costs()` sert le snapshot daté le
plus récent (data/alpha20/cost_snapshots/<venue>_<instrument>.json) et ne
retombe sur les défauts "assumed" de configs/alpha20.yaml qu'à défaut — la
sortie porte TOUJOURS `source` et `as_of`, un consommateur peut donc refuser
un coût assumed.

Snapshots réels :
  • Binance USD-M : GET /fapi/v1/commissionRate (SIGNÉ — clés via env
    BINANCE_API_KEY/BINANCE_API_SECRET ; varie avec VIP et promotions) ;
  • découverte DYNAMIQUE des contrats trimestriels via /fapi/v1/exchangeInfo
    (contractType CURRENT_QUARTER/NEXT_QUARTER + deliveryDate) — remplace
    toute liste d'échéances figée.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.alpha20 import ROOT, load_config
from src.alpha20.contracts import CostSnapshot

SNAP_DIR = ROOT / "data" / "alpha20" / "cost_snapshots"
FAPI = "https://fapi.binance.com"
_EXCHANGE_INFO_CACHE = {"ts": 0.0, "data": None}
EXCHANGE_INFO_TTL_S = 6 * 3600


def _get(url: str, headers: Dict = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


# ── snapshots ────────────────────────────────────────────────────────────────
def _snap_path(venue: str, instrument: str) -> Path:
    return SNAP_DIR / f"{venue}_{instrument}.json"


def save_snapshot(snap: CostSnapshot) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = _snap_path(snap.venue, snap.instrument)
    hist = json.loads(p.read_text()) if p.exists() else []
    hist.append(snap.__dict__)
    p.write_text(json.dumps(hist, indent=1, default=str))
    return p


def effective_costs(venue: str, instrument: str) -> CostSnapshot:
    """Snapshot réel le plus récent, sinon défaut assumed (étiqueté)."""
    p = _snap_path(venue, instrument)
    if p.exists():
        hist = json.loads(p.read_text())
        if hist:
            last = sorted(hist, key=lambda s: s["as_of"])[-1]
            return CostSnapshot(**{k: v for k, v in last.items()
                                   if k in CostSnapshot.__dataclass_fields__})
    d = load_config()["costs"]["assumed_defaults"]
    key = venue if venue in d else "binance_usdm"
    base = d[key]
    return CostSnapshot(venue=venue, instrument=instrument,
                        maker_bp=float(base["maker_bp"]),
                        taker_bp=float(base["taker_bp"]),
                        as_of=str(base["as_of"]), source="assumed",
                        borrow_ann=float(d["borrow_ann"]["rate"]),
                        slippage_bp=float(d["slippage_bp_default"]))


# ── commission réelle Binance (signée) ───────────────────────────────────────
def fetch_binance_commission(symbol: str) -> Optional[CostSnapshot]:
    """commissionRate du COMPTE (VIP/promotions). None si pas de clés API."""
    key = os.environ.get("BINANCE_API_KEY")
    sec = os.environ.get("BINANCE_API_SECRET")
    if not key or not sec:
        return None
    qs = urllib.parse.urlencode(
        {"symbol": symbol, "timestamp": int(time.time() * 1000)})
    sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    data = _get(f"{FAPI}/fapi/v1/commissionRate?{qs}&signature={sig}",
                headers={"X-MBX-APIKEY": key})
    snap = CostSnapshot(
        venue="binance_usdm", instrument=symbol,
        maker_bp=float(data["makerCommissionRate"]) * 1e4,
        taker_bp=float(data["takerCommissionRate"]) * 1e4,
        as_of=datetime.now(timezone.utc).date().isoformat(),
        source="api_signed")
    save_snapshot(snap)
    return snap


# ── découverte dynamique des trimestriels ────────────────────────────────────
def discover_quarterlies(underlying: str = None) -> List[Dict]:
    """Contrats livrables actifs depuis exchangeInfo (cache 6 h).
    Retourne [{symbol, pair, contract_type, delivery_ts_ms, days_to_expiry}]."""
    now = time.time()
    if (_EXCHANGE_INFO_CACHE["data"] is None
            or now - _EXCHANGE_INFO_CACHE["ts"] > EXCHANGE_INFO_TTL_S):
        _EXCHANGE_INFO_CACHE["data"] = _get(f"{FAPI}/fapi/v1/exchangeInfo")
        _EXCHANGE_INFO_CACHE["ts"] = now
    return parse_quarterlies(_EXCHANGE_INFO_CACHE["data"], underlying,
                             now_ms=int(now * 1000))


def parse_quarterlies(exchange_info: dict, underlying: str = None,
                      now_ms: int = 0) -> List[Dict]:
    """Parseur pur (testé hors réseau)."""
    out = []
    for s in exchange_info.get("symbols", []):
        if s.get("contractType") not in ("CURRENT_QUARTER", "NEXT_QUARTER"):
            continue
        if s.get("status") != "TRADING":
            continue
        if underlying and s.get("pair") != underlying:
            continue
        dts = int(s.get("deliveryDate", 0))
        if dts <= now_ms:
            continue
        out.append({"symbol": s["symbol"], "pair": s.get("pair"),
                    "contract_type": s["contractType"],
                    "delivery_ts_ms": dts,
                    "days_to_expiry": (dts - now_ms) / 86_400_000.0})
    return sorted(out, key=lambda r: r["delivery_ts_ms"])
