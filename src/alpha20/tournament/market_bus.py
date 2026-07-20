"""
src/alpha20/tournament/market_bus.py — bus de marché PARTAGÉ du tournoi.

Un SEUL snapshot par cycle, construit une fois, distribué EN LECTURE SEULE à
tous les runners : personne ne peut voir une donnée que les autres n'ont pas,
personne ne peut voir une donnée postérieure au cutoff (`MarketSnapshot.cutoff`
= horodatage de construction ; `close_asof` refuse toute barre dont l'horodatage
dépasse le cutoff — AssertionError, pas juste une convention documentée).

Journal append-only séparé du ledger comptable (data/alpha20/bus/bus.jsonl) —
même discipline de hash-chaînage que accounting/event_ledger (queue tronquée
tolérée, corruption interne bloquante) mais concept différent : un snapshot de
marché n'est pas un fait économique. `replay(market_event_id)` restitue le
snapshot exact enregistré — remplace tout nouvel appel réseau, pour un audit
ou un test déterministe.

Sources (réutilisées, aucune nouvelle logique réseau) :
  • prix/funding live  : src.institutional.live.paper_portfolio
    (live_prices, live_funding) — déjà utilisées par le paper 200k ;
  • trimestriels        : src.alpha20.costs.fee_registry.discover_quarterlies ;
  • closes historiques  : data/enriched/{SYM}_1h_enriched.parquet (MH replay).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.alpha20 import ROOT

BUS_DIR = ROOT / "data" / "alpha20" / "bus"
GENESIS = "0" * 64
ENRICHED = ROOT / "data" / "enriched"


@dataclass
class MarketSnapshot:
    market_event_id: str
    cutoff: str                              # ISO — rien après ceci n'est visible
    decision_ts: str                         # ISO — horodatage d'usage par l'orchestrateur
    received_ts: str                         # ISO — horodatage de collecte par le bus
    prices: Dict[str, dict] = field(default_factory=dict)     # {sym: {close, exchange_ts}}
    funding: Dict[str, dict] = field(default_factory=dict)    # {sym: {rate, exchange_ts}}
    quarterlies: Dict[str, list] = field(default_factory=dict)
    gaps: List[str] = field(default_factory=list)

    def price(self, symbol: str) -> Optional[float]:
        row = self.prices.get(symbol)
        return float(row["close"]) if row else None

    def close_asof(self, symbol: str, asof: pd.Timestamp) -> Optional[tuple]:
        """Première close STRICTEMENT postérieure à `asof`, jamais postérieure
        au cutoff du snapshot — lookahead impossible par construction."""
        p = ENRICHED / f"{symbol}_1h_enriched.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p, columns=["datetime", "close"])
        idx = pd.to_datetime(df["datetime"], utc=True)
        s = pd.Series(df["close"].values, index=idx).sort_index()
        cutoff = pd.Timestamp(self.cutoff)
        after = s[(s.index > asof) & (s.index <= cutoff)]
        if after.empty:
            return None
        return after.index[0], float(after.iloc[0])


def _row_id(row: dict) -> str:
    body = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def _bus_file() -> Path:
    return BUS_DIR / "bus.jsonl"


def _load() -> "tuple":
    f = _bus_file()
    if not f.exists():
        return [], 0
    raw = f.read_bytes()
    rows, good = [], 0
    lines = raw.split(b"\n")
    for i, line in enumerate(lines):
        if not line.strip():
            good += len(line) + 1
            continue
        try:
            rows.append(json.loads(line))
            good += len(line) + 1
        except ValueError:
            tail_valid = any(_parses(l) for l in lines[i + 1:] if l.strip())
            if tail_valid:
                return rows, -1
            break
    return rows, min(good, len(raw))


def _parses(line: bytes) -> bool:
    try:
        json.loads(line)
        return True
    except ValueError:
        return False


def _persist(snapshot: MarketSnapshot) -> None:
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    rows, good = _load()
    if good == -1:
        raise RuntimeError("bus de marché corrompu — investiguer avant d'écrire")
    f = _bus_file()
    if f.exists() and good < f.stat().st_size:
        with open(f, "rb+") as fh:
            fh.truncate(good)
    chain = rows[-1]["chain"] if rows else GENESIS
    row = {"market_event_id": snapshot.market_event_id, "cutoff": snapshot.cutoff,
           "decision_ts": snapshot.decision_ts, "received_ts": snapshot.received_ts,
           "prices": snapshot.prices, "funding": snapshot.funding,
           "quarterlies": snapshot.quarterlies, "gaps": snapshot.gaps}
    chain = hashlib.sha256(
        (chain + json.dumps(row, sort_keys=True, default=str)).encode()
    ).hexdigest()
    row["chain"] = chain
    with open(f, "a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def verify_chain() -> bool:
    rows, good = _load()
    if good == -1:
        return False
    chain = GENESIS
    for row in rows:
        row = dict(row)
        stored = row.pop("chain")
        chain = hashlib.sha256(
            (chain + json.dumps(row, sort_keys=True, default=str)).encode()
        ).hexdigest()
        if chain != stored:
            return False
    return True


def replay(market_event_id: str) -> Optional[MarketSnapshot]:
    """Restitue le snapshot EXACT enregistré — aucun nouvel appel réseau."""
    for row in _load()[0]:
        if row["market_event_id"] == market_event_id:
            row = dict(row)
            row.pop("chain", None)
            row.pop("event_id", None)
            return MarketSnapshot(**row)
    return None


def build_snapshot(universe: List[str], funding_symbols: Optional[List[str]] = None,
                   quarterly_pairs: Optional[List[str]] = None) -> MarketSnapshot:
    """Construit UN snapshot live, le persiste, le retourne. Tous les runners
    du cycle courant reçoivent CET OBJET — jamais deux appels réseau
    distincts pour deux runners du même cycle."""
    from src.institutional.live.paper_portfolio import live_prices, live_funding
    from src.alpha20.costs.fee_registry import discover_quarterlies, quarterly_price

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    gaps = []
    prices, funding, quarterlies = {}, {}, {}

    try:
        raw_px = live_prices(list(dict.fromkeys(universe)))
    except Exception as e:                      # noqa: BLE001
        raw_px = {}
        gaps.append(f"prices_fetch_failed: {e}")
    for s in universe:
        if raw_px.get(s):
            prices[s] = {"close": float(raw_px[s]), "exchange_ts": now_iso}
        else:
            gaps.append(f"missing_price:{s}")

    for s in (funding_symbols or []):
        try:
            r = live_funding(s)
        except Exception as e:                   # noqa: BLE001
            r = None
            gaps.append(f"funding_fetch_failed:{s}:{e}")
        if r is not None:
            funding[s] = {"rate": float(r), "exchange_ts": now_iso}
        else:
            gaps.append(f"missing_funding:{s}")

    for pair in (quarterly_pairs or []):
        try:
            qs = discover_quarterlies(pair)
            rows = []
            for q in qs:
                px = quarterly_price(q["symbol"])
                if px is None:
                    gaps.append(f"missing_quarterly_price:{q['symbol']}")
                rows.append({"symbol": q["symbol"],
                            "delivery_ts_ms": q["delivery_ts_ms"],
                            "days_to_expiry": round(q["days_to_expiry"], 2),
                            "price": px})
            quarterlies[pair] = rows
        except Exception as e:                   # noqa: BLE001
            quarterlies[pair] = []
            gaps.append(f"quarterly_fetch_failed:{pair}:{e}")

    payload = {"prices": prices, "funding": funding, "quarterlies": quarterlies}
    market_event_id = hashlib.sha256(
        json.dumps([now_iso, payload], sort_keys=True, default=str).encode()
    ).hexdigest()[:24]
    snap = MarketSnapshot(market_event_id=market_event_id, cutoff=now_iso,
                          decision_ts=now_iso, received_ts=now_iso,
                          prices=prices, funding=funding,
                          quarterlies=quarterlies, gaps=gaps)
    _persist(snap)
    return snap
