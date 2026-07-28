"""tests/truth/test_ledger.py -- append-only, sequencing, hash chain."""
from __future__ import annotations

import pytest

from src.futur.truth.events import CashDepositPayload, Event, EventType
from src.futur.truth.ledger import GENESIS_HASH, DuplicateEventError, Ledger


def _deposit(event_id: str, amount: float = 100.0) -> Event:
    return Event(event_id=event_id, event_type=EventType.CASH_DEPOSIT,
                ts_event="2026-01-01T00:00:00Z", ts_received="2026-01-01T00:00:00Z",
                payload=CashDepositPayload(amount=amount, currency="USD"))


def test_append_assigns_monotonic_sequence_ignoring_caller_supplied_value():
    ledger = Ledger()
    e1 = _deposit("e1")
    assert e1.sequence == -1     # not yet assigned
    stamped1 = ledger.append(e1)
    stamped2 = ledger.append(_deposit("e2"))
    assert stamped1.sequence == 0
    assert stamped2.sequence == 1
    assert e1.sequence == -1     # original event object untouched (frozen)


def test_duplicate_event_id_rejected():
    ledger = Ledger()
    ledger.append(_deposit("e1"))
    with pytest.raises(DuplicateEventError):
        ledger.append(_deposit("e1", amount=999.0))   # even with different content


def test_no_delete_or_update_api_exists():
    """Structural, not behavioral: the append-only guarantee comes from
    this class simply not exposing any other mutator. `close()` releases
    a WAL file handle, and `append_if_valid()` is a two-phase append (see
    engine.py) -- neither lets a caller edit or remove COMMITTED
    history."""
    public_methods = {name for name in dir(Ledger) if not name.startswith("_")}
    assert public_methods == {"append", "append_if_valid", "close", "entries",
                              "events", "head_hash"}


def test_entries_and_events_are_read_only_views():
    ledger = Ledger()
    ledger.append(_deposit("e1"))
    entries = ledger.entries
    entries_list_mutation_target = list(entries)
    entries_list_mutation_target.clear()          # mutating the returned copy
    assert len(ledger.entries) == 1                # ... does not affect the ledger

    events = ledger.events()
    events.clear()
    assert len(ledger.events()) == 1


def test_genesis_hash_before_any_append():
    """The genesis is bound to (engine_version, margin_config), not the raw
    all-zero constant -- GENESIS_HASH is only the ROOT the binding starts
    from (see ledger.py's _compute_genesis_hash)."""
    ledger = Ledger()
    assert ledger.head_hash != GENESIS_HASH
    assert len(ledger.head_hash) == 64
    assert len(ledger) == 0


def test_genesis_hash_is_deterministic_for_the_same_default_config():
    assert Ledger().head_hash == Ledger().head_hash


def test_genesis_hash_depends_on_margin_config():
    from src.futur.truth.margin import MarginConfig
    default = Ledger()
    custom = Ledger(margin_config=MarginConfig(initial_margin_rate=0.20,
                                               maintenance_margin_rate=0.10))
    assert default.head_hash != custom.head_hash


def test_genesis_hash_depends_on_engine_version():
    a = Ledger(engine_version="truth-engine/1")
    b = Ledger(engine_version="truth-engine/2-test-only")
    assert a.head_hash != b.head_hash


def test_hash_chains_to_previous_head():
    ledger = Ledger()
    h0 = ledger.head_hash
    ledger.append(_deposit("e1"))
    h1 = ledger.head_hash
    ledger.append(_deposit("e2"))
    h2 = ledger.head_hash
    assert h0 != h1 != h2
    assert ledger.entries[0].cumulative_hash == h1
    assert ledger.entries[1].cumulative_hash == h2


def test_same_event_sequence_produces_same_hash_deterministically():
    """The core determinism property this whole module exists for: replay
    the identical event content, in the identical order, into a fresh
    ledger -> identical final hash."""
    ledger_a, ledger_b = Ledger(), Ledger()
    for eid in ("e1", "e2", "e3"):
        ledger_a.append(_deposit(eid, amount=50.0))
        ledger_b.append(_deposit(eid, amount=50.0))
    assert ledger_a.head_hash == ledger_b.head_hash
    assert [e.event_id for e in ledger_a.events()] == [e.event_id for e in ledger_b.events()]


def test_different_event_content_produces_different_hash():
    ledger_a, ledger_b = Ledger(), Ledger()
    ledger_a.append(_deposit("e1", amount=50.0))
    ledger_b.append(_deposit("e1", amount=51.0))
    assert ledger_a.head_hash != ledger_b.head_hash


def test_different_event_order_produces_different_hash():
    ledger_a, ledger_b = Ledger(), Ledger()
    ledger_a.append(_deposit("e1")); ledger_a.append(_deposit("e2"))
    ledger_b.append(_deposit("e2")); ledger_b.append(_deposit("e1"))
    assert ledger_a.head_hash != ledger_b.head_hash
