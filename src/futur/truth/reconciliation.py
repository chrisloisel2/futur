"""src/futur/truth/reconciliation.py -- compare account state to an external snapshot.

`ExternalSnapshot` represents what an outside source of truth (an exchange,
a manual audit) reports. `reconcile()` is a pure function: it reads the
account and the snapshot, and returns a `ReconciliationResult` describing
every difference found. It never corrects anything -- there is no method
here that mutates `account`. A MISMATCH is a fact to report (typically
recorded as a RECONCILIATION event via `to_event_payload`, through
engine.apply() like any other event), never something this module resolves
on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.futur.truth.events import ReconciliationPayload
from src.futur.truth.orders import TERMINAL_STATUSES

_DEFAULT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ExternalSnapshot:
    cash: float
    nav: float
    spot_positions: dict = field(default_factory=dict)     # Instrument.key -> quantity
    perp_positions: dict = field(default_factory=dict)      # Instrument.key -> quantity
    open_order_ids: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class ReconciliationResult:
    verdict: str    # "MATCH" | "MISMATCH"
    cash_diff: float
    nav_diff: float
    spot_quantity_diffs: dict     # only keys that actually differ
    perp_quantity_diffs: dict
    missing_orders: tuple          # external believes open, account does not
    unknown_orders: tuple           # account believes open, external does not

    def as_details_dict(self) -> dict:
        return {
            "cash_diff": self.cash_diff, "nav_diff": self.nav_diff,
            "spot_quantity_diffs": self.spot_quantity_diffs,
            "perp_quantity_diffs": self.perp_quantity_diffs,
            "missing_orders": list(self.missing_orders),
            "unknown_orders": list(self.unknown_orders),
        }


def _quantity_diffs(account_positions: dict, external_positions: dict,
                    tolerance: float) -> dict:
    diffs = {}
    for key in set(account_positions) | set(external_positions):
        account_qty = account_positions[key].quantity if key in account_positions else 0.0
        external_qty = external_positions.get(key, 0.0)
        if abs(account_qty - external_qty) > tolerance:
            diffs[key] = account_qty - external_qty
    return diffs


def reconcile(account, external: ExternalSnapshot,
             tolerance: float = _DEFAULT_TOLERANCE) -> ReconciliationResult:
    cash_diff = account.cash - external.cash
    nav_diff = account.nav() - external.nav
    spot_diffs = _quantity_diffs(account.spot_positions, external.spot_positions, tolerance)
    perp_diffs = _quantity_diffs(account.perp_positions, external.perp_positions, tolerance)

    account_open_order_ids = frozenset(
        oid for oid, order in account.orders.items() if order.status not in TERMINAL_STATUSES)
    missing_orders = tuple(sorted(external.open_order_ids - account_open_order_ids))
    unknown_orders = tuple(sorted(account_open_order_ids - external.open_order_ids))

    is_match = (
        abs(cash_diff) <= tolerance and abs(nav_diff) <= tolerance
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
