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
`base_currency` (default "USD", the only currently supported quote
currency -- see events.SUPPORTED_QUOTE_CURRENCIES) -- no FX conversion is
implemented. This is a deliberate Phase 4 scope cut (documented, not
silent): real multi-currency accounting needs FX rates, which is a
data/market-data concern this phase excludes.

All money fields are Decimal, quantized to numeric.CASH_QUANTUM (8 places)
on every write -- not left to accumulate arbitrary-precision residue from
divisions (e.g. the perp weighted-average-price calculation). Every
cumulative_* counter is updated in the SAME method that touches `cash`
itself, so invariants.py's cash-vs-categories cross-check is meaningful.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal

from src.futur.truth.events import (
    BorrowCostPayload,
    CashDepositPayload,
    CashWithdrawalPayload,
    Event,
    FeePayload,
    FillPayload,
    FundingPayload,
    LiquidationPayload,
    MarginUpdatePayload,
    MarkPayload,
    OrderAcknowledgedPayload,
    OrderCancelledPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
    ProductSpec,
    ProductType,
    ReconciliationPayload,
)
from src.futur.truth.numeric import quantize_cash
from src.futur.truth.orders import Order, OrderSide, OrderStatus, OrderType
from src.futur.truth.positions import PerpPosition, SpotPosition

ZERO = Decimal(0)


class CurrencyMismatchError(Exception):
    pass


class DuplicateFillError(Exception):
    pass


class ShortSpotNotAllowedError(Exception):
    pass


class UnknownOrderError(Exception):
    pass


class UnknownPositionError(Exception):
    pass


