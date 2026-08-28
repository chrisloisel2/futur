"""src/futur/truth/reconciliation.py -- compare account state to an external snapshot.

`ExternalSnapshot` represents what an outside source of truth (an exchange,
a manual audit) reports. `reconcile()` is a pure function: it reads the
account and the snapshot, and returns a `ReconciliationResult` describing
every difference found. It never corrects anything -- there is no method
here that mutates `account`. A MISMATCH is a fact to report (typically
recorded as a RECONCILIATION event via `to_event_payload`, through
engine.apply() like any other event), never something this module resolves
on its own.

This is the ONE place in the engine a numeric tolerance is permitted (see
invariants.py's own docstring: internal accounting checks use exact
Decimal equality, never a tolerance). An external source is not guaranteed
to quantize or round the same way this engine does, so an exact comparison
against it would raise false MISMATCHes on harmless last-digit differences
that are purely the external side's own convention. `ToleranceConfig` makes
that tolerance explicit and configurable per (venue, field) -- never a
silent global fudge factor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.futur.truth.events import ReconciliationPayload
from src.futur.truth.numeric import to_decimal
from src.futur.truth.orders import TERMINAL_STATUSES

ZERO = Decimal(0)


@dataclass(frozen=True)
class ToleranceConfig:
    """default: applied to any (venue, field) pair without an explicit
    override. per_venue_field: {(venue, field_name): Decimal} checked first
    -- e.g. {("BINANCE", "cash"): Decimal("0.01")} if that venue's API only
    reports cash to 2 decimal places."""
    default: Decimal = Decimal("0.00000001")
    per_venue_field: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "default", to_decimal(self.default))

    def for_field(self, venue: str, field_name: str) -> Decimal:
        return self.per_venue_field.get((venue, field_name), self.default)


@dataclass(frozen=True)
class ExternalSnapshot:
    venue: str
    cash: Decimal
    nav: Decimal
    spot_positions: dict = field(default_factory=dict)     # ProductSpec.key -> quantity
    perp_positions: dict = field(default_factory=dict)      # ProductSpec.key -> quantity
    open_order_ids: frozenset = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cash", to_decimal(self.cash))
        object.__setattr__(self, "nav", to_decimal(self.nav))
        object.__setattr__(self, "spot_positions",
                          {k: to_decimal(v) for k, v in self.spot_positions.items()})
        object.__setattr__(self, "perp_positions",
                          {k: to_decimal(v) for k, v in self.perp_positions.items()})


@dataclass(frozen=True)
class ReconciliationResult:
    verdict: str    # "MATCH" | "MISMATCH"
    cash_diff: Decimal
    nav_diff: Decimal
    spot_quantity_diffs: dict     # only keys that actually differ
    perp_quantity_diffs: dict
    missing_orders: tuple          # external believes open, account does not
    unknown_orders: tuple           # account believes open, external does not

    def as_details_dict(self) -> dict:
        return {
            "cash_diff": str(self.cash_diff), "nav_diff": str(self.nav_diff),
            "spot_quantity_diffs": {k: str(v) for k, v in self.spot_quantity_diffs.items()},
            "perp_quantity_diffs": {k: str(v) for k, v in self.perp_quantity_diffs.items()},
            "missing_orders": list(self.missing_orders),
            "unknown_orders": list(self.unknown_orders),
        }


def _quantity_diffs(account_positions: dict, external_positions: dict,
                    tolerance: Decimal) -> dict:
    diffs = {}
    for key in set(account_positions) | set(external_positions):
        account_qty = account_positions[key].quantity if key in account_positions else ZERO
        external_qty = external_positions.get(key, ZERO)
        if abs(account_qty - external_qty) > tolerance:
            diffs[key] = account_qty - external_qty
    return diffs


def reconcile(account, external: ExternalSnapshot,
             tolerance_config: ToleranceConfig | None = None) -> ReconciliationResult:
    tolerance_config = tolerance_config or ToleranceConfig()
    venue = external.venue
    cash_tol = tolerance_config.for_field(venue, "cash")
    nav_tol = tolerance_config.for_field(venue, "nav")
    qty_tol = tolerance_config.for_field(venue, "quantity")

    cash_diff = account.cash - external.cash
    nav_diff = account.nav() - external.nav
    spot_diffs = _quantity_diffs(account.spot_positions, external.spot_positions, qty_tol)
    perp_diffs = _quantity_diffs(account.perp_positions, external.perp_positions, qty_tol)

    account_open_order_ids = frozenset(
        oid for oid, order in account.orders.items() if order.status not in TERMINAL_STATUSES)
    missing_orders = tuple(sorted(external.open_order_ids - account_open_order_ids))
    unknown_orders = tuple(sorted(account_open_order_ids - external.open_order_ids))

    is_match = (
        abs(cash_diff) <= cash_tol and abs(nav_diff) <= nav_tol
        and not spot_diffs and not perp_diffs
        and not missing_orders and not unknown_orders
    )
    return ReconciliationResult(
        verdict="MATCH" if is_match else "MISMATCH",
        cash_diff=cash_diff, nav_diff=nav_diff,
        spot_quantity_diffs=spot_diffs, perp_quantity_diffs=perp_diffs,
        missing_orders=missing_orders, unknown_orders=unknown_orders,
    )


def to_event_payload(result: ReconciliationResult) -> ReconciliationPayload:
    return ReconciliationPayload(verdict=result.verdict, details=result.as_details_dict())
