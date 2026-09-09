"""Simulated fills that refuse to invent liquidity.

The rule that matters: an order larger than what sits at the best limit is not
filled at the best limit.  It is filled up to the displayed size and the rest is
**deferred**, not silently executed deeper at a price nobody quoted.

This is not conservatism for its own sake.  Measured on this venue set, 19.4% of
orders exceed the best limit and 63.2% of notional gets deferred once the cap is
the displayed depth rather than open interest.  The median order is about $19,
so it is the large orders that were fictitious.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

BPS = 1e4


@dataclass
class Book:
    """Top of book at the decision instant."""

    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    timestamp: str = ""

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return float("inf")
        return (self.ask - self.bid) / self.mid * BPS


@dataclass
class Fill:
    symbol: str
    side: str
    requested_notional: float
    filled_notional: float
    deferred_notional: float
    price: float
    mid_at_decision: float
    slippage_bps: float
    spread_bps: float
    capped_by_depth: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "requested_notional": self.requested_notional,
            "filled_notional": self.filled_notional,
            "deferred_notional": self.deferred_notional,
            "price": self.price,
            "mid_at_decision": self.mid_at_decision,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "capped_by_depth": self.capped_by_depth,
            "reason": self.reason,
        }


def simulate_taker_fill(
    symbol: str,
    side: str,
    notional: float,
    book: Book,
    max_depth_fraction: float = 1.0,
) -> Fill:
    """Cross the spread against displayed depth only.

    ``max_depth_fraction`` is the share of the displayed best limit this account
    is willing to assume it can take.  Anything beyond it is deferred.
    """
    if side not in ("long", "short", "buy", "sell"):
        raise ValueError("unknown side %r" % (side,))
    buying = side in ("long", "buy")
    price = book.ask if buying else book.bid
    if price <= 0 or book.mid <= 0:
        return Fill(
            symbol, side, notional, 0.0, notional, 0.0, 0.0, 0.0, float("inf"), True,
            reason="no valid book",
        )

    available_notional = (book.ask_qty if buying else book.bid_qty) * price
    cap = available_notional * float(max_depth_fraction)
    filled = min(float(notional), cap)
    deferred = max(0.0, float(notional) - filled)

    signed = 1.0 if buying else -1.0
    slippage_bps = signed * (price - book.mid) / book.mid * BPS

    return Fill(
        symbol=symbol,
        side=side,
        requested_notional=float(notional),
        filled_notional=float(filled),
        deferred_notional=float(deferred),
        price=float(price),
        mid_at_decision=float(book.mid),
        slippage_bps=float(slippage_bps),
        spread_bps=float(book.spread_bps),
        capped_by_depth=bool(deferred > 0),
        reason="capped by displayed depth" if deferred > 0 else "",
    )
