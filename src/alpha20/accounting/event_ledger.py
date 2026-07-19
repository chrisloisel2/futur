"""
src/alpha20/accounting/event_ledger.py — ledger APPEND-ONLY d'ALPHA_20.

UN SEUL fichier JSONL (data/alpha20/ledger/ledger.jsonl), une ligne = un
LedgerEvent + hash chaîné (sha256 de l'événement + hash précédent) : toute
réécriture casse la chaîne. La chaîne suit l'ORDRE D'APPEND — un événement
peut porter un ts antérieur (fait passé audité) sans casser la vérification
(leçon du 2026-07-19 : le partitionnement par date de ts mélangeait ordre
d'écriture et ordre de lecture). Idempotence par event_id déterministe
(sha256 des champs métier) — rejouer un append est un no-op.

C'est la SEULE source de vérité comptable d'alpha20 ; les agrégats (NAV,
R_net) se recalculent depuis ici (net_nav.py), jamais depuis Mongo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from src.alpha20.contracts import LedgerEvent

ROOT = Path(__file__).resolve().parents[3]
LEDGER_DIR = ROOT / "data" / "alpha20" / "ledger"
GENESIS = "0" * 64


def _ledger_file() -> Path:
    return LEDGER_DIR / "ledger.jsonl"


def _event_id(ev: LedgerEvent) -> str:
    body = json.dumps([ev.ts, ev.kind, ev.sleeve, ev.venue,
                       round(float(ev.amount_usdt), 10), ev.ref],
                      sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def _rows() -> List[dict]:
    f = _ledger_file()
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def append(events: Iterable[LedgerEvent]) -> List[str]:
    """Ajoute les événements nouveaux (idempotent), retourne leurs event_id."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    rows = _rows()
    known = {r["event_id"] for r in rows}
    chain = rows[-1]["chain"] if rows else GENESIS
    written = []
    with open(_ledger_file(), "a") as f:
        for ev in events:
            ev.validate()
            eid = _event_id(ev)
            if eid in known:
                continue
            row = dict(asdict(ev), event_id=eid)
            chain = hashlib.sha256(
                (chain + json.dumps(row, sort_keys=True, default=str)).encode()
            ).hexdigest()
            row["chain"] = chain
            f.write(json.dumps(row, default=str) + "\n")
            known.add(eid)
            written.append(eid)
    return written


def verify_chain() -> bool:
    """Revalide toute la chaîne de hash — False = ledger altéré."""
    chain = GENESIS
    for row in _rows():
        stored = row.pop("chain")
        chain = hashlib.sha256(
            (chain + json.dumps(row, sort_keys=True, default=str)).encode()
        ).hexdigest()
        if chain != stored:
            return False
    return True


def read(kinds: Optional[List[str]] = None,
         since: Optional[str] = None) -> pd.DataFrame:
    df = pd.DataFrame(_rows())
    if df.empty:
        return df
    if kinds is not None:
        df = df[df["kind"].isin(kinds)]
    if since is not None:
        df = df[df["ts"] >= since]
    return df.reset_index(drop=True)
