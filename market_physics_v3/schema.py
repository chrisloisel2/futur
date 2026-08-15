from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

VALID_BOOK_EVENTS = {"snapshot", "add", "modify", "update", "remove", "cancel"}
VALID_OPTION_TYPES = {"call", "put"}


def _require_positive(name: str, value: float, allow_zero: bool = False) -> None:
    ok = value >= 0 if allow_zero else value > 0
    if not ok:
        raise ValueError("%s must be %s" % (name, "non-negative" if allow_zero else "positive"))


def _validate_clock(event_ts_ns: int, receive_ts_ns: int) -> None:
    if event_ts_ns <= 0 or receive_ts_ns <= 0:
        raise ValueError("timestamps must be positive nanoseconds")
    if receive_ts_ns < event_ts_ns:
        raise ValueError("receive_ts_ns cannot precede event_ts_ns")


@dataclass(frozen=True)
class BookLevel:
    price: float
    qty: float
    # Number of resting orders represented by this level when a venue exposes
    # it (for example Hyperliquid WsLevel.n). Missing is not fabricated as zero.
    order_count: Optional[int] = None

    def __post_init__(self) -> None:
        _require_positive("price", self.price)
        _require_positive("qty", self.qty, allow_zero=True)
        if self.order_count is not None and int(self.order_count) < 0:
            raise ValueError("order_count must be non-negative")


@dataclass(frozen=True)
class BookEvent:
    venue: str
    symbol: str
    event_ts_ns: int
    receive_ts_ns: int
    sequence_id: int
    event_type: str
    side: str
    price: float
    qty: float
    order_count: Optional[int] = None
    # Explicit wire-stream provenance. This prevents a one-level BBO snapshot
    # from being mistaken for a deep-book snapshot during replay/readiness.
    source_stream: Optional[str] = None
    # Optional update-range metadata retained for deterministic replay audits.
    first_sequence_id: Optional[int] = None
    previous_sequence_id: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_clock(self.event_ts_ns, self.receive_ts_ns)
        if self.event_type not in VALID_BOOK_EVENTS:
            raise ValueError("invalid book event_type")
        if self.side not in {"bid", "ask"}:
            raise ValueError("book side must be bid/ask")
        _require_positive("price", self.price)
        _require_positive("qty", self.qty, allow_zero=True)
        if self.sequence_id < 0:
            raise ValueError("sequence_id must be non-negative")
        if self.order_count is not None and int(self.order_count) < 0:
            raise ValueError("order_count must be non-negative")
        if self.first_sequence_id is not None and int(self.first_sequence_id) < 0:
            raise ValueError("first_sequence_id must be non-negative")
        if self.previous_sequence_id is not None and int(self.previous_sequence_id) < -1:
            raise ValueError("previous_sequence_id must be >= -1")


@dataclass(frozen=True)
class TradeEvent:
    venue: str
    symbol: str
    event_ts_ns: int
    receive_ts_ns: int
    trade_id: str
    price: float
    qty: float
    aggressor: str
    # Optional provenance exposed by venues such as Hyperliquid. These fields
    # are intentionally nullable for CEX feeds that do not expose identities.
    buyer: Optional[str] = None
    seller: Optional[str] = None
    tx_hash: Optional[str] = None
    # Wire stream and physical granularity must survive normalization. In
    # particular Binance aggTrade is event-level but aggregates fills belonging
    # to one taker order and therefore is not a true trade-by-trade tape.
    source_stream: Optional[str] = None
    granularity: Optional[str] = None  # e.g. "individual" or "aggregate"

    def __post_init__(self) -> None:
        _validate_clock(self.event_ts_ns, self.receive_ts_ns)
        if self.aggressor not in {"buy", "sell"}:
            raise ValueError("aggressor must be buy/sell")
        _require_positive("price", self.price)
        _require_positive("qty", self.qty)
        if not str(self.trade_id):
            raise ValueError("trade_id cannot be empty")
        if self.granularity is not None and self.granularity not in {"individual", "aggregate"}:
            raise ValueError("trade granularity must be individual/aggregate")


@dataclass(frozen=True)
class DerivativeEvent:
    venue: str
    symbol: str
    event_ts_ns: int
    receive_ts_ns: int
    kind: str
    value: float
    side: Optional[str] = None
    price: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_clock(self.event_ts_ns, self.receive_ts_ns)
        if self.kind not in {"open_interest", "funding", "mark", "index", "premium", "liquidation"}:
            raise ValueError("invalid derivative kind")
        if self.side is not None and self.side not in {"buy", "sell", "long", "short"}:
            raise ValueError("invalid derivative side")
        if self.price is not None:
            _require_positive("price", self.price)


@dataclass(frozen=True)
class OptionQuote:
    venue: str
    underlying: str
    event_ts_ns: int
    receive_ts_ns: int
    expiry_ts_ns: int
    strike: float
    option_type: str
    bid: float
    ask: float
    bid_iv: float
    ask_iv: float
    delta: float
    open_interest: float = 0.0
    volume: float = 0.0

    def __post_init__(self) -> None:
        _validate_clock(self.event_ts_ns, self.receive_ts_ns)
        if self.expiry_ts_ns <= self.event_ts_ns:
            raise ValueError("option expiry must be in the future")
        if self.option_type not in VALID_OPTION_TYPES:
            raise ValueError("option_type must be call/put")
        _require_positive("strike", self.strike)
        _require_positive("bid", self.bid, allow_zero=True)
        _require_positive("ask", self.ask, allow_zero=True)
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        _require_positive("bid_iv", self.bid_iv, allow_zero=True)
        _require_positive("ask_iv", self.ask_iv, allow_zero=True)

    @property
    def mid_iv(self) -> float:
        return 0.5 * (self.bid_iv + self.ask_iv)


@dataclass(frozen=True)
class ExecutionTrace:
    order_id: str
    venue: str
    symbol: str
    side: str
    decision_ts_ns: int
    send_ts_ns: int
    ack_ts_ns: int
    first_fill_ts_ns: int
    last_fill_ts_ns: int
    decision_mid: float
    requested_qty: float
    filled_qty: float
    avg_fill_price: float
    fee_quote: float
    maker: bool

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError("execution side must be buy/sell")
        times = [self.decision_ts_ns, self.send_ts_ns, self.ack_ts_ns, self.first_fill_ts_ns, self.last_fill_ts_ns]
        if any(t <= 0 for t in times):
            raise ValueError("execution timestamps must be positive")
        if times != sorted(times):
            raise ValueError("execution timestamps must be monotonic")
        _require_positive("decision_mid", self.decision_mid)
        _require_positive("requested_qty", self.requested_qty)
        _require_positive("filled_qty", self.filled_qty, allow_zero=True)
        _require_positive("avg_fill_price", self.avg_fill_price)
        _require_positive("fee_quote", self.fee_quote, allow_zero=True)
        if self.filled_qty > self.requested_qty + 1e-12:
            raise ValueError("filled_qty cannot exceed requested_qty")


def canonical_partition(event_type: str, venue: str, symbol: str, date_utc: str) -> str:
    if not event_type or not venue or not symbol or not date_utc:
        raise ValueError("partition components cannot be empty")
    return "market_physics_v3/raw/%s/venue=%s/symbol=%s/date=%s" % (
        event_type, venue.lower(), symbol.upper(), date_utc
    )
