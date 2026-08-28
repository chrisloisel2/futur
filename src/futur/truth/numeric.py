"""src/futur/truth/numeric.py -- the ONE sanctioned path from float into
this engine's arithmetic.

`Decimal(0.1)` imports the float's exact binary representation, including
its rounding noise (`Decimal(0.1) ==
Decimal('0.1000000000000000055511151231257827021181583404541015625')`).
`Decimal(str(0.1)) == Decimal('0.1')` -- exact. Every float that enters
this engine (a test literal, a JSON number, an external caller) must go
through `to_decimal()`, never `Decimal(x)` directly on a float.

Money-like fields (cash, fees, funding, borrow, PnL, margin, NAV) quantize
to `CASH_QUANTUM`. Prices and quantities quantize to their ProductSpec's
own `tick_size`/`lot_size` instead (see events.py) -- a fixed global
quantum would be wrong for products with different tick sizes.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

CASH_QUANTUM = Decimal("0.00000001")   # 8 decimal places -- satoshi-level precision


def to_decimal(value: Decimal | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not an accepted numeric input")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def quantize_cash(value: Decimal | float | str) -> Decimal:
    return to_decimal(value).quantize(CASH_QUANTUM, rounding=ROUND_HALF_EVEN)