def _require_finite(value: Decimal, label: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{label} must be finite, got {value!r}")


@dataclass
class Account:
    base_currency: str = "USD"
    allow_short_spot: bool = False
    cash: Decimal = ZERO

    spot_positions: dict = field(default_factory=dict)     # ProductSpec.key -> SpotPosition
    perp_positions: dict = field(default_factory=dict)      # ProductSpec.key -> PerpPosition
    marks: dict = field(default_factory=dict)                 # ProductSpec.key -> last mark price

    orders: dict = field(default_factory=dict)                  # order_id -> Order
    orders_by_client_id: dict = field(default_factory=dict)      # client_order_id -> [order_id, ...]

    seen_fill_ids: set = field(default_factory=set)
    last_margin_snapshot: dict = field(default_factory=dict)      # ProductSpec.key -> MarginUpdatePayload
    last_reconciliation: object = None

    # Cumulative cash-flow categories -- updated IN THE SAME METHOD as
    # `cash` itself, every time, never computed after the fact. This is a
    # double-entry-style cross-check (invariants.py sums these and compares
    # to `cash`): it can't prove a formula is right, but it catches the
    # class of bug where an edit updates `cash` without updating its
    # category (or vice versa) -- a partial/incomplete change to a handler.
    cumulative_deposits: Decimal = ZERO
    cumulative_withdrawals: Decimal = ZERO
    cumulative_fees_paid: Decimal = ZERO       # fill.fee + standalone FEE events + liquidation fee
    cumulative_borrow_paid: Decimal = ZERO
    cumulative_funding: Decimal = ZERO          # signed
    cumulative_realized_pnl: Decimal = ZERO      # perp only
    cumulative_spot_trade_cashflow: Decimal = ZERO   # signed: -buy notional, +sell notional
    cumulative_slippage_paid: Decimal = ZERO          # liquidation slippage -- a distinct category

    def __post_init__(self) -> None:
        self.cash = quantize_cash(self.cash)
        for attr in (
            "cumulative_deposits", "cumulative_withdrawals", "cumulative_fees_paid",
            "cumulative_borrow_paid", "cumulative_funding", "cumulative_realized_pnl",
            "cumulative_spot_trade_cashflow", "cumulative_slippage_paid",
        ):
            setattr(self, attr, quantize_cash(getattr(self, attr)))

    # ── snapshot / restore ───────────────────────────────────────────────
    def snapshot(self) -> dict:
        """A deep copy of every field, for all-or-nothing rollback. Public
        (not just `apply_event`'s own local use) so a caller that needs to
        undo MORE than one handler call -- e.g. `TruthEngine.apply()`
        rolling back a handler that individually succeeded but left the
        account violating a cross-cutting invariant checked afterward --
        can snapshot before and restore after, using the exact same
        mechanism."""
        return copy.deepcopy(self.__dict__)

    def restore(self, snapshot: dict) -> None:
        self.__dict__.clear()
        self.__dict__.update(snapshot)

    # ── dispatch ─────────────────────────────────────────────────────────
    def apply_event(self, event: Event) -> None:
        """All-or-nothing: an event either fully applies or leaves every
        field exactly as it was. Without this, a handler that mutates more
        than one thing before its own validation can raise (e.g.
        `_apply_fill` marks the Order as filled via `order.apply_fill()`
        *before* `_apply_spot_fill` gets a chance to reject a short-spot
        fill) would leave the Order's fill bookkeeping incremented while
        cash/positions never moved -- a real partial mutation, invisible
        until something else reads the inconsistent state later. Snapshot
        before, restore on any exception, so a rejected event is
        indistinguishable from an event that was never received.

        This only covers the ONE handler call -- it does not protect
        against a LATER, separate check (like invariants.check()) that
        finds the resulting state invalid; that's `TruthEngine.apply()`'s
        job, using the same `snapshot`/`restore` pair around the wider
        handler-plus-invariants sequence."""
        handler = getattr(self, f"_apply_{event.event_type.value.lower()}")
        snap = self.snapshot()
        try:
            handler(event.payload)
        except BaseException:
            self.restore(snap)
            raise

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
        amount = quantize_cash(p.amount)
        self.cash = quantize_cash(self.cash + amount)
        self.cumulative_deposits = quantize_cash(self.cumulative_deposits + amount)

    def _apply_cash_withdrawal(self, p: CashWithdrawalPayload) -> None:
        self._check_currency(p.currency)
        _require_finite(p.amount, "withdrawal amount")
        if p.amount <= 0:
            raise ValueError(f"withdrawal amount must be > 0, got {p.amount!r}")
        amount = quantize_cash(p.amount)
        self.cash = quantize_cash(self.cash - amount)
        self.cumulative_withdrawals = quantize_cash(self.cumulative_withdrawals + amount)

    def _apply_borrow_cost(self, p: BorrowCostPayload) -> None:
        self._check_currency(p.currency)
        if p.amount < 0:
            raise ValueError(f"borrow cost must be >= 0, got {p.amount!r}")
        amount = quantize_cash(p.amount)
        self.cash = quantize_cash(self.cash - amount)
        self.cumulative_borrow_paid = quantize_cash(self.cumulative_borrow_paid + amount)

    def _apply_fee(self, p: FeePayload) -> None:
        self._check_currency(p.currency)
        if p.amount < 0:
            raise ValueError(f"fee must be >= 0, got {p.amount!r}")
        amount = quantize_cash(p.amount)
        self.cash = quantize_cash(self.cash - amount)
        self.cumulative_fees_paid = quantize_cash(self.cumulative_fees_paid + amount)

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
        self.marks[p.instrument.key] = p.instrument.quantize_price(p.price)

    # ── fills ────────────────────────────────────────────────────────────
    def _apply_fill(self, p: FillPayload) -> None:
        if p.fill_id in self.seen_fill_ids:
            raise DuplicateFillError(f"duplicate fill_id: {p.fill_id!r}")
        order = self._get_order(p.order_id)
        order.apply_fill(p.quantity)   # enforces no-overfill / no-fill-in-terminal-status
        self.seen_fill_ids.add(p.fill_id)

        signed_qty = p.quantity if OrderSide(p.side) == OrderSide.BUY else -p.quantity
        if p.instrument.type == ProductType.SPOT:
            self._apply_spot_fill(p.instrument, signed_qty, p.price, p.fee)
        else:
            self._apply_perp_fill(p.instrument, signed_qty, p.price, p.fee)

    def _apply_spot_fill(self, instrument: ProductSpec, signed_qty: Decimal,
                         price: Decimal, fee: Decimal) -> None:
        pos = self.spot_positions.setdefault(instrument.key, SpotPosition(instrument=instrument))
        new_qty = pos.quantity + signed_qty
        if new_qty < 0 and not self.allow_short_spot:
            raise ShortSpotNotAllowedError(
                f"fill would leave {instrument.key} spot quantity at {new_qty} < 0 "
                f"-- short spot is disabled by default (allow_short_spot=False)")
        # cash impact: buying spends cash + fee; selling receives cash - fee,
        # regardless of side, expressed once via the signed quantity.
        # Each term is quantized ONCE, independently, and that exact same
        # quantized value is what's added to both `cash` and its category --
        # quantizing the combined (trade_cashflow - fee) as a single op for
        # `cash` while quantizing each term separately for the categories
        # can round differently at exact-half ties (ROUND_HALF_EVEN depends
        # on the retained digit's parity, which differs between `cash` and
        # a lone category), silently breaking the cash-vs-categories
        # invariant -- caught by test_properties.py's Hypothesis suite.
        trade_cashflow = quantize_cash(-(signed_qty * price))
        fee = quantize_cash(fee)
        self.cash = quantize_cash(self.cash + trade_cashflow - fee)
        self.cumulative_spot_trade_cashflow = quantize_cash(
            self.cumulative_spot_trade_cashflow + trade_cashflow)
        self.cumulative_fees_paid = quantize_cash(self.cumulative_fees_paid + fee)
        pos.quantity = new_qty
        pos.last_price = price

    def _apply_perp_fill(self, instrument: ProductSpec, signed_qty: Decimal,
                         price: Decimal, fee: Decimal) -> None:
        """Weighted-average-cost perpetual accounting. The full notional
        never touches cash -- only realized PnL (folded in immediately,
        per this module's convention) and the fee do.

        Three cases, by how `signed_qty` relates to the existing position:
          - flat, or same sign as the existing position -> pure increase:
            weighted-average the entry price (quantized to the product's
            tick_size immediately -- Decimal division is not guaranteed to
            terminate, so an unquantized average would carry unbounded
            precision into every later comparison).
          - opposite sign, |signed_qty| <= |existing| -> pure reduction:
            realize PnL on the closed portion at (price - avg_entry), sign-
            adjusted for long vs. short; avg_entry_price of the remaining
            open portion is unchanged (it's still the same original cost
            basis, just a smaller quantity of it).
          - opposite sign, |signed_qty| > |existing| -> flip: realize PnL on
            the entire existing position, then open a brand-new position
            in the other direction at the fill price for the remainder.
        """
        pos = self.perp_positions.setdefault(instrument.key, PerpPosition(instrument=instrument))
        old_qty, old_avg = pos.quantity, pos.avg_entry_price
        realized_pnl = ZERO

        same_direction = old_qty == 0 or (old_qty > 0) == (signed_qty > 0)
        if same_direction:
            new_qty = old_qty + signed_qty
            new_avg = (instrument.quantize_price((old_qty * old_avg + signed_qty * price) / new_qty)
                      if new_qty != 0 else ZERO)
        else:
            closing_qty = min(abs(signed_qty), abs(old_qty))
            direction = Decimal(1) if old_qty > 0 else Decimal(-1)
            realized_pnl += (price - old_avg) * closing_qty * direction
            if abs(signed_qty) <= abs(old_qty):
                new_qty = old_qty + signed_qty
                new_avg = old_avg if new_qty != 0 else ZERO
            else:
                remainder = signed_qty + old_qty     # what's left after fully offsetting old_qty
                new_qty = remainder
                new_avg = price

        realized_pnl = quantize_cash(realized_pnl)
        fee = quantize_cash(fee)   # see _apply_spot_fill's comment on why each term is quantized once
        self.cash = quantize_cash(self.cash + realized_pnl - fee)
        self.cumulative_realized_pnl = quantize_cash(self.cumulative_realized_pnl + realized_pnl)
        self.cumulative_fees_paid = quantize_cash(self.cumulative_fees_paid + fee)
        pos.quantity = new_qty
        pos.avg_entry_price = new_avg

    def _apply_funding(self, p: FundingPayload) -> None:
        self._check_currency(p.currency)
        amount = quantize_cash(p.amount)
        self.cash = quantize_cash(self.cash + amount)    # signed: + received, - paid
        self.cumulative_funding = quantize_cash(self.cumulative_funding + amount)

    # ── margin snapshots (informational) ────────────────────────────────
    def _apply_margin_update(self, p: MarginUpdatePayload) -> None:
        self.last_margin_snapshot[p.instrument.key] = p

    # ── liquidation ──────────────────────────────────────────────────────
    def _apply_liquidation(self, p: LiquidationPayload) -> None:
        """Forced closure of `quantity_closed` of an existing perp
        position at `price`, with an explicit fee and an explicit
        slippage cost -- same realized-PnL math as a normal closing fill
        (see _apply_perp_fill's reduction case), but never a fill: no
        Order is involved, this bypasses the order/fill machinery
        entirely by design. Never partially implicit -- if there's no
        such position, or the close is larger than what's open, this
        raises rather than silently clamping.

        `price` is the reference/executed price used for the PnL
        calculation; `slippage` is a SEPARATE, explicit cost on top of the
        fee (not folded into `price`) -- this is what makes the identity
        `NAV_before - NAV_after == fee + slippage` exactly true: realizing
        PnL at `price` is NAV-neutral (unrealized shrinks by exactly what
        realized grows by), so only fee and slippage can move NAV, and
        both are subtracted from cash exactly once, tracked in their own
        cumulative_* category so invariants.py's cash-vs-categories check
        never double-counts them.
        """
        pos = self.perp_positions.get(p.instrument.key)
        if pos is None or pos.quantity == 0:
            raise UnknownPositionError(
                f"liquidation for {p.instrument.key} but no open perp position exists")
        if p.quantity_closed <= 0:
            raise ValueError(
                f"liquidation quantity_closed must be > 0, got {p.quantity_closed!r}")
        if p.quantity_closed > abs(pos.quantity):
            raise ValueError(
                f"liquidation quantity_closed {p.quantity_closed} exceeds open "
                f"position {pos.quantity} for {p.instrument.key}")
        direction = Decimal(1) if pos.quantity > 0 else Decimal(-1)
        realized_pnl = quantize_cash((p.price - pos.avg_entry_price) * p.quantity_closed * direction)
        # each term quantized once, independently -- see _apply_spot_fill's
        # comment for why combining raw terms into `cash` while quantizing
        # them separately for the categories can round differently and
        # silently break the cash-vs-categories invariant.
        fee = quantize_cash(p.fee)
        slippage = quantize_cash(p.slippage)

        self.cash = quantize_cash(self.cash + realized_pnl - fee - slippage)
        self.cumulative_realized_pnl = quantize_cash(self.cumulative_realized_pnl + realized_pnl)
        self.cumulative_fees_paid = quantize_cash(self.cumulative_fees_paid + fee)
        self.cumulative_slippage_paid = quantize_cash(self.cumulative_slippage_paid + slippage)

        new_qty = pos.quantity - direction * p.quantity_closed
        pos.quantity = new_qty
        if new_qty == 0:
            pos.avg_entry_price = ZERO

    # ── reconciliation (verdict recording -- comparison logic in
    #    reconciliation.py) ──────────────────────────────────────────────
    def _apply_reconciliation(self, p: ReconciliationPayload) -> None:
        self.last_reconciliation = p

    # ── derived quantities ───────────────────────────────────────────────
    def expected_cash_from_categories(self) -> Decimal:
        """Independent cross-check total (invariants.py compares this to
        `cash`) -- see the cumulative_* fields' docstring above."""
        return quantize_cash(
            self.cumulative_deposits
            - self.cumulative_withdrawals
            - self.cumulative_fees_paid
            - self.cumulative_borrow_paid
            - self.cumulative_slippage_paid
            + self.cumulative_funding
            + self.cumulative_realized_pnl
            + self.cumulative_spot_trade_cashflow
        )

    def spot_market_value(self) -> Decimal:
        total = ZERO
        for key, pos in self.spot_positions.items():
            mark = self.marks.get(key)
            if mark is None:
                continue   # never marked yet -- contributes nothing, not an error
            total += pos.market_value(mark)
        return quantize_cash(total)

    def perp_unrealized_pnl(self) -> Decimal:
        total = ZERO
        for key, pos in self.perp_positions.items():
            mark = self.marks.get(key)
            if mark is None:
                continue   # never marked yet -- contributes nothing, not an error
            total += pos.unrealized_pnl(mark)
        return quantize_cash(total)

    def nav(self) -> Decimal:
        return quantize_cash(self.cash + self.spot_market_value() + self.perp_unrealized_pnl())
