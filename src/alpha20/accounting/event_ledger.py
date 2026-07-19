"""
src/alpha20/accounting/event_ledger.py — ledger APPEND-ONLY d'ALPHA_20.

Un fichier JSONL par jour (data/alpha20/ledger/YYYY-MM-DD.jsonl), une ligne =
un LedgerEvent + numéro de séquence + hash chaîné (sha256 de l'événement +
hash précédent) : toute réécriture casse la chaîne. Idempotence par event_id
déterministe (sha256 des champs métier) — rejouer un append est un no-op.

C'est la SEULE source de vérité comptable d'alpha20 ; les agrégats (NAV, R_net)
se recalculent depuis ici (net_nav.py), jamais depuis Mongo.
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


def _event_id(ev: LedgerEvent) -> str:
    body = json.dumps([ev.ts, ev.kind, ev.sleeve, ev.venue,
                       round(float(ev.amount_usdt), 10), ev.ref],
                      sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def _day_file(ts: str) -> Path:
    return LEDGER_DIR / f"{ts[:10]}.jsonl"


def _last_chain_hash() -> str:
    files = sorted(LEDGER_DIR.glob("*.jsonl"))
    if not files:
        return GENESIS
    last = files[-1].read_text().strip().splitlines()
    return json.loads(last[-1])["chain"] if last else GENESIS


def _known_ids() -> set:
    ids = set()
    for f in sorted(LEDGER_DIR.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                ids.add(json.loads(line)["event_id"])
    return ids


def append(events: Iterable[LedgerEvent]) -> List[str]:
    """Ajoute les événements nouveaux (idempotent), retourne leurs event_id."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    known = _known_ids()
    chain = _last_chain_hash()
    written = []
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
        with open(_day_file(ev.ts), "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        known.add(eid)
        written.append(eid)
    return written


def verify_chain() -> bool:
    """Revalide toute la chaîne de hash — False = ledger altéré."""
    chain = GENESIS
    for f in sorted(LEDGER_DIR.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            stored = row.pop("chain")
            chain = hashlib.sha256(
                (chain + json.dumps(row, sort_keys=True, default=str)).encode()
            ).hexdigest()
            if chain != stored:
                return False
    return True


def read(kinds: Optional[List[str]] = None,
         since: Optional[str] = None) -> pd.DataFrame:
    rows = []
    for f in sorted(LEDGER_DIR.glob("*.jsonl")):
        if since and f.stem < since[:10]:
            continue
        for line in f.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if kinds is not None:
        df = df[df["kind"].isin(kinds)]
    if since is not None:
        df = df[df["ts"] >= since]
    return df.reset_index(drop=True)
