"""src/futur/truth/events.py -- instruments, event types, typed payloads.

Every state change in the engine is represented as an `Event`. Events are
immutable and carry a typed payload (one dataclass per EventType) rather
than a loose dict, so a malformed event is a construction-time error, not a
runtime KeyError three layers down.

Canonical ordering key: (timestamp_received, sequence, event_id) -- see
Event.sort_key(). `sequence` is assigned by the Ledger on append (strictly
monotonic), never by the caller, so ordering can never be gamed by
constructing an Event with a chosen sequence number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InstrumentType(str, Enum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"


@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    type: InstrumentType
    base_ccy: str
    quote_ccy: str
    tick_size: float
    lot_size: float
    contract_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be > 0, got {self.tick_size!r}")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be > 0, got {self.lot_size!r}")
        if self.contract_multiplier <= 0:
            raise ValueError(
                f"contract_multiplier must be > 0, got {self.contract_multiplier!r}")

    @property
    def key(self) -> str:
        """Stable identity used everywhere positions/marks are keyed --
        (venue, symbol, type) because the same symbol can exist as both
        SPOT and PERPETUAL on the same venue with entirely separate
        accounting."""
        return f"{self.venue}:{self.symbol}:{self.type.value}"


class EventType(str, Enum):
    CASH_DEPOSIT = "CASH_DEPOSIT"
    CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    FILL = "FILL"
    MARK = "MARK"
    FUNDING = "FUNDING"
    BORROW_COST = "BORROW_COST"
    FEE = "FEE"
    MARGIN_UPDATE = "MARGIN_UPDATE"
    LIQUIDATION = "LIQUIDATION"
    RECONCILIATION = "RECONCILIATION"


# ── typed payloads, one per EventType ───────────────────────────────────────

@dataclass(frozen=True)
class CashDepositPayload:
    amount: float
    currency: str


@dataclass(frozen=True)
class CashWithdrawalPayload:
    amount: float
    currency: str


@dataclass(frozen=True)
class OrderSubmittedPayload:
    order_id: str
    client_order_id: str
    instrument: Instrument
    side: str            # "BUY" | "SELL" -- see orders.OrderSide
    order_type: str       # "MARKET" | "LIMIT" -- see orders.OrderType
    quantity: float
    limit_price: float | None = None


@dataclass(frozen=True)
class OrderAcknowledgedPayload:
    order_id: str


@dataclass(frozen=True)
class OrderRejectedPayload:
    order_id: str
    reason: str


@dataclass(frozen=True)
class OrderCancelledPayload:
    order_id: str
    reason: str = ""


@dataclass(frozen=True)
class FillPayload:
    fill_id: str
    order_id: str
    instrument: Instrument
    price: float
    quantity: float
    side: str             # "BUY" | "SELL"
    fee: float
    fee_ccy: str
    liquidity: str | None = None      # "MAKER" | "TAKER"
    venue: str = ""
    external_id: str | None = None


@dataclass(frozen=True)
class MarkPayload:
    instrument: Instrument
    price: float


@dataclass(frozen=True)
class FundingPayload:
    instrument: Instrument
    amount: float          # signed: + received, - paid
    currency: str


@dataclass(frozen=True)
class BorrowCostPayload:
    amount: float           # always >= 0 -- a cost
    currency: str


@dataclass(frozen=True)
class FeePayload:
    amount: float            # always >= 0 -- a cost
    currency: str
    reason: str = ""


@dataclass(frozen=True)
class MarginUpdatePayload:
    instrument: Instrument
    initial_margin_required: float
    maintenance_margin_required: float
    margin_available: float


@dataclass(frozen=True)
class LiquidationPayload:
    instrument: Instrument
    quantity_closed: float
    price: float
    fee: float


@dataclass(frozen=True)
class ReconciliationPayload:
    verdict: str            # "MATCH" | "MISMATCH"
    details: dict


Payload = (
    CashDepositPayload | CashWithdrawalPayload
    | OrderSubmittedPayload | OrderAcknowledgedPayload | OrderRejectedPayload
    | OrderCancelledPayload | FillPayload | MarkPayload | FundingPayload
    | BorrowCostPayload | FeePayload | MarginUpdatePayload | LiquidationPayload
    | ReconciliationPayload
)

_PAYLOAD_TYPE_FOR_EVENT: dict[EventType, type] = {
    EventType.CASH_DEPOSIT: CashDepositPayload,
    EventType.CASH_WITHDRAWAL: CashWithdrawalPayload,
    EventType.ORDER_SUBMITTED: OrderSubmittedPayload,
    EventType.ORDER_ACKNOWLEDGED: OrderAcknowledgedPayload,
    EventType.ORDER_REJECTED: OrderRejectedPayload,
    EventType.ORDER_CANCELLED: OrderCancelledPayload,
    EventType.FILL: FillPayload,
    EventType.MARK: MarkPayload,
    EventType.FUNDING: FundingPayload,
    EventType.BORROW_COST: BorrowCostPayload,
    EventType.FEE: FeePayload,
    EventType.MARGIN_UPDATE: MarginUpdatePayload,
    EventType.LIQUIDATION: LiquidationPayload,
    EventType.RECONCILIATION: ReconciliationPayload,
}


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: EventType
    ts_event: str          # ISO-8601 -- when the fact happened
    ts_received: str        # ISO-8601 -- when the engine saw it
    payload: Payload
    sequence: int = field(default=-1)   # assigned by Ledger.append(), not the caller

    def __post_init__(self) -> None:
        expected = _PAYLOAD_TYPE_FOR_EVENT[self.event_type]
        if not isinstance(self.payload, expected):
            raise TypeError(
                f"{self.event_type.value} requires payload of type "
                f"{expected.__name__}, got {type(self.payload).__name__}"
            )

    def sort_key(self) -> tuple:
        """Canonical ordering: (timestamp_received, sequence, event_id)."""
        return (self.ts_received, self.sequence, self.event_id)

    def with_sequence(self, sequence: int) -> Event:
        """Ledger.append() calls this to stamp the assigned sequence --
        Event is frozen, so this returns a new instance rather than
        mutating in place."""
        return Event(
            event_id=self.event_id, event_type=self.event_type,
            ts_event=self.ts_event, ts_received=self.ts_received,
            payload=self.payload, sequence=sequence,
        )
