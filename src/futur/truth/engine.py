"""src/futur/truth/engine.py -- ties ledger, account, and invariants together.

`TruthEngine.apply(event)` is the single entrypoint for changing state:
append to the ledger, apply to the account, check every invariant. Live
processing and pure replay (replay.py) both go through this one method --
there is no second path that could drift from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.futur.truth import invariants
from src.futur.truth.account import Account
from src.futur.truth.events import Event
from src.futur.truth.ledger import Ledger
from src.futur.truth.margin import MarginConfig


@dataclass
class TruthEngine:
    account: Account = field(default_factory=Account)
    ledger: Ledger = field(default_factory=Ledger)
    margin_config: MarginConfig = field(default_factory=MarginConfig)

    def apply(self, event: Event) -> Event:
        """Append `event` to the ledger (which assigns its real sequence
        number), apply the stamped event to the account, then run every
        invariant. If a violation is found, it raises straight out of
        here -- the event is already in the ledger (append-only, it can't
        be un-appended) and the account has already been mutated, so a
        caller catching this should treat the whole engine as poisoned,
        not attempt to continue."""
        stamped = self.ledger.append(event)
        self.account.apply_event(stamped)
        invariants.check(self.account, self.ledger, self.margin_config)
        return stamped
