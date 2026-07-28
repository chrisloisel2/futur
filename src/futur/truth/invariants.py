"""src/futur/truth/invariants.py -- the central post-event check.

`check(account, ledger, margin_config)` is meant to run after EVERY event
(engine.py's apply() does exactly that). It never catches or narrows an
exception -- any violation raises InvariantViolation (or lets the
underlying ValueError/TypeError through), so a broken invariant stops
processing immediately and loudly. Nothing here uses a bare
`except Exception` to paper over a check.
"""
from __future__ import annotations

import math

from src.futur.truth.events import FillPayload, LiquidationPayload
from src.futur.truth.ledger import Ledger
from src.futur.truth.margin import MarginConfig, compute_margin_state
from src.futur.truth.orders import OrderSide

_EPS = 1e-6


def _close(a: float, b: float) -> bool:
    """Relative + absolute tolerance, not a bare fixed epsilon -- a fixed
    `abs(a - b) > 1e-6` is fine for order quantities but breaks down for
    cumulative cash/position sums at real-world scale (float64 has ~15-17
    significant digits, so at 1e12 the smallest representable difference
    is already ~1e-4, larger than a fixed 1e-6). Found by Hypothesis
    generating a large deposit, not by hand-picked examples -- every
    hand-written test in this suite happened to use numbers small enough
    to never hit this."""
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=_EPS)


class InvariantViolation(Exception):
    pass


def _check_finite_scalars(account) -> None:
    if not math.isfinite(account.cash):
        raise InvariantViolation(f"cash is not finite: {account.cash!r}")
    nav = account.nav()
    if not math.isfinite(nav):
        raise InvariantViolation(f"NAV is not finite: {nav!r}")


def _check_positive_prices(account) -> None:
    for key, price in account.marks.items():
        if not (math.isfinite(price) and price > 0):
            raise InvariantViolation(f"mark price for {key} is not positive/finite: {price!r}")


def _check_finite_quantities(account) -> None:
    for key, pos in account.spot_positions.items():
        if not math.isfinite(pos.quantity):
            raise InvariantViolation(f"spot quantity for {key} is not finite: {pos.quantity!r}")
    for key, pos in account.perp_positions.items():
        if not math.isfinite(pos.quantity):
            raise InvariantViolation(f"perp quantity for {key} is not finite: {pos.quantity!r}")
        if not math.isfinite(pos.avg_entry_price):
            raise InvariantViolation(
                f"perp avg_entry_price for {key} is not finite: {pos.avg_entry_price!r}")


def _check_order_fill_bounds(account) -> None:
    for order_id, order in account.orders.items():
        if order.filled_quantity > order.quantity + _EPS:
            raise InvariantViolation(
                f"order {order_id}: filled_quantity {order.filled_quantity} > "
                f"quantity {order.quantity}")


def _check_no_naked_short_spot(account) -> None:
    if account.allow_short_spot:
        return
    for key, pos in account.spot_positions.items():
        if pos.quantity < -_EPS:
            raise InvariantViolation(
                f"spot position {key} is negative ({pos.quantity}) without "
                f"allow_short_spot enabled")


def _check_margin_non_negative_and_ordered(account, margin_config: MarginConfig) -> None:
    state = compute_margin_state(account, margin_config)
    if state.initial_margin_required < -_EPS:
        raise InvariantViolation(
            f"initial_margin_required is negative: {state.initial_margin_required}")
    if state.maintenance_margin_required < -_EPS:
        raise InvariantViolation(
            f"maintenance_margin_required is negative: {state.maintenance_margin_required}")
    if state.maintenance_margin_required > state.initial_margin_required + _EPS:
        raise InvariantViolation(
            f"maintenance_margin_required ({state.maintenance_margin_required}) > "
            f"initial_margin_required ({state.initial_margin_required})")


