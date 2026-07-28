"""src/futur/truth/account.py -- the mutable account state Events apply to.

Accounting convention (documented in full in docs/TRUTH_ACCOUNTING.md):
realized PnL, fees, funding, and borrow cost are folded into `cash`
IMMEDIATELY when their event is applied -- there is no separate "accrued"
or "pending" bucket. This makes NAV a 3-term sum with nothing left to
double-count:

    NAV = cash + spot_market_value + perp_unrealized_pnl

`Account.apply_event()` is the ONLY place state changes -- used by both
live command handling (engine.py) and pure replay (replay.py), so replay
can never drift from live behavior by construction: there is no second
code path that could diverge.

Mono-currency: every cash-affecting event's `currency` must equal
`base_currency` (default "USD") -- no FX conversion is implemented. This is
a deliberate Phase 4 scope cut (documented, not silent): real multi-currency
accounting needs FX rates, which is a data/market-data concern this phase
explicitly excludes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.futur.truth.events import (
    BorrowCostPayload,
    CashDepositPayload,
    CashWithdrawalPayload,
    Event,
    FeePayload,
    FillPayload,
    Instrument,
    InstrumentType,
    MarginUpdatePayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderCancelledPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
)
from src.futur.truth.orders import Order, OrderSide, OrderStatus, OrderType
from src.futur.truth.positions import SpotPosition


class CurrencyMismatchError(Exception):
    pass


class DuplicateFillError(Exception):
    pass


class ShortSpotNotAllowedError(Exception):
    pass


class UnknownOrderError(Exception):
    pass


def _require_finite(value: float, label: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}")


@dataclass
class Account:
    base_currency: str = "USD"
    allow_short_spot: bool = False
    cash: float = 0.0

    spot_positions: dict = field(default_factory=dict)     # Instrument.key -> SpotPosition
    perp_positions: dict = field(default_factory=dict)      # Instrument.key -> PerpPosition
    marks: dict = field(default_factory=dict)                 # Instrument.key -> last mark price

    orders: dict = field(default_factory=dict)                  # order_id -> Order
    orders_by_client_id: dict = field(default_factory=dict)      # client_order_id -> [order_id, ...]

    seen_fill_ids: set = field(default_factory=set)
    last_margin_snapshot: dict = field(default_factory=dict)      # Instrument.key -> MarginUpdatePayload
    last_reconciliation: object = None

    # ── dispatch ─────────────────────────────────────────────────────────
    def apply_event(self, event: Event) -> None:
        handler = getattr(self, f"_apply_{event.event_type.value.lower()}")
        handler(event.payload)

    # ── cash ─────────────────────────────────────────────────────────────
    def _check_currency(self, currency: str) -> None:
        if currency != self.base_currency:
            raise CurrencyMismatchError(
                f"account base currency is {self.base_currency!r}, event used "
                f"{currency!r} -- multi-currency/FX is not implemented")

    def _apply_cash_deposit(self, p: CashDepositPayload) -> None:
        self._check_currency(p.currency)
        _require_finite(p.amount, "deposit amount")
        if p.amount <= 0:
            raise ValueError(f"deposit amount must be > 0, got {p.amount!r}")
        self.cash += p.amount

    def _apply_cash_withdrawal(self, p: CashWithdrawalPayload) -> None:
        self._check_currency(p.currency)
        _require_finite(p.amount, "withdrawal amount")
        if p.amount <= 0:
            raise ValueError(f"withdrawal amount must be > 0, got {p.amount!r}")
        self.cash -= p.amount

    def _apply_borrow_cost(self, p: BorrowCostPayload) -> None:
        self._check_currency(p.currency)
        if p.amount < 0:
            raise ValueError(f"borrow cost must be >= 0, got {p.amount!r}")
        self.cash -= p.amount

    def _apply_fee(self, p: FeePayload) -> None:
        self._check_currency(p.currency)
        if p.amount < 0:
            raise ValueError(f"fee must be >= 0, got {p.amount!r}")
        self.cash -= p.amount

    # ── orders ───────────────────────────────────────────────────────────
    def _apply_order_submitted(self, p: OrderSubmittedPayload) -> None:
        order = Order(order_id=p.order_id, client_order_id=p.client_order_id,
                     instrument=p.instrument, side=OrderSide(p.side),
                     order_type=OrderType(p.order_type), quantity=p.quantity,
                     limit_price=p.limit_price)
        order.transition_to(OrderStatus.SUBMITTED)
        self.orders[p.order_id] = order
        self.orders_by_client_id.setdefault(p.client_order_id, []).append(p.order_id)

    def _get_order(self, order_id: str) -> Order:
        if order_id not in self.orders:
            raise UnknownOrderError(f"no such order: {order_id!r}")
        return self.orders[order_id]

    def _apply_order_acknowledged(self, p: OrderAcknowledgedPayload) -> None:
        self._get_order(p.order_id).transition_to(OrderStatus.ACKNOWLEDGED)

    def _apply_order_rejected(self, p: OrderRejectedPayload) -> None:
        self._get_order(p.order_id).transition_to(OrderStatus.REJECTED)

    def _apply_order_cancelled(self, p: OrderCancelledPayload) -> None:
        self._get_order(p.order_id).transition_to(OrderStatus.CANCELLED)

    # ── marks ────────────────────────────────────────────────────────────
    def _apply_mark(self, p: MarkPayload) -> None:
        if p.price <= 0:
            raise ValueError(f"mark price must be > 0, got {p.price!r}")
        self.marks[p.instrument.key] = p.price

    # ── fills ────────────────────────────────────────────────────────────
    def _apply_fill(self, p: FillPayload) -> None:
        if p.fill_id in self.seen_fill_ids:
            raise DuplicateFillError(f"duplicate fill_id: {p.fill_id!r}")
        order = self._get_order(p.order_id)
        order.apply_fill(p.quantity)   # enforces no-overfill / no-fill-in-terminal-status
        self.seen_fill_ids.add(p.fill_id)

        signed_qty = p.quantity if OrderSide(p.side) == OrderSide.BUY else -p.quantity
        if p.instrument.type == InstrumentType.SPOT:
            self._apply_spot_fill(p.instrument, signed_qty, p.price, p.fee)
        else:
            self._apply_perp_fill(p.instrument, signed_qty, p.price, p.fee)

    def _apply_spot_fill(self, instrument: Instrument, signed_qty: float,
                         price: float, fee: float) -> None:
        pos = self.spot_positions.setdefault(instrument.key, SpotPosition(instrument=instrument))
        new_qty = pos.quantity + signed_qty
        if new_qty < 0 and not self.allow_short_spot:
            raise ShortSpotNotAllowedError(
                f"fill would leave {instrument.key} spot quantity at {new_qty} < 0 "
                f"-- short spot is disabled by default (allow_short_spot=False)")
        # cash impact: buying spends cash + fee; selling receives cash - fee,
        # regardless of side, expressed once via the signed quantity.
        self.cash -= signed_qty * price
        self.cash -= fee
        pos.quantity = new_qty

    def _apply_perp_fill(self, instrument: Instrument, signed_qty: float,
                         price: float, fee: float) -> None:
        raise NotImplementedError(
            "perpetual fill accounting lands in the next commit "
            "(truth: implement perpetual accounting)")

    # ── margin / liquidation snapshots (informational at this stage) ──────
    def _apply_margin_update(self, p: MarginUpdatePayload) -> None:
        self.last_margin_snapshot[p.instrument.key] = p

    # ── derived quantities ───────────────────────────────────────────────
    def spot_market_value(self) -> float:
        total = 0.0
        for key, pos in self.spot_positions.items():
            mark = self.marks.get(key)
            if mark is None:
                continue   # never marked yet -- contributes nothing, not an error
            total += pos.market_value(mark)
        return total

    def nav(self) -> float:
        return self.cash + self.spot_market_value()
