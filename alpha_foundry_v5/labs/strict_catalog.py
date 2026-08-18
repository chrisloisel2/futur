from __future__ import annotations

from dataclasses import replace

from .catalog import LABS as BASE_LABS


LABS = dict(BASE_LABS)

# A3 needs actual trade activity in addition to book depletion observables.
_a3 = LABS["A3"]
LABS["A3"] = replace(
    _a3,
    required_any_groups=_a3.required_any_groups + (("*trade_count*", "*signed_notional*"),),
    activity_requirements=_a3.activity_requirements + (("*trade_count*", 100),),
)

# A7 is a liquidation-vs-capacity mechanism; depth is a hard prerequisite.
_a7 = LABS["A7"]
LABS["A7"] = replace(
    _a7,
    required_any_groups=_a7.required_any_groups + (("*__buy_notional_10bps", "*__sell_notional_10bps", "*depth_*"),),
)

# A8 is joint price/OI/funding state, not a derivatives-only screen.
_a8 = LABS["A8"]
LABS["A8"] = replace(
    _a8,
    required_column_patterns=_a8.required_column_patterns + ("price_fair_value",),
)

# A14 must never match `deriv__*` through the loose `*iv_*` substring.
_a14 = LABS["A14"]
LABS["A14"] = replace(
    _a14,
    required_any_groups=(
        ("option__iv_*", "option__skew*", "option__rr25*"),
        ("option__oi*", "option__gamma*"),
    ),
)