def _check_ledger_integrity(ledger: Ledger) -> None:
    entries = ledger.entries
    seen_event_ids: set = set()
    seen_fill_ids: set = set()
    for expected_sequence, entry in enumerate(entries):
        event = entry.event
        if event.sequence != expected_sequence:
            raise InvariantViolation(
                f"ledger sequence not monotonic: expected {expected_sequence}, "
                f"got {event.sequence} at event {event.event_id!r}")

        if event.event_id in seen_event_ids:
            raise InvariantViolation(f"duplicate event_id in ledger: {event.event_id!r}")
        seen_event_ids.add(event.event_id)

        if isinstance(event.payload, FillPayload):
            fill_id = event.payload.fill_id
            if fill_id in seen_fill_ids:
                raise InvariantViolation(f"duplicate fill_id in ledger: {fill_id!r}")
            seen_fill_ids.add(fill_id)


def _check_client_order_id_consistency(account) -> None:
    for client_id, order_ids in account.orders_by_client_id.items():
        if len(order_ids) < 2:
            continue
        orders = [account.orders[oid] for oid in order_ids]
        first = orders[0]
        for other in orders[1:]:
            if (other.instrument.key != first.instrument.key
                    or other.side != first.side
                    or other.order_type != first.order_type
                    or abs(other.quantity - first.quantity) > _EPS):
                raise InvariantViolation(
                    f"client_order_id {client_id!r} maps to conflicting orders: "
                    f"{first.order_id!r} and {other.order_id!r} disagree on "
                    f"instrument/side/type/quantity")


def _check_positions_equal_sum_of_fills(account, ledger: Ledger) -> None:
    """Independent of Account's own bookkeeping: replays signed quantity
    changes straight from the ledger's FILL and LIQUIDATION events (not
    from _apply_perp_fill's/_apply_liquidation's weighted-average-cost
    state) and compares the final total to the live position. Net signed
    quantity is unaffected by how PnL/avg-price get computed, so this
    catches a real class of bug -- a position update that drifted from
    what the recorded history actually said -- without duplicating the
    PnL logic itself. LIQUIDATION's directional effect depends on which
    side the position was on AT THAT POINT, so this replays in ledger
    order rather than summing independently of order (a liquidation
    always moves quantity toward zero, never away from it)."""
    expected: dict = {}
    for entry in ledger.entries:
        event = entry.event
        if isinstance(event.payload, FillPayload):
            p = event.payload
            signed = p.quantity if OrderSide(p.side) == OrderSide.BUY else -p.quantity
            expected[p.instrument.key] = expected.get(p.instrument.key, 0.0) + signed
        elif isinstance(event.payload, LiquidationPayload):
            lp = event.payload
            running = expected.get(lp.instrument.key, 0.0)
            direction = 1.0 if running > 0 else -1.0
            expected[lp.instrument.key] = running - direction * lp.quantity_closed

    for key, pos in account.spot_positions.items():
        exp = expected.get(key, 0.0)
        if not _close(pos.quantity, exp):
            raise InvariantViolation(
                f"spot position {key}: quantity {pos.quantity} != sum of fills {exp}")
    for key, pos in account.perp_positions.items():
        exp = expected.get(key, 0.0)
        if not _close(pos.quantity, exp):
            raise InvariantViolation(
                f"perp position {key}: quantity {pos.quantity} != sum of fills/"
                f"liquidations {exp}")


def _check_cash_equals_categorized_flows(account) -> None:
    expected = account.expected_cash_from_categories()
    if not _close(account.cash, expected):
        raise InvariantViolation(
            f"cash ({account.cash}) does not equal the sum of deposits/withdrawals/"
            f"trades/fees/funding/borrow ({expected})")


def check(account, ledger: Ledger, margin_config: MarginConfig | None = None) -> None:
    """Run every invariant. Raises InvariantViolation (or lets a more
    specific ValueError/TypeError from a lower layer through) on the first
    violation found -- never swallowed, never downgraded to a warning."""
    margin_config = margin_config or MarginConfig()
    _check_finite_scalars(account)
    _check_positive_prices(account)
    _check_finite_quantities(account)
    _check_order_fill_bounds(account)
    _check_no_naked_short_spot(account)
    _check_margin_non_negative_and_ordered(account, margin_config)
    _check_ledger_integrity(ledger)
    _check_client_order_id_consistency(account)
    _check_positions_equal_sum_of_fills(account, ledger)
    _check_cash_equals_categorized_flows(account)
