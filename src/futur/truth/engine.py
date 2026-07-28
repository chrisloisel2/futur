"""src/futur/truth/engine.py -- ties ledger, account, and invariants together.

`TruthEngine.apply(event)` is the single entrypoint for changing state:
stage on the ledger, apply to the account, check every invariant, and only
THEN durably commit -- live processing and pure replay (replay.py) both go
through this one method, so there is no second path that could drift from
it.
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
    margin_config: MarginConfig = field(default_factory=MarginConfig)
    ledger: Ledger | None = None

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = Ledger(margin_config=self.margin_config)
        elif self.ledger.margin_config != self.margin_config:
            raise ValueError(
                "TruthEngine.margin_config must match the margin_config the "
                "ledger's hash chain was bound to at construction -- got "
                f"engine={self.margin_config!r} ledger={self.ledger.margin_config!r}")

    @classmethod
    def from_ledger(cls, ledger: Ledger) -> TruthEngine:
        """Rebuilds a full engine (including derived Account state -- cash,
        positions, orders) from a Ledger that already has history, e.g. one
        just reloaded from its WAL after a restart. The ledger's own hash
        chain is already verified at that point (Ledger.__init__ does that
        on load); this replays every event through Account.apply_event()
        directly (not TruthEngine.apply(), which would try to re-stage and
        re-commit already-committed events and reject them as duplicates)
        to rebuild the state that lives only in Account, not the ledger
        itself."""
        engine = cls(margin_config=ledger.margin_config, ledger=ledger)
        for event in ledger.events():
            engine.account.apply_event(event)
        return engine

    def apply(self, event: Event) -> Event:
        """All-or-nothing at the ENGINE level, not just within the account's
        own handler (Account.apply_event already covers that): stages
        `event` on the ledger, applies it to the account, and checks every
        invariant, all BEFORE the event is durably committed to the WAL or
        its event_id is permanently reserved. If either the account
        rejects the event or the resulting state violates an invariant,
        both the ledger's staged entry and the account's mutation are
        rolled back -- the engine ends up exactly as it was before this
        call, and the SAME event_id can be resubmitted (e.g. a corrected
        version). A rejection therefore does not poison the engine; the
        caller may continue applying further events."""
        ledger = self.ledger
        assert ledger is not None   # __post_init__ always resolves this
        account_snapshot = self.account.snapshot()

        def validate(stamped: Event) -> None:
            self.account.apply_event(stamped)
            invariants.check(self.account, ledger, self.margin_config)

        try:
            return ledger.append_if_valid(event, validate)
        except BaseException:
            self.account.restore(account_snapshot)
            raise
