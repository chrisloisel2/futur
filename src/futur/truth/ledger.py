"""src/futur/truth/ledger.py -- append-only event ledger with a hash chain.

The Ledger is the primary source of truth: it has no delete or update
method, only `append()`. `sequence` is always assigned by the ledger
itself (monotonically, from the order events are appended), never trusted
from the caller -- so replaying the same events in the same order always
produces the same sequence assignment regardless of what was in the
original event objects.

Each entry's `cumulative_hash` chains to the previous entry's hash, so any
change to any past event (content or order) changes every hash after it --
a cheap, load-bearing tamper/drift check, and the mechanism "two replays
produce the same final hash" (commit 7) relies on.

Corrections are a POLICY, not a ledger feature: nothing here supports
editing or removing a past event. Fixing a mistake means appending a new,
compensating event (e.g. a CASH_WITHDRAWAL to undo a wrongly-sized
CASH_DEPOSIT) -- enforced by this class simply not exposing any other way.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum

from src.futur.truth.events import Event

GENESIS_HASH = "0" * 64


class DuplicateEventError(Exception):
    pass


def _json_default(obj: object) -> object:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        # str(), never float() -- a Decimal's string form preserves its
        # exact digits and scale (str(Decimal("50000.00000000")) is
        # exactly "50000.00000000"), so two processes hashing the same
        # Decimal always get the same bytes. Converting to float first
        # would reintroduce the binary-rounding problem this whole engine
        # moved to Decimal specifically to avoid.
        return str(obj)
    raise TypeError(f"not JSON-serializable in a ledger entry: {type(obj)!r}")


def _canonical_json(event: Event) -> str:
    """Deterministic serialization: sorted keys, explicit Enum->value, no
    locale/platform-dependent formatting. Two processes hashing the same
    Event must always get the same bytes."""
    return json.dumps(asdict(event), default=_json_default, sort_keys=True)


def hash_entry(prev_hash: str, event: Event) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical_json(event).encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    event: Event
    cumulative_hash: str


class Ledger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._seen_event_ids: set[str] = set()
        self._next_sequence = 0

    def append(self, event: Event) -> Event:
        """Stamps `event` with the next sequence number, chains its hash to
        the current head, and appends. Returns the stamped Event (the
        caller's original `event` is untouched -- Event is frozen)."""
        if event.event_id in self._seen_event_ids:
            raise DuplicateEventError(f"duplicate event_id: {event.event_id!r}")
        stamped = event.with_sequence(self._next_sequence)
        cum_hash = hash_entry(self.head_hash, stamped)
        self._entries.append(LedgerEntry(event=stamped, cumulative_hash=cum_hash))
        self._seen_event_ids.add(event.event_id)
        self._next_sequence += 1
        return stamped

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """Read-only view -- a tuple copy, not a reference to internal
        state, so a caller can't mutate the ledger by mutating what this
        returns."""
        return tuple(self._entries)

    def events(self) -> list[Event]:
        return [e.event for e in self._entries]

    @property
    def head_hash(self) -> str:
        return self._entries[-1].cumulative_hash if self._entries else GENESIS_HASH

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.entries)
