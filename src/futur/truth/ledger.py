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
produce the same final hash" (commit 7) relies on. The chain's ROOT (the
genesis hash returned by `head_hash` before any append) is itself derived
from `engine_version` and `margin_config`, not a fixed constant -- so two
ledgers that replay identical events under a different engine version or a
different margin config provably produce different hash chains. A
ProductSpec's own fields (tick_size, lot_size, multiplier) don't need
separate genesis treatment: they're already part of every event that
references an instrument, and so already flow into that event's own hash
via `event_to_dict`.

Corrections are a POLICY, not a ledger feature: nothing here supports
editing or removing a past event. Fixing a mistake means appending a new,
compensating event (e.g. a CASH_WITHDRAWAL to undo a wrongly-sized
CASH_DEPOSIT) -- enforced by this class simply not exposing any other way.

Durability: pass `wal_path` to persist every appended entry to a
newline-delimited JSON file, fsync'd before the in-memory append completes
-- a crash between "wrote to disk" and "updated in-memory state" loses at
most an in-flight append, never a torn one, and a crash mid-write leaves an
incomplete last line that reload detects (see `_load_from_wal`) rather than
silently accepting. Re-opening the same `wal_path` replays and verifies the
whole file (every stored hash is recomputed and checked against the chain,
every sequence checked for exact monotonicity) before any new event can be
appended -- so a caller resuming after a restart is always working from a
provably intact history, and appending an event whose id already made it to
disk before the crash still raises DuplicateEventError, not a silent
double-apply.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.futur.truth.events import Event, event_from_dict, event_to_dict
from src.futur.truth.margin import MarginConfig

ENGINE_VERSION = "truth-engine/1"
GENESIS_HASH = "0" * 64


class DuplicateEventError(Exception):
    pass


class LedgerCorruptionError(Exception):
    """Raised on reload if the WAL file is truncated, malformed, or its
    hash chain doesn't verify -- this engine never silently drops or
    repairs a corrupted history, it stops and says so."""


def _canonical_json(event: Event) -> str:
    """The ONE serialization of an Event used for hashing -- events.py's
    `event_to_dict`, the same dict shape the durable WAL persists and the
    JSONL fixture format uses, so the bytes that get hashed are always
    exactly the bytes that get written and read back."""
    return json.dumps(event_to_dict(event), sort_keys=True)


def hash_entry(prev_hash: str, event: Event) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical_json(event).encode("utf-8"))
    return h.hexdigest()


def _canonical_context_json(engine_version: str, margin_config: MarginConfig) -> str:
    return json.dumps({
        "engine_version": engine_version,
        "margin_config": {
            "initial_margin_rate": str(margin_config.initial_margin_rate),
            "maintenance_margin_rate": str(margin_config.maintenance_margin_rate),
        },
    }, sort_keys=True)


