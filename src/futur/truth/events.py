"""src/futur/truth/events.py -- products, event types, typed payloads.

Every state change in the engine is represented as an `Event`. Events are
immutable and carry a typed payload (one dataclass per EventType) rather
than a loose dict, so a malformed event is a construction-time error, not a
runtime KeyError three layers down.

All numeric payload fields are `Decimal`, converted at construction via
`numeric.to_decimal()` -- a caller may pass a float/int/str/Decimal, but
whatever comes in is always converted through `Decimal(str(x))`, never
`Decimal(x)` directly on a float (see numeric.py's own docstring for why
that distinction matters). This is a structural guarantee, not a calling
convention callers must remember.

Canonical ordering key: (timestamp_received, sequence, event_id) -- see
Event.sort_key(). `sequence` is assigned by the Ledger on append (strictly
monotonic), never by the caller, so ordering can never be gamed by
constructing an Event with a chosen sequence number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from src.futur.truth.numeric import to_decimal

# Mono-currency scope (see docs/TRUTH_ACCOUNTING.md): only these settlement
# currencies are supported. Explicit whitelist, not an accident of nobody
# having tried another one -- constructing a ProductSpec or a cash-bearing
# payload with any other quote currency raises UnsupportedCurrencyError.
SUPPORTED_QUOTE_CURRENCIES = frozenset({"USD"})


class UnsupportedCurrencyError(Exception):
    pass


class UnsupportedProductError(Exception):
    pass


def _check_supported_currency(currency: str) -> None:
    if currency not in SUPPORTED_QUOTE_CURRENCIES:
        raise UnsupportedCurrencyError(
            f"currency {currency!r} is not supported -- only "
            f"{sorted(SUPPORTED_QUOTE_CURRENCIES)} (mono-currency scope, "
            f"see docs/TRUTH_ACCOUNTING.md)"
        )


class ProductType(str, Enum):
    SPOT = "SPOT"
    LINEAR_PERP = "LINEAR_PERP"
    # Deliberately no INVERSE_PERP, no FUTURES, no OPTIONS -- this is a
    # closed enum. Constructing ProductType("INVERSE_PERP") or anything
    # else raises ValueError by construction: unsupported products are
    # rejected structurally, not by a runtime branch that might be
    # forgotten. See replay.py for the friendlier error message at the
    # JSON-deserialization boundary.


@dataclass(frozen=True)
class ProductSpec:
    venue: str
    symbol: str
    type: ProductType
    base_ccy: str
    quote_ccy: str
    tick_size: Decimal
    lot_size: Decimal
    multiplier: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick_size", to_decimal(self.tick_size))
        object.__setattr__(self, "lot_size", to_decimal(self.lot_size))
        object.__setattr__(self, "multiplier", to_decimal(self.multiplier))
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be > 0, got {self.tick_size!r}")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be > 0, got {self.lot_size!r}")
        if self.multiplier <= 0:
            raise ValueError(f"multiplier must be > 0, got {self.multiplier!r}")
        _check_supported_currency(self.quote_ccy)

    @property
    def key(self) -> str:
        """Stable identity used everywhere positions/marks are keyed --
        (venue, symbol, type) because the same symbol can exist as both
        SPOT and LINEAR_PERP on the same venue with entirely separate
        accounting."""
        return f"{self.venue}:{self.symbol}:{self.type.value}"

    def quantize_price(self, value: Decimal | float | str) -> Decimal:
        return (to_decimal(value) / self.tick_size).to_integral_value() * self.tick_size

    def quantize_quantity(self, value: Decimal | float | str) -> Decimal:
        return (to_decimal(value) / self.lot_size).to_integral_value() * self.lot_size


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


# ── typed payloads, one per EventType -- every numeric field is Decimal ────

@dataclass(frozen=True)
class CashDepositPayload:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_supported_currency(self.currency)


@dataclass(frozen=True)
class CashWithdrawalPayload:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_supported_currency(self.currency)


@dataclass(frozen=True)
class OrderSubmittedPayload:
    order_id: str
    client_order_id: str
    instrument: ProductSpec
    side: str            # "BUY" | "SELL" -- see orders.OrderSide
    order_type: str       # "MARKET" | "LIMIT" -- see orders.OrderType
    quantity: Decimal
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", to_decimal(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", to_decimal(self.limit_price))


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
    instrument: ProductSpec
    price: Decimal
    quantity: Decimal
    side: str             # "BUY" | "SELL"
    fee: Decimal
    fee_ccy: str
    liquidity: str | None = None      # "MAKER" | "TAKER"
    venue: str = ""
    external_id: str | None = None

    def __post_init__(self) -> None:
        # Tick/lot-quantized at construction, not left as a raw Decimal(str(x))
        # -- a fill price/quantity that disagreed with the instrument's own
        # tick_size/lot_size would silently diverge from how MarkPayload and
        # LiquidationPayload round the same instrument's price, breaking
        # NAV/PnL identities across event types (caught by Hypothesis).
        object.__setattr__(self, "price", self.instrument.quantize_price(self.price))
        object.__setattr__(self, "quantity", self.instrument.quantize_quantity(self.quantity))
        object.__setattr__(self, "fee", to_decimal(self.fee))
        _check_supported_currency(self.fee_ccy)


@dataclass(frozen=True)
class MarkPayload:
    instrument: ProductSpec
    price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", self.instrument.quantize_price(self.price))


@dataclass(frozen=True)
class FundingPayload:
    instrument: ProductSpec
    amount: Decimal          # signed: + received, - paid
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_supported_currency(self.currency)


@dataclass(frozen=True)
class BorrowCostPayload:
    amount: Decimal           # always >= 0 -- a cost
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_supported_currency(self.currency)


@dataclass(frozen=True)
class FeePayload:
    amount: Decimal            # always >= 0 -- a cost
    currency: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_supported_currency(self.currency)


@dataclass(frozen=True)
class MarginUpdatePayload:
    instrument: ProductSpec
    initial_margin_required: Decimal
    maintenance_margin_required: Decimal
    margin_available: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_margin_required",
                          to_decimal(self.initial_margin_required))
        object.__setattr__(self, "maintenance_margin_required",
                          to_decimal(self.maintenance_margin_required))
        object.__setattr__(self, "margin_available", to_decimal(self.margin_available))


@dataclass(frozen=True)
class LiquidationPayload:
    instrument: ProductSpec
    quantity_closed: Decimal
    price: Decimal
    fee: Decimal
    slippage: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        # Same tick/lot quantization as FillPayload/MarkPayload -- a
        # liquidation price is just as real a market price as a fill or a
        # mark, and must round to the same grid or PnL computed against a
        # tick-quantized mark won't match PnL realized at liquidation.
        object.__setattr__(self, "quantity_closed",
                          self.instrument.quantize_quantity(self.quantity_closed))
        object.__setattr__(self, "price", self.instrument.quantize_price(self.price))
        object.__setattr__(self, "fee", to_decimal(self.fee))
        object.__setattr__(self, "slippage", to_decimal(self.slippage))


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
