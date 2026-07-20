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


def _load() -> "tuple":
    """(rows valides, octets valides). Une DERNIÈRE ligne incomplète (écriture
    interrompue par un crash) est ignorée : l'événement n'était pas commité.
    Une ligne malformée AU MILIEU n'est pas réparable → chaîne invalide."""
    f = _ledger_file()
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
            # ligne illisible : queue tronquée (non commise) si RIEN de valide
            # ne suit ; sinon corruption au milieu → ledger invalide
            tail_valid = any(_parses(l) for l in lines[i + 1:] if l.strip())
            if tail_valid:
                return rows, -1          # -1 = corruption interne
            break
    return rows, min(good, len(raw))


def _parses(line: bytes) -> bool:
    try:
        json.loads(line)
        return True
    except ValueError:
        return False


def _rows() -> List[dict]:
    return _load()[0]


def append(events: Iterable[LedgerEvent]) -> List[str]:
    """Ajoute les événements nouveaux (idempotent), retourne leurs event_id.
    Répare une queue d'écriture interrompue avant d'appendre."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    rows, good = _load()
    if good == -1:
        raise RuntimeError("ledger corrompu (ligne interne illisible) — "
                           "aucune écriture autorisée, investiguer")
    f = _ledger_file()
    if f.exists() and good < f.stat().st_size:
        with open(f, "rb+") as fh:      # tronque la ligne partielle non commise
            fh.truncate(good)
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
    """Revalide toute la chaîne de hash — False = ledger altéré (réécriture,
    corruption interne). Une queue tronquée non commise n'invalide pas."""
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


def integrity() -> dict:
    """Exactement UN événement comptable par fait économique : deux événements
    fee/funding partageant (ts, kind, sleeve, ref) = double comptage (les
    montants identiques sont déjà dédupliqués par event_id)."""
    df = read(kinds=["fee", "funding"])
    dups = []
    if len(df):
        g = df.groupby(["ts", "kind", "sleeve", "ref"]).size()
        dups = [" ".join(map(str, k)) for k, n in g.items() if n > 1]
    return {"chain_ok": verify_chain(), "duplicate_facts": dups,
            "one_event_per_fact": not dups}


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