def _compute_genesis_hash(engine_version: str, margin_config: MarginConfig) -> str:
    h = hashlib.sha256()
    h.update(GENESIS_HASH.encode("utf-8"))
    h.update(_canonical_context_json(engine_version, margin_config).encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    event: Event
    cumulative_hash: str


class Ledger:
    def __init__(self, *, margin_config: MarginConfig | None = None,
                engine_version: str = ENGINE_VERSION,
                wal_path: Path | str | None = None) -> None:
        self.engine_version = engine_version
        self.margin_config = margin_config or MarginConfig()
        self._genesis_hash = _compute_genesis_hash(self.engine_version, self.margin_config)
        self._entries: list[LedgerEntry] = []
        self._seen_event_ids: set[str] = set()
        self._next_sequence = 0
        resolved_wal_path = Path(wal_path) if wal_path is not None else None
        self._wal_path = resolved_wal_path
        self._wal_file = None
        if resolved_wal_path is not None:
            if resolved_wal_path.exists() and resolved_wal_path.stat().st_size > 0:
                self._load_from_wal(resolved_wal_path)
            self._wal_file = open(resolved_wal_path, "a", encoding="utf-8")   # noqa: SIM115

    def _load_from_wal(self, wal_path: Path) -> None:
        """Crash recovery: replays and verifies every line on disk before
        this ledger accepts a single new event. Any parse failure, missing
        field, sequence gap, duplicate id, or hash mismatch raises
        LedgerCorruptionError immediately -- never skipped, never
        silently truncated away."""
        running_hash = self._genesis_hash
        with open(wal_path, encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.rstrip("\n")
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerCorruptionError(
                        f"{wal_path}: line {line_no} is not valid JSON "
                        f"(truncated write or corruption): {exc}") from exc
                try:
                    stamped = event_from_dict(record["event"])
                    stored_hash = record["cumulative_hash"]
                except (KeyError, TypeError, ValueError) as exc:
                    raise LedgerCorruptionError(
                        f"{wal_path}: line {line_no} is malformed: {exc}") from exc
                if stamped.sequence != len(self._entries):
                    raise LedgerCorruptionError(
                        f"{wal_path}: line {line_no} has sequence "
                        f"{stamped.sequence}, expected {len(self._entries)} -- "
                        f"missing, duplicated, or reordered entry on disk")
                expected_hash = hash_entry(running_hash, stamped)
                if expected_hash != stored_hash:
                    raise LedgerCorruptionError(
                        f"{wal_path}: line {line_no} hash chain broken -- "
                        f"expected {expected_hash}, found {stored_hash} "
                        f"(tampering or corruption)")
                if stamped.event_id in self._seen_event_ids:
                    raise LedgerCorruptionError(
                        f"{wal_path}: line {line_no} duplicate event_id "
                        f"{stamped.event_id!r} already present earlier on disk")
                self._entries.append(LedgerEntry(event=stamped, cumulative_hash=stored_hash))
                self._seen_event_ids.add(stamped.event_id)
                running_hash = stored_hash
        self._next_sequence = len(self._entries)

    def append(self, event: Event) -> Event:
        """Stamps `event` with the next sequence number, chains its hash to
        the current head, durably persists it first if a WAL is attached
        (fsync'd before this returns), then appends in memory. Returns the
        stamped Event (the caller's original `event` is untouched -- Event
        is frozen)."""
        if event.event_id in self._seen_event_ids:
            raise DuplicateEventError(f"duplicate event_id: {event.event_id!r}")
        stamped = event.with_sequence(self._next_sequence)
        cum_hash = hash_entry(self.head_hash, stamped)
        if self._wal_file is not None:
            record = {"event": event_to_dict(stamped), "cumulative_hash": cum_hash}
            self._wal_file.write(json.dumps(record, sort_keys=True) + "\n")
            self._wal_file.flush()
            os.fsync(self._wal_file.fileno())
        self._entries.append(LedgerEntry(event=stamped, cumulative_hash=cum_hash))
        self._seen_event_ids.add(event.event_id)
        self._next_sequence += 1
        return stamped

    def append_if_valid(self, event: Event, validate: Callable[[Event], None]) -> Event:
        """Two-phase append used by `TruthEngine.apply()`: stamps and stages
        `event` in memory (so `validate` sees a ledger that already
        includes it, exactly like `append()`'s callers used to observe),
        calls `validate(stamped_event)`, and only WRITES the durable WAL
        line -- the point at which the event becomes truly persisted --
        if `validate` returns without raising. If `validate` raises, the
        staged entry is popped back off, its event_id is un-reserved, and
        the sequence counter is rewound -- the ledger ends up byte-for-byte
        identical to before this call, nothing was ever written to disk,
        and the SAME event_id can be resubmitted afterward. The exception
        propagates to the caller either way."""
        if event.event_id in self._seen_event_ids:
            raise DuplicateEventError(f"duplicate event_id: {event.event_id!r}")
        stamped = event.with_sequence(self._next_sequence)
        cum_hash = hash_entry(self.head_hash, stamped)
        self._entries.append(LedgerEntry(event=stamped, cumulative_hash=cum_hash))
        self._seen_event_ids.add(event.event_id)
        self._next_sequence += 1
        try:
            validate(stamped)
        except BaseException:
            self._entries.pop()
            self._seen_event_ids.discard(event.event_id)
            self._next_sequence -= 1
            raise
        if self._wal_file is not None:
            record = {"event": event_to_dict(stamped), "cumulative_hash": cum_hash}
            self._wal_file.write(json.dumps(record, sort_keys=True) + "\n")
            self._wal_file.flush()
            os.fsync(self._wal_file.fileno())
        return stamped

    def close(self) -> None:
        """Releases the WAL file handle, if any. Idempotent -- safe to
        call more than once or on a ledger with no WAL attached."""
        if self._wal_file is not None:
            self._wal_file.close()
            self._wal_file = None

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
        return self._entries[-1].cumulative_hash if self._entries else self._genesis_hash

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.entries)
